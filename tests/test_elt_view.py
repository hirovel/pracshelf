"""ELT reads saved evidence. Synthetic HTTP/model data; no real calls or human records."""

import json
import sqlite3

import httpx
import pytest
from fastapi.testclient import TestClient

from learning_library.web import create_app

SOURCE = "# Synthetic developer practice\n\nKeep the evidence and preserve the conditions.\n\n" + "Concrete implementation. " * 50


def snapshot(root):
    with sqlite3.connect(root / "library.sqlite3") as db:
        return {name: db.execute('SELECT * FROM "' + name + '" ORDER BY rowid').fetchall()
                for (name,) in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}


@pytest.fixture
def setup(tmp_path, monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "synthetic-key")
    calls = {"source": 0, "model": 0}
    mode = {"status": 200, "body": SOURCE, "timeout": False}

    def fetch(request):
        calls["source"] += 1
        return httpx.Response(mode["status"], text=mode["body"], headers={"Content-Type": "text/markdown"})

    def model(request):
        calls["model"] += 1
        if mode["timeout"]:
            raise httpx.ReadTimeout("synthetic timeout", request=request)
        quote = {"quote": "Keep the evidence and preserve the conditions.", "supports": "合成原文依据"}
        output = {"category": "workflow", "recommendation": "read", "reason": "合成具体实践。",
                  "reading_kind": "practice", "reading_reason": "合成实践文章。", "reading_evidence": quote,
                  "core": "保留依据与限制，合成内容供行为验收。", "evidence": [quote],
                  "points": [{"text": "保留原文条件。", "claim_type": "author_judgment", "evidence": [quote]}],
                  "conditions": [], "time_dependencies": [], "unknowns": ["这是合成测试。"]}
        return httpx.Response(200, json={"id": "synthetic", "model": "synthetic-model",
             "choices": [{"finish_reason": "stop", "message": {"role": "assistant", "content": json.dumps(output)}}]})

    with TestClient(create_app(tmp_path, httpx.MockTransport(fetch),
                               translation_transport=httpx.MockTransport(model))) as client:
        run = client.post("/api/runs", json={"url": "https://example.com/synthetic.md"}).json()["id"]
        yield client, run, calls, mode, tmp_path


def operations(client, run):
    elt = client.get("/api/workflows/" + run).json()["elt"]
    return elt, {o["id"]: o for o in elt["operations"]}


def test_loaded_raw_exists_before_transform_and_repeat_read_does_no_work(setup):
    client, run, calls, _, root = setup
    base = "/api/runs/" + run
    client.post(base + "/stages/fetch")
    elt, ops = operations(client, run)
    assert [p["id"] for p in elt["phases"]] == ["E", "L", "T"]
    assert ops["fetch"]["status"] == ops["load"]["status"] == "completed"
    assert ops["fetch"]["execution_group"] == ops["load"]["execution_group"] == "capture"
    assert ops["load"]["output"]["complete"] and ops["load"]["output"]["bytes"] == len(SOURCE.encode())
    assert ops["clean"]["status"] == "not_started"
    assert not elt["result"]["original"] and ops["translate"]["status"] == "not_enabled"
    before = snapshot(root)
    assert operations(client, run)[0] == elt
    assert snapshot(root) == before and calls == {"source": 1, "model": 0}
    assert client.get(ops["load"]["output"]["url"]).text == SOURCE


def test_failed_transform_retains_load_and_retry_uses_saved_snapshot(setup, monkeypatch):
    client, run, calls, _, _ = setup
    from learning_library import collector
    original = collector.extract_document
    def broken(*args):
        raise ValueError("Synthetic parser failure")
    monkeypatch.setattr(collector, "extract_document", broken)
    base = "/api/runs/" + run
    client.post(base + "/stages/fetch")
    client.post(base + "/stages/extract")
    failed, ops = operations(client, run)
    assert failed["status"] == "failed" and ops["clean"]["status"] == "failed"
    assert ops["load"]["status"] == "completed" and ops["load"]["output"]["complete"]
    saved = ops["load"]["output"]
    overview = client.get("/api/workflows/" + run).json()["pipeline_view"]
    assert [node["id"] for node in overview["nodes"]] == ["original", "core", "translate"]
    assert overview["nodes"][0]["operation_id"] == "clean"
    assert overview["nodes"][0]["status"] == "failed"
    assert ops[overview["nodes"][0]["operation_id"]]["action"]["stage"] == "extract"
    monkeypatch.setattr(collector, "extract_document", original)
    client.post(base + "/stages/extract")
    _, after = operations(client, run)
    assert after["clean"]["status"] == "completed" and after["load"]["output"] == saved
    assert after["clean"]["detail"]["attempts"] == 2 and calls == {"source": 1, "model": 0}
    overview = client.get("/api/workflows/" + run).json()["pipeline_view"]
    assert overview["nodes"][0]["status"] == "completed"
    assert overview["nodes"][2]["optional"] is True


def test_http_error_is_saved_but_not_complete_original(setup):
    client, run, calls, mode, _ = setup
    mode.update(status=503, body="Synthetic upstream failure")
    client.post("/api/runs/" + run + "/stages/fetch")
    elt, ops = operations(client, run)
    assert ops["fetch"]["status"] == "failed" and elt["status"] == "failed"
    assert ops["load"]["status"] == "retained" and not ops["load"]["output"]["complete"]
    assert ops["clean"]["status"] == "waiting" and not elt["result"]["original"]
    assert calls == {"source": 1, "model": 0}
    before = snapshot(setup[-1])
    node = client.get("/api/workflows/" + run).json()["pipeline_view"]["nodes"][0]
    assert node["operation_id"] == "fetch" and node["status"] == "failed"
    assert ops[node["operation_id"]]["action"]["stage"] == "fetch"
    assert snapshot(setup[-1]) == before and calls == {"source": 1, "model": 0}


def test_core_is_ready_without_translation_and_failed_model_reuses_loaded_source(setup):
    client, run, calls, mode, _ = setup
    base = "/api/runs/" + run
    client.post(base + "/stages/fetch")
    client.post(base + "/stages/extract")
    mode["timeout"] = True
    client.post(base + "/insights", json={"submission_id": "failure"})
    failed, ops = operations(client, run)
    assert ops["core"]["status"] == "failed" and failed["status"] == "failed"
    assert ops["load"]["status"] == ops["clean"]["status"] == "completed"
    saved = (ops["load"]["output"], ops["clean"]["output"])
    mode["timeout"] = False
    client.post(base + "/insights", json={"submission_id": "retry"})
    elt, ops = operations(client, run)
    assert elt["status"] == "completed" and elt["summary"] == "原文与核心可阅读"
    assert elt["result"] == {"original": True, "core": True, "translation": False,
                             "version_id": ops["clean"]["output"]["version_id"], "url": "/read/" + run,
                             "personal_notes": "optional; never generated by this view"}
    assert ops["translate"]["status"] == "not_enabled" and not ops["translate"]["required"]
    assert (ops["load"]["output"], ops["clean"]["output"]) == saved
    assert ops["core"]["detail"]["attempts"] == 2 and calls == {"source": 1, "model": 2}


def test_actions_are_supplied_by_the_server_and_target_only_unfinished_work(setup):
    client, run, calls, _, _ = setup
    base = "/api/runs/" + run
    _, empty = operations(client, run)
    assert empty["fetch"]["action"]["stage"] == "fetch"
    assert empty["load"]["action"] is None and empty["clean"]["action"] is None
    client.post(base + "/stages/fetch")
    _, loaded = operations(client, run)
    assert loaded["fetch"]["action"] is None and loaded["clean"]["action"]["stage"] == "extract"
    assert loaded["core"]["action"] is None
    client.post(base + "/stages/extract")
    _, cleaned = operations(client, run)
    assert cleaned["clean"]["action"] is None and cleaned["core"]["action"]["kind"] == "insights"
    assert not cleaned["core"]["input"]["request_frozen"]
    client.post(base + "/insights", json={"submission_id": "once"})
    _, ready = operations(client, run)
    assert all(operation["action"] is None for key, operation in ready.items() if key != "translate")
    assert ready["translate"]["action"]["kind"] == "translation"
    assert ready["core"]["input"]["request_frozen"]
    assert calls == {"source": 1, "model": 1}
