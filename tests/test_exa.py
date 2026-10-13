import json

import pytest
from mcp.types import CallToolResult, TextContent

from learning_library import exa


@pytest.mark.parametrize(
    "case", ["hits", "empty", "empty_hint", "rate_limit", "changed_format", "timeout"]
)
def test_search_preserves_evidence_and_distinguishes_failure(monkeypatch, case):
    calls = []
    content = {
        "hits": "Title: Original\nURL: https://example.com/original\nPublished: N/A\nHighlights:\nEvidence",
        "empty": "No results found.",
        "empty_hint": "No search results found. Please try a different query.",
        "rate_limit": "Rate limit exceeded",
        "changed_format": "A different response with https://example.com/original",
    }

    async def call(query, objective):
        calls.append((query, objective))
        if case == "timeout":
            raise TimeoutError()
        return CallToolResult(
            content=[TextContent(type="text", text=content[case])],
            isError=case == "rate_limit",
        )

    monkeypatch.setattr(exa, "_call", call)
    result = exa.search("query", "purpose")
    assert calls == [("query", "purpose")]
    assert result.status == ("succeeded" if case in {"hits", "empty", "empty_hint"} else "failed")
    assert len(result.results) == (1 if case == "hits" else 0)
    if case != "timeout":
        assert json.loads(result.provider_output)["content"][0]["text"] == content[case]
    assert result.reported_cost_usd is None


def test_provider_metadata_stays_unverified_and_missing_is_not_invented(monkeypatch):
    text = "Title: First\nURL: https://example.com/one\nPublished: 2026-10-06\nAuthor: Example Author\nHighlights: Practice\n\n---\n\nTitle: Second\nURL: https://example.com/two\nPublished: N/A\nAuthor: N/A\nText: Example"

    async def call(query, objective):
        return CallToolResult(content=[TextContent(type="text", text=text)])

    monkeypatch.setattr(exa, "_call", call)
    result = exa.search("query", "purpose")
    assert result.results[0].author == "Example Author"
    assert result.results[0].published == "2026-10-06"
    assert result.results[1].author is None and result.results[1].published is None


def test_mcp_timeout_cancels_wait_and_closes_http_client(monkeypatch):
    import asyncio

    import httpx

    methods = []

    async def handler(request):
        body = json.loads(request.content)
        methods.append(body["method"])
        if body["method"] == "initialize":
            return httpx.Response(
                200,
                json={
                    "jsonrpc": "2.0",
                    "id": body["id"],
                    "result": {
                        "protocolVersion": body["params"]["protocolVersion"],
                        "capabilities": {},
                        "serverInfo": {"name": "fixture", "version": "1"},
                    },
                },
            )
        if body["method"] == "notifications/initialized":
            return httpx.Response(202)
        await asyncio.Event().wait()

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    monkeypatch.setattr(exa.httpx, "AsyncClient", lambda **kwargs: client)
    monkeypatch.setattr(exa, "CALL_TIMEOUT_SECONDS", 0.2)
    result = exa.search("query", "purpose")
    assert result.status == "failed"
    assert methods == ["initialize", "notifications/initialized", "tools/call"]
    assert client.is_closed
