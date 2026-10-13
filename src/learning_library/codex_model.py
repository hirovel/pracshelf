"""Codex Adapter: isolated subscription worker, durable events, no API fallback."""

import json
import os
import subprocess
import sys
from pathlib import Path
from uuid import uuid4

EXCLUDED_ENV = {
    "OPENAI_API_KEY", "CODEX_API_KEY", "CODEX_ACCESS_TOKEN", "DEEPSEEK_API_KEY",
    "OPENAI_BASE_URL", "OPENAI_ORG_ID", "OPENAI_ORGANIZATION", "OPENAI_PROJECT_ID",
}


def child_environment():
    # Never remove keys from the web server's process-wide environment.
    return {key: value for key, value in os.environ.items() if key.upper() not in EXCLUDED_ENV}


def call(request, schema=None, *, root=None, trace_name=None, runner=subprocess.run):
    directory = Path(root or "data") / "model-runs" / (trace_name or uuid4().hex)
    directory.mkdir(parents=True, exist_ok=False)
    packet = {"request": request, "schema": strict_schema(schema) if schema else None}
    (directory / "input.json").write_text(json.dumps(packet, ensure_ascii=False), encoding="utf-8")
    response = {"provider": "codex", "engine": "codex_sdk", "api_fallback": False,
                "request_count": None, "task_count": 1, "requested_model": request["model"],
                "execution_model": None, "artifact_id": directory.name}
    try:
        finished = runner(
            [sys.executable, "-m", "learning_library.codex_worker", str(directory.resolve())],
            env=child_environment(), capture_output=True, text=True, encoding="utf-8",
            timeout=210, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        path = directory / "result.json"
        if not path.is_file():
            raise ValueError(f"Codex worker 未保存终态，退出码 {finished.returncode}；不转 API。")
        result = json.loads(path.read_text("utf-8"))
        response.update(result["response"])
        response.update(read_trace(directory))
        error, text = result["error"], result.get("text")
        output = None
        if error is None:
            if response.get("terminal_status") != "completed":
                raise ValueError("Codex worker 未返回成功完成终态。")
            if not isinstance(text, str) or not text.strip():
                raise ValueError("Codex 没有返回有效正文。")
            if schema:
                output = schema.model_validate(json.loads(text)).model_dump(mode="json")
        return {"response": response, "output": output, "text": text, "error": error}
    except subprocess.TimeoutExpired:
        error = "Codex worker 超时，完成未确认；保留事件，不自动重试或转 API。"
    except (ValueError, KeyError, TypeError) as exc:
        error = f"Codex 输出校验失败：{type(exc).__name__}；原始响应与事件已保留。"
    except Exception as exc:  # noqa: BLE001 - never expose worker stderr or credentials
        error = f"Codex 执行失败：{type(exc).__name__}；不转 API。"
    # Re-read partial evidence even if the worker never produced a final result.
    response.update(read_trace(directory))
    return {"response": response, "output": None, "text": None, "error": error}


def read_trace(directory):
    events, last = [], {}
    path = Path(directory) / "events.jsonl"
    if path.is_file():
        for line in path.read_text("utf-8").splitlines():
            try:
                event = json.loads(line)
                events.append(event)
                last = event
            except ValueError:  # Ignore only the final partially flushed line.
                continue
    return {"events": events, "event_count": len(events), "last_event": last.get("method")}


def strict_schema(schema):
    value = schema.model_json_schema()

    def visit(node):
        if isinstance(node, dict):
            node.pop("default", None)
            # SDK outputSchema does not support every Pydantic JSON-schema keyword.
            node.pop("format", None)
            if node.get("type") == "object":
                node["required"] = list(node.get("properties", {}))
                node["additionalProperties"] = False
            for child in node.values():
                visit(child)
        elif isinstance(node, list):
            for child in node:
                visit(child)

    visit(value)
    return value
