"""Evidence-bound comparison snapshots and separate, append-only reader feedback."""

import json
from datetime import date
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from .storage import now


class Review(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    method: str
    pipeline_id: str
    version_id: int | None = None
    title: str
    url: str
    verdict: Literal["shortlist", "reject", "hold", "support"]
    depth: Literal["body_read", "skim", "unavailable"]
    reason: str
    caveat: str
    known: bool = False
    checked_date: date | None = None
    date_basis: str
    quotes: list[str] = Field(default_factory=list, max_length=2)
    facets: list[str] = Field(default_factory=list)


class Feedback(BaseModel):
    model_config = ConfigDict(extra="forbid")
    verdict: Literal["useful", "not_useful", "unread"]
    note: str = Field(default="", max_length=2000)


class Comparison:
    def __init__(self, store):
        self.store = store
        store.db.executescript("""
            CREATE TABLE IF NOT EXISTS comparison_reports(
                id TEXT PRIMARY KEY, created TEXT NOT NULL, output TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS comparison_feedback(
                id INTEGER PRIMARY KEY, report_id TEXT NOT NULL,
                source_id TEXT NOT NULL, created TEXT NOT NULL, output TEXT NOT NULL
            );
        """)

    def publish(self, payload):
        """Publish a model review; human judgments can only enter the feedback path."""
        report = dict(payload)
        if report.get("origin") != "external_model" or report.get("human_feedback"):
            raise ValueError("Model reports must not contain human feedback")
        methods = {m["id"] for m in report["methods"]}
        sources = [Review.model_validate(s) for s in report["sources"]]
        if len({s.id for s in sources}) != len(sources):
            raise ValueError("Duplicate review ID")
        for s in sources:
            if s.method not in methods:
                raise ValueError("Unknown method")
            pipe = self.store.db.execute(
                "SELECT url FROM pipelines WHERE id=?", (s.pipeline_id,)
            ).fetchone()
            if pipe is None or pipe[0] != s.url:
                raise ValueError("Review must match its captured source URL")
            if s.version_id is not None:
                row = self.store.db.execute(
                    "SELECT output FROM pipeline_attempts WHERE pipeline_id=? "
                    "AND stage='extract' AND status='succeeded' ORDER BY id DESC LIMIT 1",
                    (s.pipeline_id,),
                ).fetchone()
                if row is None or s.version_id not in json.loads(row[0])["version_ids"]:
                    raise ValueError("Review version must belong to this capture")
                text = self.store.version(s.version_id)["document"]["text"]
                if any(not q.strip() or q not in text for q in s.quotes):
                    raise ValueError("Review quote does not match saved original")
            elif s.quotes or s.depth != "unavailable":
                raise ValueError("Unavailable original cannot be marked read")
            if s.verdict == "shortlist":
                if s.depth != "body_read" or not s.quotes or s.checked_date is None:
                    raise ValueError(
                        "Shortlist requires a read original, evidence and checked date"
                    )
                age = (date.fromisoformat(report["as_of"]) - s.checked_date).days
                if not 0 <= age <= 90:
                    raise ValueError("Shortlist is outside this comparison's 90 day window")
        if not any(
            s.method == report["selected_method"] and s.verdict == "shortlist" for s in sources
        ):
            raise ValueError("Selected method must have a reviewed shortlist")
        report.update(
            id=uuid4().hex, created=now(), sources=[s.model_dump(mode="json") for s in sources]
        )
        with self.store.db:
            self.store.db.execute(
                "INSERT INTO comparison_reports VALUES(?,?,?)",
                (report["id"], report["created"], json.dumps(report, ensure_ascii=False)),
            )
        return self.get(report["id"])

    def get(self, report_id=None):
        row = self.store.db.execute(
            "SELECT output FROM comparison_reports WHERE id=?"
            if report_id
            else "SELECT output FROM comparison_reports ORDER BY rowid DESC LIMIT 1",
            (report_id,) if report_id else (),
        ).fetchone()
        if row is None:
            if report_id:
                raise ValueError("Unknown comparison")
            return None
        report = json.loads(row[0])
        report["human_feedback"] = [
            dict(r) | {"output": json.loads(r["output"])}
            for r in self.store.db.execute(
                "SELECT * FROM comparison_feedback WHERE report_id=? ORDER BY id", (report["id"],)
            )
        ]
        for method in report["methods"]:
            rows = [s for s in report["sources"] if s["method"] == method["id"]]
            method["reviewed_sources"] = len(rows)
            method["body_read"] = sum(s["depth"] == "body_read" for s in rows)
            method["shortlisted"] = sum(s["verdict"] == "shortlist" for s in rows)
            method["new_shortlisted"] = sum(
                s["verdict"] == "shortlist" and not s["known"] for s in rows
            )
        return report

    def feedback(self, report_id, source_id, payload):
        report = self.get(report_id)
        if source_id not in {s["id"] for s in report["sources"]}:
            raise ValueError("Unknown comparison source")
        value = Feedback.model_validate(payload)
        with self.store.db:
            self.store.db.execute(
                "INSERT INTO comparison_feedback(report_id,source_id,created,output) VALUES(?,?,?,?)",
                (report_id, source_id, now(), value.model_dump_json()),
            )
        return self.get(report_id)
