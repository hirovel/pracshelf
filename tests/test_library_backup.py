"""Restore uses an isolated library; no real notes, accounts or model calls."""

import json
import sqlite3

import httpx
import pytest
from fastapi.testclient import TestClient
from test_curation import capture, payload

from learning_library.library_backup import create_backup, restore_backup
from learning_library.web import create_app


@pytest.fixture
def library(tmp_path):
    source = tmp_path / "library #1"
    transport = httpx.MockTransport(lambda r: httpx.Response(200,
        text="# Synthetic source\n\n" + "An isolated source for backup and restore tests, with no personal claims. " * 4,
        headers={"Content-Type": "text/markdown"}))
    with TestClient(create_app(source, transport, review_origin="demo")) as client:
        base = capture(client)
        state = client.get(base + "/curation").json()
        saved = client.post(base + "/curation", json=payload(state, quote="", note="## Demo\n\nBACKUP_NOTE_SENTINEL"))
        assert saved.status_code == 200, saved.text
    (source / "deepseek-api-key.txt").write_text("SYNTHETIC_SECRET", "utf-8")
    (source / "connection-runtime").mkdir()
    (source / "connection-runtime/login.json").write_text("SYNTHETIC_LOGIN_URL", "utf-8")
    artifacts = source / "model-runs" / "synthetic"
    artifacts.mkdir(parents=True)
    (artifacts / "events.jsonl").write_text('{"type":"synthetic"}\n', "utf-8")
    return source, base


def test_round_trip_keeps_notes_source_and_logs_without_credentials(library, tmp_path):
    source, base = library
    backup, restored = tmp_path / "backup", tmp_path / "restored"
    result = create_backup(source, backup)
    assert result["status"] == "succeeded" and result["model_calls"] == 0
    assert not (backup / "deepseek-api-key.txt").exists()
    assert not (backup / "connection-runtime").exists()
    assert restore_backup(backup, restored)["credentials_restored"] is False
    assert (restored / "model-runs/synthetic/events.jsonl").read_bytes() == (source / "model-runs/synthetic/events.jsonl").read_bytes()
    with TestClient(create_app(restored, review_origin="demo")) as client:
        note = client.get(base + "/curation").json()["latest"]
        assert note["payload"]["note"] == "## Demo\n\nBACKUP_NOTE_SENTINEL"
        assert "BACKUP_NOTE_SENTINEL" in client.get(base.replace("/runs/", "/library/") + "/note").text
    with sqlite3.connect(restored / "library.sqlite3") as db:
        assert db.execute("PRAGMA quick_check").fetchone()[0] == "ok"
        assert db.execute("PRAGMA foreign_key_check").fetchall() == []


def test_running_task_prevents_backup_without_changing_source(library, tmp_path):
    source, _ = library
    with sqlite3.connect(source / "library.sqlite3") as db:
        db.execute("UPDATE pipeline_attempts SET status='running' WHERE stage='fetch'")
    with pytest.raises(ValueError, match="运行中的任务"):
        create_backup(source, tmp_path / "backup")
    assert not (tmp_path / "backup").exists()


def test_corruption_and_path_traversal_fail_before_restore(library, tmp_path):
    source, _ = library
    backup = tmp_path / "backup"
    create_backup(source, backup)
    manifest_path = backup / "manifest.json"
    manifest = json.loads(manifest_path.read_text("utf-8"))
    record = next(item for item in manifest["files"] if item["file"].startswith("raw/"))
    original = (backup / record["file"]).read_bytes()
    (backup / record["file"]).write_bytes(b"corrupted")
    with pytest.raises(ValueError, match="哈希|大小"):
        restore_backup(backup, tmp_path / "restored")
    assert not (tmp_path / "restored").exists()
    (backup / record["file"]).write_bytes(original)
    record["file"] = "../outside.bin"
    manifest_path.write_text(json.dumps(manifest), "utf-8")
    with pytest.raises(ValueError, match="路径"):
        restore_backup(backup, tmp_path / "restored")
    assert not (tmp_path / "restored").exists()


def test_existing_and_overlapping_destinations_are_preserved(library, tmp_path):
    source, _ = library
    with pytest.raises(ValueError, match="重叠"):
        create_backup(source, source / "backup")
    backup = tmp_path / "backup"
    create_backup(source, backup)
    with pytest.raises(ValueError, match="已存在"):
        create_backup(source, backup)
    with pytest.raises(ValueError, match="已存在"):
        restore_backup(backup, source)
    assert (source / "deepseek-api-key.txt").read_text("utf-8") == "SYNTHETIC_SECRET"
