"""Purpose-specific source selection. External LLM judgments never certify effectiveness."""

import json
from datetime import date
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from .storage import Store, now


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CandidateJudgment(StrictModel):
    pipeline_id: str
    version_id: int
    verdict: Literal["candidate", "reject", "uncertain"]
    reason: str = Field(min_length=1, max_length=1500)
    learning_value: str = Field(min_length=1, max_length=1500)
    quotes: list[str] = Field(min_length=1, max_length=4)
    applicability_note: str = Field(min_length=1, max_length=1500)


class SelectionBatch(StrictModel):
    goal: str = Field(min_length=1, max_length=2000)
    as_of: date
    max_age_days: int = Field(default=90, ge=1, le=366)
    origin: Literal["external_model", "demo", "human_edited"]
    model_label: str = Field(min_length=1)
    search_strategy: str = Field(min_length=1, max_length=4000)
    executed_queries: list[str] = Field(min_length=1, max_length=30)
    search_log_limitations: str = Field(min_length=1)
    candidates: list[CandidateJudgment] = Field(min_length=1, max_length=20)


class Discovery:
    def __init__(self, store: Store):
        self.store = store
        store.db.executescript("""
            CREATE TABLE IF NOT EXISTS discovery_runs (
                id TEXT PRIMARY KEY, created TEXT, input TEXT, output TEXT
            );
            CREATE TABLE IF NOT EXISTS source_selections (
                discovery_id TEXT REFERENCES discovery_runs(id), pipeline_id TEXT,
                output TEXT, PRIMARY KEY(discovery_id, pipeline_id)
            );
        """)

    @staticmethod
    def prepare(goal: str, as_of: date, max_age_days=90):
        """A visible model input; no hidden search/model invocation."""
        if not goal.strip() or not 1 <= max_age_days <= 366:
            raise ValueError("Provide a learning goal and a 1..366 day discovery window")
        return {
            "status": "awaiting_external_planner",
            "goal": goal,
            "as_of": str(as_of),
            "max_age_days": max_age_days,
            "prompt": (
                f"学习目标：{goal}\n截至：{as_of}；优先最近 {max_age_days} 天。\n"
                "先提出3–5条搜索查询：每条说明要回答的问题、目标来源和预期证据。"
                "不要仅搜索最新AI工具。优先原作者的完整过程、代码/配置、失败复盘与条件。"
                "有机制和限制的文章可以是观点或概念，不强迫每篇都有步骤。"
                "排除只有产品更新、宣传、事件通知而缺少实质展开的内容。"
                "保存实际查询与结果，不把计划当成已执行搜索。搜索预算不超过10次。"
                "最多读5篇候选全文，逐篇说明能学什么、逐字证据和入选/拒绝理由。"
                "摘要不足只能待判断。核实来源日期，不能从URL猜日期；无日期应待核实。"
                "把日期窗口和当前版本适用性分开；新日期不代表有效。"
                "缺少证据允许没有合格候选，不为凑数量降低标准。"
            ),
        }

    def accept(self, payload):
        batch = SelectionBatch.model_validate(payload)
        results = []
        for candidate in batch.candidates:
            # Bind the judgment to the actual document extracted by this pipeline.
            row = self.store.db.execute(
                """SELECT output FROM pipeline_attempts WHERE pipeline_id=? AND stage='extract'
                   AND status='succeeded' ORDER BY id DESC LIMIT 1""",
                (candidate.pipeline_id,),
            ).fetchone()
            if row is None or candidate.version_id not in json.loads(row[0]).get("version_ids", []):
                raise ValueError(
                    "Candidate must reference this pipeline's extracted document version"
                )
            doc = self.store.version(candidate.version_id)["document"]
            checks = [
                {"quote": quote, "matched": bool(quote.strip()) and quote in doc["text"]}
                for quote in candidate.quotes
            ]
            published = doc.get("published")
            try:
                age = (batch.as_of - date.fromisoformat(published)).days
                freshness = "within_window" if 0 <= age <= batch.max_age_days else "outside_window"
            except (TypeError, ValueError):
                age, freshness = None, "unknown_date"
            decision = "rejected" if candidate.verdict == "reject" else "candidate"
            if (
                candidate.verdict == "uncertain"
                or not all(c["matched"] for c in checks)
                or freshness != "within_window"
            ):
                decision = "needs_more_evidence" if decision != "rejected" else decision
            if not all(c["matched"] for c in checks):
                decision = "needs_more_evidence"
            results.append(
                candidate.model_dump()
                | {
                    "title": doc["title"],
                    "url": doc["url"],
                    "published": published,
                    "decision": decision,
                    "date_status": freshness,
                    "age_days": age,
                    "date_provenance": "extracted_metadata; not independently authenticated",
                    "evidence_checks": checks,
                    "current_applicability": "unverified",
                    "effectiveness": "unverified",
                    "recommendation_eligible": False,
                }
            )
        if len({c.pipeline_id for c in batch.candidates}) != len(results):
            raise ValueError("A batch cannot review the same pipeline twice")
        selection_id = uuid4().hex
        result = {
            "id": selection_id,
            "created": now(),
            **batch.model_dump(mode="json"),
            "candidates": results,
            "execution": "external_search_and_model_import",
            "model_call_observed": False,
            "search_api_called_by_application": False,
        }
        with self.store.db:
            self.store.db.execute(
                "INSERT INTO discovery_runs VALUES(?,?,?,?)",
                (
                    selection_id,
                    result["created"],
                    json.dumps(payload, ensure_ascii=False),
                    json.dumps(result, ensure_ascii=False),
                ),
            )
            for candidate in results:
                output = {
                    "selection_id": selection_id,
                    "goal": batch.goal,
                    "as_of": str(batch.as_of),
                    "origin": batch.origin,
                    "model_label": batch.model_label,
                    **candidate,
                }
                self.store.db.execute(
                    "INSERT INTO source_selections VALUES(?,?,?)",
                    (
                        selection_id,
                        candidate["pipeline_id"],
                        json.dumps(output, ensure_ascii=False),
                    ),
                )
        return result

    def list(self):
        return [
            json.loads(row[0])
            for row in self.store.db.execute(
                "SELECT output FROM discovery_runs ORDER BY created DESC"
            )
        ]

    def for_pipeline(self, pipeline_id):
        row = self.store.db.execute(
            """SELECT s.output FROM source_selections s JOIN discovery_runs d ON d.id=s.discovery_id
               WHERE s.pipeline_id=? ORDER BY d.created DESC LIMIT 1""",
            (pipeline_id,),
        ).fetchone()
        return json.loads(row[0]) if row else None
