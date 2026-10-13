"""Frozen-plan discovery runs with observable external search and local capture.

The application limits issued tasks and local captures. It cannot limit or measure
searches made outside it. Imported provider output is evidence, not authenticated
execution telemetry. No paid API is called by this module.
"""

import json
from datetime import UTC, date, datetime
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .pipeline import Pipeline
from .search_types import SearchObservation
from .storage import now


class StrictInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class SearchPlan(StrictInput):
    goal: str = Field(min_length=1, max_length=2000)
    as_of: date
    facets: list[str] = Field(min_length=1, max_length=6)
    queries: list[str] = Field(min_length=1, max_length=8)
    origin: Literal["external_model", "human", "test_fixture"]
    planner: str = Field(min_length=1, max_length=200)
    search_provider: Literal["external_codex", "exa_mcp"] = "external_codex"
    search_limit: int = Field(default=8, ge=1, le=8)
    capture_limit: int = Field(default=10, ge=1, le=10)

    @model_validator(mode="after")
    def valid_plan(self):
        if any(not q.strip() or len(q) > 1000 for q in self.queries + self.facets):
            raise ValueError("Queries and facets must be nonempty and at most 1000 characters")
        if len(set(self.queries)) != len(self.queries):
            raise ValueError("Queries must be distinct")
        if len(self.queries) > self.search_limit:
            raise ValueError("Plan exceeds search task limit")
        return self


class DiscoveryExperiment:
    """One module owns frozen plans, task reservations, evidence and capture links."""

    def __init__(self, flow: Pipeline):
        self.flow, self.store = flow, flow.store
        self.store.db.executescript("""
            CREATE TABLE IF NOT EXISTS discovery_experiments(
                id TEXT PRIMARY KEY, created TEXT NOT NULL, plan TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS discovery_events(
                id TEXT PRIMARY KEY, run_id TEXT NOT NULL,
                kind TEXT NOT NULL, task_key TEXT NOT NULL, started TEXT NOT NULL,
                finished TEXT, status TEXT NOT NULL, input TEXT NOT NULL, output TEXT,
                UNIQUE(run_id,kind,task_key)
            );
            CREATE UNIQUE INDEX IF NOT EXISTS one_active_discovery_event
                ON discovery_events(run_id) WHERE status='running';
        """)

    def create(self, payload):
        plan = SearchPlan.model_validate(payload)
        run_id = uuid4().hex
        with self.store.db:
            self.store.db.execute(
                "INSERT INTO discovery_experiments VALUES(?,?,?)",
                (run_id, now(), plan.model_dump_json()),
            )
        return self.inspect(run_id)

    def list(self):
        return [
            self.inspect(row[0], include_provider_output=False)
            for row in self.store.db.execute(
                "SELECT id FROM discovery_experiments ORDER BY created DESC"
            ).fetchall()
        ]

    def inspect(self, run_id, *, include_provider_output=True):
        row = self.store.db.execute(
            "SELECT * FROM discovery_experiments WHERE id=?", (run_id,)
        ).fetchone()
        if row is None:
            raise ValueError("Unknown discovery experiment")
        plan = json.loads(row["plan"])
        events = []
        for item in self.store.db.execute(
            "SELECT * FROM discovery_events WHERE run_id=? ORDER BY rowid", (run_id,)
        ):
            event = dict(item)
            event["input"] = json.loads(event["input"])
            event["output"] = json.loads(event["output"]) if event["output"] else None
            event["elapsed_seconds"] = (
                (
                    datetime.fromisoformat(event["finished"])
                    - datetime.fromisoformat(event["started"])
                ).total_seconds()
                if event["finished"]
                else None
            )
            events.append(event)
        searches = [e for e in events if e["kind"] == "search"]
        automatic = plan.get("search_provider") == "exa_mcp"
        captures = [e for e in events if e["kind"] == "capture"]
        candidates = {}
        # Round-robin by provider rank: do not let the first query occupy all reads.
        for event in searches:
            for rank, hit in enumerate((event["output"] or {}).get("results", []), 1):
                candidate = candidates.setdefault(
                    hit["url"],
                    dict(hit) | {"discovered_by": [], "best_rank": rank, "capture": None},
                )
                candidate["best_rank"] = min(candidate["best_rank"], rank)
                candidate["discovered_by"].append({"event_id": event["id"], "rank": rank})
        for event in captures:
            if event["task_key"] in candidates:
                candidates[event["task_key"]]["capture"] = event
        status = "awaiting_search" if automatic else "awaiting_external_search"
        if any(e["status"] == "running" for e in events):
            status = "awaiting_search_import" if events[-1]["kind"] == "search" else "capturing"
            if automatic and status == "awaiting_search_import":
                status = "searching"
        elif len(searches) == len(plan["queries"]):
            status = "awaiting_capture" if candidates else "no_candidates"
            if candidates and (
                len(captures) >= plan["capture_limit"] or len(captures) == len(candidates)
            ):
                status = "awaiting_selection_review"
        if not include_provider_output:
            for event in events:
                if event["output"]:
                    event["output"].pop("provider_output", None)
        return {
            "id": run_id,
            "created": row["created"],
            "arm": "A",
            "plan": plan,
            "status": status,
            "stop_reason": (
                "capture_budget_reached"
                if len(captures) >= plan["capture_limit"]
                else "all_candidates_attempted"
                if status == "awaiting_selection_review"
                else "no_candidates_returned"
                if status == "no_candidates"
                else "frozen_queries_finished"
                if status == "awaiting_capture"
                else "awaiting_external_action"
            ),
            "events": events,
            "candidates": sorted(candidates.values(), key=lambda c: c["best_rank"]),
            "budget": {
                "issued_search_tasks": len(searches),
                "search_limit": plan["search_limit"],
                "capture_attempts": len(captures),
                "capture_limit": plan["capture_limit"],
            },
            "execution": "anonymous_exa_mcp"
            if automatic
            else "external_search_import_with_local_capture",
            "search_api_called_by_application": any(
                e["input"].get("execution") == "application_exa_mcp" for e in searches
            ),
            "model_api_called_by_application": False,
            "external_budget_enforced": False,
            "application_search_budget_enforced": automatic,
            "billing_mode": "anonymous_free_no_key" if automatic else "external_unknown",
            "cost_usd": None,
            "limitations": (
                "应用限制 Exa 工具调用数，每次含握手最多50秒，不自动重试或转付费；服务端内部查询和成本未知。搜索摘录不等于原文或已验证内容。"
                if automatic
                else "仅限制应用发出的任务数和本地采集数；外部搜索次数、用量、费用及完整耗时无法由应用认证。导入返回未认证，URL匹配不等于内容真实。"
            ),
        }

    def next_search(self, run_id):
        return self._reserve_search(run_id, automatic=False)

    def _reserve_search(self, run_id, *, automatic):
        with self.store.db:
            self.store.db.execute("BEGIN IMMEDIATE")
            state = self.inspect(run_id)
            if (state["plan"].get("search_provider") == "exa_mcp") != automatic:
                raise ValueError("Search provider is frozen; use its matching execution endpoint")
            pending = [e for e in state["events"] if e["status"] == "running"]
            if pending:
                if automatic:
                    raise ValueError("A task is already running; refusing a duplicate network call")
                if pending[0]["kind"] != "search":
                    raise ValueError("A capture is already running")
                return pending[0]
            index = state["budget"]["issued_search_tasks"]
            if index >= len(state["plan"]["queries"]):
                raise ValueError(
                    "All frozen queries have been issued; create a new run to change plan"
                )
            if index >= state["plan"]["search_limit"]:
                raise ValueError("Search task budget exhausted")
            event = self._start(
                run_id,
                "search",
                str(index),
                {
                    "query": state["plan"]["queries"][index],
                    "max_results": 5,
                    "as_of": state["plan"]["as_of"],
                    "execution": "application_exa_mcp" if automatic else "external_import",
                    "objective": state["plan"]["goal"] + " As of " + state["plan"]["as_of"],
                    "instruction": "执行这条冻结查询，保留真实工具返回；导入最多5条链接。失败也需导入。不要补写未执行的查询或估计费用。",
                },
            )
        return event

    def search_next(self, run_id, search):
        event = self._reserve_search(run_id, automatic=True)
        # Committed before calling the provider; concurrent clicks cannot call twice.
        try:
            observation = SearchObservation.model_validate(
                search(event["input"]["query"], event["input"]["objective"])
            )
        except Exception as error:  # noqa: BLE001
            observation = SearchObservation(
                provider="exa_mcp_anonymous",
                observed_at=now(),
                status="failed",
                provider_output="No valid search observation received",
                limitations=f"Adapter failure: {type(error).__name__}; no retry or paid fallback.",
            )
        return self._complete_search(run_id, event["id"], observation, automatic=True)

    def import_search(self, run_id, event_id, payload):
        observation = SearchObservation.model_validate(payload)
        return self._complete_search(run_id, event_id, observation, automatic=False)

    def _complete_search(self, run_id, event_id, observation, *, automatic):
        with self.store.db:
            self.store.db.execute("BEGIN IMMEDIATE")
            state = self.inspect(run_id)
            if (state["plan"].get("search_provider") == "exa_mcp") != automatic:
                raise ValueError("Automatic search evidence cannot be supplied by external import")
            event = next((e for e in state["events"] if e["id"] == event_id), None)
            if not event or event["kind"] != "search" or event["status"] != "running":
                raise ValueError(
                    "Search event is unknown or already completed; evidence is immutable"
                )
            if observation.observed_at < datetime.fromisoformat(event["started"]):
                raise ValueError("Observation predates issued task")
            if observation.observed_at > datetime.now(UTC):
                raise ValueError("Observation cannot be in the future")
            self._finish(event_id, observation.status, observation.model_dump(mode="json"))
        return self.inspect(run_id)

    def capture_next(self, run_id):
        with self.store.db:
            self.store.db.execute("BEGIN IMMEDIATE")
            state = self.inspect(run_id)
            if state["status"] != "awaiting_capture":
                raise ValueError("Finish search imports first, or capture budget is exhausted")
            candidate = next(c for c in state["candidates"] if c["capture"] is None)
            event = self._start(
                run_id,
                "capture",
                candidate["url"],
                {
                    "url": candidate["url"],
                    "selection_policy": "provider_rank_round_robin_baseline; not a value judgment",
                    "discovered_by": candidate["discovered_by"],
                },
            )
        # Reservation is committed before any network side effect.
        try:
            capture = self.flow.create(candidate["url"])
            pipeline_id = capture["id"]
            capture = self.flow.run(pipeline_id, "fetch")
            if capture["stages"][0]["status"] == "succeeded":
                capture = self.flow.run(pipeline_id, "extract")
            success = capture["stages"][1]["status"] == "succeeded"
            output = {
                "pipeline_id": pipeline_id,
                "pipeline": capture,
                "completeness": "needs_human_review",
            }
            status = "succeeded" if success else "failed"
        except Exception as error:  # noqa: BLE001 - preserve failure without unsafe error details
            status, output = "failed", {"error_type": type(error).__name__}
        with self.store.db:
            self._finish(event["id"], status, output)
        return self.inspect(run_id)

    def _start(self, run_id, kind, task_key, incoming):
        event = {
            "id": uuid4().hex,
            "run_id": run_id,
            "kind": kind,
            "task_key": task_key,
            "started": now(),
            "status": "running",
            "input": incoming,
        }
        self.store.db.execute(
            "INSERT INTO discovery_events(id,run_id,kind,task_key,started,status,input) "
            "VALUES(?,?,?,?,?,?,?)",
            (
                event["id"],
                run_id,
                kind,
                task_key,
                event["started"],
                "running",
                json.dumps(incoming, ensure_ascii=False),
            ),
        )
        return event

    def _finish(self, event_id, status, output):
        self.store.db.execute(
            "UPDATE discovery_events SET status=?,finished=?,output=? WHERE id=?",
            (status, now(), json.dumps(output, ensure_ascii=False), event_id),
        )
