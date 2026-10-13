"""Exercise the same start/read Interface as the browser; all material is synthetic."""

import json
from contextlib import closing
from datetime import UTC, datetime

import httpx
import pytest
from fastapi.testclient import TestClient

from learning_library.pipeline import Pipeline
from learning_library.review import ReviewConflict
from learning_library.storage import Store
from learning_library.web import create_app
from learning_library.workflows import Workflows

SOURCE = "# Synthetic workflow\n\nKeep the evidence and preserve the conditions.\n\n" + ("An explicit synthetic practice paragraph with useful details. " * 25 + "\n\n") * 6


@pytest.fixture
def scenario(tmp_path, monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "synthetic-secret")
    controls = {"source": SOURCE, "recommendation": "read", "quote": "Keep the evidence and preserve the conditions.", "fail_screen": False, "fail_translation": 0, "change_source": False}
    calls = {"source": 0, "screen": 0, "translate": 0}

    def fetch(request):
        calls["source"] += 1
        return httpx.Response(200, text=controls["source"], headers={"Content-Type": "text/markdown"})

    def respond(request):
        body = json.loads(request.content)
        if "译者" in body["messages"][0]["content"]:
            calls["translate"] += 1
            if controls["change_source"]:
                controls["change_source"] = False
                with closing(Store(tmp_path)) as store:
                    version = store.version(candidate["version_id"] or store.documents()[0]["version_id"])
                    store.save(version["document_key"], version["document"] | {"text": SOURCE + "\nNew version."}, None)
                    latest = store.documents()[0]["version_id"]
                    old = store.db.execute("SELECT input,output FROM pipeline_attempts WHERE pipeline_id=? AND stage='extract' ORDER BY id DESC LIMIT 1", (candidate["pipeline_id"],)).fetchone()
                    with store.db:
                        store.db.execute(
                            "INSERT INTO pipeline_attempts(pipeline_id,stage,status,started,finished,input,output) VALUES(?,?,?,?,?,?,?)",
                            (candidate["pipeline_id"], "extract", "succeeded", "fixture", "fixture", old["input"], json.dumps(json.loads(old["output"]) | {"version_ids": [latest]})),
                        )
            if calls["translate"] == controls["fail_translation"]:
                raise httpx.ReadTimeout("synthetic timeout with synthetic-secret")
            # Echo masked prose solely to test structural checks, not translation quality.
            content = body["messages"][1]["content"]
        else:
            calls["screen"] += 1
            if controls["fail_screen"]:
                controls["fail_screen"] = False
                raise httpx.ReadTimeout("synthetic timeout with synthetic-secret")
            content = json.dumps({
                "category": "workflow", "recommendation": controls["recommendation"],
                "learning_value": "合成材料", "reason": "验证自动流程，不代表真实质量。",
                "evidence": [{"quote": controls["quote"], "supports": "合成测试条件"}],
                "conditions": ["仅供测试"], "time_dependencies": [], "unknowns": ["尚无真实质量结论"],
            })
        return httpx.Response(200, json={"id": "fixture", "model": "fixture",
            "usage": {"prompt_tokens": 50, "completion_tokens": 30, "total_tokens": 80},
            "choices": [{"finish_reason": "stop", "message": {"role": "assistant", "content": content}}]})

    transport, model = httpx.MockTransport(fetch), httpx.MockTransport(respond)
    with TestClient(create_app(tmp_path, transport, translation_transport=model)) as client:
        candidate = client.post("/api/candidates", json={
            "submission_id": "fixture-import", "url": "https://example.com/workflow.md",
            "goal": "学习实践方法", "origin": "demo", "source_note": "Explicitly synthetic source",
            "as_of": datetime.now(UTC).date().isoformat(),
        }).json()
        yield client, candidate, "/api/workflows/" + candidate["pipeline_id"], calls, controls, tmp_path, transport, model


def start(client, path, submission):
    response = client.post(path + "/start", json={"submission_id": submission})
    assert response.status_code == 202, response.text
    return client.get(path).json()


def test_automatic_success_and_replayed_submission_do_not_repeat_calls_or_confirm_user(scenario):
    client, _, path, calls, _, root, _, _ = scenario
    view = start(client, path, "first")
    assert [s["status"] for s in view["steps"]] == ["completed", "completed", "completed", "pending_review"]
    assert view["execution"]["latest"]["status"] == "pending_review"
    assert calls["source"] == calls["screen"] == 1 and calls["translate"] >= 2
    before = calls.copy()
    assert client.post(path + "/start", json={"submission_id": "first"}).json()["launch"] is False
    assert client.post(path + "/start", json={"submission_id": "another"}).status_code == 400
    assert client.get(path).json() == view
    assert calls == before
    with closing(Store(root)) as store:
        assert store.db.execute("SELECT count(*) FROM processing_results").fetchone()[0] == 1
        assert store.db.execute("SELECT count(*) FROM processing_reviews").fetchone()[0] == 0
        assert store.db.execute("SELECT count(*) FROM curation_records").fetchone()[0] == 0


@pytest.mark.parametrize("mode", ["skip", "invalid_quote"])
def test_stop_before_translation_after_negative_or_unsupported_screening(scenario, mode):
    client, _, path, calls, controls, _, _, _ = scenario
    if mode == "skip":
        controls["recommendation"] = "skip"
    else:
        controls["quote"] = "Invented quotation absent from the original."
    view = start(client, path, "stopping")
    assert view["execution"]["latest"]["status"] == "stopped"
    assert view["steps"][1]["status"] == "stopped" and view["execution"]["action"] is None
    assert calls == {"source": 1, "screen": 1, "translate": 0}


def test_translation_failure_retries_only_failed_and_pending_batches(scenario):
    client, _, path, calls, controls, _, _, _ = scenario
    controls["fail_translation"] = 2
    failed = start(client, path, "first")
    attempts = failed["steps"][2]["trace"]["native"]["attempts"]
    first = attempts[0]
    assert len(attempts) == 2 and first["status"] == "succeeded" and attempts[1]["status"] == "failed"
    assert failed["execution"]["latest"]["stage"] == "translate"
    assert failed["execution"]["action"] == "retry" and "synthetic-secret" not in json.dumps(failed)
    assert client.post(path + "/start", json={"submission_id": "first"}).json()["launch"] is False
    assert calls == {"source": 1, "screen": 1, "translate": 2}
    completed = start(client, path, "retry")
    assert completed["steps"][2]["trace"]["native"]["attempts"][0] == first
    assert completed["steps"][3]["status"] == "pending_review"
    assert calls["source"] == calls["screen"] == 1
    assert completed["steps"][2]["trace"]["native"]["attempts"][2]["segment"] == attempts[1]["segment"]


def test_screen_timeout_reuses_original_on_explicit_retry(scenario):
    client, _, path, calls, controls, _, _, _ = scenario
    controls["fail_screen"] = True
    failed = start(client, path, "first")
    assert failed["execution"]["latest"]["stage"] == "screen"
    assert calls == {"source": 1, "screen": 1, "translate": 0}
    original = failed["steps"][0]
    succeeded = start(client, path, "retry")
    assert succeeded["steps"][0] == original
    assert calls["source"] == 1 and calls["screen"] == 2
    assert succeeded["steps"][3]["status"] == "pending_review"


def test_claim_blocks_concurrent_starts_and_worker_replay(scenario):
    _, candidate, _, calls, _, root, transport, model = scenario
    with closing(Store(root)) as store, httpx.Client(transport=transport) as client:
        module = Workflows(Pipeline(store, client), transport=model)
        identifier = module.begin(candidate["pipeline_id"], "first")["id"]
        assert module.begin(candidate["pipeline_id"], "first") == {"id": identifier, "launch": False}
        with pytest.raises(ReviewConflict):
            module.begin(candidate["pipeline_id"], "second")
        module.run(identifier)
        before = calls.copy()
        module.run(identifier)
        assert calls == before


def test_call_budget_stops_before_any_model_call(scenario):
    client, _, path, calls, controls, _, _, _ = scenario
    controls["source"] = SOURCE * 14
    failed = start(client, path, "too-large")
    assert failed["steps"][0]["status"] == "completed"
    assert failed["execution"]["latest"]["status"] == "failed"
    assert "上限" in failed["execution"]["latest"]["error"]
    assert calls == {"source": 1, "screen": 0, "translate": 0}


def test_source_change_during_translation_stops_before_mixing_versions(scenario):
    client, _, path, calls, controls, _, _, _ = scenario
    controls["change_source"] = True
    view = start(client, path, "version-change")
    assert view["execution"]["latest"]["status"] == "failed"
    assert "原文版本改变" in view["execution"]["latest"]["error"]
    assert view["steps"][1]["status"] == "not_started"
    assert view["steps"][2]["output"]["translation_html"] is None
    assert view["steps"][3]["status"] == "waiting"
    assert calls == {"source": 1, "screen": 1, "translate": 1}


def test_expired_claim_requires_new_submission_and_old_worker_does_no_work(scenario):
    _, candidate, _, calls, _, root, transport, model = scenario
    with closing(Store(root)) as store, httpx.Client(transport=transport) as client:
        module = Workflows(Pipeline(store, client), transport=model)
        old = module.begin(candidate["pipeline_id"], "old")["id"]
        with store.db:
            store.db.execute("UPDATE workflow_runs SET updated='2000-01-01T00:00:00+00:00' WHERE id=?", (old,))
        fresh = module.begin(candidate["pipeline_id"], "explicit-retry")["id"]
        module.run(old)
        assert calls == {"source": 0, "screen": 0, "translate": 0}
        module.run(fresh)
        state = module.inspect(candidate["pipeline_id"])
        assert state["execution"]["history"][0]["status"] == "interrupted"
        assert state["execution"]["latest"]["status"] == "pending_review"
