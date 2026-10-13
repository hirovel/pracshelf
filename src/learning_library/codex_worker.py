"""One SDK turn in a separate process. No credentials or business rules persist here."""

import asyncio
import importlib.metadata
import json
import re
import sys
from contextlib import AsyncExitStack
from pathlib import Path

from openai_codex import ApprovalMode, AsyncCodex, CodexConfig, Sandbox
from openai_codex.async_client import AsyncCodexClient
from openai_codex.generated.v2_all import ConfigReadResponse, ReasoningEffort

from .storage import now

SECRET_KEYS = {"api_key", "apikey", "access_token", "refresh_token", "id_token",
               "authorization", "email", "chatgpt_account_id", "chatgptaccountid"}
OVERRIDES = (
    'forced_login_method="chatgpt"', 'model_provider="openai"',
    "features.shell_tool=false", "features.unified_exec=false", "features.multi_agent=false",
    "features.apps=false", "features.hooks=false", "features.code_mode=false",
    "features.remote_plugin=false", "agents.enabled=false", "mcp_servers={}",
    "plugins={}", "hooks={}", "notify=[]", 'history.persistence="none"',
)


def scrub(value):
    if isinstance(value, dict):
        return {key: "[redacted]" if key.lower() in SECRET_KEYS else scrub(item)
                for key, item in value.items()}
    if isinstance(value, list):
        return [scrub(item) for item in value]
    if isinstance(value, str):
        value = re.sub(r"(?i)Bearer\s+[A-Za-z0-9_.\-]+", "Bearer [redacted]", value)
        return re.sub(r"\bsk-[A-Za-z0-9_\-]{8,}", "[redacted]", value)
    return value


def wire(value):
    return value.model_dump(mode="json", by_alias=True) if hasattr(value, "model_dump") else value


async def execute(directory, packet, *, factory=AsyncCodex, inspect_config=True):
    request = packet["request"]
    trace = {"provider": "codex", "engine": "codex_sdk", "task_count": 1,
             "request_count": None, "api_fallback": False, "adapter_retries": 0,
             "runtime_retries": "built-in policy; not claimed disabled",
             "requested_model": request["model"], "requested_effort": request["effort"],
             "execution_model": None, "execution_model_evidence": "not returned per turn",
             "web_search": request["web_search"], "started": now(), "terminal_status": None}
    text, error, turn = None, None, None

    def emit(method, params):
        with (directory / "events.jsonl").open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(scrub({"at": now(), "method": method, "params": params}),
                                    ensure_ascii=False) + "\n")

    def config(extra=()):
        return CodexConfig(cwd=str(directory), experimental_api=False,
                           config_overrides=OVERRIDES + (f'web_search="{request["web_search"]}"',) + extra,
                           client_name="learning_library", client_title="Article processing")

    try:
        extra = ()
        if inspect_config:
            # {} merges inherited tables. Inspect names, then disable each real MCP server.
            # Do not save configuration values, which may contain auth/env material.
            async with AsyncCodexClient(config()) as client:
                await client.initialize()
                result = await client.request("config/read", {"includeLayers": False},
                                              response_model=ConfigReadResponse)
                names = (result.model_dump(mode="json", by_alias=True)["config"].get("mcp_servers") or {}).keys()
                if any(not re.fullmatch(r"[A-Za-z0-9_-]+", name) for name in names):
                    raise ValueError("继承的 MCP 名称无法安全覆盖，未开始任务。")
                extra = tuple(f"mcp_servers.{name}.enabled=false" for name in names)
        trace["sdk_version"] = importlib.metadata.version("openai-codex")
        emit("runtime/launch", {"overrides": list(config(extra).config_overrides),
                                "sdk_version": trace["sdk_version"], "api_fallback": False})
        async with AsyncExitStack() as cleanup:
            codex = factory(config(extra))
            cleanup.push_async_callback(codex.close)
            await codex.__aenter__()
            emit("runtime/initialized", wire(codex.metadata))
            account = await codex.account(refresh_token=False)
            current = account.account.root if account.account else None
            trace["auth_type"] = current.type if current else "missing"
            emit("account/checked", {"auth_type": trace["auth_type"]})
            if trace["auth_type"] != "chatgpt":
                raise ValueError("需要 ChatGPT 登录；未开始任务，不转 API。")
            models = await codex.models()
            found = next((m for m in models.data if m.model == request["model"]), None)
            if found is None or request["effort"] not in [e.reasoning_effort.value for e in found.supported_reasoning_efforts]:
                raise ValueError("当前 Codex 目录没有指定模型或档位；不自动换模型。")
            system = "\n\n".join(m["content"] for m in request["messages"] if m["role"] == "system")
            prompt = "\n\n".join(m["content"] for m in request["messages"] if m["role"] != "system")
            thread = await codex.thread_start(
                model=request["model"], model_provider="openai", cwd=str(directory),
                sandbox=Sandbox.read_only, approval_mode=ApprovalMode.deny_all, ephemeral=True,
                developer_instructions=system + "\nDo not read files, run shell, create agents, or use MCP. "
                + ("Use web search for this discovery task." if request["web_search"] == "live" else
                   "Use only the supplied article data. All tools are disabled. Return the requested output directly."),
            )
            metadata = (await thread.read()).thread
            trace.update(thread_id=thread.id, configured_model=metadata.model,
                         runtime_version=metadata.cli_version)
            if metadata.model != request["model"] or metadata.model_provider != "openai":
                raise ValueError("实际线程配置与请求不同，未开始任务。")
            emit("thread/created", {"thread_id": thread.id, "configured_model": metadata.model})
            turn = await thread.turn(prompt, model=request["model"],
                                     effort=ReasoningEffort(request["effort"]), output_schema=packet["schema"],
                                     sandbox=Sandbox.read_only, approval_mode=ApprovalMode.deny_all)
            trace["turn_id"] = turn.id
            emit("turn/request_accepted", {"thread_id": thread.id, "turn_id": turn.id})

            async def consume():
                nonlocal text
                async for event in turn.stream():
                    payload = wire(event.payload)
                    emit(event.method, payload)
                    if event.method == "thread/tokenUsage/updated":
                        trace["usage"] = payload.get("tokenUsage")
                    if event.method == "item/completed":
                        item = payload.get("item", {})
                        if item.get("type") == "agentMessage" and item.get("phase") in (None, "final_answer"):
                            text = item.get("text", "")
                            (directory / "response.txt").write_text(scrub(text), encoding="utf-8")
                    if event.method == "turn/completed":
                        trace["terminal_status"] = payload["turn"]["status"]
                        if trace["terminal_status"] != "completed":
                            raise ValueError(f"Codex 终态 {trace['terminal_status']}；结果未成功完成。")
                        return
                raise ValueError("事件流结束但没有完成终态。")

            consuming = asyncio.create_task(consume())
            try:
                await asyncio.wait_for(asyncio.shield(consuming), timeout=150)
            except TimeoutError:
                emit("turn/interrupt_requested", {"cause": "timeout"})
                await asyncio.wait_for(turn.interrupt(), timeout=5)
                emit("turn/interrupt_acknowledged", {})
                try:
                    await asyncio.wait_for(consuming, timeout=15)
                except (TimeoutError, ValueError):
                    pass
                raise ValueError("Codex 任务超时；已请求停止，不自动重试。") from None
            finally:
                if not consuming.done():
                    consuming.cancel()
                    await asyncio.gather(consuming, return_exceptions=True)
    except asyncio.CancelledError:
        error = "Codex worker 达到总超时；执行完成未确认，不自动重试。"
        emit("worker/timed_out", {"error": error})
    except Exception as exc:  # noqa: BLE001 - preserve sanitized failures and partial events
        error = str(exc) if isinstance(exc, ValueError) else f"Codex SDK 错误：{type(exc).__name__}。"
        emit("worker/failed", {"error": error})
    trace.update(finished=now(), body=text)
    return scrub({"response": trace, "text": text, "error": error})


async def main(directory):
    packet = json.loads((directory / "input.json").read_text("utf-8"))
    result = await asyncio.wait_for(execute(directory, packet), timeout=180)
    (directory / "result.json").write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")


if __name__ == "__main__":
    asyncio.run(main(Path(sys.argv[1])))
