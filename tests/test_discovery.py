from datetime import date

import httpx
import pytest
from fastapi.testclient import TestClient

from learning_library.discovery import Discovery
from learning_library.pipeline import Pipeline
from learning_library.storage import Store
from learning_library.web import create_app


@pytest.fixture
def selection(tmp_path):
    store = Store(tmp_path)
    sentence = "I retained intermediate results outside the model context."
    html = (
        '<html><head><title>Synthetic article</title><meta property="article:published_time" content="2026-10-06"></head><body><article><p>'
        + sentence * 10
        + "</p></article></body></html>"
    )
    with httpx.Client(
        transport=httpx.MockTransport(lambda req: httpx.Response(200, text=html))
    ) as client:
        flow = Pipeline(store, client)
        pipeline = flow.create("https://example.com/article")
        flow.run(pipeline["id"], "fetch")
        flow.run(pipeline["id"], "extract")
        version = store.documents()[0]["version_id"]
        payload = {
            "goal": "Learn practical agent workflows",
            "as_of": "2026-10-08",
            "max_age_days": 90,
            "origin": "demo",
            "model_label": "synthetic fixture",
            "search_strategy": "synthetic plan",
            "executed_queries": ["synthetic query"],
            "search_log_limitations": "No actual search.",
            "candidates": [
                {
                    "pipeline_id": pipeline["id"],
                    "version_id": version,
                    "verdict": "candidate",
                    "reason": "Concrete mechanism",
                    "learning_value": "Context management",
                    "quotes": ["I retained intermediate results outside the model context."],
                    "applicability_note": "Not independently validated",
                }
            ],
        }
        yield flow, payload
    store.close()


def test_recent_but_irrelevant_is_rejected(selection):
    flow, payload = selection
    payload["candidates"][0]["verdict"] = "reject"
    payload["candidates"][0]["reason"] = "Not useful for this learning purpose"
    output = Discovery(flow.store).accept(payload)["candidates"][0]
    assert output["date_status"] == "within_window"
    assert output["decision"] == "rejected"
    state = flow.inspect(payload["candidates"][0]["pipeline_id"])
    assert [s["id"] for s in state["stages"]] == ["fetch", "extract", "validate"]
    archived = Discovery(flow.store).for_pipeline(state["id"])
    assert archived["decision"] == "rejected"
    assert archived["goal"] == payload["goal"]


def test_value_candidate_is_never_effectiveness_certification(selection):
    flow, payload = selection
    output = Discovery(flow.store).accept(payload)["candidates"][0]
    assert output["decision"] == "candidate"
    assert output["current_applicability"] == "unverified"
    assert output["effectiveness"] == "unverified"
    assert output["recommendation_eligible"] is False


@pytest.mark.parametrize("as_of", ["2027-10-08", "2026-01-01"])
def test_old_or_future_dated_source_is_held(selection, as_of):
    flow, payload = selection
    payload["as_of"] = as_of
    output = Discovery(flow.store).accept(payload)["candidates"][0]
    assert output["decision"] == "needs_more_evidence"
    assert output["date_status"] == "outside_window"


def test_invented_evidence_and_wrong_document_binding_cannot_pass(selection):
    flow, payload = selection
    payload["candidates"][0]["quotes"] = ["Invented supporting statement"]
    assert Discovery(flow.store).accept(payload)["candidates"][0]["decision"] == "needs_more_evidence"
    payload["candidates"][0]["version_id"] += 100
    with pytest.raises(ValueError, match="document version"):
        Discovery(flow.store).accept(payload)


def test_search_preparation_is_explicit_about_external_execution(tmp_path):
    prepared = Discovery.prepare("Learn skill composition", date(2026, 10, 8))
    assert prepared["status"] == "awaiting_external_planner"
    with TestClient(create_app(tmp_path)) as client:
        result = client.post(
            "/api/discovery/prepare",
            json={"goal": "Learn skill composition", "as_of": "2026-10-08"},
        )
        assert result.status_code == 200
        assert result.json()["goal"] in result.json()["prompt"]
        assert client.get("/api/discovery").json() == []
