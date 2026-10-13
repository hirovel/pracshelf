from datetime import UTC, datetime

import httpx
import pytest
from fastapi.testclient import TestClient

from learning_library.experiment import DiscoveryExperiment
from learning_library.pipeline import Pipeline
from learning_library.storage import Store
from learning_library.web import create_app


def plan(**changes):
    return {
        "goal": "Synthetic context learning test",
        "as_of": "2026-10-08",
        "facets": ["practices", "failures"],
        "queries": ["query one", "query two"],
        "origin": "test_fixture",
        "planner": "test fixture",
        **changes,
    }


def observation(urls=(), **changes):
    return {
        "provider": "synthetic test only",
        "observed_at": datetime.now(UTC).isoformat(),
        "status": "succeeded",
        "results": [{"url": u, "title": u} for u in urls],
        "provider_output": "  Test tool output: " + ",".join(urls) + "\n",
        "limitations": "Synthetic. No actual search executed.",
        **changes,
    }


@pytest.fixture
def experiment(tmp_path):
    store = Store(tmp_path)
    calls = []

    def respond(request):
        calls.append(str(request.url))
        if request.url.path == "/fail":
            return httpx.Response(403)
        return httpx.Response(
            200,
            text=(
                "<html><head><title>Synthetic original</title></head><body><article><p>"
                + "Concrete synthetic evidence for context practices. " * 30
                + "</p></article></body></html>"
            ),
        )

    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        yield DiscoveryExperiment(Pipeline(store, client)), calls
    store.close()


def complete_search(exp, run_id, urls=(), **changes):
    task = exp.next_search(run_id)
    return exp.import_search(run_id, task["id"], observation(urls, **changes))


def test_automatic_search_reserves_before_network_and_rejects_import(experiment):
    exp, _ = experiment
    run_id = exp.create(plan(search_provider="exa_mcp", queries=["one"]))["id"]
    assert exp.inspect(run_id)["search_api_called_by_application"] is False
    with pytest.raises(ValueError, match="provider"):
        exp.next_search(run_id)
    calls = []

    def search(query, objective):
        calls.append(query)
        state = exp.inspect(run_id)
        assert state["status"] == "searching"
        assert state["budget"]["issued_search_tasks"] == 1
        with pytest.raises(ValueError, match="already running"):
            exp.search_next(run_id, search)
        with pytest.raises(ValueError, match="external import"):
            exp.import_search(run_id, state["events"][0]["id"], observation())
        return observation(["https://example.com/original"])

    state = exp.search_next(run_id, search)
    assert state["status"] == "awaiting_capture"
    assert state["search_api_called_by_application"] is True
    assert state["application_search_budget_enforced"] is True
    assert state["events"][0]["finished"]
    with pytest.raises(ValueError, match="frozen queries"):
        exp.search_next(run_id, search)
    assert calls == ["one"]


def test_automatic_failure_spends_budget_without_retry_or_provider_switch(experiment):
    exp, _ = experiment
    calls = []

    def broken(query, objective):
        calls.append(query)
        raise TimeoutError()

    external = exp.create(plan())["id"]
    with pytest.raises(ValueError, match="provider"):
        exp.search_next(external, broken)
    run_id = exp.create(plan(search_provider="exa_mcp", queries=["one"]))["id"]
    state = exp.search_next(run_id, broken)
    assert state["status"] == "no_candidates"
    assert state["events"][0]["status"] == "failed"
    assert "TimeoutError" in state["events"][0]["output"]["limitations"]
    assert state["budget"]["issued_search_tasks"] == 1
    assert calls == ["one"]


def test_exa_endpoint_and_raw_observability(tmp_path, monkeypatch):
    from learning_library import web

    calls = []

    def search(query, objective):
        calls.append((query, objective))
        return observation(["https://example.com/original"])

    monkeypatch.setattr(web, "exa_search", search)
    with TestClient(create_app(tmp_path)) as client:
        run = client.post(
            "/api/experiments", json=plan(search_provider="exa_mcp", queries=["one"])
        ).json()
        response = client.post(f"/api/experiments/{run['id']}/search-next")
        assert response.status_code == 200
        event = response.json()["events"][0]
        assert event["elapsed_seconds"] >= 0
        assert "provider_output" in event["output"]
        assert (
            "provider_output" not in client.get("/api/experiments").json()[0]["events"][0]["output"]
        )
        assert client.post(f"/api/experiments/{run['id']}/search-next").status_code == 400
        assert len(calls) == 1


def test_frozen_plan_reservations_are_idempotent_and_import_is_immutable(experiment):
    exp, calls = experiment
    run_id = exp.create(plan())["id"]
    first = exp.next_search(run_id)
    assert exp.next_search(run_id)["id"] == first["id"]
    payload = observation(["https://example.com/a"])
    state = exp.import_search(run_id, first["id"], payload)
    assert state["events"][0]["output"]["provider_output"] == payload["provider_output"]
    with pytest.raises(ValueError, match="immutable"):
        exp.import_search(run_id, first["id"], payload)
    complete_search(exp, run_id, status="failed", provider_output="Synthetic timeout")
    assert exp.inspect(run_id)["budget"]["issued_search_tasks"] == 2
    with pytest.raises(ValueError, match="frozen queries"):
        exp.next_search(run_id)
    assert calls == []


def test_dedup_provenance_rank_order_and_capture_budget(experiment):
    exp, calls = experiment
    run_id = exp.create(plan(capture_limit=1))["id"]
    complete_search(exp, run_id, ["https://example.com/a", "https://example.com/b"])
    complete_search(exp, run_id, ["https://example.com/b#section"])
    state = exp.inspect(run_id)
    assert len(state["candidates"]) == 2
    assert len(state["candidates"][1]["discovered_by"]) == 2
    state = exp.capture_next(run_id)
    assert state["status"] == "awaiting_selection_review"
    captured = state["candidates"][0]["capture"]
    assert captured["status"] == "succeeded"
    assert captured["output"]["pipeline"]["stages"][1]["attempts"][0]["output"]["version_ids"]
    with pytest.raises(ValueError, match="budget"):
        exp.capture_next(run_id)
    assert calls == ["https://example.com/a"]


def test_empty_and_failed_search_are_not_successful_recommendations(experiment):
    exp, calls = experiment
    run_id = exp.create(plan())["id"]
    complete_search(exp, run_id)
    complete_search(exp, run_id, status="failed", provider_output="synthetic failure")
    state = exp.inspect(run_id)
    assert state["status"] == "no_candidates"
    assert state["cost_usd"] is None
    assert not state["external_budget_enforced"]
    assert not state["search_api_called_by_application"]
    assert not state["model_api_called_by_application"]
    assert calls == []


def test_capture_failure_keeps_attempt_and_budget(experiment):
    exp, calls = experiment
    run_id = exp.create(plan(queries=["single"], capture_limit=1))["id"]
    complete_search(exp, run_id, ["https://example.com/fail"])
    state = exp.capture_next(run_id)
    assert state["budget"]["capture_attempts"] == 1
    assert state["events"][-1]["status"] == "failed"
    assert state["candidates"][0]["capture"]["output"]["pipeline_id"]
    assert calls == ["https://example.com/fail"]


def test_wrong_run_and_fabricated_result_cannot_be_imported(experiment):
    exp, _ = experiment
    run_id = exp.create(plan())["id"]
    task = exp.next_search(run_id)
    other = exp.create(plan())["id"]
    with pytest.raises(ValueError, match="unknown"):
        exp.import_search(other, task["id"], observation())
    with pytest.raises(ValueError, match="occur"):
        exp.import_search(
            run_id,
            task["id"],
            observation(["https://example.com/not-returned"], provider_output="unrelated output"),
        )
    with pytest.raises(ValueError, match="limit"):
        exp.create(plan(search_limit=1))


def test_http_interface_exposes_frozen_plan_and_real_execution_boundary(tmp_path):
    with TestClient(create_app(tmp_path)) as client:
        state = client.post("/api/experiments", json=plan()).json()
        run_id = state["id"]
        task = client.post(f"/api/experiments/{run_id}/next-search").json()
        result = client.post(f"/api/experiments/{run_id}/searches/{task['id']}", json=observation())
        assert result.status_code == 200
        assert client.get(f"/api/experiments/{run_id}").json()["events"][0]["output"]
        assert (
            "provider_output" not in client.get("/api/experiments").json()[0]["events"][0]["output"]
        )
