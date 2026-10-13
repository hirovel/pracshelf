"""Version-bound personal selection and use records, independent of translation.

inspect / record own evidence validation, append-only history, stale-form
conflicts and retry idempotency. They never infer a human judgment from AI output.
"""

import json
from datetime import UTC, date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .review import OriginalReview, ReviewConflict
from .storage import now

CATEGORIES = {
    "unclassified": "未分类",
    "skill": "Skills",
    "prompt": "提示词",
    "workflow": "工作流",
    "knowledge": "知识",
}
DECISIONS = {"inbox": "待整理", "kept": "已保留", "archived": "已归档"}
FRESHNESS = {"unknown": "适用性待核实", "current": "在记录条件下适用", "outdated": "已不适用"}
TRIALS = {
    "not_tried": "未试用",
    "useful": "试用有帮助",
    "mixed": "部分有帮助",
    "not_useful": "试用无帮助",
    "not_applicable": "阅读材料，无需试用",
}


class CurationInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    submission_id: str = Field(min_length=1, max_length=80, pattern=r"^[a-zA-Z0-9_-]+$")
    version_id: int = Field(gt=0, strict=True)
    previous_id: int | None = Field(..., gt=0, strict=True)
    category: Literal["unclassified", "skill", "prompt", "workflow", "knowledge"] = "unclassified"
    decision: Literal["inbox", "kept", "archived"] = "inbox"
    purpose: str = Field(default="", max_length=1000)
    quote: str = Field(default="", max_length=4000)
    freshness: Literal["unknown", "current", "outdated"] = "unknown"
    checked_on: date | None = None
    applicability: str = Field(default="", max_length=2000)
    trial: Literal["not_tried", "useful", "mixed", "not_useful", "not_applicable"] = "not_tried"
    trial_note: str = Field(default="", max_length=2000)
    note: str = Field(default="", max_length=16000)

    @model_validator(mode="after")
    def consistent(self):
        if self.decision == "kept" and (self.category == "unclassified" or not self.purpose):
            raise ValueError("保留资料前请选择类别，并写明它解决什么问题或值得留下的原因。")
        if self.decision == "archived" and not self.purpose:
            raise ValueError("归档前请简短记录原因，之后仍可恢复。")
        if self.freshness != "unknown" and (not self.checked_on or not self.applicability):
            raise ValueError("判断适用性时需记录核查日期、具体版本或条件及判断依据。")
        if self.checked_on and self.checked_on > datetime.now(UTC).date():
            raise ValueError("核查日期不能在未来。")
        if self.trial in {"useful", "mixed", "not_useful"} and not self.trial_note:
            raise ValueError("已试用时请记录实际任务、环境和观察到的结果。")
        return self


class Curation:
    def __init__(self, store, *, origin="human"):
        if origin not in {"human", "demo"}:
            raise ValueError("Unsupported curation origin")
        self.store, self.origin = store, origin
        self.originals = OriginalReview(store)
        store.db.executescript("""
            CREATE TABLE IF NOT EXISTS curation_records (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                document_key TEXT NOT NULL,
                version_id INTEGER NOT NULL REFERENCES versions(id),
                pipeline_id TEXT NOT NULL REFERENCES pipelines(id),
                submission_id TEXT NOT NULL,
                created TEXT NOT NULL,
                origin TEXT NOT NULL CHECK(origin IN ('human','demo')),
                payload TEXT NOT NULL,
                UNIQUE(version_id, submission_id)
            );
            CREATE INDEX IF NOT EXISTS curation_document ON curation_records(document_key, id);
        """)

    def inspect(self, pipeline_id):
        original = self.originals.inspect(pipeline_id)
        state = {
            "available": original["available"],
            "version_id": original["version_id"],
            "latest": None,
            "history": [],
            "origin": self.origin,
            "older_version": False,
        }
        if not original["available"]:
            return state
        version = self.store.version(original["version_id"])
        history = [
            dict(row) | {"payload": json.loads(row["payload"])}
            for row in self.store.db.execute(
                "SELECT * FROM curation_records WHERE document_key=? ORDER BY id",
                (version["document_key"],),
            )
        ]
        relevant = [row for row in history if row["version_id"] == version["id"]]
        newest = self.store.db.execute(
            "SELECT MAX(id) FROM versions WHERE document_key=?", (version["document_key"],)
        ).fetchone()[0]
        return state | {
            "latest": relevant[-1] if relevant else None,
            "history": history,
            "older_version": newest != version["id"],
        }

    def record(self, pipeline_id, payload: CurationInput):
        payload = CurationInput.model_validate(payload.model_dump())
        encoded = json.dumps(payload.model_dump(mode="json"), ensure_ascii=False, sort_keys=True)
        with self.store.db:
            self.store.db.execute("BEGIN IMMEDIATE")
            state = self.inspect(pipeline_id)
            if not state["available"]:
                raise ValueError("成功提取原文后才能保存整理记录。")
            if state["version_id"] != payload.version_id:
                raise ReviewConflict("原文版本不一致，请刷新后重新核对。草稿尚未保存。")
            existing = self.store.db.execute(
                "SELECT * FROM curation_records WHERE version_id=? AND submission_id=?",
                (payload.version_id, payload.submission_id),
            ).fetchone()
            if existing:
                if existing["payload"] != encoded or existing["origin"] != self.origin:
                    raise ReviewConflict("同一提交标识不能保存不同的整理内容。")
                return state
            if payload.previous_id != (state["latest"]["id"] if state["latest"] else None):
                raise ReviewConflict("这份原文已有更新的整理记录。请查看新记录后再合并草稿。")
            version = self.store.version(payload.version_id)
            if payload.quote and payload.quote not in version["document"]["text"]:
                raise ValueError("原文依据未逐字匹配保存的正文，请复制原文文本后重试。")
            self.store.db.execute(
                """INSERT INTO curation_records(document_key,version_id,pipeline_id,
                   submission_id,created,origin,payload) VALUES(?,?,?,?,?,?,?)""",
                (
                    version["document_key"],
                    payload.version_id,
                    pipeline_id,
                    payload.submission_id,
                    now(),
                    self.origin,
                    encoded,
                ),
            )
        return self.inspect(pipeline_id)


def export_note(document, state):
    """A portable personal note, not a republication of the full source."""
    record = state["latest"]
    if not record:
        raise ValueError("这份原文尚无整理记录。")
    p = record["payload"]
    if not p["note"].strip():
        raise ValueError("请先保存笔记正文，再导出 Markdown。")
    lines = [
        f"# {document['title']}",
        "",
        f"来源：{document['url']}",
        f"原文版本：{state['version_id']}",
        "记录性质：" + ("演示记录" if record["origin"] == "demo" else "本人填写"),
        f"保存时间：{record['created']}",
        "",
        p["note"],
    ]
    if p["category"] != "unclassified":
        lines.extend(["", f"分类：{CATEGORIES[p['category']]}"])
    if p["freshness"] != "unknown":
        lines.extend(["", f"适用性：{FRESHNESS[p['freshness']]} · 核查日期：{p['checked_on']}"])
    if p["trial"] != "not_tried":
        lines.extend(["", f"试用状态：{TRIALS[p['trial']]}"])
    for title, value in [
        ("用途或取舍原因", p["purpose"]),
        ("原文依据", p["quote"]),
        ("适用条件与依据", p["applicability"]),
        ("试用记录", p["trial_note"]),
    ]:
        if value:
            lines.extend(["", f"## {title}", "", value])
    lines.append("")
    return "\n".join(lines)
