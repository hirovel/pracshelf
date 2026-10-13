"""User-facing processing: real storage, isolated sources, counted external calls."""

import json
from datetime import UTC, datetime

import httpx
import pytest
from fastapi.testclient import TestClient

from learning_library.web import create_app

TEXT = (
    "# Synthetic workflow\n\n"
    + "Preserve the source and check the conditions before adopting a prompt. " * 8
)


@pytest.fixture
def setup(tmp_path, monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "synthetic-test-key")
    counts = {"fetch": 0, "model": 0}
    failures = {"fetch": False, "model": False}

    def fetch(request):
        counts["fetch"] += 1
        return httpx.Response(
            503 if failures["fetch"] else 200, text=TEXT, headers={"Content-Type": "text/markdown"}
        )

    def model(request):
        counts["model"] += 1
        if failures["model"]:
            raise httpx.ReadTimeout("Synthetic timeout", request=request)
        judgment = {
            "category": "workflow",
            "recommendation": "uncertain",
            "learning_value": "合成测试资料",
            "reason": "效果没有验证",
            "evidence": [
                {
                    "quote": "Preserve the source and check the conditions",
                    "supports": "保留来源和条件",
                }
            ],
            "conditions": [],
            "time_dependencies": [],
            "unknowns": ["效果未知"],
        }
        return httpx.Response(
            200,
            json={
                "model": "fixture",
                "usage": {"prompt_tokens": 30, "completion_tokens": 20, "total_tokens": 50},
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {"role": "assistant", "content": json.dumps(judgment)},
                    }
                ],
            },
        )

    app = create_app(
        tmp_path, httpx.MockTransport(fetch), translation_transport=httpx.MockTransport(model)
    )
    payload = {
        "submission_id": "add-one",
        "url": "https://example.com/workflow.md",
        "goal": "学习开发工作流",
        "origin": "demo",
        "source_note": "合成测试",
        "as_of": datetime.now(UTC).date().isoformat(),
        "max_age_days": 90,
    }
    with TestClient(app) as client:
        yield client, payload, counts, failures


def stage_counts(result):
    return {s["id"]: s["attempts"] for s in result["processing"]["stages"]}


def test_default_save_never_calls_model_even_without_a_key(setup, monkeypatch):
    client, payload, counts, _ = setup
    monkeypatch.delenv("DEEPSEEK_API_KEY")
    result = client.post("/api/candidates/process", json=payload).json()
    assert result["processing"]["status"] == "completed"
    assert result["latest"] is None
    assert result["processing"]["action"] is None
    assert result["screening_action"] == "start"
    assert result["date_check"]["status"] == "unknown_date"
    assert stage_counts(result) == {"fetch": 1, "extract": 1}
    replay = client.post("/api/candidates/process", json=payload).json()
    assert replay["id"] == result["id"]
    client.post(f"/api/candidates/{result['id']}/process", json={"submission_id": "new-click"})
    assert counts == {"fetch": 1, "model": 0}
    assert client.get(f"/api/library/{result['pipeline_id']}").status_code == 200
    assert client.get(f"/api/runs/{result['pipeline_id']}/curation").json()["latest"] is None


def test_optional_model_timeout_retry_preserves_original_and_failure(setup):
    client, payload, counts, failures = setup
    saved = client.post("/api/candidates/process", json=payload).json()
    failures["model"] = True
    url = f"/api/candidates/{saved['id']}/review"
    failed = client.post(url, json={"submission_id": "screen-one"}).json()
    assert failed["processing"]["status"] == "completed"
    assert failed["screening_action"] == "retry"
    assert "超时" in failed["latest"]["error"]
    assert failed["latest"]["response"]["request_count"] == 1
    assert failed["latest"]["response"]["http_status"] is None
    assert client.get(f"/api/library/{saved['pipeline_id']}").status_code == 200
    client.post(url, json={"submission_id": "screen-one"})
    client.post("/api/candidates/process", json=payload)
    assert counts == {"fetch": 1, "model": 1}
    failures["model"] = False
    result = client.post(url, json={"submission_id": "retry-screen"}).json()
    assert result["status"] == "completed"
    assert result["screening_action"] is None
    assert result["version_id"] == saved["version_id"]
    assert stage_counts(result) == {"fetch": 1, "extract": 1}
    assert result["attempts"][0] == failed["attempts"][0]
    assert len(result["attempts"]) == 2
    client.post(url, json={"submission_id": "retry-screen"})
    assert counts == {"fetch": 1, "model": 2}


def test_fetch_failure_blocks_downstream_then_resumes(setup):
    client, payload, counts, failures = setup
    failures["fetch"] = True
    failed = client.post("/api/candidates/process", json=payload).json()
    assert failed["processing"]["stage"] == "fetch"
    assert stage_counts(failed) == {"fetch": 1, "extract": 0}
    client.post("/api/candidates/process", json=payload)
    assert counts == {"fetch": 1, "model": 0}
    failures["fetch"] = False
    result = client.post(
        f"/api/candidates/{failed['id']}/process", json={"submission_id": "retry-fetch"}
    ).json()
    assert stage_counts(result) == {"fetch": 2, "extract": 1}
    assert counts == {"fetch": 2, "model": 0}


def test_extraction_failure_reuses_saved_response(setup, monkeypatch):
    from learning_library import collector

    client, payload, counts, _ = setup
    original = collector.extract_document

    def broken(*args, **kwargs):
        raise ValueError("Synthetic parser failure")

    monkeypatch.setattr(collector, "extract_document", broken)
    failed = client.post("/api/candidates/process", json=payload).json()
    assert failed["processing"]["stage"] == "extract"
    assert counts == {"fetch": 1, "model": 0}
    monkeypatch.setattr(collector, "extract_document", original)
    result = client.post(
        f"/api/candidates/{failed['id']}/process", json={"submission_id": "retry-extract"}
    ).json()
    assert stage_counts(result) == {"fetch": 1, "extract": 2}
    assert counts == {"fetch": 1, "model": 0}


def test_duplicate_in_flight_submission_cannot_start_a_second_run(setup, monkeypatch):
    from learning_library.candidates import Candidates

    client, payload, counts, _ = setup
    capture = Candidates.capture

    def during_capture(module, candidate_id):
        url = f"/api/candidates/{candidate_id}/process"
        replay = client.post(url, json={"submission_id": payload["submission_id"]})
        assert replay.status_code == 200
        assert replay.json()["processing"]["status"] == "running"
        competing = client.post(url, json={"submission_id": "second-tab"})
        assert competing.status_code == 409
        return capture(module, candidate_id)

    monkeypatch.setattr(Candidates, "capture", during_capture)
    result = client.post("/api/candidates/process", json=payload).json()
    assert result["processing"]["status"] == "completed"
    assert len(result["processing"]["runs"]) == 1
    assert counts == {"fetch": 1, "model": 0}


def test_filters_search_and_optional_screening_are_independent(setup):
    client, payload, counts, failures = setup
    saved = client.post("/api/candidates/process", json=payload).json()
    failures["fetch"] = True
    failed = client.post(
        "/api/candidates/process",
        json=payload
        | {
            "submission_id": "failed-link",
            "url": "https://other.example/broken.md",
            "origin": "agent_supplied",
            "source_note": "独立作者实践",
        },
    ).json()

    def ids(**params):
        response = client.get("/api/candidates", params=params)
        assert response.status_code == 200
        return [r["id"] for r in response.json()]

    assert ids(state="saved") == [saved["id"]]
    assert ids(state="failed") == [failed["id"]]
    assert set(ids(state="unscreened")) == {saved["id"], failed["id"]}
    assert ids(q="SYNTHETIC WORKFLOW", state="saved", freshness="unknown_date", origin="demo") == [
        saved["id"]
    ]
    assert ids(q="独立作者", origin="agent_supplied") == [failed["id"]]
    assert ids(q="no match") == []
    assert ids(freshness="within_window") == []
    assert counts["model"] == 0
    client.post(f"/api/candidates/{saved['id']}/review", json={"submission_id": "optional"})
    assert ids(state="unscreened") == [failed["id"]]
    assert ids(state="saved") == [saved["id"]]
    assert client.get("/api/candidates", params={"state": "invalid"}).status_code == 400
