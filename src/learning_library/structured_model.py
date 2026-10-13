"""Unified model Interface. Provider choice is explicit; failures never switch it."""

from importlib.metadata import PackageNotFoundError, version

SETTINGS = {"base_url": "https://api.deepseek.com", "structured_output": "json_mode",
            "max_retries": 0, "request_limit": 1, "timeout_seconds": 120, "cloud_tracing": False}


def execution(provider, revision):
    names = ("openai-codex", "openai-codex-cli-bin") if provider == "codex" else (
        "langchain-deepseek", "langchain-core", "langchain-openai", "openai")
    packages = {}
    for name in names:
        try:
            packages[name] = version(name)
        except PackageNotFoundError:
            packages[name] = "not installed"
    if provider == "codex":
        return {"engine": "codex_sdk", "adapter": "AsyncCodex", "provider": provider,
                "revision": revision, "packages": packages, "api_fallback": False,
                "task_limit": 1, "internal_model_requests": "unknown", "web_search": "disabled",
                "output_token_limit_enforced": False, "adapter_retries": 0}
    return {"engine": "langchain", "adapter": "ChatDeepSeek", "provider": provider,
            "revision": revision, "packages": packages, **SETTINGS}


def for_provider(request, provider, effort="medium"):
    if provider == "deepseek":
        return request
    # Codex does not support this API's max_tokens/thinking/response_format fields.
    return {"provider": "codex", "model": request["model"], "effort": effort,
            "web_search": "disabled", "messages": request["messages"]}


def call_json(request, schema, *, api_key="", transport=None, root=None, trace_name=None):
    provider = request.get("provider", "deepseek")
    if provider == "codex":
        from .codex_model import call
        return call(request, schema, root=root, trace_name=trace_name)
    if provider != "deepseek":
        raise ValueError("未知提供方；未执行任务。")
    from .deepseek_model import call_json as call_api
    return call_api(request, schema, api_key=api_key, transport=transport)


def call_text(request, *, api_key="", transport=None, root=None, trace_name=None):
    if request.get("provider", "deepseek") == "codex":
        from .codex_model import call
        return call(request, root=root, trace_name=trace_name)
    if request.get("provider", "deepseek") != "deepseek":
        raise ValueError("未知提供方；未执行任务。")
    from .deepseek_model import call_text as call_api
    return call_api(request, api_key=api_key, transport=transport)
