"""Synthetic HTTP fixtures only. Real smoke results live outside the test suite."""

import json

import httpx
import pytest

from learning_library.collector import Collector
from learning_library.storage import Store


@pytest.fixture
def store(tmp_path):
    value = Store(tmp_path)
    yield value
    value.close()


def post(text="Original statement", post_id="1234", likes=1):
    return {
        "type": "status",
        "id": post_id,
        "text": text,
        "url": f"https://x.com/example/status/{post_id}",
        "author": {"screen_name": "example"},
        "likes": likes,
    }


def collect(store, handler, kind="post", value="1234", **options):
    with httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True) as client:
        return Collector(store, client).collect(kind, value, **options)


def test_repeat_deduplicates_text_but_keeps_raw_evidence_and_human_notes(store):
    note = store.root / "human-notes" / "my-note.md"
    note.write_text("我自己写的判断", encoding="utf-8")
    for likes in (1, 2):
        outcome = collect(
            store,
            lambda req, likes=likes: httpx.Response(
                200, json={"code": 200, "status": post(likes=likes)}
            ),
        )
        assert outcome["new_versions"] == (1 if likes == 1 else 0)
    changed = collect(
        store,
        lambda req: httpx.Response(200, json={"code": 200, "status": post("Changed statement")}),
    )
    assert changed["new_versions"] == 1
    assert len(store.documents()) == 1
    assert store.documents()[0]["document"]["text"] == "Changed statement"
    assert store.db.execute("SELECT count(*) FROM versions").fetchone()[0] == 2
    assert store.db.execute("SELECT count(*) FROM requests").fetchone()[0] == 3
    assert len(list((store.root / "raw").iterdir())) == 3
    assert store.export().exists()
    assert note.read_text(encoding="utf-8") == "我自己写的判断"


def test_search_paginates_deduplicates_and_stops_cursor_cycle(store):
    requests = []

    def handler(request):
        requests.append(request)
        items = [post()] if len(requests) == 1 else [post(), post(post_id="5678")]
        return httpx.Response(
            200, json={"code": 200, "results": items, "cursor": {"bottom": "same"}}
        )

    outcome = collect(store, handler, "search", "from:example workflow", limit=5, max_pages=5)
    assert outcome["saved"] == 2
    assert outcome["stop_reason"] == "cursor_cycle"
    assert len(requests) == 2
    assert requests[1].url.params["cursor"] == "same"


@pytest.mark.parametrize("limit,max_pages,reason", [(1, 5, "limit"), (5, 1, "max_pages")])
def test_search_reports_bounded_results(store, limit, max_pages, reason):
    result = collect(
        store,
        lambda req: httpx.Response(
            200, json={"code": 200, "results": [post()], "cursor": {"bottom": "more"}}
        ),
        "search",
        "workflow",
        limit=limit,
        max_pages=max_pages,
    )
    assert result["saved"] == 1
    assert result["stop_reason"] == reason


@pytest.mark.parametrize(
    "status,payload", [(429, {"code": 429}), (200, {"code": 503}), (200, {"code": 200})]
)
def test_errors_preserve_response_and_do_not_claim_empty_success(store, status, payload):
    result = collect(store, lambda req: httpx.Response(status, json=payload), "search", "workflow")
    assert result["status"] == "failed"
    assert result["saved"] == 0
    request = store.db.execute("SELECT * FROM requests").fetchone()
    assert json.loads((store.root / request["snapshot"]).read_bytes()) == payload
    assert json.loads(store.runs()[0]["result"])["stop_reason"] == "error"


def test_later_page_failure_preserves_first_page(store):
    def handler(request):
        if request.url.params.get("cursor"):
            return httpx.Response(503, text="Unavailable")
        return httpx.Response(
            200, json={"code": 200, "results": [post()], "cursor": {"bottom": "more"}}
        )

    result = collect(store, handler, "search", "workflow", max_pages=2)
    assert result["status"] == "partial"
    assert result["saved"] == 1
    assert len(store.documents()) == 1


def test_network_failure_is_recorded_without_inventing_snapshot(store):
    def handler(request):
        raise httpx.ConnectError("Synthetic offline failure", request=request)

    result = collect(store, handler)
    assert result["status"] == "failed"
    request = store.db.execute("SELECT * FROM requests").fetchone()
    assert request["snapshot"] is None
    assert "offline" in request["error"]


def test_feed_excerpt_is_a_candidate_and_article_is_separate(store):
    rss = b"""<?xml version="1.0"?><rss version="2.0"><channel><title>Example</title>
      <link>https://example.com</link><description>Test</description><item><title>AI workflow</title>
      <link>https://example.com/article</link><description>A short excerpt.</description>
      </item></channel></rss>"""
    result = collect(
        store, lambda req: httpx.Response(200, content=rss), "feed", "https://example.com/feed"
    )
    assert result["status"] == "ok"
    candidate = store.documents()[0]["document"]
    assert candidate["kind"] == "feed_candidate"
    assert "fetch_article_separately" in candidate["completeness"]
    paragraph = "This is a synthetic article about testing AI development workflows. " * 12
    html = f"<html><head><title>Example</title></head><body><article><p>{paragraph}</p></article></body></html>"
    result = collect(
        store, lambda req: httpx.Response(200, text=html), "article", "https://example.com/article"
    )
    assert result["status"] == "ok", result
    assert len(store.documents()) == 2
    assert store.documents()[0]["document"]["kind"] == "article"
    assert store.documents()[0]["document"]["title"] == "Example"


def test_login_page_is_not_a_successfully_extracted_article(store):
    result = collect(
        store,
        lambda req: httpx.Response(200, text="<html>Log in</html>"),
        "article",
        "https://example.com/article",
    )
    assert result["status"] == "failed"
    assert len(store.documents()) == 0


def test_interrupted_download_keeps_only_a_marked_partial_snapshot(store):
    class InterruptedBody(httpx.SyncByteStream):
        def __iter__(self):
            yield b"partial bytes"
            raise httpx.ReadError("Synthetic interrupted download")

    result = collect(store, lambda req: httpx.Response(200, stream=InterruptedBody()))
    assert result["status"] == "failed"
    request = store.db.execute("SELECT * FROM requests").fetchone()
    assert request["truncated"] == 1
    assert (store.root / request["snapshot"]).read_bytes() == b"partial bytes"
