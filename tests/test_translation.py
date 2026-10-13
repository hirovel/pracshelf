"""Translation behavior through HTTP, with explicitly synthetic model responses."""

import hashlib
import json
import sqlite3

import httpx
import pytest
from fastapi.testclient import TestClient

from learning_library.web import create_app

SOURCE = (
    "# Synthetic translation fixture\n\n"
    "This is a test, not verified advice. Keep `--safe` and https://example.com/docs.\n\n"
    "```python\ndef run():\n    return True\n```\n\n"
    "Only under these conditions might the method help. It is not guaranteed.\n"
)


def reply(request, **changes):
    incoming = json.loads(request.content)
    return httpx.Response(
        200,
        json={
            "model": "synthetic-test",
            "usage": {"total_tokens": 42},
            "choices": [
                {
                    "finish_reason": "stop",
                    "message": {"content": "模拟译文：" + incoming["messages"][-1]["content"]},
                    **changes,
                }
            ],
        },
    )


@pytest.fixture
def configured(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key-never-persist")
    monkeypatch.delenv("DEEPSEEK_MODEL", raising=False)


def app(root, handler=reply):
    return create_app(
        root,
        httpx.MockTransport(
            lambda r: httpx.Response(
                200, text=SOURCE, headers={"Content-Type": "text/markdown; charset=utf-8"}
            )
        ),
        translation_transport=httpx.MockTransport(handler),
    )


def capture(client):
    run = client.post("/api/runs", json={"url": "https://example.com/article.md"}).json()
    base = f"/api/runs/{run['id']}"
    client.post(base + "/stages/fetch")
    client.post(base + "/stages/extract")
    return base


def test_translation_preserves_source_code_links_and_records_observed_calls(tmp_path, configured):
    calls = []

    def model(request):
        calls.append(json.loads(request.content))
        return reply(request)

    with TestClient(app(tmp_path, model)) as client:
        base = capture(client)
        original = client.get(base + "/artifacts/document").json()
        assert client.get(base + "/translation").json()["status"] == "not_started"
        assert calls == []
        part = client.post(base + "/translation/next").json()
        assert part["status"] == "partial" and part["text"] is None
        assert client.get(base + "/processing").json()["results"] == []
        assert client.get(base + "/translation/text").status_code == 409
        done = client.post(base + "/translation/next").json()
        assert done["status"] == "succeeded"
        assert "".join(p["source"] for p in done["segments"]) == SOURCE
        assert "```python\ndef run():\n    return True\n```\n" in done["text"]
        assert "`--safe`" in done["text"] and "https://example.com/docs" in done["text"]
        assert "⟦KEEP" not in done["text"]
        assert len(calls) == 2
        assert all("def run" not in r["messages"][-1]["content"] for r in calls)
        assert done["origin"] == "model_api"
        assert "usage" in done["attempts"][0]["output"]["body"]
        assert "test-key-never-persist" not in json.dumps(done)
        assert client.post(base + "/translation/next").json() == done
        assert len(calls) == 2
        assert client.get(base + "/artifacts/document").json() == original
        assert client.get(base + "/reviews").json()["latest"] is None
        # Completing translation makes it readable without a separate receive action.
        results = client.get(base + "/processing").json()["results"]
        assert len(results) == 1 and results[0]["reviews"] == []
        assert results[0]["execution"]["translation_id"] == done["id"]
        reading = client.get("/api/library/" + base.split("/")[-1]).json()
        assert reading["has_translation"] and reading["status"] == "中文待核对"
        assert reading["note"] == "" and reading["curation"]["latest"] is None
    with TestClient(app(tmp_path, model)) as client:
        assert client.get(base + "/translation").json() == done


def test_failed_batch_is_retained_and_retry_skips_completed_batches(tmp_path, configured):
    calls = []

    def model(request):
        calls.append(json.loads(request.content))
        return httpx.Response(429, text="rate limited") if len(calls) == 2 else reply(request)

    with TestClient(app(tmp_path, model)) as client:
        base = capture(client)
        client.post(base + "/translation/next")
        failed = client.post(base + "/translation/next").json()
        assert failed["status"] == "failed" and failed["text"] is None
        assert "429" in failed["attempts"][-1]["error"]
        done = client.post(base + "/translation/next").json()
        assert done["status"] == "succeeded"
        assert [a["status"] for a in done["attempts"]] == ["succeeded", "failed", "succeeded"]
        assert calls[1] == calls[2] and calls[0] != calls[2]


@pytest.mark.parametrize(
    "changes",
    [
        {"finish_reason": "length"},
        {"message": {"content": ""}},
        {"message": {"content": "遗漏了代码与链接"}},
        {"message": {"content": r"模拟译文\n\n⟦KEEP0⟧ ⟦KEEP1⟧"}},
    ],
)
def test_truncated_empty_or_corrupt_output_cannot_be_complete(tmp_path, configured, changes):
    with TestClient(app(tmp_path, lambda r: reply(r, **changes))) as client:
        base = capture(client)
        result = client.post(base + "/translation/next").json()
        assert result["status"] == "failed" and result["text"] is None
        assert result["attempts"][0]["output"]
        assert client.get(base + "/translation/text").status_code == 409


def test_missing_key_is_actionable_and_key_file_is_not_exposed(tmp_path, monkeypatch):
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    with TestClient(app(tmp_path)) as client:
        base = capture(client)
        assert not client.get(base + "/translation").json()["configured"]
        assert client.post(base + "/translation/next").status_code == 400
        (tmp_path / "deepseek-api-key.txt").write_text("test-key-never-persist")
        state = client.post(base + "/translation/next").json()
        assert state["configured"] and "test-key-never-persist" not in json.dumps(state)


def test_active_call_rejected_then_interrupted_call_can_retry(tmp_path, configured):
    with TestClient(app(tmp_path)) as client:
        base = capture(client)
        part = client.post(base + "/translation/next").json()
        with sqlite3.connect(tmp_path / "library.sqlite3") as db:
            db.execute(
                "UPDATE translation_attempts SET status='running' WHERE id=?",
                (part["attempts"][0]["id"],),
            )
        assert client.post(base + "/translation/next").status_code == 409
        with sqlite3.connect(tmp_path / "library.sqlite3") as db:
            db.execute("UPDATE translation_attempts SET started='2000-01-01T00:00:00+00:00'")
        retried = client.post(base + "/translation/next").json()
        assert retried["attempts"][0]["status"] == "failed"
        assert retried["attempts"][1]["status"] == "succeeded"


def test_reextraction_and_model_change_do_not_reuse_old_translation(
    tmp_path, configured, monkeypatch
):
    with TestClient(app(tmp_path)) as client:
        base = capture(client)
        first = client.post(base + "/translation/next").json()
        new = client.post(base + "/reextract").json()
        new_base = f"/api/runs/{new['id']}"
        client.post(new_base + "/stages/extract")
        assert client.get(new_base + "/translation").json()["status"] == "not_started"
        monkeypatch.setenv("DEEPSEEK_MODEL", "another-model")
        assert client.get(base + "/translation").json()["status"] == "not_started"
        monkeypatch.delenv("DEEPSEEK_MODEL")
        assert client.get(base + "/translation").json() == first


def test_long_article_keeps_every_source_character_and_inline_code(tmp_path, configured):
    source = ("Long paragraph. " * 250 + "`" + "inline code " * 60 + "`\n\n") * 3
    capture_transport = httpx.MockTransport(
        lambda r: httpx.Response(200, text=source, headers={"Content-Type": "text/plain"})
    )
    with TestClient(
        create_app(tmp_path, capture_transport, translation_transport=httpx.MockTransport(reply))
    ) as client:
        base = capture(client)
        for _ in range(20):
            result = client.post(base + "/translation/next").json()
            if result["status"] == "succeeded":
                break
        assert result["status"] == "succeeded"
        assert "".join(p["source"] for p in result["segments"]) == source
        assert result["text"].count("`" + "inline code " * 60 + "`") == 3


def test_previous_prompt_run_remains_readable_and_cannot_cross_pipeline(tmp_path, configured):
    with TestClient(app(tmp_path)) as client:
        base = capture(client)
        saved = client.post(base + "/translation/next").json()
        with sqlite3.connect(tmp_path / "library.sqlite3") as db:
            db.execute("UPDATE translations SET prompt_version='translation-zh-v1'")
        current = client.get(base + "/translation").json()
        assert current["status"] == "not_started"
        assert current["history"][0]["prompt_version"] == "translation-zh-v1"
        old = client.get(base + f"/translation?translation_id={saved['id']}").json()
        assert old["segments"] == saved["segments"]
        assert old["prompt_version"] == "translation-zh-v1"
        other = capture(client)
        assert client.get(other + f"/translation?translation_id={saved['id']}").status_code == 400


def test_ai_audit_must_match_exact_translation_and_does_not_create_human_review(
    tmp_path, configured
):
    with TestClient(app(tmp_path)) as client:
        base = capture(client)
        client.post(base + "/translation/next")
        done = client.post(base + "/translation/next").json()
        folder = tmp_path / "translation-audits"
        folder.mkdir()
        path = folder / f"{done['id']}.json"
        audit = {
            "translation_id": done["id"],
            "pipeline_id": base.rsplit("/", 1)[-1],
            "version_id": done["version_id"],
            "translation_sha256": "wrong",
            "origin": "codex_ai_review",
            "verdict": "needs_revision",
        }
        path.write_text(json.dumps(audit))
        assert client.get(base + "/translation").json()["audit"] is None
        audit["translation_sha256"] = hashlib.sha256(done["text"].encode()).hexdigest()
        path.write_text(json.dumps(audit))
        result = client.get(base + "/translation").json()
        assert result["audit"] == audit and result["text"] == done["text"]
        assert client.get(base + "/reviews").json()["latest"] is None
