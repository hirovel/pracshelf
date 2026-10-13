"""Append-only original-text acceptance, pinned to an extraction attempt.

The interface owns validation, stale-form detection and retry idempotency.
It records a local user's judgment; it does not certify factual correctness.
"""

import json
from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .storage import Store, now


class ReviewConflict(ValueError):
    pass


class ReviewInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    submission_id: str = Field(min_length=1, max_length=80, pattern=r"^[a-zA-Z0-9_-]+$")
    version_id: int = Field(gt=0, strict=True)
    extract_attempt_id: int = Field(gt=0, strict=True)
    previous_review_id: int | None = Field(..., gt=0, strict=True)
    verdict: Literal["accepted", "needs_fix", "pending"]
    body: Literal["unreviewed", "matched", "issues"]
    structure: Literal["unreviewed", "matched", "not_applicable", "issues"]
    date_status: Literal["unreviewed", "confirmed", "unknown"]
    confirmed_date: date | None = None
    date_evidence: str = Field(default="", max_length=2000)
    note: str = Field(default="", max_length=2000)

    @model_validator(mode="after")
    def consistent(self):
        if self.date_status == "confirmed":
            if self.confirmed_date is None or not self.date_evidence:
                raise ValueError("确认日期时需填写日期及依据（原文位置、引文或来源链接）。")
        elif self.confirmed_date is not None or self.date_evidence:
            raise ValueError("只有确认日期时才填写日期与依据；未知不需要猜测。")
        if self.verdict == "accepted" and (
            self.body != "matched"
            or self.structure not in {"matched", "not_applicable"}
            or self.date_status == "unreviewed"
        ):
            raise ValueError("通过前需核对正文与结构，并明确日期已核实或仍未知。")
        if self.verdict in {"needs_fix", "pending"} and not self.note:
            raise ValueError("需修复或暂缓时，请说明问题或尚未核对的内容。")
        return self


class OriginalReview:
    def __init__(self, store: Store, *, origin: Literal["human", "demo"] = "human"):
        self.store, self.origin = store, origin
        store.db.executescript("""
            CREATE TABLE IF NOT EXISTS original_reviews (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                pipeline_id TEXT NOT NULL REFERENCES pipelines(id),
                extract_attempt_id INTEGER NOT NULL REFERENCES pipeline_attempts(id),
                version_id INTEGER NOT NULL REFERENCES versions(id),
                submission_id TEXT NOT NULL,
                created TEXT NOT NULL,
                origin TEXT NOT NULL CHECK(origin IN ('human', 'demo')),
                payload TEXT NOT NULL,
                UNIQUE(pipeline_id, submission_id)
            );
        """)

    def inspect(self, pipeline_id):
        if not self.store.db.execute(
            "SELECT 1 FROM pipelines WHERE id=?", (pipeline_id,)
        ).fetchone():
            raise ValueError("Unknown pipeline")
        attempt = self.store.db.execute(
            "SELECT * FROM pipeline_attempts WHERE pipeline_id=? AND stage='extract' ORDER BY id DESC LIMIT 1",
            (pipeline_id,),
        ).fetchone()
        version_id = None
        if attempt and attempt["status"] == "succeeded":
            version_id = json.loads(attempt["output"])["version_ids"][0]
        history = [
            dict(row) | {"payload": json.loads(row["payload"])}
            for row in self.store.db.execute(
                "SELECT * FROM original_reviews WHERE pipeline_id=? ORDER BY id", (pipeline_id,)
            )
        ]
        relevant = [
            r
            for r in history
            if attempt
            and r["extract_attempt_id"] == attempt["id"]
            and r["version_id"] == version_id
        ]
        return {
            "available": version_id is not None,
            "version_id": version_id,
            "extract_attempt_id": attempt["id"] if version_id is not None else None,
            "origin": self.origin,
            "latest": relevant[-1] if relevant else None,
            "history": history,
        }

    def record(self, pipeline_id, payload: ReviewInput):
        # Validate here too: callers of the Python interface share HTTP's rules.
        payload = ReviewInput.model_validate(payload.model_dump())
        encoded = json.dumps(payload.model_dump(mode="json"), ensure_ascii=False, sort_keys=True)
        with self.store.db:
            self.store.db.execute("BEGIN IMMEDIATE")
            existing = self.store.db.execute(
                "SELECT * FROM original_reviews WHERE pipeline_id=? AND submission_id=?",
                (pipeline_id, payload.submission_id),
            ).fetchone()
            if existing:
                if existing["payload"] != encoded or existing["origin"] != self.origin:
                    raise ReviewConflict("同一提交标识不能用于不同验收内容。")
                return self.inspect(pipeline_id)
            state = self.inspect(pipeline_id)
            if not state["available"]:
                raise ValueError("请先成功提取正文，再记录人工验收。")
            if (payload.version_id, payload.extract_attempt_id) != (
                state["version_id"],
                state["extract_attempt_id"],
            ):
                raise ReviewConflict("提取版本已变化，请刷新并重新核对。")
            previous = state["latest"]["id"] if state["latest"] else None
            if payload.previous_review_id != previous:
                raise ReviewConflict(
                    "已有更新的验收记录，请刷新历史后再修改。当前填写内容尚未保存。"
                )
            self.store.db.execute(
                """INSERT INTO original_reviews(
                    pipeline_id,extract_attempt_id,version_id,submission_id,created,origin,payload
                ) VALUES(?,?,?,?,?,?,?)""",
                (
                    pipeline_id,
                    payload.extract_attempt_id,
                    payload.version_id,
                    payload.submission_id,
                    now(),
                    self.origin,
                    encoded,
                ),
            )
        return self.inspect(pipeline_id)
