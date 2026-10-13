"""Real tool + HTTP interface, synthetic provider and documents; no paid API."""

import json
from datetime import UTC, datetime

import httpx
import pytest
from fastapi.testclient import TestClient

from learning_library.web import create_app


@pytest.fixture
def setup(tmp_path):
    calls = {"search": 0, "fetch": 0, "model": 0}
    options = {"failed": False, "empty": False, "fetch_failed": False}

    def provider(query, objective):
        calls["search"] += 1
        assert query and objective
        hits = (
            []
            if options["failed"] or options["empty"]
            else [
                {
                    "url": "https://example.com/practice.md",
                    "title": "Synthetic practice",
                    "snippet": "Provider excerpt only",
                    "author": "Fixture Author",
                    "published": "unknown",
                },
                {"url": "https://example.com/practice.md#repeated", "title": "Duplicate URL"},
            ]
        )
        return {
            "provider": "synthetic",
            "observed_at": datetime.now(UTC).isoformat(),
            "status": "failed" if options["failed"] else "succeeded",
            "results": hits,
            "provider_output": json.dumps(hits) if not options["failed"] else "Rate limit",
            "limitations": "Synthetic rate limit"
            if options["failed"]
            else "Synthetic evidence, unverified",
        }

    def fetch(request):
        calls["fetch"] += 1
        return httpx.Response(
            503 if options["fetch_failed"] else 200,
            text="# Full source\n\n" + "Detailed source condition and implementation. " * 8,
            headers={"Content-Type": "text/markdown"},
        )

    def model(request):
        calls["model"] += 1
        raise AssertionError("No model permitted")

    payload = {
        "submission_id": "one",
        "query": "senior developer AI practices",
        "objective": "学习有依据的开发实践",
    }
    with TestClient(
        create_app(
            tmp_path,
            httpx.MockTransport(fetch),
            translation_transport=httpx.MockTransport(model),
            search_provider=provider,
        )
    ) as client:
        yield client, payload, calls, options


def test_explicit_tool_preserves_provenance_and_replay_does_not_search(setup):
    client, payload, calls, _ = setup
    assert client.get("/api/searches").json() == []
    state = client.post("/api/searches", json=payload).json()
    assert state["status"] == "succeeded"
    assert state["output"]["duplicate_urls"] == 1
    assert len(state["items"]) == 1
    assert state["items"][0]["author"] == "Fixture Author"
    assert state["execution"]["engine"] == "langchain_tool"
    assert state["execution"]["provider_arguments"] == {
        "query": payload["query"],
        "objective": payload["objective"],
        "numResults": 5,
    }
    assert state["execution"]["model_calls"] == 0
    assert state["output"]["reported_cost_usd"] is None
    assert client.post("/api/searches", json=payload).json() == state
    assert client.get("/api/searches/" + state["id"]).json() == state
    assert len(client.get("/api/searches").json()) == 1
    assert client.get("/api/candidates").json() == []
    assert client.get("/api/library").json() == []
    assert calls == {"search": 1, "fetch": 0, "model": 0}
    assert client.post("/api/searches", json=payload | {"query": "changed"}).status_code == 409


def test_save_reuses_capture_and_records_search_origin_without_screening(setup):
    client, payload, calls, _ = setup
    state = client.post("/api/searches", json=payload).json()
    path = f"/api/searches/{state['id']}/items/0/decision"
    ignored = client.post(path, json={"action": "ignore"}).json()
    assert ignored["items"][0]["decision"] == "ignored"
    assert calls["fetch"] == 0
    restored = client.post(path, json={"action": "restore"}).json()
    assert restored["items"][0]["decision"] == "pending"
    saved = client.post(path, json={"action": "save", "submission_id": "capture-1"}).json()
    item = saved["items"][0]
    assert item["capture"]["version_id"]
    candidate = client.get("/api/candidates/" + item["candidate_id"]).json()
    assert candidate["input"]["origin"] == "web_search"
    assert state["id"] in candidate["input"]["source_note"]
    assert payload["query"] in candidate["input"]["source_note"]
    assert candidate["attempts"] == []
    assert len(client.get("/api/candidates?origin=web_search").json()) == 1
    article = client.get("/api/library/" + item["capture"]["pipeline_id"]).json()
    assert "Detailed source condition" in article["source_html"]
    assert "Provider excerpt" not in article["source_html"]
    assert article["note"] == ""
    client.post(path, json={"action": "save", "submission_id": "capture-2"})
    assert len(client.get("/api/candidates").json()) == 1
    assert calls == {"search": 1, "fetch": 1, "model": 0}
    assert (
        client.post(
            f"/api/searches/{state['id']}/items/99/decision",
            json={"action": "save", "submission_id": "bad"},
        ).status_code
        == 400
    )


def test_failure_empty_and_manual_capture_retry_are_distinct(setup):
    client, payload, calls, options = setup
    options["failed"] = True
    failed = client.post("/api/searches", json=payload).json()
    assert failed["status"] == "failed" and "rate limit" in failed["error"].lower()
    assert client.post("/api/searches", json=payload).json() == failed
    options.update(failed=False, empty=True)
    empty = client.post("/api/searches", json=payload | {"submission_id": "empty"}).json()
    assert empty["status"] == "succeeded" and empty["items"] == []
    options.update(empty=False, fetch_failed=True)
    state = client.post("/api/searches", json=payload | {"submission_id": "next", "refresh": True}).json()
    path = f"/api/searches/{state['id']}/items/0/decision"
    capture = client.post(path, json={"action": "save", "submission_id": "failed"}).json()
    assert capture["items"][0]["capture"]["status"] == "failed"
    assert calls["fetch"] == 1
    client.post(path, json={"action": "save", "submission_id": "failed"})
    assert calls["fetch"] == 1
    options["fetch_failed"] = False
    capture = client.post(path, json={"action": "save", "submission_id": "retry"}).json()
    assert capture["items"][0]["capture"]["status"] == "completed"
    assert calls == {"search": 3, "fetch": 2, "model": 0}


def test_input_limits_reject_invalid_request_before_search(setup):
    client, payload, calls, _ = setup
    assert client.post("/api/searches", json=payload | {"query": " "}).status_code == 422
    assert client.post("/api/searches", json=payload | {"query": "x" * 1001}).status_code == 422
    assert calls["search"] == 0


def test_same_day_cache_keeps_choices_and_submission_alias_is_idempotent(setup):
    client, payload, calls, _ = setup
    first = client.post('/api/searches', json=payload).json()
    client.post(f"/api/searches/{first['id']}/items/0/decision", json={'action': 'ignore'})
    second_payload = payload | {'submission_id': 'another-tab'}
    reused = client.post('/api/searches', json=second_payload).json()
    assert reused['id'] == first['id'] and reused['items'][0]['decision'] == 'ignored'
    assert reused['input']['submission_id'] == payload['submission_id']
    assert reused['execution']['cache']['timezone'] == 'America/New_York'
    assert calls['search'] == 1 and len(client.get('/api/searches').json()) == 1
    assert client.post('/api/searches', json=second_payload).json() == reused
    assert client.post('/api/searches', json=second_payload | {'query': 'different'}).status_code == 409


def test_cache_rollover_uses_new_york_day_and_explicit_refresh(setup, monkeypatch):
    from learning_library import searches
    client, payload, calls, _ = setup
    monkeypatch.setattr(searches, 'now', lambda: '2026-10-12T23:00:00+00:00')
    first = client.post('/api/searches', json=payload).json()
    # UTC midnight does not change the user's local day.
    monkeypatch.setattr(searches, 'now', lambda: '2026-10-13T02:00:00+00:00')
    assert client.post('/api/searches', json=payload | {'submission_id': 'same-day'}).json()['id'] == first['id']
    assert calls['search'] == 1
    forced = client.post('/api/searches', json=payload | {'submission_id': 'forced', 'refresh': True}).json()
    assert forced['id'] != first['id'] and calls['search'] == 2
    # New York has now crossed midnight. A new submission performs a new search.
    monkeypatch.setattr(searches, 'now', lambda: '2026-10-13T05:00:00+00:00')
    tomorrow = client.post('/api/searches', json=payload | {'submission_id': 'tomorrow'}).json()
    assert tomorrow['id'] != forced['id'] and calls['search'] == 3
    # Replaying the earlier submission still returns its original result across dates.
    assert client.post('/api/searches', json=payload | {'submission_id': 'same-day'}).json()['id'] == first['id']
    assert calls['search'] == 3


def test_changed_goal_provider_or_effort_requires_its_own_search(setup, monkeypatch):
    client, payload, calls, _ = setup
    monkeypatch.setenv('LIBRARY_CODEX_EFFORT', 'high')
    first = client.post('/api/searches', json=payload).json()
    assert client.post('/api/searches', json=payload | {'submission_id':'goal', 'objective':'另一个目标'}).json()['id'] != first['id']
    assert client.post('/api/searches', json=payload | {'submission_id':'provider', 'provider':'exa'}).json()['id'] != first['id']
    monkeypatch.setenv('LIBRARY_CODEX_EFFORT', 'medium')
    assert client.post('/api/searches', json=payload | {'submission_id':'effort'}).json()['id'] != first['id']
    assert calls['search'] == 4


def test_known_original_reuses_existing_candidate_and_keeps_first_discovery(setup):
    client, payload, calls, _ = setup
    first = client.post(
        "/api/candidates/process",
        json={
            "submission_id": "manual",
            "url": "https://example.com/practice.md",
            "goal": "Read this source",
            "origin": "user_link",
            "source_note": "First found manually",
            "as_of": datetime.now(UTC).date().isoformat(),
            "max_age_days": 30,
        },
    ).json()
    state = client.post("/api/searches", json=payload).json()
    assert state["items"][0]["already_saved"]
    assert state["items"][0]["candidate_id"] == first["id"]
    assert state["items"][0]["decision"] == "pending"
    path = f"/api/searches/{state['id']}/items/0/decision"
    client.post(path, json={"action": "save", "submission_id": "selected"})
    assert len(client.get("/api/candidates").json()) == 1
    assert client.get("/api/candidates/" + first["id"]).json()["input"] == first["input"]
    assert calls == {"search": 1, "fetch": 1, "model": 0}


def test_inflight_replay_does_not_duplicate_provider_call(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event

    entered, release = Event(), Event()
    calls = []

    def provider(query, objective):
        calls.append(query)
        entered.set()
        assert release.wait(5)
        return {
            "provider": "fixture",
            "observed_at": datetime.now(UTC).isoformat(),
            "status": "succeeded",
            "results": [],
            "provider_output": "No results found",
            "limitations": "Synthetic",
        }

    payload = {"submission_id": "concurrent", "query": "q", "objective": "o"}
    with (
        TestClient(create_app(tmp_path, search_provider=provider)) as client,
        ThreadPoolExecutor() as pool,
    ):
        running = pool.submit(client.post, "/api/searches", json=payload)
        try:
            assert entered.wait(5)
            replay = client.post("/api/searches", json=payload | {"submission_id": "parallel-tab"}).json()
            assert replay["status"] == "running"
            assert calls == ["q"]
        finally:
            release.set()
        done = running.result().json()
        assert done["id"] == replay["id"] and done["status"] == "succeeded"
        assert client.post('/api/searches', json=payload | {'submission_id':'parallel-tab'}).json()['id'] == done['id']
        assert len(client.get("/api/searches").json()) == 1
