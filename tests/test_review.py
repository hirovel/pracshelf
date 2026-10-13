"""Behavior tests for version-pinned human review using synthetic originals."""

from uuid import uuid4

import httpx
import pytest
from fastapi.testclient import TestClient

from learning_library.web import create_app

HTML = (
    "<html><title>Synthetic original</title><article><p>"
    + "Synthetic evidence for review. " * 20
    + "</p></article></html>"
)


@pytest.fixture
def app(tmp_path):
    return create_app(tmp_path, httpx.MockTransport(lambda request: httpx.Response(200, text=HTML)))


def capture(client):
    run = client.post("/api/runs", json={"url": "https://example.com/synthetic"}).json()
    base = f"/api/runs/{run['id']}"
    client.post(base + "/stages/fetch")
    client.post(base + "/stages/extract")
    client.post(base + "/stages/validate")
    return base


def accepted(state, **changes):
    return {
        "submission_id": uuid4().hex,
        "version_id": state["version_id"],
        "extract_attempt_id": state["extract_attempt_id"],
        "previous_review_id": state["latest"]["id"] if state["latest"] else None,
        "verdict": "accepted",
        "body": "matched",
        "structure": "not_applicable",
        "date_status": "unknown",
        "note": "Synthetic test judgment",
        **changes,
    }


def test_automatic_checks_do_not_create_acceptance_and_unchecked_cannot_pass(app):
    with TestClient(app) as client:
        base = capture(client)
        state = client.get(base + "/reviews").json()
        assert state["latest"] is None and state["history"] == []
        assert (
            client.post(base + "/reviews", json=accepted(state, body="unreviewed")).status_code
            == 422
        )
        assert (
            client.post(
                base + "/reviews", json=accepted(state, date_status="unreviewed")
            ).status_code
            == 422
        )
        assert client.get(base + "/reviews").json()["history"] == []
        missing = client.post("/api/runs", json={"url": "https://example.com/new"}).json()
        assert (
            client.post(f"/api/runs/{missing['id']}/reviews", json=accepted(state)).status_code
            == 400
        )


def test_acceptance_is_durable_and_does_not_overwrite_program_checks_or_document(app):
    with TestClient(app) as client:
        base = capture(client)
        before = client.get(base).json()["stages"]
        document = client.get(base + "/artifacts/document").json()
        state = client.get(base + "/reviews").json()
        saved = client.post(base + "/reviews", json=accepted(state)).json()
        assert saved["latest"]["origin"] == "human"
        assert saved["latest"]["payload"]["date_status"] == "unknown"
        assert client.get(base).json()["stages"] == before
        assert client.get(base + "/artifacts/document").json() == document
    with TestClient(app) as client:
        assert client.get(base + "/reviews").json() == saved


def test_revisions_append_retry_is_idempotent_and_stale_forms_are_rejected(app):
    with TestClient(app) as client:
        base = capture(client)
        state = client.get(base + "/reviews").json()
        first_payload = accepted(state)
        first = client.post(base + "/reviews", json=first_payload).json()
        assert client.post(base + "/reviews", json=first_payload).json() == first
        assert client.post(base + "/reviews", json=accepted(state)).status_code == 409
        second = client.post(
            base + "/reviews",
            json=accepted(
                first, verdict="needs_fix", body="issues", note="Missing final paragraph"
            ),
        ).json()
        assert len(second["history"]) == 2
        assert second["history"][0] == first["latest"]
        assert second["latest"]["payload"]["verdict"] == "needs_fix"
        assert client.post(base + "/reviews", json=first_payload).json() == second
        assert (
            client.post(
                base + "/reviews", json=first_payload | {"note": "Changed retry"}
            ).status_code
            == 409
        )


def test_wrong_version_bad_date_and_forged_origin_cannot_be_saved(app):
    with TestClient(app) as client:
        base = capture(client)
        state = client.get(base + "/reviews").json()
        assert (
            client.post(base + "/reviews", json=accepted(state, version_id=999)).status_code == 409
        )
        assert (
            client.post(base + "/reviews", json=accepted(state, extract_attempt_id=999)).status_code
            == 409
        )
        assert (
            client.post(base + "/reviews", json=accepted(state, origin="human")).status_code == 422
        )
        assert (
            client.post(
                base + "/reviews",
                json=accepted(state, date_status="confirmed", confirmed_date="2026-10-09"),
            ).status_code
            == 422
        )
        assert (
            client.post(
                base + "/reviews", json=accepted(state, verdict="pending", note=" ")
            ).status_code
            == 422
        )
        payload = accepted(
            state,
            date_status="confirmed",
            confirmed_date="2026-10-09",
            date_evidence="Synthetic byline date",
        )
        saved = client.post(base + "/reviews", json=payload).json()
        assert saved["latest"]["payload"]["confirmed_date"] == "2026-10-09"
        assert client.get(base + "/artifacts/document").json()["document"]["published"] is None


def test_reextraction_does_not_inherit_acceptance_even_when_version_deduplicates(app):
    with TestClient(app) as client:
        base = capture(client)
        old = client.post(
            base + "/reviews", json=accepted(client.get(base + "/reviews").json())
        ).json()
        new = client.post(base + "/reextract").json()
        new_base = f"/api/runs/{new['id']}"
        client.post(new_base + "/stages/extract")
        state = client.get(new_base + "/reviews").json()
        assert state["version_id"] == old["version_id"]
        assert state["latest"] is None and state["history"] == []
        assert state["extract_attempt_id"] != old["extract_attempt_id"]
        assert client.get(base + "/reviews").json() == old


def test_demo_records_are_server_labeled_and_isolated(tmp_path):
    transport = httpx.MockTransport(lambda request: httpx.Response(200, text=HTML))
    with TestClient(create_app(tmp_path / "demo", transport, review_origin="demo")) as client:
        base = capture(client)
        state = client.get(base + "/reviews").json()
        saved = client.post(base + "/reviews", json=accepted(state)).json()
        assert saved["latest"]["origin"] == "demo"
    with TestClient(create_app(tmp_path / "real", transport)) as client:
        assert client.get("/api/runs").json() == []
