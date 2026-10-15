"""HTTP recovery after a real process exit, with synthetic external Adapters."""

import json
import os
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
from fastapi.testclient import TestClient

from learning_library.web import create_app

SOURCE = (
    "# Synthetic recovery\n\nChecks caught errors in one synthetic project.\n\n"
    + "This is synthetic test content; it does not demonstrate real model quality. " * 80
)
QUOTE = "Checks caught errors in one synthetic project."


def isolated_app(root, calls, *, crash=None):
    def source(request):
        calls["source"] += 1
        if crash == "fetch":
            os._exit(73)
        return httpx.Response(200, text=SOURCE, headers={"Content-Type": "text/markdown"})

    def model(request):
        incoming = json.loads(request.content)
        kind = "core" if incoming.get("response_format", {}).get("type") == "json_object" else "translation"
        calls[kind] += 1
        if crash == kind and (kind != "translation" or calls[kind] == 2):
            os._exit(73)
        if kind == "core":
            content = json.dumps({
                "category": "workflow", "recommendation": "read", "reason": "合成项目实践。",
                "reading_kind": "practice", "reading_reason": "合成项目实践。",
                "reading_evidence": {"quote": QUOTE, "supports": "项目观察"},
                "core": "合成项目中的检查发现错误，效果未经独立验证。",
                "evidence": [{"quote": QUOTE, "supports": "项目观察"}],
                "points": [{"text": "检查发现错误。", "claim_type": "author_judgment",
                            "evidence": [{"quote": QUOTE, "supports": "项目观察"}]}],
                "conditions": [], "time_dependencies": [], "unknowns": ["合成数据。"],
            }, ensure_ascii=False)
        else:
            content = "合成译文：" + incoming["messages"][-1]["content"]
        return httpx.Response(200, json={
            "model": "synthetic-recovery-model", "usage": {"total_tokens": 30},
            "choices": [{"finish_reason": "stop", "message": {"role": "assistant", "content": content}}],
        })

    return create_app(root, httpx.MockTransport(source), review_origin="demo",
                      translation_transport=httpx.MockTransport(model))


def crash_during_request(root, phase):
    """The child dies inside an external request, after the durable claim."""
    calls = {"source": 0, "core": 0, "translation": 0}
    with TestClient(isolated_app(Path(root), calls, crash=phase)) as client:
        added = client.post("/api/candidates/process", json={
            "submission_id": "original", "url": "https://example.com/recovery.md",
            "goal": "合成恢复测试。", "origin": "demo", "source_note": "合成故障注入。",
            "as_of": datetime.now(UTC).date().isoformat(),
        }).json()
        if phase != "fetch":
            assert added["version_id"], added
        base = f"/api/runs/{added['pipeline_id']}"
        generated = client.post(base + "/insights", json={"submission_id": "interrupted"})
        assert generated.status_code == 202, generated.text
        if phase == "translation":
            translated = client.post(base + "/translation/start", json={"submission_id": "interrupted"})
            assert translated.status_code == 202, translated.text
        raise AssertionError(f"Interruption was not triggered: {calls}")


def advance_clock(monkeypatch):
    from learning_library import (
        article_insights,
        candidates,
        pipeline,
        storage,
        translation,
        workflows,
    )

    future = datetime.now(UTC) + timedelta(minutes=6)

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return future.astimezone(tz) if tz else future.replace(tzinfo=None)

    for module in (article_insights, candidates, pipeline, storage, translation, workflows):
        monkeypatch.setattr(module, "datetime", Clock, raising=False)


def test_expired_core_is_retryable_in_the_flow_and_preserves_the_original(tmp_path, monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "synthetic-recovery-key")
    child = subprocess.run([
        sys.executable, "-c",
        "import runpy,sys; runpy.run_path(sys.argv[1])['crash_during_request'](sys.argv[2], 'core')",
        str(Path(__file__).resolve()), str(tmp_path),
    ], check=False, capture_output=True, timeout=20, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    assert child.returncode == 73, child.stderr.decode(errors="replace")
    calls = {"source": 0, "core": 0, "translation": 0}
    with TestClient(isolated_app(tmp_path, calls)) as client:
        run = client.get("/api/workflows").json()[0]["run_id"]
        base = f"/api/runs/{run}"
        original = client.get(base + "/artifacts/document").json()
        fresh = client.get(base + "/insights").json()
        assert not fresh["expired"] and fresh["action"] is None
        duplicate = client.post(base + "/insights", json={"submission_id": "too-soon"})
        assert duplicate.status_code == 409
        assert calls == {"source": 0, "core": 0, "translation": 0}
        advance_clock(monkeypatch)
        before = client.get(base + "/insights").json()
        assert before["expired"] and before["action"] == "retry"
        view = client.get(f"/api/workflows/{run}?detail=summary").json()
        operation = next(o for o in view["elt"]["operations"] if o["id"] == "core")
        assert operation["status"] == "failed"
        assert operation["action"]["label"] == "重试核心提取"
        assert calls == {"source": 0, "core": 0, "translation": 0}
        same = client.post(base + "/insights", json={"submission_id": "interrupted"})
        assert same.status_code == 202 and not same.json()["launch"]
        retry = client.post(base + "/insights", json={"submission_id": "explicit-retry"})
        assert retry.status_code == 202 and retry.json()["launch"]
        history = client.get(base + "/insights").json()["history"]
        assert [attempt["status"] for attempt in history] == ["interrupted", "completed"]
        assert history[0]["input"] == before["history"][0]["input"]
        assert client.get(base + "/artifacts/document").json() == original
        assert calls == {"source": 0, "core": 1, "translation": 0}
        assert client.get(base + "/curation").json()["latest"] is None


def test_interrupted_translation_continues_only_the_unsaved_batches(tmp_path, monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "synthetic-recovery-key")
    child = subprocess.run([
        sys.executable, "-c",
        "import runpy,sys; runpy.run_path(sys.argv[1])['crash_during_request'](sys.argv[2], 'translation')",
        str(Path(__file__).resolve()), str(tmp_path),
    ], check=False, capture_output=True, timeout=20, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    assert child.returncode == 73, child.stderr.decode(errors="replace")
    advance_clock(monkeypatch)
    calls = {"source": 0, "core": 0, "translation": 0}
    with TestClient(isolated_app(tmp_path, calls)) as client:
        run = client.get("/api/workflows").json()[0]["run_id"]
        base = f"/api/runs/{run}"
        original = client.get(base + "/artifacts/document").json()
        core = client.get(base + "/insights").json()["latest"]
        partial = client.get(base + "/translation").json()
        assert 0 < partial["completed"] < partial["total"]
        assert [a["status"] for a in partial["attempts"]] == ["succeeded", "running"]
        first_batch = partial["segments"][0]["translation"]
        view = client.get(f"/api/workflows/{run}?detail=summary").json()
        operation = next(o for o in view["elt"]["operations"] if o["id"] == "translate")
        assert operation["status"] == "failed"
        assert operation["action"]["label"] == "继续未完成批次"
        assert calls == {"source": 0, "core": 0, "translation": 0}
        same = client.post(base + "/translation/start", json={"submission_id": "interrupted"})
        assert same.status_code == 202 and not same.json()["launch"]
        retry = client.post(base + "/translation/start", json={"submission_id": "explicit-retry"})
        assert retry.status_code == 202 and retry.json()["launch"]
        done = client.get(base + "/translation").json()
        assert done["id"] == partial["id"] and done["status"] == "succeeded"
        assert done["segments"][0]["translation"] == first_batch
        assert [a["status"] for a in done["attempts"]] == ["succeeded", "failed", "succeeded", "succeeded"]
        assert "中断" in done["attempts"][1]["error"]
        jobs = client.get(base + "/translation/plan").json()["jobs"]
        assert [job["status"] for job in jobs] == ["interrupted", "completed"]
        assert (done["provider"], done["model"], done["effort"]) == (
            partial["provider"], partial["model"], partial["effort"],
        )
        assert client.get(base + "/artifacts/document").json() == original
        assert client.get(base + "/insights").json()["latest"] == core
        assert calls == {"source": 0, "core": 0, "translation": 2}


def test_interrupted_capture_can_be_explicitly_retried_after_the_deadline(tmp_path, monkeypatch):
    child = subprocess.run([
        sys.executable, "-c",
        "import runpy,sys; runpy.run_path(sys.argv[1])['crash_during_request'](sys.argv[2], 'fetch')",
        str(Path(__file__).resolve()), str(tmp_path),
    ], check=False, capture_output=True, timeout=20, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    assert child.returncode == 73, child.stderr.decode(errors="replace")
    advance_clock(monkeypatch)
    calls = {"source": 0, "core": 0, "translation": 0}
    with TestClient(isolated_app(tmp_path, calls)) as client:
        candidate = client.get("/api/candidates").json()[0]
        run = candidate["pipeline_id"]
        assert candidate["processing"]["status"] == "failed"
        assert candidate["processing"]["action"] == "retry"
        view = client.get(f"/api/workflows/{run}?detail=summary").json()
        operation = next(o for o in view["elt"]["operations"] if o["id"] == "fetch")
        assert operation["status"] == "failed" and operation["action"]["label"] == "重试采集"
        assert operation["action"]["kind"] == "capture"
        assert operation["action"]["candidate_id"] == candidate["id"]
        assert "结果未知" in operation["summary"]
        same = client.post(f"/api/candidates/{candidate['id']}/process", json={"submission_id": "original"})
        assert same.status_code == 200
        assert calls == {"source": 0, "core": 0, "translation": 0}
        retry = client.post(f"/api/candidates/{operation['action']['candidate_id']}/process", json={"submission_id": "explicit-retry"})
        assert retry.status_code == 200, retry.text
        result = retry.json()
        assert result["version_id"] and result["processing"]["status"] == "completed"
        assert [job["status"] for job in result["processing"]["runs"]] == ["failed", "completed"]
        attempts = client.get(f"/api/runs/{run}").json()["stages"][0]["attempts"]
        assert [a["status"] for a in attempts] == ["failed", "succeeded"]
        assert "结果未知" in attempts[0]["output"]["error"]
        assert calls == {"source": 1, "core": 0, "translation": 0}
