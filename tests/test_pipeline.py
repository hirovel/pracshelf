import httpx
from fastapi.testclient import TestClient

from learning_library.collector import Collector
from learning_library.storage import Store
from learning_library.web import create_app

HTML = (
    "<html><head><title>Current practice</title></head><body><article><h1>Current practice</h1><p>"
    + (
        "A synthetic article describing a complete development workflow with its testing conditions. "
        * 15
    )
    + "</p></article></body></html>"
)


def test_stages_are_independent_and_extraction_never_refetches(tmp_path):
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(200, text=HTML, headers={"Content-Type": "text/html"})

    with TestClient(create_app(tmp_path, httpx.MockTransport(handler))) as client:
        run = client.post("/api/runs", json={"url": "https://example.com/article"}).json()
        base = f"/api/runs/{run['id']}"
        assert client.post(base + "/stages/extract").status_code == 400
        assert client.post(base + "/stages/fetch").json()["stages"][0]["status"] == "succeeded"
        raw = client.get(base + "/artifacts/raw")
        assert raw.text == HTML
        assert raw.headers["content-type"].startswith("text/plain")
        assert client.post(base + "/stages/extract").json()["stages"][1]["status"] == "succeeded"
        assert len(requests) == 1
        assert "synthetic article" in client.get(base + "/artifacts/text").text
        final = client.post(base + "/stages/validate").json()
        assert final["stages"][2]["status"] == "needs_review"
        assert [s["id"] for s in final["stages"]] == ["fetch", "extract", "validate"]
        assert client.get(base + "/stages/analyze").status_code == 404
        assert client.post(base + "/stages/analyze").status_code == 400
        assert client.get(base + "/stages/extract").json()["attempts"][0]["input"]["request_id"]
        assert client.post(base + "/stages/fetch").status_code == 400


def test_failed_fetch_can_retry_with_history_and_raw_error_preserved(tmp_path):
    responses = iter(
        [httpx.Response(503, text="temporary upstream error"), httpx.Response(200, text=HTML)]
    )
    with TestClient(
        create_app(tmp_path, httpx.MockTransport(lambda req: next(responses)))
    ) as client:
        run = client.post("/api/runs", json={"url": "https://example.com/article"}).json()
        base = f"/api/runs/{run['id']}"
        first = client.post(base + "/stages/fetch").json()
        assert first["stages"][0]["status"] == "failed"
        assert first["stages"][1]["status"] == "blocked"
        assert client.get(base + "/artifacts/raw").text == "temporary upstream error"
        second = client.post(base + "/stages/fetch").json()
        assert [a["status"] for a in second["stages"][0]["attempts"]] == ["failed", "succeeded"]


def test_extraction_failure_keeps_fetch_success(tmp_path):
    with TestClient(
        create_app(
            tmp_path,
            httpx.MockTransport(lambda req: httpx.Response(200, text="<html>Login</html>")),
        )
    ) as client:
        run = client.post("/api/runs", json={"url": "https://example.com/article"}).json()
        base = f"/api/runs/{run['id']}"
        client.post(base + "/stages/fetch")
        state = client.post(base + "/stages/extract").json()
        assert state["stages"][0]["status"] == "succeeded"
        assert state["stages"][1]["status"] == "failed"
        assert state["stages"][2]["status"] == "blocked"
        assert "Login" in client.get(base + "/artifacts/raw").text


def test_tampered_snapshot_is_rejected_offline(tmp_path):
    store = Store(tmp_path)
    try:
        with httpx.Client(
            transport=httpx.MockTransport(lambda req: httpx.Response(200, text=HTML))
        ) as client:
            collector = Collector(store, client)
            fetch = collector.collect("fetch", "https://example.com/article")
            snapshot = store.captured_request(fetch["request_id"])["snapshot"]
            (tmp_path / snapshot).write_bytes(b"tampered")
            result = collector.collect("extract", str(fetch["request_id"]))
            assert result["status"] == "failed"
            assert "hash mismatch" in result["error"]
    finally:
        store.close()


def test_new_workbench_does_not_import_old_documents_as_pipeline_runs(tmp_path):
    with TestClient(create_app(tmp_path)) as client:
        assert client.get("/api/runs").json() == []
        assert "获取" not in client.get("/api/runs/missing").text
        assert client.get("/api/runs/missing").status_code == 400
        assert client.get("/").status_code == 200
        assert client.get("/openapi.json").status_code == 200


def test_reextraction_reuses_snapshot_keeps_history_and_deduplicates_version(tmp_path):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(200, text=HTML)

    with TestClient(create_app(tmp_path, httpx.MockTransport(handler))) as client:
        old = client.post("/api/runs", json={"url": "https://example.com/article"}).json()
        base = f"/api/runs/{old['id']}"
        assert client.post(base + "/reextract").status_code == 400
        client.post(base + "/stages/fetch")
        original = client.post(base + "/stages/extract").json()
        old_version = original["stages"][1]["attempts"][-1]["output"]["version_ids"]
        new = client.post(base + "/reextract").json()
        assert new["id"] != old["id"]
        assert new["stages"][0]["attempts"][0]["input"]["reused_from_pipeline_id"] == old["id"]
        extracted = client.post(f"/api/runs/{new['id']}/stages/extract").json()
        output = extracted["stages"][1]["attempts"][-1]["output"]
        assert output["version_ids"] == old_version
        assert output["new_versions"] == 0
        assert len(calls) == 1
        assert client.get(base).json() == original
