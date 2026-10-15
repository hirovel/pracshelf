"""Candidate intake and bounded, version-pinned screening; no automatic curation."""

import json
from datetime import UTC, date, datetime, timedelta
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from .collector import web_url
from .model_config import selected_model
from .pipeline import INTERRUPTED_CAPTURE_REASON, capture_expired
from .review import ReviewConflict
from .storage import now
from .value_preview import prepare as prepare_preview
from .value_preview import revalidate
from .value_preview import run as run_preview


class ImportCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    submission_id: str = Field(min_length=1, max_length=80)
    url: str = Field(min_length=1, max_length=2000)
    goal: str = Field(min_length=1, max_length=1500)
    origin: Literal["user_link", "agent_supplied", "rss", "web_search", "demo"]
    source_note: str = Field(min_length=1, max_length=1500)
    as_of: date
    max_age_days: int = Field(default=90, ge=1, le=366)


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def date_check(document, as_of, max_age_days):
    published = document.get("published")
    try:
        age = (date.fromisoformat(as_of) - date.fromisoformat(published)).days
        status = (
            "future_date"
            if age < 0
            else "within_window"
            if age <= max_age_days
            else "outside_window"
        )
    except (ValueError, TypeError):
        age, status = None, "unknown_date"
    return {
        "published": published,
        "age_days": age,
        "status": status,
        "provenance": "网页提取元数据，未独立核实；不从 URL 或抓取时间推断",
        "as_of": as_of,
        "max_age_days": max_age_days,
    }


class Candidates:
    def __init__(self, pipeline, *, transport=None):
        self.pipeline, self.store, self.transport = pipeline, pipeline.store, transport
        self.store.db.executescript("""
            CREATE TABLE IF NOT EXISTS candidates (
                id TEXT PRIMARY KEY, submission_id TEXT UNIQUE NOT NULL,
                created TEXT NOT NULL, input TEXT NOT NULL,
                pipeline_id TEXT NOT NULL REFERENCES pipelines(id),
                version_id INTEGER REFERENCES versions(id)
            );
            CREATE TABLE IF NOT EXISTS candidate_screenings (
                id INTEGER PRIMARY KEY, candidate_id TEXT NOT NULL REFERENCES candidates(id),
                submission_id TEXT NOT NULL, started TEXT NOT NULL, finished TEXT,
                status TEXT NOT NULL, input TEXT NOT NULL, response TEXT, output TEXT, error TEXT,
                UNIQUE(candidate_id,submission_id)
            );
            CREATE UNIQUE INDEX IF NOT EXISTS one_candidate_screening
                ON candidate_screenings(candidate_id) WHERE status='running';
            CREATE UNIQUE INDEX IF NOT EXISTS one_preview_input
                ON candidate_screenings(json_extract(input,'$.cache_key'))
                WHERE status='running' AND json_extract(input,'$.cache_key') IS NOT NULL;
            CREATE TABLE IF NOT EXISTS candidate_processing (
                id INTEGER PRIMARY KEY, candidate_id TEXT NOT NULL REFERENCES candidates(id),
                pipeline_id TEXT NOT NULL REFERENCES pipelines(id), submission_id TEXT NOT NULL,
                started TEXT NOT NULL, finished TEXT, status TEXT NOT NULL,
                error TEXT, UNIQUE(candidate_id,submission_id)
            );
            CREATE UNIQUE INDEX IF NOT EXISTS one_candidate_processing
                ON candidate_processing(pipeline_id) WHERE status='running';
        """)

    def import_link(self, payload):
        value = ImportCandidate.model_validate(payload)
        value.url = web_url(value.url)
        if value.as_of > datetime.now(UTC).date():
            raise ValueError("审核基准日期不能在未来。")
        data = value.model_dump(mode="json")
        previous = self.store.db.execute(
            "SELECT * FROM candidates WHERE submission_id=?", (value.submission_id,)
        ).fetchone()
        if previous:
            if previous["input"] != encoded(data):
                raise ReviewConflict("同一提交标识不能导入不同内容。")
            return self.inspect(previous["id"])
        # Exact URL reuse preserves existing capture history; changed source needs a new capture.
        row = self.store.db.execute(
            "SELECT id FROM pipelines WHERE url=? ORDER BY created DESC LIMIT 1", (value.url,)
        ).fetchone()
        pipeline_id = row[0] if row else self.pipeline.create(value.url)["id"]
        candidate_id = uuid4().hex
        with self.store.db:
            self.store.db.execute(
                "INSERT INTO candidates VALUES(?,?,?,?,?,NULL)",
                (candidate_id, value.submission_id, now(), encoded(data), pipeline_id),
            )
        return self.inspect(candidate_id)

    def list(self, *, q="", state="all", freshness="all", origin="all"):
        if state not in {"all", "saved", "failed", "unscreened"}:
            raise ValueError("未知的采集筛选条件。")
        if freshness not in {
            "all",
            "within_window",
            "outside_window",
            "unknown_date",
            "future_date",
        }:
            raise ValueError("未知的日期筛选条件。")
        if origin not in {"all", "user_link", "agent_supplied", "rss", "web_search", "demo"}:
            raise ValueError("未知的来源筛选条件。")
        rows = [
            self.inspect(row[0], include_trace=False)
            for row in self.store.db.execute("SELECT id FROM candidates ORDER BY created DESC")
        ]
        query = q.strip().casefold()
        return [
            r
            for r in rows
            if (
                not query
                or query
                in " ".join(
                    [
                        (r["document"] or {}).get("title", "") or "",
                        r["input"]["url"],
                        r["input"]["source_note"],
                        r["input"]["goal"],
                    ]
                ).casefold()
            )
            and (state == "all" or state in r["facets"])
            and (freshness == "all" or freshness == r["date_check"]["status"])
            and (origin == "all" or origin == r["input"]["origin"])
        ]

    def inspect(self, candidate_id, *, include_trace=True):
        row = self.store.db.execute(
            "SELECT * FROM candidates WHERE id=?", (candidate_id,)
        ).fetchone()
        if not row:
            raise ValueError("候选资料不存在。")
        result = dict(row)
        result["input"] = json.loads(result["input"])
        document = self.store.version(row["version_id"])["document"] if row["version_id"] else None
        result["document"] = (
            {key: document.get(key) for key in ("title", "url", "published")}
            | {"characters": len(document["text"])}
            if document
            else None
        )
        attempts = [
            self._screening(r)
            for r in self.store.db.execute(
                "SELECT * FROM candidate_screenings WHERE candidate_id=? ORDER BY id",
                (candidate_id,),
            )
        ]
        result["latest"] = attempts[-1] if attempts else None
        result["attempts"] = attempts if include_trace else []
        if not include_trace and result["latest"]:
            result["latest"] = {
                k: v for k, v in result["latest"].items() if k not in {"input", "response"}
            }
        choice = selected_model(self.store.root)
        result["configured"] = choice.ready
        result["model_configuration"] = choice.public()
        result["status"] = (
            attempts[-1]["status"] if attempts else "ready" if document else "awaiting_capture"
        )
        result["capture"] = self.pipeline.inspect(row["pipeline_id"])["stages"][:2]
        result["processing"] = self._processing_state(result)
        # Current metadata checks do not depend on a model call or an old screening date.
        result["date_check"] = date_check(
            document or {}, datetime.now(UTC).date().isoformat(), result["input"]["max_age_days"]
        )
        result["preview"] = (
            self._preview_state(result, include_trace=include_trace) if document else None
        )
        result["screening_action"] = result["preview"]["action"] if document else None
        result["facets"] = []
        if document:
            result["facets"].append("saved")
        if result["processing"]["status"] == "failed":
            result["facets"].append("failed")
        if not attempts:
            result["facets"].append("unscreened")
        if not include_trace:
            result.pop("capture")
        return result

    def _screening(self, row):
        result = dict(row)
        for key in ("input", "response", "output"):
            result[key] = json.loads(result[key]) if result[key] else None
        if result["output"]:
            source = self.store.version(result["input"]["version_id"])["document"]["text"]
            result["current_output"] = revalidate(result["input"], result["output"], source)
        return result

    def _preview_state(self, state, *, include_trace=True):
        choice = selected_model(self.store.root)
        model = choice.model
        incoming = prepare_preview(
            self.store.version(state["version_id"]), state["input"]["goal"], model,
            provider=choice.provider, effort=choice.effort,
        )
        rows = [self._screening(row) for row in self.store.db.execute(
            "SELECT * FROM candidate_screenings WHERE json_extract(input,'$.cache_key')=? ORDER BY id DESC",
            (incoming["cache_key"],),
        ).fetchall()]
        valid = next(
            (
                r
                for r in rows
                if r["status"] == "completed"
                and r["current_output"]["quote_alignment"] in {"passed", "no_evidence"}
            ),
            None,
        )
        row = valid or (rows[0] if rows else None)
        expired = bool(
            row
            and row["status"] == "running"
            and datetime.fromisoformat(row["started"]) < datetime.now(UTC) - timedelta(minutes=5)
        )
        latest = row
        if latest and not include_trace:
            latest.pop("input")
            latest.pop("response")
        return {
            **{k: v for k, v in incoming.items() if k != "request"},
            "model": model,
            "goal": state["input"]["goal"],
            "expired": expired,
            "max_output_tokens": incoming["request"].get("max_tokens"),
            "latest": latest,
            "reused_from_other_candidate": bool(row and row["candidate_id"] != state["id"]),
            "action": None
            if valid or (row and row["status"] == "running" and not expired)
            else "retry"
            if row
            else "start",
        }

    def _processing_state(self, state):
        """One read model for page controls; an uncertain judgment is not a failed task."""
        runs = [
            dict(r)
            for r in self.store.db.execute(
                "SELECT * FROM candidate_processing WHERE candidate_id=? ORDER BY id",
                (state["id"],),
            )
        ]
        stages = []
        for s in state["capture"]:
            last = s["attempts"][-1] if s["attempts"] else None
            stages.append(
                {
                    "id": s["id"],
                    "label": s["label"],
                    "status": s["status"],
                    "attempts": len(s["attempts"]),
                    "error": s.get("interruption_reason") or ((last["output"] or {}).get("error") if last else None),
                }
            )
        active = next((s for s in stages if s["status"] != "succeeded"), None)
        expired = bool(runs and runs[-1]["status"] == "running" and capture_expired(runs[-1]["started"]))
        running = (runs and runs[-1]["status"] == "running" and not expired) or any(
            s["status"] == "running" for s in stages
        )
        failed = expired or (runs and runs[-1]["status"] == "failed") or any(
            s["status"] in {"failed", "interrupted"} for s in stages
        )
        status = (
            "running" if running else "completed" if not active else "failed" if failed else "ready"
        )
        return {
            "status": status,
            "stage": active["id"] if active else None,
            "error": None
            if status == "completed"
            else (INTERRUPTED_CAPTURE_REASON if expired else (runs[-1]["error"] if runs else None)) or (active["error"] if active else None),
            "action": None if running or not active else "retry" if failed else "start",
            "stages": stages,
            "runs": runs,
        }

    def submit(self, payload):
        """Add a link and save the original without a model call. Replaying the submission does not retry failures."""
        state = self.import_link(payload)
        already_saved = all(s["status"] == "succeeded" for s in state["capture"])
        result = self.process(state["id"], state["input"]["submission_id"])
        result["submission_result"] = {
            "original": ("reused" if already_saved else "saved") if result["version_id"] else "not_saved",
            "model_tasks_started": 0,
        }
        return result

    def process(self, candidate_id, submission_id):
        """Resume incomplete work under one durable submission and pipeline lock.

        Failed tasks need a new submission. Existing successful stages and complete
        captures are reused. Screening is an explicit, separate action.
        """
        if not isinstance(submission_id, str) or not 1 <= len(submission_id) <= 80:
            raise ValueError("需要有效的处理提交标识。")
        with self.store.db:
            self.store.db.execute("BEGIN IMMEDIATE")
            state = self.inspect(candidate_id)
            if self.store.db.execute(
                "SELECT 1 FROM candidate_processing WHERE candidate_id=? AND submission_id=?",
                (candidate_id, submission_id),
            ).fetchone():
                return state
            if state["version_id"]:
                return state
            for run in self.store.db.execute(
                "SELECT id,started FROM candidate_processing WHERE pipeline_id=? AND status='running'",
                (state["pipeline_id"],),
            ).fetchall():
                if capture_expired(run["started"]):
                    self.store.db.execute(
                        "UPDATE candidate_processing SET status='failed',finished=?,error=? WHERE id=? AND status='running'",
                        (now(), INTERRUPTED_CAPTURE_REASON, run["id"]),
                    )
            if (
                state["processing"]["status"] == "running"
                or self.store.db.execute(
                    "SELECT 1 FROM candidate_processing WHERE pipeline_id=? AND status='running'",
                    (state["pipeline_id"],),
                ).fetchone()
            ):
                raise ReviewConflict("此资料已有处理正在运行，可刷新查看进度。")
            run_id = self.store.db.execute(
                """INSERT INTO candidate_processing(candidate_id,pipeline_id,submission_id,started,status)
                   VALUES(?,?,?,?,'running')""",
                (candidate_id, state["pipeline_id"], submission_id, now()),
            ).lastrowid
        status, error = "failed", None
        try:
            state = self.capture(candidate_id)
            if not state["version_id"]:
                failed = next(
                    s for s in state["processing"]["stages"] if s["status"] != "succeeded"
                )
                error = failed["error"] or f"{failed['label']}未完成。"
            else:
                status = "completed"
        except Exception as exc:  # noqa: BLE001 - persist the orchestration failure, leave successful stages intact
            error = f"处理未完成：{type(exc).__name__}。已有阶段记录保留，可查看详情。"
        with self.store.db:
            self.store.db.execute(
                "UPDATE candidate_processing SET status=?,finished=?,error=? WHERE id=? AND status='running'",
                (status, now(), error, run_id),
            )
        return self.inspect(candidate_id)

    def capture(self, candidate_id):
        state = self.inspect(candidate_id)
        if state["version_id"]:
            return state
        for name in ("fetch", "extract"):
            current = self.pipeline.inspect(state["pipeline_id"])
            stage = next(s for s in current["stages"] if s["id"] == name)
            if stage["status"] != "succeeded":
                current = self.pipeline.run(state["pipeline_id"], name)
                stage = next(s for s in current["stages"] if s["id"] == name)
                if stage["status"] != "succeeded":
                    return self.inspect(candidate_id)
        version = self.pipeline.reviews.inspect(state["pipeline_id"])["version_id"]
        with self.store.db:
            self.store.db.execute(
                "UPDATE candidates SET version_id=? WHERE id=? AND version_id IS NULL",
                (version, candidate_id),
            )
        return self.inspect(candidate_id)

    def review(self, candidate_id, submission_id):
        if not isinstance(submission_id, str) or not 1 <= len(submission_id) <= 80:
            raise ValueError("需要有效的审核提交标识。")
        state = self.inspect(candidate_id)
        if not state["version_id"]:
            raise ValueError("请先保存完整原文。")
        version = self.store.version(state["version_id"])
        doc = version["document"]
        choice = selected_model(self.store.root)
        model = choice.model
        checks = date_check(doc, state["input"]["as_of"], state["input"]["max_age_days"])
        incoming = prepare_preview(version, state["input"]["goal"], model,
                                   provider=choice.provider, effort=choice.effort)
        incoming["date_check"] = checks
        with self.store.db:
            self.store.db.execute("BEGIN IMMEDIATE")
            duplicate = self.store.db.execute(
                "SELECT id FROM candidate_screenings WHERE candidate_id=? AND submission_id=?",
                (candidate_id, submission_id),
            ).fetchone()
            if duplicate:
                return self.inspect(candidate_id)
            preview = self._preview_state(state)
            if (
                preview["latest"]
                and preview["latest"]["status"] == "completed"
                and preview["action"] is None
            ):
                return self.inspect(candidate_id)
            # A bounded call times out after 120s; expired records remain visible, never auto-retried.
            cutoff = (datetime.now(UTC) - timedelta(minutes=5)).isoformat()
            self.store.db.execute(
                """UPDATE candidate_screenings SET status='interrupted',finished=?,
                error='运行超过五分钟，结果未知；本次由新的手动请求重试。'
                WHERE (candidate_id=? OR json_extract(input,'$.cache_key')=?) AND status='running' AND julianday(started)<julianday(?)""",
                (now(), candidate_id, incoming["cache_key"], cutoff),
            )
            if self.store.db.execute(
                "SELECT 1 FROM candidate_screenings WHERE (candidate_id=? OR json_extract(input,'$.cache_key')=?) AND status='running'",
                (candidate_id, incoming["cache_key"]),
            ).fetchone():
                raise ReviewConflict("已有审核在运行，等待完成后再操作。")
            attempt_id = self.store.db.execute(
                """INSERT INTO candidate_screenings
                (candidate_id,submission_id,started,status,input) VALUES(?,?,?,'running',?)""",
                (candidate_id, submission_id, now(), encoded(incoming)),
            ).lastrowid
        result = run_preview(incoming, api_key=choice.api_key, transport=self.transport,
                             root=self.store.root, trace_name=f"screening-{attempt_id}")
        response_data, output, error = result["response"], result["output"], result["error"]
        status = "failed" if error else "completed"
        if output is not None:
            recommendation = output["effective_recommendation"]
            output.update(
                date_check=checks,
                disposition="needs_review"
                if recommendation == "uncertain"
                or incoming["source_scope"] != "full_saved_text"
                or checks["status"] != "within_window"
                else "skip_suggested"
                if recommendation == "skip"
                else "read_suggested",
            )
        with self.store.db:
            self.store.db.execute(
                """UPDATE candidate_screenings SET status=?,finished=?,response=?,output=?,error=?
                WHERE id=? AND status='running'""",
                (status, now(), encoded(response_data), encoded(output), error, attempt_id),
            )
        return self.inspect(candidate_id)
