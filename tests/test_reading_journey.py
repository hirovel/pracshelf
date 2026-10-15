"""Selected article to portable note, through the same HTTP Interface as the UI.

All source/model/note text is synthetic. No account or live source is used.
"""

import json
from datetime import UTC, datetime

import httpx
from fastapi.testclient import TestClient

from learning_library.web import create_app

URL = "https://example.com/practice.md"
SOURCE = (
    "# Synthetic developer practice\n\n"
    "In this single project, executable checks caught boundary violations.\n\n"
    "This observation does not establish a general reduction in defects.\n"
)
QUOTE = "In this single project, executable checks caught boundary violations."
CORE = "合成案例：单个项目使用可执行检查发现边界违规，未证明普遍减少缺陷。"
NOTE = "## Engineering fixture\n\nThis is a demo note, not the user's learning.\n\n```py\nprint('fixture')\n```"


def test_selected_article_to_note_export_reuses_saved_outputs(tmp_path, monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "synthetic-journey-key")
    calls = {"source": 0, "core": 0, "translation": 0}

    def source(request):
        assert str(request.url) == URL
        calls["source"] += 1
        return httpx.Response(200, text=SOURCE, headers={"Content-Type": "text/markdown"})

    def model(request):
        incoming = json.loads(request.content)
        # The saved request is observable through HTTP too. Only a real external
        # dependency is replaced here; no application Module is mocked.
        if incoming.get("response_format", {}).get("type") == "json_object":
            calls["core"] += 1
            content = json.dumps({
                "category": "workflow", "recommendation": "read", "reason": CORE,
                "reading_kind": "practice", "reading_reason": "合成项目观察。",
                "reading_evidence": {"quote": QUOTE, "supports": "合成实践"},
                "core": CORE, "evidence": [{"quote": QUOTE, "supports": "项目范围"}],
                "points": [{"text": CORE, "claim_type": "author_judgment",
                            "evidence": [{"quote": QUOTE, "supports": "项目范围"}]}],
                "conditions": [], "time_dependencies": [], "unknowns": ["合成测试。"],
            }, ensure_ascii=False)
        else:
            calls["translation"] += 1
            content = "合成译文：" + incoming["messages"][-1]["content"]
        return httpx.Response(200, json={
            "model": "synthetic-journey-model",
            "usage": {"prompt_tokens": 120, "completion_tokens": 60, "total_tokens": 180},
            "choices": [{"finish_reason": "stop", "message": {"role": "assistant", "content": content}}],
        })

    app = create_app(tmp_path, httpx.MockTransport(source), review_origin="demo",
                     translation_transport=httpx.MockTransport(model))
    selected = {
        "submission_id": "journey-select", "url": URL, "origin": "user_link",
        "source_note": "Synthetic manual selection for HTTP verification.",
        "goal": "学习具体开发实践及其限制。", "max_age_days": 30,
        "as_of": datetime.now(UTC).date().isoformat(),
    }
    with TestClient(app) as client:
        added = client.post("/api/candidates/process", json=selected)
        assert added.status_code == 200, added.text
        candidate = added.json()
        run = candidate["pipeline_id"]
        base = f"/api/runs/{run}"
        version = candidate["version_id"]
        assert candidate["processing"]["status"] == "completed"
        assert candidate["submission_result"] == {"original": "saved", "model_tasks_started": 0}
        assert calls == {"source": 1, "core": 0, "translation": 0}
        original = client.get(base + "/artifacts/document").json()

        generated = client.post(base + "/insights", json={"submission_id": "journey-core"})
        assert generated.status_code == 202 and generated.json()["launch"]
        reading = client.get(f"/api/library/{run}").json()
        assert reading["version_id"] == reading["insights"]["version_id"] == version
        assert reading["insights"]["output"]["judgment"]["core"] == CORE
        assert reading["insights"]["output"]["semantic_fidelity"] == "unverified"
        assert not reading["has_translation"]
        assert client.get(base + "/curation").json()["latest"] is None
        assert client.get(f"/api/library/{run}/note").status_code == 400

        translated = client.post(base + "/translation/start", json={"submission_id": "journey-zh"})
        assert translated.status_code == 202 and translated.json()["launch"]
        reading = client.get(f"/api/library/{run}").json()
        assert reading["has_translation"] and reading["pairs"]
        assert calls["core"] == 1 and calls["translation"] > 0
        executed = calls.copy()

        # A new selection of the exact saved URL must keep the original version,
        # core and Chinese copy; it is a new intake record, not new model work.
        again = client.post("/api/candidates/process", json=selected | {
            "submission_id": "journey-select-again",
        }).json()
        assert (again["pipeline_id"], again["version_id"]) == (run, version)
        assert again["submission_result"] == {"original": "reused", "model_tasks_started": 0}
        assert client.post("/api/candidates/process", json=selected).json()["id"] == candidate["id"]
        for endpoint in ("insights", "translation/start"):
            reused = client.post(base + "/" + endpoint, json={"submission_id": "reuse-result"})
            assert reused.status_code == 202 and not reused.json()["launch"]
        view = client.get(f"/api/workflows/{run}?detail=summary").json()
        assert view["provenance"]["source_note"] == selected["source_note"]
        assert view["url"] == URL and view["version_id"] == version

        saved = client.post(base + "/curation", json={
            "submission_id": "journey-note", "version_id": version,
            "previous_id": None, "note": NOTE,
        })
        assert saved.status_code == 200, saved.text
        assert saved.json()["latest"]["origin"] == "demo"

    # Reloading the application must read the persisted output and saved note.
    with TestClient(app) as client:
        assert client.get(base + "/curation").json() == saved.json()
        exported = client.get(f"/api/library/{run}/note")
        assert exported.status_code == 200 and "attachment" in exported.headers["content-disposition"]
        assert NOTE in exported.text and f"来源：{URL}" in exported.text
        assert f"原文版本：{version}" in exported.text and "记录性质：演示记录" in exported.text
        assert SOURCE not in exported.text and CORE not in exported.text
        assert client.get(base + "/artifacts/document").json() == original
        assert len(client.get(base + "/insights").json()["history"]) == 1
        assert len(client.get(base + "/translation").json()["history"]) == 1
        assert client.get(base + "/reviews").json()["latest"] is None
        assert calls == executed
