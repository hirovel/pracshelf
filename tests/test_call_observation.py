"""Synthetic telemetry; cross the same read-only Interface as the workbench."""

import json

from learning_library.call_observation import observe


def counts(input_tokens, cached, output):
    return {"inputTokens": input_tokens, "cachedInputTokens": cached,
            "cacheWriteInputTokens": 0, "outputTokens": output,
            "reasoningOutputTokens": 2, "totalTokens": input_tokens + output}


def event(method, second, **params):
    return {"method": method, "at": f"2026-10-12T00:00:{second:02d}+00:00", "params": params}


def fixture(root, events, **response):
    directory = root / "model-runs" / "insights-3"
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "events.jsonl").write_text("\n".join(json.dumps(e) for e in events), "utf-8")
    return {"id": 3, "pipeline_id": "fixture", "status": "completed",
            "started": "2026-10-12T00:00:00+00:00", "finished": "2026-10-12T00:00:10+00:00",
            "input": {"request": {"provider": "codex", "model": "fixture-model", "effort": "medium"}},
            "response": {"provider": "codex", "artifact_id": "insights-3", "thread_id": "t", "turn_id": "a",
                         "started": "2026-10-12T00:00:01+00:00", **response}}


def test_duplicate_cumulative_notifications_are_not_summed_and_timing_is_scoped(tmp_path):
    total = counts(100, 0, 20)
    events = [event("thread/created", 1, thread_id="t"),
              event("turn/request_accepted", 2, thread_id="t", turn_id="a"),
              event("turn/started", 2, threadId="t", turn={"id": "a"}),
              event("thread/tokenUsage/updated", 5, threadId="t", turnId="a", tokenUsage={"total": total, "last": counts(5, 0, 1)}),
              event("thread/tokenUsage/updated", 6, threadId="t", turnId="a", tokenUsage={"total": total}),
              event("turn/completed", 7, threadId="t", turn={"id": "a", "status": "completed"})]
    attempt = fixture(tmp_path, events)
    result = observe(attempt, tmp_path)
    assert result["tokens"] == {"input": 100, "cached_input": 0, "cache_write_input": 0,
                                "output": 20, "reasoning_output": 2, "total": 120}
    assert result["cache_read_ratio"] == 0
    assert result["timing"] == {"task_seconds": 10, "preparation_seconds": 1, "turn_seconds": 5}
    assert result["event_count"] == 6
    assert result["execution_model"] is result["provider_request_count"] is None
    assert result["effort"] == "medium"


def test_second_turn_uses_previous_thread_baseline_and_ignores_other_threads(tmp_path):
    first, second = counts(100, 10, 20), counts(150, 40, 30)
    second["reasoningOutputTokens"] = 5
    events = [event("thread/created", 1, thread_id="t"),
              event("turn/started", 2, threadId="t", turn={"id": "a"}),
              event("thread/tokenUsage/updated", 3, threadId="t", turnId="a", tokenUsage={"total": first}),
              event("turn/completed", 4, threadId="t", turn={"id": "a"}),
              event("turn/started", 5, threadId="t", turn={"id": "b"}),
              event("thread/tokenUsage/updated", 6, threadId="other", turnId="b", tokenUsage={"total": counts(999, 999, 999)}),
              event("thread/tokenUsage/updated", 6, threadId="t", turnId="b", tokenUsage={"total": second}),
              event("thread/tokenUsage/updated", 7, threadId="t", turnId="b", tokenUsage={"total": second}),
              event("turn/completed", 8, threadId="t", turn={"id": "b"})]
    result = observe(fixture(tmp_path, events, turn_id="b"), tmp_path)
    assert result["tokens"] == {"input": 50, "cached_input": 30, "cache_write_input": 0,
                                "output": 10, "reasoning_output": 3, "total": 60}
    assert result["timing"]["turn_seconds"] == 3 and result["cache_read_ratio"] == .6


def test_resumed_thread_without_baseline_is_unknown_not_zero(tmp_path):
    total = counts(100, 0, 20)
    events = [event("turn/started", 2, threadId="t", turn={"id": "a"}),
              event("thread/tokenUsage/updated", 6, threadId="t", turnId="a", tokenUsage={"total": total})]
    result = observe(fixture(tmp_path, events), tmp_path)
    assert all(value is None for value in result["tokens"].values())
    assert result["reported_thread_total"] == total and result["cache_read_ratio"] is None
    assert result["timing"]["turn_seconds"] is None


def test_visible_characters_do_not_replace_tokens_or_invent_hidden_context(tmp_path):
    attempt = fixture(tmp_path, [])
    unknown = observe(attempt, tmp_path)["visible_input"]
    assert unknown["message_characters"] is None  # Missing messages are not a zero-length request.
    attempt["input"]["request"]["messages"] = [
        {"role": "system", "content": "中文"}, {"role": "user", "content": "a🙂"},
    ]
    known = observe(attempt, tmp_path)
    assert known["visible_input"]["message_characters"] == 4
    assert known["tokens"]["input"] is None  # Local text cannot establish provider token usage.
    assert known["visible_input"]["instruction_sources"] is None
    assert known["visible_input"]["full_rendered_input"] == "not_observed"
    assert "中文" not in json.dumps(known["visible_input"], ensure_ascii=False)


def test_counter_reset_and_missing_fields_cannot_be_reported_as_savings(tmp_path):
    first, second = counts(100, 10, 20), counts(50, 0, 10)
    events = [event("thread/created", 1, thread_id="t"),
              event("turn/started", 2, threadId="t", turn={"id": "a"}),
              event("thread/tokenUsage/updated", 4, threadId="t", turnId="a", tokenUsage={"total": first}),
              event("thread/tokenUsage/updated", 6, threadId="t", turnId="a", tokenUsage={"total": second})]
    result = observe(fixture(tmp_path, events), tmp_path)
    assert result["tokens"]["total"] is None and result["reported_thread_total"] == second
    del first["cachedInputTokens"]
    events = events[:3]
    result = observe(fixture(tmp_path, events), tmp_path)
    assert result["tokens"]["input"] == 100 and result["tokens"]["cached_input"] is None


def test_missing_events_partial_lines_and_file_growth(tmp_path):
    attempt = fixture(tmp_path, [event("turn/started", 2, threadId="t", turn="malformed")])
    path = tmp_path / "model-runs" / "insights-3" / "events.jsonl"
    with path.open("a", encoding="utf-8") as stream:
        stream.write('\n{"method":')
    assert observe(attempt, tmp_path)["event_count"] == 1
    fixture(tmp_path, [event("thread/created", 1, thread_id="t"),
                       event("turn/started", 2, threadId="t", turn={"id": "a"}),
                       event("thread/tokenUsage/updated", 4, threadId="t", turnId="a", tokenUsage={"total": counts(100, 0, 20)})])
    assert observe(attempt, tmp_path)["tokens"]["input"] == 100
    path.unlink()
    assert observe(attempt, tmp_path)["tokens"]["total"] is None
    attempt["response"]["artifact_id"] = "../insights-3"
    assert observe(attempt, tmp_path)["log_url"] is None


def test_api_usage_distinguishes_reported_zero_from_missing_cache(tmp_path):
    attempt = {"id": 1, "pipeline_id": "fixture", "status": "failed",
               "input": {"request": {"provider": "deepseek", "model": "fixture-model"}},
               "response": {"usage": {"prompt_tokens": 100, "completion_tokens": 20, "total_tokens": 120}}}
    result = observe(attempt, tmp_path)
    assert result["tokens"]["total"] == 120 and result["tokens"]["cached_input"] is None
    assert result["timing"]["task_seconds"] is None and result["terminal_status"] == "failed"
    attempt["response"]["usage"]["prompt_tokens_details"] = {"cached_tokens": 0}
    assert observe(attempt, tmp_path)["tokens"]["cached_input"] == 0


def test_framework_labels_follow_saved_execution_and_do_not_infer_from_provider(tmp_path):
    attempt = fixture(tmp_path, [])
    assert observe(attempt, tmp_path)["execution"]["engine"] is None
    attempt["input"]["execution"] = {
        "engine": "langchain", "adapter": "ChatDeepSeek", "packages": {"langchain-core": "fixture"},
    }
    attempt["response"]["engine"] = "codex_sdk"
    actual = observe(attempt, tmp_path)["execution"]
    assert actual == {"engine": "codex_sdk", "adapter": None, "packages": {}}
    attempt["input"]["execution"] = {
        "engine": "codex_sdk", "adapter": "AsyncCodex", "packages": {"openai-codex": "fixture"},
    }
    actual = observe(attempt, tmp_path)["execution"]
    assert actual["adapter"] == "AsyncCodex" and actual["packages"] == {"openai-codex": "fixture"}
