"""Anonymous Exa MCP search; one bounded call, no credentials or paid fallback."""

import asyncio
import re
from datetime import timedelta

import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

from .search_types import SearchObservation
from .storage import now

ENDPOINT = "https://mcp.exa.ai/mcp"
CALL_TIMEOUT_SECONDS = 50
LIMITATIONS = (
    "Exa 免费匿名 MCP，有服务端限流；未使用账户或密钥，不转付费。"
    "返回为提供方摘要/摘录，不是已保存的完整原文；日期、价值和有效性待审核。"
)


async def _call(query, objective):
    async with asyncio.timeout(CALL_TIMEOUT_SECONDS):
        async with (
            httpx.AsyncClient(timeout=httpx.Timeout(10, read=35)) as client,
            streamable_http_client(ENDPOINT, http_client=client) as (read, write, _),
            ClientSession(read, write, read_timeout_seconds=timedelta(seconds=40)) as session,
        ):
            await session.initialize()
            return await session.call_tool(
                "web_search_exa",
                {"query": query, "objective": objective, "numResults": 5},
            )


def search(query, objective):
    """Return validated evidence, including failures; never treat a parse error as empty."""
    raw = "No tool response received"
    failure = "connection_or_tool_call_failed"
    try:
        response = asyncio.run(_call(query, objective))
        raw = response.model_dump_json()
        if response.isError:
            failure = "provider_tool_error"
            raise ValueError("Provider reported a tool error")
        failure = "unrecognized_or_invalid_response"
        text = "\n".join(c.text for c in response.content if c.type == "text")
        # Hosted MCP currently returns labelled text, not structured JSON hits.
        blocks = re.split(r"(?m)(?=^Title: .+\nURL: https?://)", text)
        hits = []
        for block in blocks:
            match = re.match(r"Title: ([^\n]+)\nURL: (https?://[^\s]+)\n", block)
            if match:
                details = block[match.end() :]

                hits.append(
                    {
                        "title": match[1][:1000],
                        "url": match[2],
                        "snippet": details[:6000],
                        "author": _metadata(details, "Author", 500),
                        "published": _metadata(details, "Published", 200),
                    }
                )
        if not hits and text.strip().lower().rstrip(".") not in {
            "no results found",
            "no search results found",
            "no search results found. please try a different query",
            "no results",
        }:
            raise ValueError("Unrecognized Exa response; inspect preserved tool output")
        return SearchObservation(
            provider="exa_mcp_anonymous",
            observed_at=now(),
            status="succeeded",
            results=hits[:5],
            provider_output=raw,
            limitations=LIMITATIONS,
        )
    except Exception as error:  # noqa: BLE001 - failures are observable, never retried
        return SearchObservation(
            provider="exa_mcp_anonymous",
            observed_at=now(),
            status="failed",
            provider_output=raw[:500000],
            limitations=LIMITATIONS
            + f" {failure}: {type(error).__name__}."
            + (" Tool output truncated at 500000 characters." if len(raw) > 500000 else ""),
        )


def _metadata(text, label, limit):
    field = re.search(rf"(?m)^{label}: (.+)$", text)
    value = field[1].strip() if field else ""
    return value[:limit] if value and value.casefold() not in {"n/a", "unknown", "none"} else None
