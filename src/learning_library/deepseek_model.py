"""One observed LangChain JSON call. No persistence, retries or business verdicts."""

import json

import httpx
from langchain_deepseek import ChatDeepSeek
from langsmith import tracing_context
from openai import APIConnectionError, APIStatusError

from .storage import now

SETTINGS = {
    "base_url": "https://api.deepseek.com",
    "structured_output": "json_mode",
    "max_retries": 0,
    "request_limit": 1,
    "timeout_seconds": 120,
    "cloud_tracing": False,
}

def call_json(request, schema, *, api_key, transport=None):
    """One structured model call, with wire evidence and no automatic retry.

    Returns response/output/error even on failure. Persistence and cache decisions
    belong to callers; the transport seam also lets tests exercise the SDK.
    Only body/URL/method are recorded, never authentication headers.
    """
    trace = {"provider": "deepseek", "engine": "langchain", "request_count": 0,
             "requests": [], "http_status": None, "body": None}
    output, error = None, None

    def redact(text):
        return text.replace(api_key, "[REDACTED]") if api_key else text

    def record_request(request):
        if trace["request_count"] >= SETTINGS["request_limit"]:
            raise ValueError("本次模型请求已达到一次上限。")
        trace["requests"].append(
            {
                "started": now(),
                "method": request.method,
                "url": str(request.url),
                "body": json.loads(redact(request.content.decode("utf-8"))),
            }
        )
        trace["request_count"] += 1

    def record_response(response):
        # Read before SDK parsing so malformed and non-200 responses also survive.
        trace["http_status"] = response.status_code
        response.read()
        trace["body"] = redact(response.text)
        trace["requests"][-1]["finished"] = now()

    try:
        if not api_key:
            raise ValueError("未配置 DeepSeek 密钥，未调用模型。")
        with (
            tracing_context(enabled=False),
            httpx.Client(
                timeout=SETTINGS["timeout_seconds"],
                transport=transport,
                follow_redirects=False,
                event_hooks={"request": [record_request], "response": [record_response]},
            ) as client,
        ):
            model = ChatDeepSeek(
                model=request["model"],
                api_key=api_key,
                base_url=SETTINGS["base_url"],
                max_tokens=request["max_tokens"],
                extra_body={"thinking": request["thinking"]},
                max_retries=SETTINGS["max_retries"],
                timeout=SETTINGS["timeout_seconds"],
                http_client=client,
                cache=False,
            )
            result = model.with_structured_output(
                schema, method=SETTINGS["structured_output"], include_raw=True
            ).invoke(request["messages"])
        raw = result["raw"]
        trace["langchain_message"] = json.loads(redact(raw.model_dump_json()))
        if raw.response_metadata.get("finish_reason") != "stop":
            raise ValueError("模型输出未正常结束，不能作为完整审核结果。")
        # Do not accept JSON which the framework's permissive parser repaired.
        json.loads(raw.content)
        if result["parsing_error"] or result["parsed"] is None:
            raise ValueError(f"模型输出不符合 {schema.__name__} 结构；原始响应已保留。")
        output = schema.model_validate(json.loads(raw.content)).model_dump()
    except (APIConnectionError, httpx.HTTPError):
        error = "DeepSeek 网络或超时错误；结果未确认，不自动重试。"
    except APIStatusError as exc:
        error = f"DeepSeek HTTP {exc.status_code}，详见保存的响应。"
    except (ValueError, KeyError, IndexError, TypeError) as exc:
        error = redact(str(exc))
    except Exception as exc:  # noqa: BLE001 — retain unexpected framework failures.
        error = f"内部错误：{type(exc).__name__}；已保留失败记录。"
    # Keep usage even if the schema, finish reason, or business checks failed.
    try:
        body = json.loads(trace["body"] or "{}")
        trace.update(
            usage=body.get("usage"),
            provider_model=body.get("model"),
            response_id=body.get("id"),
        )
    except (ValueError, AttributeError):
        pass
    return json.loads(redact(json.dumps({"response": trace, "output": output, "error": error}, ensure_ascii=False, sort_keys=True)))


def call_text(request, *, api_key, transport=None):
    """Observed API text call; translation-specific checks remain with Translation."""
    trace = {"provider": "deepseek", "engine": "httpx", "request_count": 0,
             "http_status": None, "body": None, "requests": []}
    text, error = None, None
    try:
        if not api_key:
            raise ValueError("未配置 DeepSeek 密钥，未调用模型。")
        trace["requests"].append({"started": now(), "body": request,
                                  "url": SETTINGS["base_url"] + "/chat/completions"})
        trace["request_count"] = 1
        with httpx.Client(timeout=120, transport=transport, follow_redirects=False) as client:
            response = client.post(SETTINGS["base_url"] + "/chat/completions",
                                   headers={"Authorization": f"Bearer {api_key}"}, json=request)
        trace.update(http_status=response.status_code, body=response.text.replace(api_key, "[REDACTED]"))
        if response.status_code != 200:
            raise ValueError(f"DeepSeek HTTP {response.status_code}；本批未完成。")
        body = response.json()
        trace.update(usage=body.get("usage"), provider_model=body.get("model"), response_id=body.get("id"))
        choice = body["choices"][0]
        if choice["finish_reason"] != "stop":
            raise ValueError("模型输出未正常结束（可能被截断），未计入完整译文。")
        text = choice["message"]["content"]
    except httpx.HTTPError:
        error = "DeepSeek 网络或超时错误；本批未确认完成，不自动重试。"
    except (ValueError, KeyError, IndexError, TypeError) as exc:
        error = str(exc).replace(api_key, "[REDACTED]") if api_key else str(exc)
    return {"response": trace, "text": text, "error": error}
