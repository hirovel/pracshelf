"""Web build behavior. All notes and approvals here are isolated synthetic fixtures."""

import hashlib
import json
import uuid

import httpx
import pytest
from fastapi.testclient import TestClient
from lxml.html import fromstring
from test_curation import capture
from test_publication import accept, approve, curate, preview

from learning_library.reading_builds import BuildFailure
from learning_library.web import create_app


@pytest.fixture
def setup(tmp_path):
    def start(runner=None, origin="human"):
        transport = httpx.MockTransport(
            lambda r: httpx.Response(
                200,
                text="# Synthetic source\n\nPreserve the source. PRIVATE_FULL_TEXT_SENTINEL. "
                "This is isolated test content with enough characters for extraction, not a real user note.",
                headers={"Content-Type": "text/markdown"},
            )
        )
        return TestClient(
            create_app(tmp_path, transport, reading_runner=runner, review_origin=origin)
        )

    return start


def confirmed(client):
    base = capture(client)
    curate(client, base)
    assert accept(client, base).status_code == 200
    p = preview(client, base).json()["latest"]
    assert approve(client, base, p).status_code == 200
    return base, p["id"]


def request(ids=None, **changes):
    return {"submission_id": str(uuid.uuid4()), "preview_ids": ids or [], **changes}


def fixture_runner(directory, root):
    """Synthetic static output exercises lifecycle without requiring Astro per test."""
    bodies = [json.loads(p.read_text(encoding="utf-8")) for p in directory.glob("*.json")]
    assert all(b["approved"] and not b["demo"] for b in bodies)
    assert all("PRIVATE_" not in json.dumps(b) for b in bodies)
    site = root / "site"
    site.mkdir()
    content = b"<!doctype html><p>Synthetic artifact</p>"
    (site / "index.html").write_bytes(content)
    return {
        "status": "succeeded",
        "notes": len(bodies),
        "artifacts": [
            {
                "file": "index.html",
                "bytes": len(content),
                "sha256": hashlib.sha256(content).hexdigest(),
            }
        ],
    }, "Synthetic build log"


def test_real_astro_build_exports_only_approved_fields_and_relative_navigation(setup):
    with setup() as client:
        base, id = confirmed(client)
        before = client.get(base + "/publication").json()
        payload = request([id])
        result = client.post("/api/reading-builds", json=payload)
        assert result.status_code == 200
        job = result.json()
        assert job["status"] == "succeeded", job
        assert job["report"]["notes"] == 1
        assert job["log"] and len(job["report"]["artifacts"]) >= 4
        home = client.get(job["preview_url"])
        assert home.status_code == 200 and "<script" not in home.text
        assert 'href="./about/"' in home.text
        note_id = before["latest"]["public_note"]["note_id"]
        article = client.get(job["preview_url"] + f"notes/{note_id}/")
        assert article.status_code == 200
        assert 'href="../../"' in article.text
        assert "PRIVATE_" not in article.text
        assert "记录方法，也记录条件" not in article.text
        assert client.get(job["preview_url"] + "about/").status_code == 200
        for path in (
            "report.json",
            "input.json",
            "snapshots/snapshot-1.json",
            "api/reading-builds",
        ):
            assert client.get(job["preview_url"] + path).status_code == 404
        again = client.post("/api/reading-builds", json=payload).json()
        assert again["id"] == job["id"]
        assert len(client.get("/api/reading-builds").json()["jobs"]) == 1
        assert (
            client.get(base + "/publication").json()["latest"]["approval"]
            == before["latest"]["approval"]
        )
        assert (
            client.post(
                "/api/reading-builds", json={**payload, "preview_ids": [], "allow_empty": True}
            ).status_code
            == 409
        )


def test_real_astro_build_renders_note_only_markdown_without_private_or_active_content(setup):
    with setup() as client:
        base = capture(client)
        body = "## 实际观察\n\n这是合成验收内容。\n\n- 第一项\n- 第二项\n\n```python\ndef run():\n    return 1\n```\n\n<script>alert(1)</script>\n\n![image](https://example.test/not-loaded.png)"
        curate(client, base, decision="inbox", category="unclassified", note=body)
        assert client.get(base + "/reviews").json()["latest"] is None
        p = preview(client, base, title="合成 Markdown 博客").json()["latest"]
        assert p["blockers"] == []
        assert approve(client, base, p).status_code == 200
        job = client.post("/api/reading-builds", json=request([p["id"]])).json()
        assert job["status"] == "succeeded", job
        article = client.get(job["preview_url"] + f"notes/{p['public_note']['note_id']}/")
        page = fromstring(article.text)
        heading = page.xpath("//div[@class='note-body']/h2")[0]
        assert heading.text_content() == '实际观察'
        assert '#' + heading.attrib['id'] in page.xpath("//aside[@class='article-toc']//a/@href")
        assert '<li>第一项</li>' in article.text
        assert '<pre><code class="language-python">' in article.text
        assert '    return 1' in article.text
        assert '<script' not in article.text and '<img ' not in article.text
        assert '&lt;script&gt;' in article.text
        assert '条件与核查' not in article.text and 'PRIVATE_' not in article.text


def test_unapproved_and_stale_inputs_fail_without_running_builder(setup):
    def forbidden(*args):
        pytest.fail("Invalid selection reached the process runner")

    with setup(forbidden) as client:
        base = capture(client)
        p = preview(client, base).json()["latest"]
        job = client.post("/api/reading-builds", json=request([p["id"]])).json()
        assert job["status"] == "failed" and job["stage"] == "validate"
        assert "尚未人工确认" in job["error"]
        curate(client, base)
        accept(client, base)
        p = preview(client, base).json()["latest"]
        approve(client, base, p)
        curate(client, base, note="Changed synthetic note")
        job = client.post("/api/reading-builds", json=request([p["id"]])).json()
        assert job["status"] == "failed" and "已有变化" in job["error"]


def test_failure_preserves_previous_success_and_exposes_reason(setup):
    calls = 0
    artifacts = []

    def runner(directory, root):
        nonlocal calls
        calls += 1
        if calls > 1:
            raise BuildFailure(
                "Synthetic compiler failure", "SyntaxError: synthetic failure detail"
            )
        artifacts.append(root / "site" / "index.html")
        return fixture_runner(directory, root)

    with setup(runner) as client:
        assert client.post("/api/reading-builds", json=request()).status_code == 400
        first = client.post("/api/reading-builds", json=request(allow_empty=True)).json()
        assert first["status"] == "succeeded"
        failed = client.post("/api/reading-builds", json=request(allow_empty=True)).json()
        assert failed["status"] == "failed" and failed["stage"] == "build"
        assert "SyntaxError" in failed["log"] and failed["error"] == "Synthetic compiler failure"
        assert failed["preview_url"] is None
        assert client.get(first["preview_url"]).status_code == 200
        artifacts[0].write_text("Changed after build", encoding="utf-8")
        changed = client.get(first["preview_url"])
        assert changed.status_code == 404 and "校验失败" in changed.text


def test_mid_build_change_invalidates_result_and_second_build_is_blocked(setup):
    def runner(directory, root):
        with setup(fixture_runner) as other:
            assert (
                other.post("/api/reading-builds", json=request(allow_empty=True)).status_code == 409
            )
            curate(other, base, note="Updated during synthetic build")
        return fixture_runner(directory, root)

    with setup(runner) as client:
        base, id = confirmed(client)
        job = client.post("/api/reading-builds", json=request([id])).json()
        assert job["status"] == "failed" and job["stage"] == "verify"
        assert "构建期间" in job["error"]
        assert client.get(f"/reading-preview/{job['id']}/").status_code == 404


def test_demo_approval_does_not_enter_real_build(setup):
    with setup(origin="demo") as client:
        _, id = confirmed(client)
    with setup(fixture_runner) as client:
        state = client.get("/api/reading-builds").json()
        assert not state["candidates"][0]["eligible"]
        job = client.post("/api/reading-builds", json=request([id])).json()
        assert job["status"] == "failed" and job["stage"] == "validate"


def test_demo_astro_build_consumes_its_exported_note_and_remains_isolated(setup):
    with setup(origin="demo") as client:
        base = capture(client)
        curate(client, base, decision="inbox", category="unclassified", note="## 本次导入\n\nDEMO_IMPORT_SENTINEL：隔离工程自检，不是用户笔记。")
        p = preview(client, base, title="本次测试笔记").json()["latest"]
        assert approve(client, base, p).status_code == 200
        job = client.post("/api/reading-builds", json=request([p["id"]])).json()
        assert job["status"] == "succeeded", job
        assert job["mode"] == job["report"]["mode"] == "demo"
        article = client.get(job["preview_url"] + f"notes/{p['public_note']['note_id']}/")
        assert article.status_code == 200
        assert "DEMO_IMPORT_SENTINEL" in article.text and "演示" in article.text
        assert "PRIVATE_" not in article.text
        state = client.get("/api/reading-builds").json()
        assert state["mode"] == "demo" and state["model_calls"] == 0
    with setup(fixture_runner) as real:
        assert real.get(job["preview_url"]).status_code == 404
        assert real.get("/api/reading-builds").json()["jobs"] == []


def test_real_notes_cannot_enter_demo_build(setup):
    with setup(fixture_runner) as client:
        _, id = confirmed(client)
    with setup(origin="demo") as client:
        job = client.post("/api/reading-builds", json=request([id])).json()
        assert job["status"] == "failed" and job["stage"] == "validate"
        assert "demo" in job["error"]
