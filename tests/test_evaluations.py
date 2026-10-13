"""HTTP behavior on explicitly synthetic documents; no external model or labels."""

import json
import sqlite3
from contextlib import closing
from datetime import UTC, datetime

import httpx
import pytest
from fastapi.testclient import TestClient

from learning_library.storage import Store
from learning_library.web import create_app

QUOTE = "Keep original evidence and check conditions before applying a technique."


@pytest.fixture
def setup(tmp_path, monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-secret")
    calls = {"fetch": 0, "model": 0}

    def fetch(request):
        calls["fetch"] += 1
        return httpx.Response(
            200, text=f"# Synthetic {request.url.path}\n\n{QUOTE}\n\n" + "Example and limitations. " * 12,
            headers={"Content-Type": "text/markdown"},
        )

    def respond(request):
        calls["model"] += 1
        goal = json.loads(json.loads(request.content)["messages"][1]["content"])["goal"]
        if goal == "fail":
            return httpx.Response(503, json={"error": {"message": "synthetic failure"}})
        judgment = {
            "category": "workflow", "recommendation": goal.split("-")[0],
            "learning_value": "合成测试资料", "reason": "只用于验证评测行为。",
            "evidence": [{"quote": QUOTE, "supports": "保留证据与条件"}],
            "conditions": ["测试环境"], "time_dependencies": [], "unknowns": ["真实质量未验证"],
        }
        return httpx.Response(200, json={
            "id": "fixture", "model": "fixture-model",
            "usage": {"prompt_tokens": 50, "completion_tokens": 27, "total_tokens": 77},
            "choices": [{"finish_reason": "stop", "message": {
                "role": "assistant", "content": json.dumps(judgment),
            }}],
        })

    with TestClient(create_app(
        tmp_path, httpx.MockTransport(fetch), translation_transport=httpx.MockTransport(respond),
    )) as client:
        def freeze(goal="read", split="development", origin="user_link"):
            candidate = client.post("/api/candidates/process", json={
                "submission_id": "import-" + goal, "url": f"https://example.com/{goal}.md",
                "goal": goal, "origin": origin, "source_note": "Synthetic test material",
                "as_of": datetime.now(UTC).date().isoformat(), "max_age_days": 90,
            }).json()
            state = client.post(f"/api/candidates/{candidate['id']}/review", json={
                "submission_id": "screen-" + goal,
            }).json()
            attempt = state["preview"]["latest"]
            payload = {"screening_id": attempt["id"], "split": split}
            response = client.post("/api/evaluations", json=payload)
            assert response.status_code == 200, response.text
            return response.json(), candidate, payload

        yield client, freeze, calls, tmp_path


def label(reading="read", *, previous_id=None, submission_id="label-1", **extra):
    return {"submission_id": submission_id, "previous_id": previous_id,
            "reading": reading, "fidelity": "passed", "conditions": "passed",
            "reason": "合成测试标签，只验证计算，不代表用户意见。", **extra}


def test_snapshot_is_immutable_idempotent_and_independent_of_current_source(setup):
    client, freeze, calls, root = setup
    case, candidate, payload = freeze()
    snapshot = case["snapshot"]
    assert snapshot["source"]["content_hash"] == snapshot["attempt"]["input"]["content_hash"]
    assert QUOTE in snapshot["source"]["document"]["text"]
    assert snapshot["attempt"]["response"]["request_count"] == 1
    assert client.post("/api/evaluations", json=payload).json() == case
    assert client.post("/api/evaluations", json=payload | {"split": "test"}).status_code == 409
    with closing(Store(root)) as store:
        old = snapshot["source"]
        store.save(old["document_key"], old["document"] | {"text": "Changed source"}, None)
        new_id = store.documents()[0]["version_id"]
        with store.db:
            store.db.execute("UPDATE candidates SET version_id=? WHERE id=?", (new_id, candidate["id"]))
    assert client.get(f"/api/evaluations/{case['id']}").json()["snapshot"] == snapshot
    assert client.get("/api/evaluations/available").json()[0]["version_id"] == old["id"]
    report = client.get("/api/evaluations?split=development").json()
    assert report["counts"]["unlabeled"] == 1
    assert report["counts"]["fidelity_unknown"] == report["counts"]["conditions_unknown"] == 1
    assert all(value is None for value in report["metrics"].values())
    assert report["usage"]["known_total_tokens"] == 77
    assert calls == {"fetch": 1, "model": 1}
    newer = client.post(f"/api/candidates/{candidate['id']}/review", json={
        "submission_id": "review-new-source",
    }).json()["preview"]["latest"]
    assert newer["input"]["version_id"] == new_id
    assert client.post("/api/evaluations", json={
        "screening_id": newer["id"], "split": "test",
    }).status_code == 409
    assert client.get("/api/evaluations?split=test").json()["items"] == []


def test_human_labels_are_append_only_with_retry_and_conflict_protection(setup):
    client, freeze, calls, root = setup
    case, _, _ = freeze()
    path = f"/api/evaluations/{case['id']}/labels"
    first = client.post(path, json=label()).json()
    assert first["latest"]["origin"] == "human"
    assert client.post(path, json=label()).json() == first
    assert client.post(path, json=label("skip")).status_code == 409
    assert client.post(path, json=label("skip", submission_id="label-2")).status_code == 409
    second = client.post(path, json=label("skip", previous_id=first["latest"]["id"], submission_id="label-2")).json()
    assert len(second["history"]) == 2
    assert second["history"][0]["payload"]["reading"] == "read"
    assert second["latest"]["payload"]["reading"] == "skip"
    assert client.post(path, json=label(reason=" ", submission_id="bad")).status_code == 422
    report = client.get("/api/evaluations?split=development").json()
    assert report["counts"]["false_positive"] == 1
    assert report["metrics"]["recommendation_precision"] == 0
    with sqlite3.connect(root / "library.sqlite3") as db:
        tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        for table in ("curation_records", "original_reviews"):
            if table in tables:
                assert db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0
    assert calls == {"fetch": 1, "model": 1}


def test_metrics_separate_abstention_missing_labels_demo_and_holdout(setup):
    client, freeze, calls, _ = setup
    rows = [("read", "read"), ("focus", "skip"), ("skip", "read"), ("uncertain", "read")]
    for goal, reading in rows:
        case, _, _ = freeze(goal)
        extra = {"fidelity": "failed", "conditions": "failed"} if goal == "focus" else {}
        assert client.post(f"/api/evaluations/{case['id']}/labels", json=label(reading, **extra)).status_code == 200
    freeze("read-unlabeled")
    freeze("read-demo", origin="demo")
    freeze("read-holdout", split="test")
    report = client.get("/api/evaluations?split=development").json()
    counts = report["counts"]
    assert {key: counts[key] for key in ("included", "excluded_demo", "labeled", "unlabeled")} == {
        "included": 5, "excluded_demo": 1, "labeled": 4, "unlabeled": 1,
    }
    assert counts["true_positive"] == counts["false_positive"] == counts["false_negative"] == counts["abstained_positive"] == 1
    assert counts["fidelity_failed"] == counts["conditions_failed"] == 1
    assert counts["model_abstentions"] == 1
    assert report["metrics"] == {
        "recommendation_precision": .5, "fixed_set_recall": 1 / 3, "decision_coverage": .75,
    }
    assert report["usage"]["application_requests"] == 5
    assert report["usage"]["known_total_tokens"] == 5 * 77
    heldout = client.get("/api/evaluations?split=test").json()
    assert heldout["counts"]["included"] == heldout["counts"]["unlabeled"] == 1
    assert heldout["metrics"]["recommendation_precision"] is None
    assert calls == {"fetch": 7, "model": 7}


def test_failures_unknown_usage_and_missing_data_do_not_become_good_scores(setup):
    client, freeze, calls, _ = setup
    empty = client.get("/api/evaluations").json()
    assert all(value is None for value in empty["metrics"].values())
    case, _, _ = freeze("fail")
    assert case["snapshot"]["attempt"]["status"] == "failed"
    report = client.get("/api/evaluations?split=development").json()
    assert report["counts"]["failed_attempts"] == 1
    assert report["usage"]["unknown_usage_cases"] == 1
    assert report["usage"]["application_requests"] == 1
    assert client.post(f"/api/evaluations/{case['id']}/labels", json=label("uncertain", fidelity="unknown", conditions="unknown")).status_code == 200
    report = client.get("/api/evaluations?split=development").json()
    assert report["counts"]["gold_uncertain"] == 1
    assert all(value is None for value in report["metrics"].values())
    assert client.post("/api/evaluations", json={"screening_id": 9999, "split": "test"}).status_code == 400
    assert client.get("/api/evaluations?split=all").status_code == 400
    assert calls == {"fetch": 1, "model": 1}
