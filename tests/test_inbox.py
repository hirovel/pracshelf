"""Exercise the inbox's public HTTP interface with isolated feeds and counted calls."""

import httpx
import pytest
from fastapi.testclient import TestClient

from learning_library.web import create_app

FEED = "https://example.com/feed"
ARTICLE = "https://example.com/workflow"


@pytest.fixture
def inbox(tmp_path, monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "fixture-do-not-use")
    state = {
        "feed_calls": 0,
        "article_calls": 0,
        "model_calls": 0,
        "feed_status": 200,
        "article_fail": False,
        "summary": "A workflow with tests.",
        "date": "2026-10-09T12:00:00Z",
    }

    def fetch(request):
        if str(request.url) == FEED:
            state["feed_calls"] += 1
            date = f"<updated>{state['date']}</updated>" if state["date"] else ""
            return httpx.Response(
                state["feed_status"],
                text=f'''<feed xmlns="http://www.w3.org/2005/Atom">
                <title>Example engineering feed</title><id>test</id>
                <entry><id>one</id><title>Agent workflow</title><link href="{ARTICLE}"/>
                {date}<summary type="html">{state["summary"]}</summary></entry></feed>''',
            )
        assert str(request.url) == ARTICLE, "Unexpected source or model request"
        state["article_calls"] += 1
        if state["article_fail"]:
            raise httpx.ReadTimeout("Synthetic article timeout", request=request)
        return httpx.Response(
            200,
            text="# Agent workflow\n\n" + "Check test results before accepting edits. " * 30,
            headers={"Content-Type": "text/markdown"},
        )

    def model(request):
        state["model_calls"] += 1
        raise AssertionError("Inbox must never call a model")

    with TestClient(
        create_app(
            tmp_path,
            transport=httpx.MockTransport(fetch),
            translation_transport=httpx.MockTransport(model),
        )
    ) as client:
        yield client, state


def refresh(client):
    response = client.post("/api/inbox/refresh", json={"url": FEED})
    assert response.status_code == 200
    return response.json()


def items(client, **params):
    return client.get("/api/inbox", params=params).json()["items"]


def decide(client, item, action, submission="save-one"):
    return client.post(
        f"/api/inbox/{item['id']}/decision", json={"action": action, "submission_id": submission}
    )


def test_refresh_only_reads_feed_and_does_not_invent_author_or_publish_date(inbox):
    client, state = inbox
    state["summary"] = '&lt;img src="https://example.com/tracker"&gt;A &lt;b&gt;workflow&lt;/b&gt;'
    assert refresh(client)["saved"] == 1
    item = items(client)[0]
    assert item["summary"] == "A workflow"
    assert item["author"] == ""  # Feed title is not an author.
    assert item["date_kind"] == "updated"
    assert item["date_iso"] == "2026-10-09"
    assert item["candidate_id"] is None
    assert state["article_calls"] == state["model_calls"] == 0
    state["date"] = ""
    refresh(client)
    assert items(client)[0]["date_check"]["status"] == "unknown_date"
    assert client.get("/api/candidates").json() == []


def test_ignore_survives_changed_metadata_refresh_and_can_be_restored(inbox):
    client, state = inbox
    refresh(client)
    item = items(client)[0]
    assert decide(client, item, "ignore").json()["decision"] == "ignored"
    state["summary"] = "Changed source description"
    refresh(client)
    assert items(client) == []
    ignored = items(client, state="ignored")[0]
    assert ignored["id"] == item["id"]
    assert ignored["summary"] == state["summary"]
    assert len(ignored["decisions"]) == 1
    decide(client, ignored, "restore")
    assert len(items(client)) == 1
    assert state["article_calls"] == state["model_calls"] == 0


def test_save_replay_reuses_original_and_does_not_create_human_notes(inbox):
    client, state = inbox
    refresh(client)
    item = items(client)[0]
    saved = decide(client, item, "save").json()
    assert saved["capture"]["version_id"]
    for submission in ["save-one", "save-two"]:
        assert (
            decide(client, item, "save", submission).json()["capture"]["version_id"]
            == saved["capture"]["version_id"]
        )
    refresh(client)
    assert items(client) == []
    assert len(items(client, state="selected")) == 1
    assert state["article_calls"] == 1 and state["model_calls"] == 0
    run = saved["capture"]["pipeline_id"]
    assert client.get(f"/api/runs/{run}/curation").json()["latest"] is None
    assert decide(client, item, "ignore").json()["decision"] == "ignored"
    restored = decide(client, item, "restore").json()
    assert restored["decision"] == "selected"
    assert restored["capture"]["version_id"] == saved["capture"]["version_id"]
    assert client.get("/api/candidates", params={"origin": "rss"}).json()[0]["latest"] is None


def test_failed_capture_is_selected_and_retry_preserves_failure_history(inbox):
    client, state = inbox
    refresh(client)
    item = items(client)[0]
    state["article_fail"] = True
    failed = decide(client, item, "save").json()
    assert failed["decision"] == "selected"
    assert failed["capture"]["status"] == "failed"
    assert "timeout" in failed["capture"]["error"].lower()
    decide(client, item, "save")  # A lost response replay does not retry.
    assert state["article_calls"] == 1
    state["article_fail"] = False
    saved = decide(client, item, "save", "retry-two").json()
    assert saved["capture"]["version_id"]
    assert [r["status"] for r in saved["capture"]["runs"]] == ["failed", "completed"]
    assert state["article_calls"] == 2 and state["model_calls"] == 0


def test_feed_failure_keeps_existing_items_and_observable_attempt(inbox):
    client, state = inbox
    refresh(client)
    state["feed_status"] = 503
    assert refresh(client)["status"] == "failed"
    response = client.get("/api/inbox").json()
    assert len(response["items"]) == 1
    assert response["feeds"][0]["attempts"][0]["result"]["error"]
    assert len(response["feeds"][0]["attempts"]) == 2
    assert state["article_calls"] == state["model_calls"] == 0
