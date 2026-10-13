"""Private local backup/restore. New directories only; no network or model calls.

The Interface owns a consistent SQLite snapshot, persistent artifacts, validation
and fail-safe publication. Credentials and transient login files are excluded.
"""

import hashlib
import json
import shutil
import sqlite3
from contextlib import closing, contextmanager
from pathlib import Path, PurePosixPath
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .model_config import ModelChoice
from .storage import now

SCHEMA = "learning-library-backup/v1"
DIRECTORIES = {"raw", "model-runs", "human-notes", "analysis", "reading-builds"}
ROOT_FILES = {"library.sqlite3", "model-provider.json"}


class BackupFile(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    file: str = Field(min_length=1, max_length=1000)
    bytes: int = Field(ge=0)
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")


class BackupManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: Literal["learning-library-backup/v1"] = Field(alias="schema")
    created: str
    model_calls: Literal[0]
    credentials_included: Literal[False]
    files: list[BackupFile] = Field(min_length=1, max_length=100000)

    @model_validator(mode="after")
    def unique_database(self):
        names = [item.file for item in self.files]
        if len(set(names)) != len(names) or "library.sqlite3" not in names:
            raise ValueError("备份文件清单重复或缺少数据库。")
        return self


def _hash(path):
    result = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            result.update(block)
    return result.hexdigest()


def _safe_file(root, name):
    parts = PurePosixPath(name).parts
    if not parts or PurePosixPath(name).as_posix() != name or "\\" in name or ":" in name or name.startswith("/") or any(
        part in {".", ".."} for part in parts
    ):
        raise ValueError("备份文件路径无效。")
    if not (name in ROOT_FILES or len(parts) > 1 and parts[0] in DIRECTORIES):
        raise ValueError("备份包含未允许的文件。")
    path = root.joinpath(*parts)
    if not path.resolve().is_relative_to(root.resolve()) or any(
        item.is_symlink() or item.is_junction() for item in (path, *path.parents)
        if item != root.parent
    ):
        raise ValueError("备份不接受符号链接或目录联接。")
    return path


@contextmanager
def _fresh_destination(source, destination):
    source, destination = Path(source).resolve(), Path(destination).resolve()
    if source == destination or destination.is_relative_to(source) or source.is_relative_to(destination):
        raise ValueError("备份或恢复目录不能与输入目录重叠。")
    if destination.exists():
        raise ValueError("目标目录已存在；请选择新目录，不覆盖现有资料。")
    staging = destination.parent / ("." + destination.name + ".tmp-" + uuid4().hex)
    staging.mkdir(parents=True)
    try:
        yield staging
        if destination.exists():
            raise ValueError("目标目录已经被创建；未覆盖它。")
        staging.rename(destination)
    finally:
        # Only our freshly-created staging sibling is eligible for cleanup.
        resolved = staging.resolve()
        if resolved.parent != destination.parent or not resolved.name.startswith("." + destination.name + ".tmp-"):
            raise ValueError("临时目录不在指定目标范围内。")
        if staging.exists():
            shutil.rmtree(staging)


def _database_check(directory):
    path = _safe_file(directory, "library.sqlite3")
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as db:
        if db.execute("PRAGMA quick_check").fetchone()[0] != "ok" or db.execute("PRAGMA foreign_key_check").fetchall():
            raise ValueError("数据库完整性或外键检查失败。")
        for (snapshot,) in db.execute("SELECT snapshot FROM requests WHERE snapshot IS NOT NULL"):
            raw = _safe_file(directory, snapshot)
            if not raw.is_file() or raw.stem != _hash(raw):
                raise ValueError("原始快照缺失或内容哈希不一致。")


def _persistent_files(root):
    result = {}
    for directory in DIRECTORIES:
        folder = root / directory
        if folder.is_symlink() or folder.is_junction():
            raise ValueError("持久产物目录不能是链接。")
        for path in folder.rglob("*"):
            if path.is_symlink() or path.is_junction():
                raise ValueError("持久产物不能包含链接。")
            if path.is_file():
                name = path.relative_to(root).as_posix()
                result[name] = _hash(_safe_file(root, name))
    config = root / "model-provider.json"
    if config.exists():
        ModelChoice.model_validate_json(config.read_text("utf-8"))
        result["model-provider.json"] = _hash(_safe_file(root, "model-provider.json"))
    return result


def _assert_idle(db):
    for (table,) in db.execute("SELECT name FROM sqlite_master WHERE type='table'"):
        quoted = '"' + table.replace('"', '""') + '"'
        if "status" in {row[1] for row in db.execute("PRAGMA table_info(" + quoted + ")")} and db.execute(
            "SELECT 1 FROM " + quoted + " WHERE status IN ('queued','running') LIMIT 1"
        ).fetchone():
            raise ValueError("资料库仍有排队或运行中的任务；请等待结束后再备份。")


def create_backup(source, destination):
    source, destination = Path(source).resolve(), Path(destination).resolve()
    database = _safe_file(source, "library.sqlite3")
    if not database.is_file():
        raise ValueError("找不到资料库数据库。")
    # A reserved write lock prevents new DB commits during artifact copying.
    # Backup uses a separate read connection; backing up the locking connection
    # itself would wait for its own active write transaction.
    with closing(sqlite3.connect(database.as_uri() + "?mode=rw", uri=True, timeout=5)) as guard:
        guard.execute("BEGIN IMMEDIATE")
        try:
            _assert_idle(guard)
            _database_check(source)
            artifacts = _persistent_files(source)
            with _fresh_destination(source, destination) as staging:
                with (
                    closing(sqlite3.connect(database.as_uri() + "?mode=ro", uri=True)) as reader,
                    closing(sqlite3.connect(staging / "library.sqlite3")) as copy,
                ):
                    reader.backup(copy)
                for name in artifacts:
                    target = staging / name
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(_safe_file(source, name), target)
                if _persistent_files(source) != artifacts or any(
                    _hash(staging / name) != digest for name, digest in artifacts.items()
                ):
                    raise ValueError("复制期间产物变化；本次备份未完成，请重试。")
                _database_check(staging)
                names = ["library.sqlite3", *sorted(artifacts)]
                manifest = {"schema": SCHEMA, "created": now(), "model_calls": 0,
                            "credentials_included": False, "files": [
                                {"file": name, "bytes": (staging / name).stat().st_size,
                                 "sha256": _hash(staging / name)} for name in names
                            ]}
                (staging / "manifest.json").write_text(json.dumps(manifest, indent=2), "utf-8")
        finally:
            guard.rollback()
    return {"status": "succeeded", "directory": str(destination), "files": len(manifest["files"]),
            "bytes": sum(item["bytes"] for item in manifest["files"]),
            "credentials_included": False, "model_calls": 0}


def restore_backup(backup, destination):
    backup, destination = Path(backup).resolve(), Path(destination).resolve()
    manifest = BackupManifest.model_validate_json((backup / "manifest.json").read_text("utf-8"))
    files = [item.model_dump() for item in manifest.files]
    for item in files:
        file = _safe_file(backup, item["file"])
        if not file.is_file() or file.stat().st_size != item["bytes"] or _hash(file) != item["sha256"]:
            raise ValueError("备份文件缺失、大小不符或哈希校验失败。")
    _database_check(backup)
    with _fresh_destination(backup, destination) as staging:
        for item in files:
            target = staging / item["file"]
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(_safe_file(backup, item["file"]), target)
            if _hash(target) != item["sha256"]:
                raise ValueError("恢复复制后的文件校验失败。")
        _database_check(staging)
        if (staging / "model-provider.json").exists():
            ModelChoice.model_validate_json((staging / "model-provider.json").read_text("utf-8"))
    return {"status": "succeeded", "directory": str(destination), "files": len(files),
            "credentials_restored": False, "model_calls": 0}
