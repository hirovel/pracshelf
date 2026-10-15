"""Optional Chinese-copy Interface; isolated fixtures never call a real model."""

import json
from contextlib import closing

import httpx
import pytest
from fastapi.testclient import TestClient

from learning_library import translation
from learning_library.article_insights import ArticleInsights, Insights, prepare, validate
from learning_library.pipeline import Pipeline
from learning_library.storage import Store, now
from learning_library.web import create_app

SOURCE = ("# Synthetic practice\n\nOnly under these conditions might this help.\n\n"
          + ("Synthetic explanation. " * 100 + "\n\n") * 2
          + "Keep `--safe` and [docs](https://example.com/docs). This is not guaranteed.\n")


@pytest.fixture
def scenario(tmp_path, monkeypatch):
    monkeypatch.setenv("LIBRARY_MODEL_PROVIDER", "codex")
    monkeypatch.setenv("LIBRARY_CODEX_MODEL", "gpt-6.1-sol")
    monkeypatch.setenv("LIBRARY_CODEX_EFFORT", "high")
    calls = []

    def fake(request, **kwargs):
        calls.append(request)
        return {"text": "合成译文：\n" + request["messages"][-1]["content"], "error": None,
                "response": {"provider": "codex", "configured_model": request["model"],
                             "requested_effort": request["effort"], "terminal_status": "completed",
                             "request_count": None, "events": [{"method": "synthetic-event"}]}}

    monkeypatch.setattr(translation, "call_text", fake)
    with TestClient(create_app(tmp_path, httpx.MockTransport(
        lambda r: httpx.Response(200, text=SOURCE, headers={"Content-Type": "text/markdown"})
    ))) as client:
        run = client.post("/api/runs", json={"url": "https://example.com/practice.md"}).json()["id"]
        base = "/api/runs/" + run
        client.post(base + "/stages/fetch")
        client.post(base + "/stages/extract")
        yield client, base, run, calls, tmp_path, fake


def save_core(root, run, recommendation="read", valid=True):
    with closing(Store(root)) as store, httpx.Client() as http:
        flow = Pipeline(store, http)
        ArticleInsights(flow)  # Initialize the established record schema.
        version = store.version(flow.reviews.inspect(run)["version_id"])
        incoming = prepare(version, "Synthetic goal", "gpt-6.1-sol", provider="codex", effort="high")
        quote = {"quote": "Only under these conditions might this help.", "supports": "仅合成条件"}
        judgment = Insights(category="workflow", recommendation=recommendation, reason="合成选材记录",
                            core="合成核心" if recommendation == "read" else "",
                            evidence=[quote], points=[], conditions=[], time_dependencies=[], unknowns=["仅测试"])
        output = validate(judgment.model_dump(), incoming)
        if not valid:
            output.update(core_available=False, effective_recommendation="uncertain")
        with store.db:
            store.db.execute("INSERT INTO article_insights(pipeline_id,submission_id,started,finished,status,input,output) VALUES(?,?,?,?,'completed',?,?)",
                             (run, "synthetic-core", now(), now(), json.dumps(incoming), json.dumps(output)))


@pytest.mark.parametrize("recommendation,valid", [(None, True), ("skip", True), ("read", False)])
def test_selection_gate_blocks_calls(scenario, recommendation, valid):
    client, base, run, calls, root, _ = scenario
    if recommendation:
        save_core(root, run, recommendation, valid)
    plan = client.get(base + "/translation/plan").json()
    assert plan["action"] is None and not plan["eligible"]
    assert client.post(base + "/translation/start", json={"submission_id": "blocked"}).status_code == 400
    assert calls == []


def test_complete_copy_reuses_results_and_logs_are_on_demand(scenario, monkeypatch):
    client, base, run, calls, root, _ = scenario
    save_core(root, run)
    plan = client.get(base + "/translation/plan").json()
    assert plan["budget"]["remaining_model_calls"] == 2
    assert not plan["budget"]["output_token_limit_enforced"] and calls == []
    claimed = client.post(base + "/translation/start", json={"submission_id": "copy-1"}).json()
    assert claimed["launch"] and len(calls) == 2
    assert all(r["effort"] == "high" and r["web_search"] == "disabled" for r in calls)
    replay = client.post(base + "/translation/start", json={"submission_id": "copy-1"}).json()
    assert replay["id"] == claimed["id"] and not replay["launch"]
    monkeypatch.setenv("LIBRARY_CODEX_EFFORT", "medium")
    fresh_submit = client.post(base + "/translation/start", json={"submission_id": "copy-2"}).json()
    assert fresh_submit["reused"] and not fresh_submit["launch"] and len(calls) == 2
    monkeypatch.setenv("LIBRARY_CODEX_MODEL", "different-config-only")
    assert client.post(base + "/translation/next").json()["status"] == "succeeded"
    assert len(calls) == 2  # The legacy batch entry cannot retransmit a completed copy either.
    reading = client.get("/api/library/" + run).json()
    assert reading["has_translation"] and reading["note"] == ""
    assert client.get(base + "/processing").json()["results"][0]["reviews"] == []
    summary = client.get("/api/workflows/" + run + "?detail=summary")
    assert '"events":' not in summary.text and '"messages":' not in summary.text
    native = summary.json()["steps"][2]["trace"]["native"]
    attempt = native["attempts"][0]
    assert attempt["observation"]["effort"] == "high"
    detail = client.get(attempt["observation"]["detail_url"]).json()
    assert detail["input"]["messages"] and detail["response"]["events"]
    assert client.get("/api/runs/wrong/translation/attempts/" + str(attempt["id"])).status_code == 400
    assert len(calls) == 2


def test_failed_batch_retry_preserves_completed_batch_and_fixed_configuration(scenario, monkeypatch):
    client, base, run, calls, root, fake = scenario
    save_core(root, run)
    failures = [True]

    def flaky(request, **kwargs):
        if calls and failures:
            failures.pop()
            calls.append(request)
            return {"text": None, "error": "Synthetic timeout", "response": {"provider": "codex"}}
        return fake(request, **kwargs)

    monkeypatch.setattr(translation, "call_text", flaky)
    first = client.post(base + "/translation/start", json={"submission_id": "first"}).json()
    failed = client.get(base + "/translation/plan").json()
    assert failed["latest"]["status"] == "failed" and failed["action"] == "retry"
    assert failed["budget"]["reused_segments"] == 1 and failed["budget"]["remaining_model_calls"] == 1
    monkeypatch.setenv("LIBRARY_CODEX_EFFORT", "medium")
    retry = client.post(base + "/translation/start", json={"submission_id": "retry"}).json()
    assert retry["translation_id"] == first["translation_id"] and len(calls) == 3
    assert calls[1] == calls[2] and calls[2]["effort"] == "high"
    done = client.get(base + "/translation/plan").json()
    assert done["latest"]["status"] == "completed" and done["budget"]["remaining_model_calls"] == 0
    assert [a["status"] for a in done["native"]["attempts"]] == ["succeeded", "failed", "succeeded"]


def test_queued_claim_prevents_second_start_and_execution_is_once(scenario):
    _, _, run, calls, root, _ = scenario
    save_core(root, run)
    with closing(Store(root)) as store:
        module = translation.Translation(store)
        first = module.begin(run, "queued")
        assert module.begin(run, "queued")["launch"] is False and calls == []
        with pytest.raises(translation.ReviewConflict):
            module.begin(run, "duplicate")
        with pytest.raises(translation.ReviewConflict):
            module.advance(run)
        module.execute(first["id"])
        module.execute(first["id"])
        assert len(calls) == 2


def test_interrupted_copy_retry_keeps_identity_after_global_model_change(scenario, monkeypatch):
    client, base, run, calls, root, fake = scenario
    save_core(root, run)

    def interrupted(request, **kwargs):
        if len(calls) == 1:
            calls.append(request)
            return {"text": None, "error": "Synthetic interrupted batch",
                    "response": {"provider": "codex"}}
        return fake(request, **kwargs)

    monkeypatch.setattr(translation, "call_text", interrupted)
    with closing(Store(root)) as store:
        module = translation.Translation(store)
        first = module.begin(run, "interrupted-copy")
        module.advance(run, first["translation_id"])
        stopped = module.advance(run, first["translation_id"])
        with store.db:
            store.db.execute(
                "UPDATE translation_attempts SET status='running',started=?,finished=NULL "
                "WHERE id=?", ("2000-01-01T00:00:00+00:00", stopped["attempts"][-1]["id"]),
            )
            store.db.execute(
                "UPDATE translation_jobs SET status='running',updated=? WHERE id=?",
                ("2000-01-01T00:00:00+00:00", first["id"]),
            )

    monkeypatch.setattr(translation, "call_text", fake)
    monkeypatch.setenv("LIBRARY_CODEX_MODEL", "different-global-model")
    monkeypatch.setenv("LIBRARY_CODEX_EFFORT", "medium")
    plan = client.get(base + "/translation/plan").json()
    assert plan["expired"] and plan["action"] == "retry"
    assert plan["budget"]["reused_segments"] == plan["budget"]["remaining_model_calls"] == 1
    retried = client.post(base + "/translation/start", json={"submission_id": "resume-copy"}).json()
    done = client.get(base + "/translation/plan").json()
    assert retried["translation_id"] == first["translation_id"]
    assert done["latest"]["status"] == "completed", done["latest"]
    assert done["jobs"][0]["status"] == "interrupted"
    assert done["native"]["model"] == "gpt-6.1-sol" and done["native"]["effort"] == "high"
    assert len(calls) == 3 and calls[1] == calls[2]
    assert [a["status"] for a in done["native"]["attempts"]] == ["succeeded", "failed", "succeeded"]
    assert client.get("/api/library/" + run).json()["has_translation"]


def test_source_change_after_claim_stops_without_transmitting_old_source(scenario):
    _, _, run, calls, root, _ = scenario
    save_core(root, run)
    with closing(Store(root)) as store:
        module = translation.Translation(store)
        claimed = module.begin(run, "before-source-update")
        old = store.db.execute("SELECT * FROM pipeline_attempts WHERE pipeline_id=? AND stage='extract' ORDER BY id DESC LIMIT 1", (run,)).fetchone()
        # A new extraction attempt is enough to invalidate the frozen copy identity.
        with store.db:
            store.db.execute("INSERT INTO pipeline_attempts(pipeline_id,stage,status,started,finished,input,output) VALUES(?,'extract','succeeded',?,?,?,?)",
                             (run, now(), now(), old["input"], old["output"]))
        module.execute(claimed["id"])
        job = store.db.execute("SELECT status,error FROM translation_jobs WHERE id=?", (claimed["id"],)).fetchone()
        assert job["status"] == "failed" and "原文版本已变化" in job["error"]
        assert calls == []
