"""SDK onboarding worker. No thread, turn, article input, or credential logging."""

import asyncio
import json
import sys
import time
from contextlib import AsyncExitStack, suppress
from pathlib import Path
from urllib.parse import urlsplit


async def connect(action, directory, model, effort, *, factory=None, emit=None,
                  check_timeout=30, login_timeout=300):
    from openai_codex import AsyncCodex, CodexConfig

    emit = emit or (lambda value: print(json.dumps(value, ensure_ascii=False), flush=True))
    factory = factory or AsyncCodex
    started, calls = time.monotonic(), []
    result = {"auth_type": "unknown", "models": None, "model_found": None,
              "effort_found": None, "model_tasks": 0, "api_fallback": False,
              "model": model, "effort": effort}

    async def cancelled():
        while not (directory / "cancel").exists():
            await asyncio.sleep(0.1)

    async def run():
        config = CodexConfig(cwd=str(directory), client_name="learning_library_setup",
            client_title="Local Codex connection", config_overrides=(
                'forced_login_method="chatgpt"', 'model_provider="openai"',
                'web_search="disabled"', 'history.persistence="none"',
                'features.apps=false', 'features.hooks=false', 'notify=[]',
            ))
        async with AsyncExitStack() as cleanup:
            codex = factory(config)
            cleanup.push_async_callback(codex.close)
            emit({"status": "checking"})
            await asyncio.wait_for(codex.__aenter__(), check_timeout)
            calls.append("account/read")
            account = await asyncio.wait_for(codex.account(refresh_token=False), check_timeout)
            current = account.account.root if account.account else None
            result["auth_type"] = current.type if current else "missing"
            if action == "login" and result["auth_type"] != "chatgpt":
                calls.append("account/login/start")
                handle = await asyncio.wait_for(codex.login_chatgpt(), check_timeout)
                pending = True

                async def cancel_login():
                    if pending:
                        with suppress(Exception):
                            await asyncio.wait_for(handle.cancel(), 3)

                cleanup.push_async_callback(cancel_login)
                url = urlsplit(handle.auth_url)
                if url.scheme != "https" or url.hostname != "auth.openai.com":
                    raise ValueError("官方登录地址与预期不同。")
                emit({"status": "waiting_login", "login_url": handle.auth_url})
                complete = await asyncio.wait_for(handle.wait(), login_timeout)
                pending = False
                if not complete.success:
                    raise ValueError("官方登录未完成。")
                emit({"status": "checking", "login_url": None})
                calls.append("account/read")
                account = await asyncio.wait_for(codex.account(refresh_token=False), check_timeout)
                current = account.account.root if account.account else None
                result["auth_type"] = current.type if current else "missing"
            if result["auth_type"] == "chatgpt":
                calls.append("model/list")
                catalog = await asyncio.wait_for(codex.models(), check_timeout)
                result["models"] = [{"model": entry.model,
                    "efforts": [item.reasoning_effort.value
                                for item in entry.supported_reasoning_efforts]}
                    for entry in catalog.data]
                found = next((item for item in result["models"] if item["model"] == model), None)
                result["model_found"] = found is not None
                result["effort_found"] = bool(found and effort in found["efforts"])

    task, stop = asyncio.create_task(run()), asyncio.create_task(cancelled())
    status, error = "completed", None
    try:
        done, _ = await asyncio.wait({task, stop}, return_when=asyncio.FIRST_COMPLETED)
        if stop in done and not task.done():
            status = "cancelled"
            task.cancel()
        else:
            await task
    except TimeoutError:
        status, error = "failed", "SDK 检查或登录等待超时，可重新操作；没有执行模型任务。"
    except Exception as exc:  # noqa: BLE001 - SDK errors can include account details
        status, error = "failed", f"SDK 连接失败：{type(exc).__name__}；可检查网络后重试。"
    finally:
        stop.cancel()
        if not task.done():
            task.cancel()
        await asyncio.gather(task, stop, return_exceptions=True)
    result.update(calls=calls, elapsed_seconds=round(time.monotonic() - started, 3))
    emit({"status": status, "error": error, "result": result, "login_url": None})


if __name__ == "__main__":
    asyncio.run(connect(sys.argv[1], Path(sys.argv[2]), sys.argv[3], sys.argv[4]))
