"""Selection is a personal record, not a side effect of extraction or translation."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import httpx
import pytest
from fastapi.testclient import TestClient

from learning_library.web import create_app

SOURCE = "# Synthetic workflow\n\nOnly use this fixture for tests. Preserve the source and review the result before reuse.\n"


@pytest.fixture
def setup(tmp_path):
    body = [SOURCE]
    app = create_app(
        tmp_path,
        httpx.MockTransport(
            lambda request: httpx.Response(
                200, text=body[0], headers={"Content-Type": "text/markdown"}
            )
        ),
    )
    return app, body


def capture(client, url="https://example.com/workflow.md"):
    run = client.post("/api/runs", json={"url": url}).json()
    base = "/api/runs/" + run["id"]
    for stage in ("fetch", "extract"):
        value = client.post(base + "/stages/" + stage).json()
        assert next(s for s in value["stages"] if s["id"] == stage)["status"] == "succeeded"
    return base


def payload(state, **changes):
    return {
        "submission_id": uuid4().hex,
        "version_id": state["version_id"],
        "previous_id": state["latest"]["id"] if state["latest"] else None,
        "category": "workflow",
        "decision": "kept",
        "purpose": "A synthetic test purpose",
        "note": "My synthetic reading note",
        "quote": "Preserve the source",
        **changes,
    }


def test_selection_is_explicit_and_does_not_change_original_or_technical_reviews(setup):
    app, _ = setup
    with TestClient(app) as client:
        base = capture(client)
        state = client.get(base + "/curation").json()
        source = client.get(base + "/artifacts/document").json()
        assert state["latest"] is None and state["history"] == []
        assert client.get("/api/library").json()[0]["note"] == ""
        assert client.post(base + "/curation", json=payload(state, purpose="")).status_code == 422
        assert (
            client.post(
                base + "/curation", json=payload(state, category="unclassified")
            ).status_code
            == 422
        )
        assert (
            client.post(
                base + "/curation", json=payload(state, quote="not in original")
            ).status_code
            == 400
        )
        assert (
            client.post(base + "/curation", json=payload(state, origin="human")).status_code == 422
        )
        saved = client.post(base + "/curation", json=payload(state)).json()
        assert saved["latest"]["origin"] == "human"
        assert saved["latest"]["payload"]["freshness"] == "unknown"
        assert saved["latest"]["payload"]["trial"] == "not_tried"
        assert client.get(base + "/reviews").json()["latest"] is None
        assert client.get(base + "/artifacts/document").json() == source
        entry = client.get("/api/library").json()[0]
        assert entry["note"] == "My synthetic reading note"
        assert entry["status"] == "已保存原文"
        exported = client.get("/api/library/" + entry["run_id"] + "/note")
        assert "本人填写" in exported.text and "My synthetic reading note" in exported.text
        assert "未记录" not in exported.text and "适用性待核实" not in exported.text
        assert "https://example.com/workflow.md" in exported.text
        assert SOURCE not in exported.text  # notes export does not publish the full source
        assert "attachment" in exported.headers["content-disposition"]
    with TestClient(app) as client:
        assert client.get(base + "/curation").json() == saved


def test_history_restore_idempotency_and_conflicting_tabs(setup):
    app, _ = setup
    with TestClient(app) as client:
        base = capture(client)
        empty = client.get(base + "/curation").json()
        first_input = payload(empty)
        first = client.post(base + "/curation", json=first_input).json()
        assert client.post(base + "/curation", json=first_input).json() == first
        assert client.post(base + "/curation", json=payload(empty)).status_code == 409
        assert (
            client.post(base + "/curation", json=first_input | {"note": "changed"}).status_code
            == 409
        )
        archived = client.post(
            base + "/curation", json=payload(first, decision="archived", purpose="Not relevant now")
        ).json()
        restored = client.post(base + "/curation", json=payload(archived)).json()
        assert len(restored["history"]) == 3
        assert restored["history"][0] == first["latest"]
        assert restored["latest"]["payload"]["decision"] == "kept"
        assert client.post(base + "/curation", json=first_input).json() == restored


def test_note_only_save_and_export_are_independent_of_selection_and_models(setup):
    app, _ = setup
    with TestClient(app) as client:
        base = capture(client)
        state = client.get(base + "/curation").json()
        original = client.get(base + "/artifacts/document").json()
        run_id = base.rsplit("/", 1)[-1]
        assert client.get(f"/api/library/{run_id}/note").status_code == 400
        incoming = {
            "submission_id": "note-only-test", "version_id": state["version_id"],
            "previous_id": None, "note": "## Synthetic observation\n\n- Fixture only.\n\n```py\nprint('test')\n```",
        }
        saved = client.post(base + "/curation", json=incoming).json()
        assert saved["latest"]["payload"]["decision"] == "inbox"
        assert saved["latest"]["payload"]["category"] == "unclassified"
        assert saved["latest"]["payload"]["purpose"] == ""
        assert client.post(base + "/curation", json=incoming).json() == saved
        exported = client.get(f"/api/library/{run_id}/note")
        assert exported.status_code == 200 and incoming["note"] in exported.text
        assert f"原文版本：{state['version_id']}" in exported.text
        assert "分类：" not in exported.text and "试用状态：" not in exported.text
        assert SOURCE not in exported.text
        assert client.get(base + "/artifacts/document").json() == original
        assert client.get(base + "/reviews").json()["latest"] is None
        # Existing read Interfaces confirm saving a note did not run either model task.
        assert client.get(base + "/insights").json()["history"] == []
        assert client.get(base + "/translation").json()["history"] == []


def test_empty_note_body_cannot_be_exported_as_personal_writing(setup):
    app, _ = setup
    with TestClient(app) as client:
        base = capture(client)
        state = client.get(base + "/curation").json()
        saved = client.post(base + "/curation", json=payload(state, note="")).json()
        assert saved["latest"]["payload"]["note"] == ""
        exported = client.get("/api/library/" + base.rsplit("/", 1)[-1] + "/note")
        assert exported.status_code == 400 and "笔记正文" in exported.json()["detail"]


def test_note_properties_survive_save_and_export(setup):
    import json

    app, _ = setup
    with TestClient(app) as client:
        base = capture(client)
        state = client.get(base + "/curation").json()
        incoming = payload(state, note_title='Fixture: "local"', tags=["SQLite", "工作流", "SQLite"])
        saved = client.post(base + "/curation", json=incoming)
        assert saved.status_code == 200
        record = saved.json()["latest"]
        assert record["payload"]["tags"] == ["SQLite", "工作流"]
        assert client.post(base + "/curation", json=incoming).json()["latest"]["id"] == record["id"]
        exported = client.get("/api/library/" + base.rsplit("/", 1)[-1] + "/note").text
        frontmatter = exported.split("---", 2)[1]
        properties = {key: json.loads(value) for key, value in (
            line.split(": ", 1) for line in frontmatter.strip().splitlines()
        )}
        assert properties["title"] == 'Fixture: "local"'
        assert properties["tags"] == ["SQLite", "工作流"]
        assert properties["source_version"] == state["version_id"]
        assert properties["source_url"] == "https://example.com/workflow.md"
        assert SOURCE not in exported

        updated = payload(saved.json(), tags=["x" * 41])
        assert client.post(base + "/curation", json=updated).status_code == 422
        assert client.post(base + "/curation", json=payload(saved.json(), tags=[" "])).status_code == 422
        assert client.post(base + "/curation", json=payload(saved.json(), tags=list(map(str, range(13))))).status_code == 422


def test_legacy_payload_defaults_do_not_break_retry_or_rewrite_history(tmp_path):
    import json
    import sqlite3

    app = create_app(tmp_path, httpx.MockTransport(lambda request: httpx.Response(
        200, text=SOURCE, headers={"Content-Type": "text/markdown"},
    )))
    with TestClient(app) as client:
        base = capture(client)
        incoming = payload(client.get(base + "/curation").json())
        record = client.post(base + "/curation", json=incoming).json()["latest"]
        historical = dict(record["payload"])
        historical.pop("note_title")
        historical.pop("tags")
        encoded = json.dumps(historical, ensure_ascii=False, sort_keys=True)
        with sqlite3.connect(tmp_path / "library.sqlite3") as db:
            db.execute("UPDATE curation_records SET payload=? WHERE id=?", (encoded, record["id"]))
        retried = client.post(base + "/curation", json=incoming)
        assert retried.status_code == 200
        assert retried.json()["latest"]["payload"] == historical
        assert len(retried.json()["history"]) == 1
        with sqlite3.connect(tmp_path / "library.sqlite3") as db:
            assert db.execute("SELECT payload FROM curation_records").fetchone()[0] == encoded


def test_same_version_shares_notes_but_new_version_requires_new_judgment(setup):
    app, body = setup
    with TestClient(app) as client:
        old = capture(client)
        state = client.get(old + "/curation").json()
        saved = client.post(old + "/curation", json=payload(state)).json()
        duplicate = capture(client)
        assert client.get(duplicate + "/curation").json()["latest"] == saved["latest"]
        assert len(client.get("/api/library").json()) == 1
        assert client.get("/api/library").json()[0]["note"] == "My synthetic reading note"
        body[0] += "\nNew source content changes the scope of this synthetic fixture.\n"
        new = capture(client)
        new_state = client.get(new + "/curation").json()
        assert new_state["latest"] is None and len(new_state["history"]) == 1
        assert client.get(old + "/curation").json()["older_version"]
        entry = client.get("/api/library").json()[0]
        assert entry["note"] == "" and entry["needs_reassessment"]
        assert client.post(new + "/curation", json=payload(state)).status_code == 409
        assert client.post(new + "/curation", json=payload(new_state)).status_code == 200


def test_applicability_and_trials_require_explicit_evidence(setup):
    app, _ = setup
    with TestClient(app) as client:
        base = capture(client)
        state = client.get(base + "/curation").json()
        for changes in (
            {"freshness": "current"},
            {"freshness": "outdated"},
            {"trial": "useful"},
            {"trial": "mixed"},
            {"trial": "not_useful"},
            {"checked_on": str(datetime.now(UTC).date() + timedelta(days=1))},
        ):
            assert (
                client.post(base + "/curation", json=payload(state, **changes)).status_code == 422
            )
        saved = client.post(
            base + "/curation",
            json=payload(
                state,
                freshness="current",
                checked_on=str(datetime.now(UTC).date()),
                applicability="Synthetic environment A, reviewed against fixture B.",
                trial="mixed",
                trial_note="Task A in environment B: useful for C, failed at D.",
            ),
        ).json()
        assert saved["latest"]["payload"]["trial"] == "mixed"


def test_capture_required_bad_urls_report_errors_and_demo_keeps_its_origin(tmp_path):
    app = create_app(
        tmp_path,
        httpx.MockTransport(
            lambda request: httpx.Response(
                200, text=SOURCE, headers={"Content-Type": "text/markdown"}
            )
        ),
        review_origin="demo",
    )
    with TestClient(app) as client:
        assert client.post("/api/runs", json={"url": "file:///secret"}).status_code == 400
        run = client.post("/api/runs", json={"url": "https://example.com/new"}).json()
        base = "/api/runs/" + run["id"]
        assert not client.get(base + "/curation").json()["available"]
        assert (
            client.post(
                base + "/curation", json=payload({"version_id": 1, "latest": None})
            ).status_code
            == 400
        )
        base = capture(client)
        state = client.get(base + "/curation").json()
        assert (
            client.post(base + "/curation", json=payload(state)).json()["latest"]["origin"]
            == "demo"
        )
        row = client.get("/api/library").json()[0]
        assert row["note"] == "" and row["status"] == "演示资料"
        assert "演示记录" in client.get("/api/library/" + row["run_id"] + "/note").text
