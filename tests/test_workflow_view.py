"""The visible four-stage interface consumes saved facts without advancing work."""

import json
import sqlite3
from contextlib import closing
from datetime import UTC, datetime

import httpx
import pytest
from fastapi.testclient import TestClient

from learning_library.storage import Store
from learning_library.web import create_app

SOURCE = "# Synthetic practice\n\nKeep the evidence and preserve the conditions.\n\n" + "Synthetic explanatory paragraph. " * 12


@pytest.fixture
def setup(tmp_path, monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "synthetic-secret")
    calls = {"source": 0, "model": 0}
    judgment = {
        "category": "workflow", "recommendation": "read",
        "learning_value": "合成实践说明", "reason": "用来验证已有筛选结果的展示。",
        "evidence": [{"quote": "Keep the evidence and preserve the conditions.", "supports": "条件保留"}],
        "conditions": ["仅供测试"], "time_dependencies": [], "unknowns": ["没有真实质量结论"],
    }

    def fetch(request):
        calls["source"] += 1
        return httpx.Response(200, text=SOURCE, headers={"Content-Type": "text/markdown"})

    def respond(request):
        calls["model"] += 1
        return httpx.Response(200, json={
            "id": "synthetic", "model": "fixture-model",
            "usage": {"prompt_tokens": 40, "completion_tokens": 30, "total_tokens": 70},
            "choices": [{"finish_reason": "stop", "message": {
                "role": "assistant", "content": json.dumps(judgment),
            }}],
        })

    with TestClient(create_app(
        tmp_path, httpx.MockTransport(fetch), translation_transport=httpx.MockTransport(respond),
    )) as client:
        candidate = client.post("/api/candidates/process", json={
            "submission_id": "fixture-import", "url": "https://example.com/practice.md",
            "goal": "了解实践方法", "origin": "demo", "source_note": "Explicitly synthetic source",
            "as_of": datetime.now(UTC).date().isoformat(),
        }).json()
        path = "/api/workflows/" + candidate["pipeline_id"]
        yield client, candidate, path, calls, judgment, tmp_path


def screen(client, candidate):
    response = client.post("/api/candidates/" + candidate["id"] + "/review", json={"submission_id": "fixture-screen"})
    assert response.status_code == 200, response.text
    return response.json()


def rows(root):
    with closing(sqlite3.connect(root / "library.sqlite3")) as db:
        return {
            name: db.execute("SELECT * FROM " + name + " ORDER BY rowid").fetchall()
            for (name,) in db.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }


def test_read_only_current_stages_preserve_call_records_and_labels(setup):
    client, candidate, path, calls, _, root = setup
    state = screen(client, candidate)
    first = client.get(path).json()
    before = rows(root)
    for _ in range(2):
        assert client.get(path).json() == first
        summary = client.get("/api/workflows").json()[0]
        assert summary["created"] == first["created"]
        assert summary["steps"] == [{k: s[k] for k in ("id", "title", "status", "label", "summary")} for s in first["steps"]]
    assert calls == {"source": 1, "model": 1}
    assert rows(root) == before
    assert [s["status"] for s in first["steps"]] == ["completed", "completed", "not_started", "waiting"]
    assert first["read_only"] and first["provenance"]["origin"] == "demo"
    assert "Keep the evidence" in first["steps"][0]["output"]["source_html"]
    assert first["steps"][1]["trace"]["selected_attempt_id"] == state["latest"]["id"]
    assert first["steps"][1]["output"]["usage"]["total_tokens"] == 70
    assert first["steps"][3]["output"] is None


def test_invalid_quotes_stop_before_translation_instead_of_reporting_execution_failure(setup):
    client, candidate, path, calls, judgment, _ = setup
    judgment["evidence"][0]["quote"] = "This quotation does not occur in the saved source."
    screen(client, candidate)
    view = client.get(path).json()
    assert view["status"] == "stopped"
    assert view["steps"][1]["status"] == "stopped"
    assert view["steps"][1]["output"]["effective_recommendation"] == "uncertain"
    assert view["steps"][2]["status"] == "not_started"
    assert "未执行翻译" in view["steps"][2]["summary"]
    assert view["steps"][2]["trace"]["native"]["attempts"] == []
    assert calls == {"source": 1, "model": 1}


def test_reextracted_reader_and_workflow_keep_explicit_discovery_without_old_judgment(setup):
    client, candidate, _, calls, _, root = setup
    screen(client, candidate)
    current = candidate["pipeline_id"]
    for _ in range(2):
        current = client.post(f"/api/runs/{current}/reextract").json()["id"]
        extracted = client.post(f"/api/runs/{current}/stages/extract").json()
        assert extracted["stages"][1]["status"] == "succeeded"
    article = client.get(f"/api/library/{current}").json()
    workflow = client.get(f"/api/workflows/{current}?detail=summary").json()
    before = rows(root)  # First access may initialize empty module tables.
    assert client.get(f"/api/library/{current}").json() == article
    assert client.get(f"/api/workflows/{current}?detail=summary").json() == workflow
    assert article["discovery"]["candidate_id"] == candidate["id"]
    assert article["discovery"]["pipeline_id"] == candidate["pipeline_id"]
    assert article["discovery"]["source_note"] == "Explicitly synthetic source"
    assert workflow["provenance"] == article["discovery"]
    assert workflow["candidate_id"] is None  # Discovery does not move an old screening.
    assert workflow["steps"][1]["status"] == "not_started"
    assert rows(root) == before
    assert calls == {"source": 1, "model": 1}


def test_translation_output_and_old_source_results_are_not_mixed(setup):
    client, candidate, path, calls, _, root = setup
    screen(client, candidate)
    task = client.get("/api/runs/" + candidate["pipeline_id"] + "/processing/task").json()
    payload = {
        "task_key": task["task_key"], "submission_id": "synthetic-copy",
        "origin": "demo", "executor": "Test fixture", "model": None,
        "segments": [{"index": p["index"], "text": p["source"]} for p in task["segments"]],
    }
    assert client.post("/api/runs/" + candidate["pipeline_id"] + "/processing/results", json=payload).status_code == 200
    view = client.get(path).json()
    assert view["steps"][2]["status"] == "completed"
    assert "Keep the evidence" in view["steps"][2]["output"]["translation_html"]
    assert view["steps"][3]["status"] == "pending_review"  # No inferred human acceptance.
    with closing(Store(root)) as store:
        version = store.version(candidate["version_id"])
        store.save(version["document_key"], version["document"] | {"text": SOURCE + "\nNew source material."}, None)
        new_id = store.documents()[0]["version_id"]
        old = store.db.execute(
            "SELECT input,output FROM pipeline_attempts WHERE pipeline_id=? AND stage='extract' ORDER BY id DESC LIMIT 1",
            (candidate["pipeline_id"],),
        ).fetchone()
        output = json.loads(old["output"]) | {"version_ids": [new_id]}
        with store.db:
            store.db.execute(
                "INSERT INTO pipeline_attempts(pipeline_id,stage,status,started,finished,input,output) VALUES(?,?,?,?,?,?,?)",
                (candidate["pipeline_id"], "extract", "succeeded", "fixture", "fixture", old["input"], json.dumps(output)),
            )
    fresh = client.get(path).json()
    assert fresh["version_id"] == new_id
    assert fresh["steps"][1]["status"] == "not_started"  # Candidate still bound to old version.
    assert fresh["steps"][2]["output"]["translation_html"] is None
    assert fresh["steps"][3]["status"] == "waiting"
    assert calls == {"source": 1, "model": 1}


def test_legacy_entry_redirects_keep_the_selected_material_and_remove_label_ui(setup):
    client, candidate, path, _, _, _ = setup
    state = screen(client, candidate)
    case = client.post("/api/evaluations", json={
        "screening_id": state["latest"]["id"], "split": "development",
    }).json()
    for entry in (
        "/workbench?run=" + candidate["pipeline_id"],
        "/candidates?id=" + candidate["id"],
        "/evaluations?case=" + str(case["id"]),
    ):
        response = client.get(entry, follow_redirects=False)
        assert response.status_code == 307
        assert response.headers["location"] == "/pipeline?run=" + candidate["pipeline_id"]
        page = client.get(entry)
        assert page.status_code == 200
        assert "flow-steps" in page.text and "label-form" not in page.text
        assert "本人评测标签" not in page.text and 'href="/evaluations' not in page.text
    assert client.get(path).status_code == 200
    assert client.get("/api/workflows/unknown").status_code == 400
