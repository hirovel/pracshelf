"""Local publication snapshots: explicit fields, immutable previews, version-bound approval.

prepare / approve / export / inspect share one interface for the UI and tests.
No network, model call or deployment occurs here. Approval is a local submission,
not an authenticated identity or a certification of the source's claims.
"""

import hashlib
import json
from typing import Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field

from .curation import CATEGORIES, FRESHNESS, TRIALS, Curation
from .review import ReviewConflict
from .storage import now


def encode(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n"


def digest(value):
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


class PreviewInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version_id: int = Field(gt=0, strict=True)
    curation_id: int | None = Field(..., gt=0, strict=True)
    previous_preview_id: int | None = Field(..., gt=0, strict=True)
    title: str = Field(default="", max_length=240)
    include_note: bool = True
    include_purpose: bool = False
    include_quote: bool = False


class ApprovalInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    content_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    content_reviewed: Literal[True]
    quote_rights_confirmed: bool = False


PRIVATE_FIELDS = [
    "完整原文与网页响应",
    "全文翻译与中英对照",
    "模型提示词、响应与调用日志",
    "未选中的私人笔记、试用详情与技术验收备注",
    "内部版本关联、文件路径与密钥",
]


class Publication:
    def __init__(self, store, *, origin="human"):
        if origin not in {"human", "demo"}:
            raise ValueError("Unsupported publication origin")
        self.store, self.origin = store, origin
        self.curation = Curation(store, origin=origin)
        store.db.executescript("""
            CREATE TABLE IF NOT EXISTS publication_previews (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                document_key TEXT NOT NULL, pipeline_id TEXT NOT NULL,
                created TEXT NOT NULL, binding TEXT NOT NULL, options TEXT NOT NULL,
                public_note TEXT NOT NULL, content_hash TEXT NOT NULL, blockers TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS publication_approvals (
                preview_id INTEGER PRIMARY KEY REFERENCES publication_previews(id),
                created TEXT NOT NULL, origin TEXT NOT NULL, content_hash TEXT NOT NULL,
                quote_rights_confirmed INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS publication_exports (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                preview_id INTEGER NOT NULL REFERENCES publication_previews(id),
                mode TEXT NOT NULL, created TEXT NOT NULL, content_hash TEXT NOT NULL,
                body TEXT NOT NULL, UNIQUE(preview_id, mode)
            );
            CREATE INDEX IF NOT EXISTS publication_document
                ON publication_previews(document_key, id);
        """)

    def _context(self, run_id):
        state = self.curation.inspect(run_id)
        if not state["available"]:
            raise ValueError("请先成功保存完整原文。")
        version = self.store.version(state["version_id"])
        review = self.curation.originals.inspect(run_id)
        record = state["latest"]
        binding = {
            "version_id": version["id"],
            "curation_id": record["id"] if record else None,
            "review_id": review["latest"]["id"] if review["latest"] else None,
            "extract_attempt_id": review["extract_attempt_id"],
        }
        return state, version, review, binding

    def _latest(self, key):
        return self.store.db.execute(
            "SELECT * FROM publication_previews WHERE document_key=? ORDER BY id DESC LIMIT 1",
            (key,),
        ).fetchone()

    def _current(self, row):
        state, version, _, binding = self._context(row["pipeline_id"])
        latest = self._latest(version["document_key"])
        return (
            not state["older_version"]
            and binding == json.loads(row["binding"])
            and latest["id"] == row["id"]
        )

    def _row(self, run_id, preview_id):
        _, version, _, _ = self._context(run_id)
        row = self.store.db.execute(
            "SELECT * FROM publication_previews WHERE id=? AND document_key=?",
            (preview_id, version["document_key"]),
        ).fetchone()
        if not row:
            raise ValueError("找不到此资料的预览。")
        return row

    def inspect(self, run_id):
        state, version, _, binding = self._context(run_id)
        history = []
        for row in self.store.db.execute(
            "SELECT * FROM publication_previews WHERE document_key=? ORDER BY id DESC",
            (version["document_key"],),
        ).fetchall():
            approval = self.store.db.execute(
                "SELECT * FROM publication_approvals WHERE preview_id=?", (row["id"],)
            ).fetchone()
            exports = [
                dict(r)
                for r in self.store.db.execute(
                    "SELECT id,mode,created,content_hash FROM publication_exports WHERE preview_id=?",
                    (row["id"],),
                )
            ]
            history.append(
                {
                    "id": row["id"],
                    "created": row["created"],
                    "binding": json.loads(row["binding"]),
                    "options": json.loads(row["options"]),
                    "public_note": json.loads(row["public_note"]),
                    "content_hash": row["content_hash"],
                    "blockers": json.loads(row["blockers"]),
                    "current": self._current(row),
                    "approval": dict(approval) if approval else None,
                    "exports": exports,
                }
            )
        personal = state["latest"]["payload"] if state["latest"] else {}
        return {
            "run_id": run_id,
            "input": binding,
            "title": version["document"]["title"],
            "available_fields": {k: bool(personal.get(k)) for k in ("note", "purpose", "quote")},
            "private_fields": PRIVATE_FIELDS,
            "history": history,
            "latest": history[0] if history else None,
            "deployment": "not_deployed",
            "model_calls": 0,
        }

    def prepare(self, run_id, payload: PreviewInput):
        payload = PreviewInput.model_validate(payload.model_dump())
        with self.store.db:
            self.store.db.execute("BEGIN IMMEDIATE")
            state, version, review, binding = self._context(run_id)
            if (payload.version_id, payload.curation_id) != (
                binding["version_id"],
                binding["curation_id"],
            ):
                raise ReviewConflict("原文或笔记已变化，请刷新后重新生成预览。")
            if state["older_version"]:
                raise ReviewConflict("已有更新的原文版本，请从资料库打开新版重新判断。")
            options = payload.model_dump(
                exclude={"previous_preview_id", "version_id", "curation_id"}
            )
            latest = self._latest(version["document_key"])
            if (
                latest
                and latest["binding"] == encode(binding)
                and latest["options"] == encode(options)
            ):
                return self.inspect(run_id)
            if payload.previous_preview_id != (latest["id"] if latest else None):
                raise ReviewConflict("另一页面已生成新预览，请刷新后比较。")
            doc = version["document"]
            url = urlsplit(doc["url"])
            if (
                url.scheme not in {"http", "https"}
                or not url.hostname
                or url.username
                or url.password
            ):
                raise ValueError("来源不是可公开的 HTTP(S) 链接。")
            record = state["latest"]
            p = record["payload"] if record else {}
            checked = review["latest"]["payload"] if review["latest"] else {}
            # A positive allowlist, never a filtered copy of an internal document/trace.
            note = {
                "note_id": "note-" + digest(version["document_key"])[:20],
                "title": payload.title.strip() or doc["title"],
                "source_url": doc["url"],
                "source_date": checked.get("confirmed_date"),
                "source_date_status": "confirmed" if checked.get("confirmed_date") else "unknown",
                "category": CATEGORIES[p.get("category", "unclassified")],
                "note": p.get("note", "") if payload.include_note else "",
                "purpose": p.get("purpose", "") if payload.include_purpose else "",
                "quote": p.get("quote", "") if payload.include_quote else "",
                "applicability": p.get("applicability", ""),
                "freshness": FRESHNESS[p.get("freshness", "unknown")],
                "checked_on": p.get("checked_on"),
                "trial": TRIALS[p.get("trial", "not_tried")],
                "content_origin": record["origin"] if record else "no_personal_note",
                "note_updated_at": record["created"] if record else None,
            }
            blockers = []
            if p.get("decision") == "archived":
                blockers.append("这篇资料已归档，请先恢复后再准备公开内容。")
            if not note["note"]:
                blockers.append("需要本人保存非空笔记，并选择将它纳入预览。")
            if note["quote"] and note["quote"] not in doc["text"]:
                blockers.append("摘录未逐字匹配当前原文。")
            public = encode(note)
            self.store.db.execute(
                """INSERT INTO publication_previews(document_key,pipeline_id,created,binding,
                   options,public_note,content_hash,blockers) VALUES(?,?,?,?,?,?,?,?)""",
                (
                    version["document_key"],
                    run_id,
                    now(),
                    encode(binding),
                    encode(options),
                    public,
                    digest(public),
                    encode(blockers),
                ),
            )
        return self.inspect(run_id)

    def approve(self, run_id, preview_id, payload: ApprovalInput):
        payload = ApprovalInput.model_validate(payload.model_dump())
        with self.store.db:
            self.store.db.execute("BEGIN IMMEDIATE")
            row = self._row(run_id, preview_id)
            if not self._current(row) or row["content_hash"] != payload.content_hash:
                raise ReviewConflict("预览已过期或内容不一致，请重新预览并核对。")
            blockers = json.loads(row["blockers"])
            if blockers:
                raise ValueError("无法确认：" + " ".join(blockers))
            if json.loads(row["public_note"])["quote"] and not payload.quote_rights_confirmed:
                raise ValueError("包含摘录时，请先确认其公开使用依据。")
            self.store.db.execute(
                "INSERT OR IGNORE INTO publication_approvals VALUES(?,?,?,?,?)",
                (
                    preview_id,
                    now(),
                    self.origin,
                    payload.content_hash,
                    int(payload.quote_rights_confirmed),
                ),
            )
        return self.inspect(run_id)

    def export(self, run_id, preview_id, *, mode="preview"):
        if mode not in {"preview", "approved"}:
            raise ValueError("Unknown export mode")
        with self.store.db:
            self.store.db.execute("BEGIN IMMEDIATE")
            row = self._row(run_id, preview_id)
            if not self._current(row):
                raise ReviewConflict("预览已过期，请重新生成后导出。历史内容仍可查看。")
            approval = self.store.db.execute(
                "SELECT * FROM publication_approvals WHERE preview_id=?", (preview_id,)
            ).fetchone()
            if mode == "approved" and not approval:
                raise ValueError("尚未确认这一版；只能导出未批准的预览文件。")
            note = json.loads(row["public_note"])
            body = encode(
                {
                    "schema": "public-note-snapshot/v1",
                    "mode": mode,
                    "approved": mode == "approved",
                    "deployed": False,
                    "demo": note["content_origin"] == "demo" or self.origin == "demo",
                    "content_hash": row["content_hash"],
                    "note": note,
                }
            )
            self.store.db.execute(
                """INSERT OR IGNORE INTO publication_exports
                   (preview_id,mode,created,content_hash,body) VALUES(?,?,?,?,?)""",
                (preview_id, mode, now(), digest(body), body),
            )
            stored = self.store.db.execute(
                "SELECT body FROM publication_exports WHERE preview_id=? AND mode=?",
                (preview_id, mode),
            ).fetchone()[0]
        return stored
