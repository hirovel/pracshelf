import re

import httpx
from fastapi.testclient import TestClient

from learning_library.library import render_markdown
from learning_library.web import create_app


def test_compiled_theme_pages_serve_their_same_origin_assets(tmp_path):
    with TestClient(create_app(tmp_path)) as client:
        assets = set()
        for path in [
            "/",
            "/inbox",
            "/search",
            "/candidates",
            "/read/sample",
            "/workbench",
            "/publication",
            "/reading-builds",
        ]:
            response = client.get(path)
            assert response.status_code == 200
            assert 'id="main-header"' in response.text
            assets.update(re.findall(r'(?:href|src)="(/_astro/[^\"]+)"', response.text))
        assert any(path.endswith(".css") for path in assets)
        assert any(path.endswith(".js") for path in assets)
        for path in assets:
            response = client.get(path)
            assert response.status_code == 200, path
            assert len(response.content) > 0


def test_rendering_preserves_readable_structure_without_executing_source_html():
    html = render_markdown(
        "# Heading\n\n<script>alert(1)</script>\n\n[x](javascript:alert(1))\n\n![remote](https://example.com/tracker.png)\n\n| A | B |\n|---|---|\n| 1 | 2 |"
    )
    assert "<h1>Heading</h1>" in html
    assert "<script>" not in html and 'href="javascript:' not in html
    assert "<img" not in html and "[图片：remote]" in html
    assert "<table>" in html


def test_library_deduplicates_runs_and_reader_does_not_invent_notes_or_acceptance(tmp_path):
    transport = httpx.MockTransport(
        lambda r: httpx.Response(
            200,
            text="# Saved article\n\nOnly if A applies. This is a synthetic fixture for checking a saved reading view, not verified advice.\n",
            headers={"Content-Type": "text/markdown"},
        )
    )
    with TestClient(create_app(tmp_path, transport)) as client:
        empty = client.get("/api/library").json()
        assert empty == []
        ids = []
        for _ in range(2):
            run = client.post("/api/runs", json={"url": "https://example.com/article.md"}).json()
            base = "/api/runs/" + run["id"]
            ids.append(run["id"])
            client.post(base + "/stages/fetch")
            assert (
                client.post(base + "/stages/extract").json()["stages"][1]["status"] == "succeeded"
            )
        rows = client.get("/api/library").json()
        assert len(rows) == 1 and rows[0]["run_id"] == ids[-1]
        assert rows[0]["note"] == "" and rows[0]["status"] == "已保存原文"
        original = client.get("/api/runs/" + ids[0] + "/reviews").json()
        client.post(
            "/api/runs/" + ids[0] + "/reviews",
            json={
                "submission_id": "reader-note",
                "version_id": original["version_id"],
                "extract_attempt_id": original["extract_attempt_id"],
                "previous_review_id": None,
                "verdict": "pending",
                "body": "unreviewed",
                "structure": "unreviewed",
                "date_status": "unreviewed",
                "note": "测试本人备注，待核对。",
            },
        )
        article = client.get("/api/library/" + ids[0]).json()
        assert article["review_note"] == "测试本人备注，待核对。"
        assert article["note"] == ""  # technical review remarks are not learning notes
        task = client.get("/api/runs/" + ids[0] + "/processing/task").json()
        payload = task["result_template"]
        payload.update(executor="Synthetic", origin="demo", submission_id="test")
        payload["segments"] = [{"index": p["index"], "text": p["source"]} for p in task["segments"]]
        client.post("/api/runs/" + ids[0] + "/processing/results", json=payload)
        rows = client.get("/api/library").json()
        assert len(rows) == 1 and rows[0]["run_id"] == ids[0]
        article = client.get("/api/library/" + ids[0]).json()
        assert article["has_translation"] and article["review_note"] == "测试本人备注，待核对。"
        assert article["translation_origin"] == "demo"
        assert article["status"] == "演示资料"
        assert article["status"] != "已验收副本"
        assert len(article["pairs"]) == len(task["segments"])
        assert "<h1>Saved article</h1>" in article["source_html"]
        assert (
            client.get("/?run=" + ids[0], follow_redirects=False).headers["location"]
            == "/workbench?run=" + ids[0]
        )
        assert "搜索资料" in client.get("/").text
        assert 'aria-label="固定处理步骤"' in client.get("/workbench").text
        assert "阅读模式" in client.get("/read/" + ids[0]).text


def test_relative_reading_links_resolve_to_source_without_enabling_unsafe_html():
    source = "[guide](/docs/guide) [next](next.md) [anchor](#section) [bad](javascript:alert(1))"
    html = render_markdown(source, "https://example.com/articles/original.md")
    assert 'href="https://example.com/docs/guide"' in html
    assert 'href="https://example.com/articles/next.md"' in html
    assert 'href="https://example.com/articles/original.md#section"' in html
    assert 'href="javascript:' not in html
    assert 'rel="noopener noreferrer"' in html
