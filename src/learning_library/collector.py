"""One collection interface, two actual providers, traceable bounded requests."""

import hashlib
import json
import re
from calendar import timegm
from datetime import UTC, datetime
from urllib.parse import urldefrag, urlparse

import feedparser
import httpx

from .extraction import extract_document
from .storage import Store

FEEDS = {
    "simon": "https://simonwillison.net/atom/entries/",
    "mario": "https://mariozechner.at/rss.xml",
    "armin": "https://lucumr.pocoo.org/feed.atom",
    "peter": "https://steipete.me/rss.xml",
}
MAX_BYTES = 8 * 1024 * 1024


class CollectionError(Exception):
    pass


def web_url(value):
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username:
        raise CollectionError("Expected an HTTP(S) URL without credentials")
    return urldefrag(value)[0]


class Collector:
    def __init__(self, store: Store, client: httpx.Client):
        self.store = store
        self.client = client

    def collect(self, kind, value, *, limit=5, max_pages=1):
        """Return a persisted run result, including partial success and its stop reason.

        Repeating a collection keeps request history but creates no duplicate text versions.
        Search limits count unique posts. Feeds save candidates, never presumed full articles.
        """
        if kind not in {"post", "search", "feed", "article", "fetch", "extract"}:
            raise ValueError("Unknown collection kind")
        if not 1 <= limit <= 100 or not 1 <= max_pages <= 10:
            raise ValueError("limit must be 1..100; max_pages must be 1..10")
        run_id = self.store.start(kind, value, {"limit": limit, "max_pages": max_pages})
        result = {
            "run_id": run_id,
            "status": "ok",
            "saved": 0,
            "new_versions": 0,
            "pages": 0,
            "stop_reason": None,
            "warnings": [],
        }
        try:
            if kind == "fetch":
                _, request_id, _ = self._fetch(value, result)
                result.update(request_id=request_id, stop_reason="snapshot_saved")
            elif kind == "extract":
                captured = self.store.captured_request(int(value))
                if captured["truncated"] or captured["status"] != 200 or not captured["snapshot"]:
                    raise CollectionError("Extraction requires a complete HTTP 200 snapshot")
                body = (self.store.root / captured["snapshot"]).read_bytes()
                if (
                    hashlib.sha256(body).hexdigest()
                    != captured["snapshot"].split("/")[-1].split(".")[0]
                ):
                    raise CollectionError("Snapshot hash mismatch; original bytes changed")
                self._extract_article(body, captured["id"], captured["final_url"], result)
            elif kind == "search":
                self._search(value, limit, max_pages, result)
            elif kind == "post":
                match = re.fullmatch(r"\d{2,20}", value)
                if not match:
                    parsed = urlparse(value)
                    if parsed.hostname not in {
                        "x.com",
                        "twitter.com",
                        "fxtwitter.com",
                        "www.x.com",
                    }:
                        raise CollectionError("Expected an X post URL or numeric post ID")
                    match = re.fullmatch(r"/[^/]+/status/(\d{2,20})/?", parsed.path)
                if not match:
                    raise CollectionError("Invalid post ID / URL")
                post_id = match.group(1) if match.lastindex else match.group()
                data, request_id = self._json(
                    f"https://api.fxtwitter.com/2/status/{post_id}", result
                )
                self._post(data.get("status"), request_id, result)
                result["stop_reason"] = "single_post"
            elif kind == "feed":
                self._feed(FEEDS.get(value, value), limit, result)
            else:
                self._article(value, result)
        except (
            CollectionError,
            httpx.HTTPError,
            ValueError,
            KeyError,
            TypeError,
            OSError,
        ) as error:
            result.update(
                status="partial" if result["saved"] else "failed",
                stop_reason="error",
                error=f"{type(error).__name__}: {error}",
            )
        self.store.finish(run_id, result)
        return result

    def _fetch(self, url, result, params=None):
        url = web_url(url)
        request = self.client.build_request("GET", url, params=params)
        body = bytearray()
        response = None
        truncated = False
        complete = False
        error = None
        try:
            response = self.client.send(request, stream=True)
            for chunk in response.iter_bytes():
                room = MAX_BYTES - len(body)
                body.extend(chunk[:room])
                if len(chunk) > room:
                    truncated = True
                    raise CollectionError("Response exceeds 8 MiB; partial snapshot retained")
            complete = True
            response.raise_for_status()
        except (httpx.HTTPError, CollectionError) as exc:
            error = str(exc)
            truncated = response is not None and not complete
            raise
        finally:
            request_id = self.store.request(
                result["run_id"],
                str(request.url),
                body=bytes(body) if response is not None else None,
                final_url=str(response.url) if response is not None else None,
                status=response.status_code if response is not None else None,
                content_type=response.headers.get("content-type") if response is not None else None,
                truncated=truncated,
                error=error,
            )
            if response is not None:
                response.close()
        result["pages"] += 1
        return bytes(body), request_id, str(response.url)

    def _json(self, url, result, params=None):
        body, request_id, _ = self._fetch(url, result, params)
        data = json.loads(body)
        if not isinstance(data, dict) or data.get("code") != 200:
            raise CollectionError(
                f"FxTwitter rejected request: code={data.get('code') if isinstance(data, dict) else 'invalid response'}"
            )
        return data, request_id

    def _save(self, key, doc, request_id, result):
        result["new_versions"] += self.store.save(key, doc, request_id)
        result["saved"] += 1
        result.setdefault("version_ids", []).append(
            self.store.db.execute(
                "SELECT latest_version FROM documents WHERE document_key=?", (key,)
            ).fetchone()[0]
        )

    def _post(self, item, request_id, result):
        if (
            not isinstance(item, dict)
            or not item.get("id")
            or not isinstance(item.get("text"), str)
        ):
            raise CollectionError("Provider returned an invalid post")
        author = (item.get("author") or {}).get("screen_name", "")
        self._save(
            f"x:{item['id']}",
            {
                "kind": "x_post",
                "title": item["text"][:100].replace("\n", " ") or str(item["id"]),
                "url": item.get("url") or f"https://x.com/{author}/status/{item['id']}",
                "author": author,
                "text": item["text"],
                "published": item.get("created_at"),
                "completeness": "provider_post_text; thread/media/linked_article_not_collected",
            },
            request_id,
            result,
        )

    def _search(self, query, limit, max_pages, result):
        if not query.strip():
            raise CollectionError("Search query cannot be empty")
        cursor = None
        cursors, seen = set(), set()
        for _ in range(max_pages):
            params = {"q": query, "feed": "latest", "count": min(30, limit - len(seen))}
            if cursor:
                params["cursor"] = cursor
            data, request_id = self._json("https://api.fxtwitter.com/2/search", result, params)
            items = data.get("results")
            if not isinstance(items, list):
                raise CollectionError("Missing results list; cannot infer empty search")
            for item in items:
                if not isinstance(item, dict):
                    raise CollectionError("Invalid search result item")
                if item.get("type") != "status":
                    result["warnings"].append("Skipped non-status result")
                    continue
                if item.get("id") in seen:
                    continue
                self._post(item, request_id, result)
                seen.add(item["id"])
                if len(seen) >= limit:
                    break
            cursor = (data.get("cursor") or {}).get("bottom")
            result["next_cursor"] = cursor
            if len(seen) >= limit:
                result["stop_reason"] = "limit"
                return
            if not cursor:
                result["stop_reason"] = "provider_exhausted"
                return
            if cursor in cursors:
                result["stop_reason"] = "cursor_cycle"
                return
            cursors.add(cursor)
        result["stop_reason"] = "max_pages"

    def _feed(self, url, limit, result):
        body, request_id, final_url = self._fetch(url, result)
        feed = feedparser.parse(body)
        if not feed.version or feed.bozo:
            raise CollectionError("Invalid/malformed RSS or Atom; raw response retained")
        for entry in feed.entries[:limit]:
            if not entry.get("link"):
                result["warnings"].append("Skipped feed entry without a link")
                continue
            try:
                link = web_url(entry.link)
            except CollectionError:
                result["warnings"].append("Skipped feed entry with an invalid article URL")
                continue
            date_kind = "published" if entry.get("published") else "updated"
            parsed_date = entry.get(date_kind + "_parsed")
            # The raw feed preserves supplied content; this record is only a discovery candidate.
            self._save(
                f"candidate:{link}",
                {
                    "kind": "feed_candidate",
                    "title": entry.get("title", link),
                    "url": link,
                    "author": entry.get("author", feed.feed.get("author", "")),
                    "published": entry.get("published", entry.get("updated")),
                    "date_kind": date_kind if entry.get(date_kind) else None,
                    "date_iso": datetime.fromtimestamp(timegm(parsed_date), UTC).date().isoformat()
                    if parsed_date
                    else None,
                    "text": entry.get("summary", ""),
                    "feed_url": final_url,
                    "completeness": "feed_candidate_only; fetch_article_separately",
                },
                request_id,
                result,
            )
        result["stop_reason"] = "limit" if len(feed.entries) > limit else "feed_end"

    def _article(self, url, result):
        body, request_id, final_url = self._fetch(url, result)
        self._extract_article(body, request_id, final_url, result)

    def _extract_article(self, body, request_id, final_url, result):
        captured = self.store.captured_request(request_id)
        document = extract_document(body, final_url, captured["content_type"])
        self._save(
            f"article:{web_url(final_url)}",
            document,
            request_id,
            result,
        )
        result["warnings"].extend(document["extraction"]["warnings"])
        result["extraction"] = document["extraction"]
        result["stop_reason"] = "single_article"
        result["request_id"] = request_id
