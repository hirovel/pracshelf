"""Exercise publication through the same interface used by the local page."""

import hashlib

import httpx
import pytest
from fastapi.testclient import TestClient
from test_curation import capture, payload

from learning_library.web import create_app


@pytest.fixture
def sample(tmp_path):
    body = [
        (
            "# Test source\n\nPreserve the source. PRIVATE_FULL_TEXT_SENTINEL. "
            "This synthetic fixture is long enough to exercise extraction; it is not practical advice.\n"
        )
    ]
    app = create_app(
        tmp_path,
        httpx.MockTransport(
            lambda r: httpx.Response(200, text=body[0], headers={"Content-Type": "text/markdown"})
        ),
    )
    with TestClient(app) as client:
        base = capture(client)
        yield client, base, body


def curate(client, base, **changes):
    state = client.get(base + "/curation").json()
    return client.post(
        base + "/curation",
        json=payload(
            state,
            purpose="PRIVATE_PURPOSE_SENTINEL",
            trial_note="PRIVATE_TRIAL_SENTINEL",
            **changes,
        ),
    ).json()


@pytest.mark.parametrize("origin", ["human", "demo"])
def test_publication_mode_is_known_before_a_note_or_preview_exists(tmp_path, origin):
    article = "# Synthetic source\n\n" + "Preserve the source. " * 30
    app = create_app(
        tmp_path,
        httpx.MockTransport(lambda request: httpx.Response(
            200, text=article, headers={"Content-Type": "text/markdown"}
        )),
        review_origin=origin,
    )
    with TestClient(app) as client:
        base = capture(client)
        state = client.get(base + "/publication").json()
        assert state["origin"] == origin
        assert state["latest"] is None
        assert state["model_calls"] == 0


def accept(client, base):
    state = client.get(base + "/reviews").json()
    return client.post(
        base + "/reviews",
        json={
            "submission_id": "accept-source",
            "version_id": state["version_id"],
            "extract_attempt_id": state["extract_attempt_id"],
            "previous_review_id": None,
            "verdict": "accepted",
            "body": "matched",
            "structure": "matched",
            "date_status": "unknown",
            "note": "PRIVATE_REVIEW_SENTINEL",
        },
    )


def preview(client, base, **changes):
    state = client.get(base + "/publication").json()
    return client.post(
        base + "/publication/previews",
        json={
            "version_id": state["input"]["version_id"],
            "curation_id": state["input"]["curation_id"],
            "previous_preview_id": state["latest"]["id"] if state["latest"] else None,
            **changes,
        },
    )


def approve(client, base, p, **changes):
    return client.post(
        base + f"/publication/previews/{p['id']}/approve",
        json={
            "content_hash": p["content_hash"],
            "content_reviewed": True,
            **changes,
        },
    )


def export(client, base, p, mode="preview"):
    return client.post(base + f"/publication/previews/{p['id']}/export?mode={mode}")


def test_real_boundary_allows_incomplete_preview_but_blocks_approval(sample):
    client, base, _ = sample
    result = preview(client, base)
    assert result.status_code == 200
    p = result.json()["latest"]
    assert len(p["blockers"]) == 1
    assert p["public_note"]["note"] == ""
    assert p["public_note"]["source_date"] is None
    assert approve(client, base, p).status_code == 400
    assert export(client, base, p, "approved").status_code == 400
    result = export(client, base, p)
    assert result.status_code == 200
    assert result.json()["approved"] is False and result.json()["deployed"] is False
    assert "PRIVATE_FULL_TEXT_SENTINEL" not in result.text
    assert client.get(base + "/curation").json()["latest"] is None


def test_personal_note_title_is_preview_default_but_explicit_blog_title_wins(sample):
    client, base, _ = sample
    curate(client, base, note_title="Synthetic personal note title")
    assert client.get(base + "/publication").json()["title"] == "Synthetic personal note title"
    initial = preview(client, base).json()["latest"]
    assert initial["public_note"]["title"] == "Synthetic personal note title"
    assert initial["approval"] is None
    explicit = preview(client, base, title="Synthetic blog title").json()["latest"]
    assert explicit["public_note"]["title"] == "Synthetic blog title"
    assert explicit["approval"] is None


def test_allowlist_approval_determinism_and_persisted_trace(sample):
    client, base, _ = sample
    curate(client, base)
    assert accept(client, base).status_code == 200
    p = preview(client, base).json()["latest"]
    assert p["blockers"] == []
    assert preview(client, base).json()["latest"]["id"] == p["id"]
    assert approve(client, base, p).status_code == 200
    assert approve(client, base, p).status_code == 200
    first = export(client, base, p, "approved")
    assert first.status_code == 200 and first.json()["approved"]
    assert first.content == export(client, base, p, "approved").content
    assert "PRIVATE_" not in first.text
    note = first.json()["note"]
    assert set(note) == {
        "note_id",
        "title",
        "source_url",
        "source_date",
        "source_date_status",
        "category",
        "note",
        "purpose",
        "quote",
        "applicability",
        "freshness",
        "checked_on",
        "trial",
        "content_origin",
        "note_updated_at",
    }
    state = client.get(base + "/publication").json()
    assert len(state["history"]) == 1 and len(state["latest"]["exports"]) == 1
    assert (
        state["latest"]["exports"][0]["content_hash"] == hashlib.sha256(first.content).hexdigest()
    )
    assert state["deployment"] == "not_deployed" and state["model_calls"] == 0


def test_note_only_can_publish_without_selection_or_original_review_and_title_is_frozen(sample):
    client, base, _ = sample
    state = client.get(base + "/curation").json()
    body = "## 我的合成笔记\n\n" + "这一段用于隔离测试长笔记保存和导出，不是实际学习内容。\n\n" * 100
    result = client.post(base + "/curation", json=payload(state,
        note=body, decision="inbox", category="unclassified", purpose="", quote=""))
    assert result.status_code == 200
    assert len(result.json()["latest"]["payload"]["note"]) > 2000
    assert client.get(base + "/reviews").json()["latest"] is None
    p = preview(client, base, title="我的博客标题").json()["latest"]
    assert p["blockers"] == [] and p["public_note"]["category"] == "未分类"
    assert p["public_note"]["title"] == "我的博客标题"
    assert approve(client, base, p).status_code == 200
    assert export(client, base, p, "approved").json()["note"]["note"] == body.strip()
    changed = preview(client, base, title="新的标题").json()["latest"]
    assert changed["approval"] is None and changed["content_hash"] != p["content_hash"]
    assert export(client, base, p, "approved").status_code == 409
    run_id = base.rsplit("/", 1)[-1]
    assert body.strip() in client.get(f"/api/library/{run_id}/note").text
    assert client.get(base + "/insights").json()["history"] == []
    assert client.get(base + "/translation").json()["history"] == []


def test_archived_note_stays_blocked_until_restored(sample):
    client, base, _ = sample
    curate(client, base, decision="archived")
    p = preview(client, base).json()["latest"]
    assert len(p["blockers"]) == 1 and "归档" in p["blockers"][0]
    assert approve(client, base, p).status_code == 400


def test_changed_notes_invalidate_approval_and_old_tab(sample):
    client, base, _ = sample
    curate(client, base)
    accept(client, base)
    p = preview(client, base).json()["latest"]
    approve(client, base, p)
    curate(client, base, note="A changed public note")
    assert not client.get(base + "/publication").json()["latest"]["current"]
    assert approve(client, base, p).status_code == 409
    assert export(client, base, p, "approved").status_code == 409
    current = preview(client, base).json()["latest"]
    assert current["approval"] is None and current["id"] != p["id"]
    assert current["public_note"]["note_id"] == p["public_note"]["note_id"]
    assert export(client, base, current, "approved").status_code == 400


def test_quote_requires_separate_rights_confirmation_and_hash_match(sample):
    client, base, _ = sample
    curate(client, base)
    accept(client, base)
    p = preview(client, base, include_quote=True).json()["latest"]
    assert approve(client, base, p).status_code == 400
    assert approve(client, base, p, content_hash="0" * 64).status_code == 409
    assert approve(client, base, p, content_reviewed=False).status_code == 422
    assert approve(client, base, p, quote_rights_confirmed=True).status_code == 200
    assert export(client, base, p, "approved").json()["note"]["quote"] == "Preserve the source"
    new = preview(client, base, include_quote=False).json()["latest"]
    assert new["approval"] is None
    assert export(client, base, p).status_code == 409


def test_new_source_version_and_other_source_cannot_reuse_approval(sample):
    client, base, body = sample
    curate(client, base)
    accept(client, base)
    p = preview(client, base).json()["latest"]
    approve(client, base, p)
    other = capture(client, "https://example.com/different.md")
    assert export(client, other, p).status_code == 400
    body[0] += "\nAn updated source changes its advice.\n"
    new_base = capture(client)
    assert export(client, base, p, "approved").status_code == 409
    assert preview(client, base).status_code == 409
    new = preview(client, new_base).json()["latest"]
    assert new["approval"] is None and len(new["blockers"]) == 1


def test_stale_preview_form_cannot_overwrite_new_selection(sample):
    client, base, _ = sample
    p = preview(client, base).json()["latest"]
    assert preview(client, base, include_purpose=True).status_code == 200
    response = client.post(
        base + "/publication/previews",
        json={
            "version_id": p["binding"]["version_id"],
            "curation_id": None,
            "previous_preview_id": p["id"],
            "include_quote": True,
        },
    )
    assert response.status_code == 409
