"""Read-only call evidence. No provider calls, database writes or inferred savings."""

import hashlib
import json
import re
from datetime import datetime
from functools import lru_cache
from pathlib import Path

FIELDS = {"input": "inputTokens", "cached_input": "cachedInputTokens",
          "cache_write_input": "cacheWriteInputTokens", "output": "outputTokens",
          "reasoning_output": "reasoningOutputTokens", "total": "totalTokens"}
ARTIFACT = re.compile(r"(?:insights|screening|translation)-[0-9]+|search-[a-f0-9]{32}")


def _number(value):
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


def _seconds(start, end):
    try:
        elapsed = (datetime.fromisoformat(end) - datetime.fromisoformat(start)).total_seconds()
        return round(elapsed, 3) if elapsed >= 0 else None
    except (TypeError, ValueError):
        return None


def _ids(params):
    turn = params.get("turn")
    return (params.get("threadId") or params.get("thread_id"),
            params.get("turnId") or params.get("turn_id") or (turn.get("id") if isinstance(turn, dict) else None))


@lru_cache(maxsize=64)
def _scan(path, mtime, size):
    """File stamp invalidates the in-memory scan; only retain compact evidence."""
    created, totals, turns = set(), {}, {}
    count, last_method = 0, None
    with Path(path).open(encoding="utf-8") as stream:
        for line in stream:
            try:
                event = json.loads(line)
            except ValueError:
                continue  # A running writer may have an incomplete final line.
            if not isinstance(event, dict):
                continue
            count += 1
            method, params = event.get("method"), event.get("params") or {}
            last_method = method
            if not isinstance(params, dict):
                continue
            thread, turn = _ids(params)
            key = (thread, turn)
            if method == "thread/created" and thread:
                created.add(thread)
            if method in {"turn/request_accepted", "turn/started"} and all(key) and key not in turns:
                turns[key] = {"started": event.get("at"), "finished": None,
                              "baseline": totals.get(thread, dict.fromkeys(FIELDS.values(), 0) if thread in created else None),
                              "total": None, "reset": False}
            if method == "thread/tokenUsage/updated" and thread:
                usage = params.get("tokenUsage") or {}
                total = usage.get("total") if isinstance(usage, dict) else None
                if isinstance(total, dict):
                    if key in turns:
                        previous = totals.get(thread) or turns[key]["baseline"] or {}
                        if any(_number(total.get(k)) is not None and _number(previous.get(k)) is not None
                               and total[k] < previous[k] for k in FIELDS.values()):
                            turns[key]["reset"] = True
                        turns[key]["total"] = total
                    totals[thread] = total
            if method == "turn/completed" and key in turns:
                turns[key]["finished"] = event.get("at")
    return {"event_count": count, "last_event": last_method, "turns": turns}


def _trace(root, artifact):
    if not isinstance(artifact, str) or not ARTIFACT.fullmatch(artifact):
        return None
    path = Path(root) / "model-runs" / artifact / "events.jsonl"
    try:
        stamp = path.stat()
        return _scan(str(path.resolve()), stamp.st_mtime_ns, stamp.st_size)
    except OSError:
        return None


def observe(attempt, root):
    """Describe one saved attempt; unknown evidence remains null, never zero."""
    response, incoming = attempt.get("response") or {}, attempt.get("input") or {}
    execution = incoming.get("execution") or {}
    engine = response.get("engine") or execution.get("engine")
    request = incoming.get("request") or {}
    messages = request.get("messages")
    known_messages = isinstance(messages, list) and all(
        isinstance(m, dict) and isinstance(m.get("content"), str) for m in messages
    )
    visible = {
        "system_characters": sum(len(m["content"]) for m in messages if m.get("role") == "system") if known_messages else None,
        "user_characters": sum(len(m["content"]) for m in messages if m.get("role") == "user") if known_messages else None,
        "message_characters": sum(len(m["content"]) for m in messages) if known_messages else None,
        "messages_sha256": hashlib.sha256(json.dumps(messages, ensure_ascii=False, sort_keys=True).encode()).hexdigest() if known_messages else None,
        "sdk_version": response.get("sdk_version"), "runtime_version": response.get("runtime_version"),
        "full_rendered_input": "not_observed", "instruction_sources": None,
        "scope": "仅计数已保存 messages 的 Unicode 字符；不是 tokens，不含未观测的运行时上下文。",
    }
    provider = response.get("provider") or request.get("provider") or "deepseek"
    artifact = response.get("artifact_id")
    trace = _trace(root, artifact) if provider == "codex" else None
    key = (response.get("thread_id"), response.get("turn_id"))
    turn = (trace or {}).get("turns", {}).get(key)
    if turn is None and trace and len(trace["turns"]) == 1 and not any(key):
        key, turn = next(iter(trace["turns"].items()))
    tokens = dict.fromkeys(FIELDS)
    basis, reported = "未观测", None
    if provider == "codex":
        reported = (response.get("usage") or {}).get("total")
        if turn and turn["total"] and turn["baseline"] is not None and not turn["reset"]:
            baseline, total = turn["baseline"], turn["total"]
            for field, wire in FIELDS.items():
                before = _number(baseline.get(wire))
                after = _number(total.get(wire))
                if before is not None and after is not None and after >= before:
                    tokens[field] = after - before
            basis = "同一线程累计值减去 turn 起点基线"
        elif reported or (turn and turn["total"]):
            basis = "只有线程累计值；任务增量未知" if not turn or not turn["reset"] else "累计计数回退；任务增量未知"
    else:
        usage = response.get("usage") or {}
        tokens = {"input": _number(usage.get("prompt_tokens")),
                  "cached_input": _number((usage.get("prompt_tokens_details") or {}).get("cached_tokens")),
                  "cache_write_input": None, "output": _number(usage.get("completion_tokens")),
                  "reasoning_output": _number((usage.get("completion_tokens_details") or {}).get("reasoning_tokens")),
                  "total": _number(usage.get("total_tokens"))}
        if any(value is not None for value in tokens.values()):
            basis = "供应商响应 usage"
    cached, input_tokens = tokens["cached_input"], tokens["input"]
    ratio = round(cached / input_tokens, 4) if input_tokens and cached is not None and cached <= input_tokens else None
    return {"execution": {"engine": engine,
                          "adapter": execution.get("adapter") if engine == execution.get("engine") else None,
                          "packages": execution.get("packages", {}) if engine == execution.get("engine") else {}},
            "visible_input": visible, "provider": provider, "configured_model": response.get("configured_model") or request.get("model"),
            "effort": response.get("requested_effort") or request.get("effort"),
            "execution_model": response.get("execution_model") or response.get("provider_model"),
            "terminal_status": response.get("terminal_status") or attempt.get("status"),
            "application_attempt_id": attempt["id"], "provider_request_count": response.get("request_count"),
            "thread_id": key[0], "turn_id": key[1], "tokens": tokens, "usage_basis": basis,
            "reported_thread_total": (reported or (turn or {}).get("total")) if provider == "codex" and tokens["total"] is None else None,
            "cache_read_ratio": ratio,
            "timing": {"task_seconds": _seconds(attempt.get("started"), attempt.get("finished")),
                       "preparation_seconds": _seconds(response.get("started"), (turn or {}).get("started")),
                       "turn_seconds": _seconds((turn or {}).get("started"), (turn or {}).get("finished"))},
            "timing_basis": {"task": "应用登记至保存结束", "preparation": "worker 入口至 turn 接收，含配置/认证检查",
                             "turn": "turn 接收至完成事件；不是纯推理耗时"},
            "event_count": trace["event_count"] if trace else response.get("event_count"),
            "last_event": trace["last_event"] if trace else response.get("last_event"),
            "log_url": "/api/model-runs/" + artifact if isinstance(artifact, str) and ARTIFACT.fullmatch(artifact) else None,
            "detail_url": f"/api/runs/{attempt['pipeline_id']}/insights/{attempt['id']}"}
