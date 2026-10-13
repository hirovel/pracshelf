import copy

import httpx
import pytest
from fastapi.testclient import TestClient

from learning_library.comparison import Comparison
from learning_library.pipeline import Pipeline
from learning_library.storage import Store
from learning_library.web import create_app


@pytest.fixture
def reviewed(tmp_path):
    store = Store(tmp_path)
    with httpx.Client(
        transport=httpx.MockTransport(
            lambda _: httpx.Response(
                200,
                text="<html><title>Test original</title><article>"
                + "<p>Keep evidence and verify outcomes before reusing context.</p>" * 20
                + "</article></html>",
            )
        )
    ) as client:
        flow = Pipeline(store, client)
        state = flow.create("https://example.com/original")
        flow.run(state["id"], "fetch")
        state = flow.run(state["id"], "extract")
        version = state["stages"][1]["attempts"][-1]["output"]["version_ids"][0]
        source = {
            "id": "source",
            "method": "codex_curated",
            "pipeline_id": state["id"],
            "version_id": version,
            "title": "Test",
            "url": "https://example.com/original",
            "verdict": "shortlist",
            "depth": "body_read",
            "reason": "Synthetic test judgment",
            "caveat": "Not real data",
            "checked_date": "2026-09-12",
            "date_basis": "Test fixture",
            "quotes": ["Keep evidence and verify outcomes before reusing context."],
        }
        payload = {
            "origin": "external_model",
            "as_of": "2026-10-08",
            "selected_method": "codex_curated",
            "methods": [{"id": "codex_curated", "status": "active"}],
            "sources": [source],
        }
        yield Comparison(store), payload, tmp_path
    store.close()


def test_evidence_and_capture_binding_are_required(reviewed):
    comparison, payload, _ = reviewed
    for key, value in [
        ("quotes", ["Invented evidence"]),
        ("version_id", 999),
        ("url", "https://example.com/other"),
        ("depth", "skim"),
        ("checked_date", "2026-11-01"),
    ]:
        bad = copy.deepcopy(payload)
        bad["sources"][0][key] = value
        with pytest.raises(ValueError):
            comparison.publish(bad)
    assert comparison.get() is None


def test_snapshot_history_and_human_feedback_stay_separate(reviewed):
    comparison, payload, _ = reviewed
    first = comparison.publish(payload)
    assert first["human_feedback"] == []
    assert first["methods"][0]["new_shortlisted"] == 1
    comparison.feedback(first["id"], "source", {"verdict": "useful", "note": "Human fixture"})
    result = comparison.feedback(
        first["id"], "source", {"verdict": "not_useful", "note": "Changed mind"}
    )
    assert len(result["human_feedback"]) == 2
    assert result["sources"][0]["verdict"] == "shortlist"
    second = comparison.publish(payload)
    assert second["id"] != first["id"]
    assert second["human_feedback"] == []
    assert len(comparison.get(first["id"])["human_feedback"]) == 2
    with pytest.raises(ValueError, match="human feedback"):
        comparison.publish(payload | {"human_feedback": [{"verdict": "useful"}]})
    with pytest.raises(ValueError, match="Unknown"):
        comparison.feedback(first["id"], "fake", {"verdict": "useful"})


def test_archived_search_cannot_run_but_report_and_feedback_remain_available(reviewed):
    comparison, payload, path = reviewed
    payload["methods"].append({"id": "exa_fixed", "status": "archived", "experiment_id": "old"})
    report = comparison.publish(payload)
    with TestClient(create_app(path)) as client:
        assert client.get("/api/comparisons/latest").json()["id"] == report["id"]
        assert client.post("/api/experiments/old/search-next").status_code == 409
        assert client.post("/api/experiments/old/capture-next").status_code == 409
        response = client.post(
            f"/api/comparisons/{report['id']}/feedback/source",
            json={"verdict": "useful", "note": "Human fixture"},
        )
        assert response.status_code == 200
        assert response.json()["human_feedback"][0]["output"]["note"] == "Human fixture"
        assert client.get(f"/api/comparisons/{report['id']}/methods/unknown").status_code == 404
