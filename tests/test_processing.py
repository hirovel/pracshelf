"""Behavior at the HTTP interface; all generated outputs here are synthetic."""

from copy import deepcopy

import httpx
import pytest
from fastapi.testclient import TestClient

from learning_library.web import create_app

SOURCE = "# Fixture\n\nOnly under condition A might this help. [Docs](https://example.com/a) `--safe`\n\n```py\nprint(1)\n```\n\nNot guaranteed.\n"


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "synthetic-secret")
    monkeypatch.delenv("DEEPSEEK_MODEL", raising=False)

    def model(request):
        import json

        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {
                            "content": "合成译文："
                            + json.loads(request.content)["messages"][-1]["content"]
                        },
                    }
                ]
            },
        )

    with TestClient(
        create_app(
            tmp_path,
            httpx.MockTransport(
                lambda r: httpx.Response(
                    200, text=SOURCE, headers={"Content-Type": "text/markdown"}
                )
            ),
            translation_transport=httpx.MockTransport(model),
        )
    ) as value:
        yield value


def capture(client):
    run = client.post("/api/runs", json={"url": "https://example.com/material.md"}).json()
    base = f"/api/runs/{run['id']}"
    client.post(base + "/stages/fetch")
    client.post(base + "/stages/extract")
    return base


def result_input(client, base):
    task = client.get(base + "/processing/task").json()
    payload = task["result_template"]
    payload.update(submission_id="synthetic-1", origin="demo", executor="Synthetic test")
    # Intentionally NOT a translation: mechanical success must not imply faithful meaning.
    payload["segments"] = [{"index": p["index"], "text": p["source"]} for p in task["segments"]]
    return payload


def review(**changes):
    return dict(
        submission_id="review-1",
        previous_review_id=None,
        verdict="accepted",
        meaning="matched",
        conditions="matched",
        note="合成测试验收",
        **changes,
    )


def test_receive_is_idempotent_bound_and_never_auto_accepts(client):
    base = capture(client)
    original = client.get(base + "/artifacts/document").json()
    payload = result_input(client, base)
    received = client.post(base + "/processing/results", json=payload).json()
    result = received["results"][0]
    assert result["checks"]["status"] == "passed"
    assert result["reviews"] == []  # Same-language text can pass structure, never meaning.
    assert result["execution"]["call_count"] is None
    assert client.post(base + "/processing/results", json=payload).json() == received
    payload["segments"][0]["text"] += " Changed"
    assert client.post(base + "/processing/results", json=payload).status_code == 409
    other = capture(client)
    assert client.post(other + "/processing/results", json=payload).status_code == 409
    unchanged = client.get(base + "/artifacts/document").json()
    assert unchanged["document"] == original["document"]
    assert unchanged["content_hash"] == original["content_hash"]
    assert client.get(base + "/reviews").json()["latest"] is None
    assert client.get(base + "/translation").json()["attempts"] == []


@pytest.mark.parametrize(
    "failure,code",
    [
        ("missing", "missing"),
        ("duplicate", "duplicate"),
        ("reorder", "order"),
        ("code", "preserved"),
        ("link", "markdown_links"),
        ("empty", "empty"),
    ],
)
def test_broken_result_is_preserved_but_cannot_pass_human_acceptance(client, failure, code):
    base = capture(client)
    payload = result_input(client, base)
    if failure == "missing":
        payload["segments"].pop()
    elif failure == "duplicate":
        payload["segments"].append(deepcopy(payload["segments"][0]))
    elif failure == "reorder":
        payload["segments"].reverse()
    elif failure == "code":
        payload["segments"][1]["text"] = "changed code"
    elif failure == "link":
        payload["segments"][0]["text"] = payload["segments"][0]["text"].replace("/a)", "/a）")
    else:
        payload["segments"][0]["text"] = ""
    received = client.post(base + "/processing/results", json=payload).json()["results"][0]
    assert received["payload"] == payload
    assert code in {i["code"] for i in received["checks"]["issues"]}
    article = client.get("/api/library/" + base.rsplit("/", 1)[-1]).json()
    assert article["has_translation"] is False
    assert article["translation_html"] is None
    assert article["pairs"] == []
    assert article["translation_checks"] == received["checks"]
    assert "print(1)" in article["source_html"]
    assert (
        client.post(
            base + f"/processing/results/{received['id']}/reviews", json=review()
        ).status_code
        == 400
    )


def test_human_review_has_history_conflicts_and_survives_refresh(client):
    base = capture(client)
    result = client.post(base + "/processing/results", json=result_input(client, base)).json()[
        "results"
    ][0]
    path = base + f"/processing/results/{result['id']}/reviews"
    invalid = review()
    invalid["conditions"] = "unreviewed"
    assert client.post(path, json=invalid).status_code == 422
    first = client.post(path, json=review()).json()
    assert client.post(path, json=review()).json() == first
    stale = review()
    stale["submission_id"] = "review-2"
    assert client.post(path, json=stale).status_code == 409
    stale.update(
        previous_review_id=first["results"][0]["reviews"][0]["id"],
        verdict="needs_fix",
        note="合成测试改判",
    )
    assert client.post(path, json=stale).status_code == 200
    history = client.get(base + "/processing").json()["results"][0]["reviews"]
    assert [r["payload"]["verdict"] for r in history] == ["accepted", "needs_fix"]
    other = capture(client)
    assert (
        client.post(
            other + f"/processing/results/{result['id']}/reviews", json=review()
        ).status_code
        == 400
    )


def test_native_result_uses_observed_trace_and_does_not_call_model_again(client):
    base = capture(client)
    partial = client.post(base + "/translation/next").json()
    assert client.post(base + f"/processing/translations/{partial['id']}").status_code == 400
    done = client.post(base + "/translation/next").json()
    path = base + f"/processing/translations/{done['id']}"
    received = client.post(path).json()
    result = received["results"][0]
    assert result["execution"]["call_count"] == 2
    assert result["execution"]["kind"] == "observed"
    assert result["reviews"] == []
    assert "".join(p["text"] for p in result["payload"]["segments"]) == done["text"]
    assert client.post(path).json() == received
    assert client.get(base + "/translation").json() == done


def test_reader_uses_latest_copy_checks_and_keeps_failed_revisions(client):
    base = capture(client)
    reader = "/api/library/" + base.rsplit("/", 1)[-1]
    original = client.get(reader).json()["source_html"]
    good = result_input(client, base)
    client.post(base + "/processing/results", json=good)
    assert client.get(reader).json()["has_translation"] is True

    broken = deepcopy(good)
    broken["submission_id"] = "broken-revision"
    broken["segments"][1]["text"] = "changed code"
    client.post(base + "/processing/results", json=broken)
    # Do not silently fall back to an older copy after receiving a broken revision.
    assert client.get(reader).json()["has_translation"] is False

    good["submission_id"] = "corrected-revision"
    history = client.post(base + "/processing/results", json=good).json()["results"]
    article = client.get(reader).json()
    assert article["has_translation"] is True
    assert article["translation_checks"]["status"] == "passed"
    assert len(article["pairs"]) == len(good["segments"])
    assert article["source_html"] == original
    assert [r["checks"]["status"] for r in history] == ["passed", "issues", "passed"]
    assert all(r["reviews"] == [] for r in history)
    assert client.get(base + "/translation").json()["attempts"] == []


def test_external_cannot_label_itself_as_observed_or_replace_native(client):
    base = capture(client)
    payload = result_input(client, base)
    payload["origin"] = "model_api"
    assert client.post(base + "/processing/results", json=payload).status_code == 422
    payload["origin"] = "external_ai"
    payload["submission_id"] = "native-1"
    assert client.post(base + "/processing/results", json=payload).status_code == 400
