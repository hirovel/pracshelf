import json
from datetime import UTC, datetime, timedelta, timezone

import httpx
import pytest

from learning_library.candidates import Candidates, date_check
from learning_library.curation import Curation
from learning_library.pipeline import Pipeline
from learning_library.review import ReviewConflict
from learning_library.storage import Store

TEXT = (
    "# A practical skill\n\nKeep the original source and record the conditions before adopting a prompt. "
    * 5
)


def setup(tmp_path, monkeypatch, *, quote=None, status=200):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-secret-never-log")
    store = Store(tmp_path)
    source = httpx.Client(
        transport=httpx.MockTransport(
            lambda r: httpx.Response(200, text=TEXT, headers={"Content-Type": "text/markdown"})
        )
    )
    pipeline = Pipeline(store, source)
    calls = []

    def respond(request):
        calls.append(json.loads(request.content))
        judgment = {
            "category": "skill",
            "recommendation": "read",
            "learning_value": "保留证据和使用条件。",
            "reason": "有明确检查方法。",
            "evidence": [
                {
                    "quote": quote
                    or "Keep the original source and record the conditions before adopting a prompt.",
                    "supports": "先保存原文和使用条件。",
                }
            ],
            "conditions": ["用于提示词采纳"],
            "time_dependencies": [],
            "unknowns": ["当前效果待试用"],
        }
        return httpx.Response(
            status,
            json={
                "model": "fixture",
                "id": "mock",
                "usage": {"prompt_tokens": 50, "completion_tokens": 27, "total_tokens": 77},
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {"role": "assistant", "content": json.dumps(judgment)},
                    }
                ],
            },
        )

    module = Candidates(pipeline, transport=httpx.MockTransport(respond))
    payload = {
        "submission_id": "import-1",
        "url": "https://example.com/skill.md#part",
        "goal": "学习有依据的 AI 工作方法",
        "origin": "demo",
        "source_note": "synthetic fixture",
        "as_of": datetime.now(UTC).date().isoformat(),
        "max_age_days": 90,
    }
    return module, payload, calls, store, source


def test_import_capture_and_model_review_are_idempotent_and_do_not_curate(tmp_path, monkeypatch):
    module, payload, calls, store, source = setup(tmp_path, monkeypatch)
    try:
        candidate = module.import_link(payload)
        assert candidate["input"]["url"] == "https://example.com/skill.md"
        assert module.import_link(payload)["id"] == candidate["id"]
        with pytest.raises(ReviewConflict):
            module.import_link(payload | {"goal": "changed"})
        with pytest.raises(ValueError, match="原文"):
            module.review(candidate["id"], "review-1")
        captured = module.capture(candidate["id"])
        assert captured["version_id"] == module.capture(candidate["id"])["version_id"]
        result = module.review(candidate["id"], "review-1")
        assert result["status"] == "completed"
        out = result["latest"]["output"]
        assert out["quote_alignment"] == "passed"
        assert out["date_check"]["status"] == "unknown_date"
        assert out["disposition"] == "needs_review"
        assert out["current_applicability"] == "unverified"
        assert Curation(store).inspect(result["pipeline_id"])["latest"] is None
        assert module.review(candidate["id"], "review-1")["latest"]["id"] == result["latest"]["id"]
        assert len(calls) == 1
        assert "test-secret-never-log" not in json.dumps(result)
        assert result["latest"]["input"]["source_characters"] == len(
            store.version(captured["version_id"])["document"]["text"]
        )
    finally:
        source.close()
        store.close()


def test_saved_output_recheck_is_visible_and_reuses_response_without_model_call(tmp_path, monkeypatch):
    module, payload, calls, store, source = setup(tmp_path, monkeypatch)
    try:
        candidate = module.submit(payload)
        state = module.review(candidate["id"], "review-1")
        attempt = state["preview"]["latest"]
        old = attempt["output"] | {"quote_alignment": "failed", "effective_recommendation": "uncertain"}
        old.pop("validation_version", None)
        incoming = attempt["input"].copy()
        incoming.pop("code_ranges", None)
        with store.db:
            store.db.execute("UPDATE candidate_screenings SET input=?,output=? WHERE id=?", (
                json.dumps(incoming), json.dumps(old), attempt["id"],
            ))
        checked = module.inspect(candidate["id"])
        assert checked["preview"]["latest"]["output"]["quote_alignment"] == "failed"
        assert checked["preview"]["latest"]["current_output"]["quote_alignment"] == "passed"
        assert checked["preview"]["action"] is None
        assert module.review(candidate["id"], "review-after-policy-change")["preview"]["latest"]["id"] == attempt["id"]
        assert len(calls) == 1
        assert json.loads(store.db.execute("SELECT output FROM candidate_screenings WHERE id=?", (attempt["id"],)).fetchone()[0]) == old
    finally:
        source.close()
        store.close()


def test_missing_quote_blocks_recommendation_and_failure_retains_trace(tmp_path, monkeypatch):
    module, payload, _calls, store, source = setup(
        tmp_path, monkeypatch, quote="This sentence is invented, it never occurs in the source."
    )
    try:
        item = module.import_link(payload)
        module.capture(item["id"])
        result = module.review(item["id"], "review-1")
        assert result["latest"]["output"]["quote_alignment"] == "failed"
        assert result["latest"]["output"]["disposition"] == "needs_review"
        module.transport = httpx.MockTransport(lambda r: httpx.Response(503, text="unavailable"))
        failed = module.review(item["id"], "review-2")
        assert failed["status"] == "failed"
        assert failed["latest"]["response"]["http_status"] == 503
        assert len(failed["attempts"]) == 2
        assert failed["attempts"][0]["output"] == result["latest"]["output"]
    finally:
        source.close()
        store.close()


def test_active_review_is_not_restarted_across_timezone_offsets(tmp_path, monkeypatch):
    module, payload, _calls, store, source = setup(tmp_path, monkeypatch)
    monkeypatch.setattr(
        "learning_library.candidates.now",
        lambda: datetime.now(timezone(timedelta(hours=-4))).isoformat(),
    )
    try:
        item = module.import_link(payload)
        module.capture(item["id"])

        def concurrent_request(request):
            # LangChain may invoke the transport on a worker thread. A concurrent
            # HTTP request has its own SQLite connection, as the real app does.
            other_store = Store(tmp_path)
            try:
                with pytest.raises(ReviewConflict, match="已有审核"):
                    Candidates(Pipeline(other_store, source)).review(
                        item["id"], "concurrent-review"
                    )
            finally:
                other_store.close()
            return httpx.Response(503, text="fixture failure after concurrency check")

        module.transport = httpx.MockTransport(concurrent_request)
        result = module.review(item["id"], "review-1")
        assert len(result["attempts"]) == 1
        assert result["latest"]["response"]["http_status"] == 503
    finally:
        source.close()
        store.close()


@pytest.mark.parametrize(
    "published,status",
    [
        (None, "unknown_date"),
        ("2026-10-10", "future_date"),
        ("2026-07-11", "within_window"),
        ("2026-07-10", "outside_window"),
        ("bad", "unknown_date"),
    ],
)
def test_date_window_does_not_guess_or_certify_applicability(published, status):
    assert date_check({"published": published}, "2026-10-09", 90)["status"] == status


def test_preview_cache_reuses_result_across_submissions_and_candidates(tmp_path, monkeypatch):
    module, payload, calls, store, source = setup(tmp_path, monkeypatch)
    try:
        first = module.submit(payload)
        reviewed = module.review(first["id"], "review-one")
        preview_id = reviewed["preview"]["latest"]["id"]
        assert module.review(first["id"], "review-two")["preview"]["latest"]["id"] == preview_id
        second = module.submit(payload | {"submission_id": "import-two"})
        reused = module.review(second["id"], "review-three")
        assert reused["preview"]["reused_from_other_candidate"]
        assert reused["preview"]["latest"]["id"] == preview_id
        assert reused["attempts"] == []
        assert len(calls) == 1
        changed = module.submit(payload | {"submission_id": "import-three", "goal": "学习测试设计"})
        module.review(changed["id"], "review-four")
        assert len(calls) == 2
        monkeypatch.setenv("DEEPSEEK_MODEL", "different-test-model")
        assert module.inspect(first["id"])["preview"]["action"] == "start"
        module.review(first["id"], "review-five")
        assert len(calls) == 3
        # The explicit new submission also reuses its own successful result.
        module.review(first["id"], "review-six")
        assert len(calls) == 3
    finally:
        source.close()
        store.close()
