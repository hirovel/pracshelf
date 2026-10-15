"""The public demo must not use the viewer's accounts or live network."""

from fastapi.testclient import TestClient

from examples.curation_demo import create_demo


def test_offline_demo_allows_notes_but_blocks_live_actions(tmp_path):
    with TestClient(create_demo(tmp_path)) as client:
        articles = client.get("/api/library").json()
        assert len(articles) == 3
        run = articles[0]["run_id"]
        base = f"/api/runs/{run}"
        document = client.get(base + "/artifacts/document").json()
        for path in (
            base + "/insights", base + "/translation/start", base + "/stages/fetch",
            "/api/searches", "/api/inbox/refresh", "/api/candidates/process",
            "/api/connection", "/api/model-configuration",
        ):
            blocked = client.post(path, json={})
            assert blocked.status_code == 403, (path, blocked.text)
            assert "离线演示" in blocked.json()["detail"]
        state = client.get(base + "/curation").json()
        saved = client.post(base + "/curation", json={
            "submission_id": "demo-note", "version_id": state["version_id"],
            "previous_id": state["latest"]["id"] if state["latest"] else None,
            "note": "Synthetic demo note. Not personal learning.",
        })
        assert saved.status_code == 200, saved.text
        assert saved.json()["latest"]["origin"] == "demo"
        exported = client.get(f"/api/library/{run}/note")
        assert exported.status_code == 200 and "Synthetic demo note" in exported.text
        assert "记录性质：演示记录" in exported.text
        assert client.get(base + "/artifacts/document").json() == document
        assert client.get(base + "/insights").json()["history"] == []
        assert client.get("/api/connection").json()["operation"]["status"] == "unchecked"
