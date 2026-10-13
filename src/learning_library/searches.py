"""One explicit search, preserved evidence, then optional full-text capture."""

import hashlib
import json
from datetime import datetime, timedelta
from importlib.metadata import version
from uuid import uuid4
from zoneinfo import ZoneInfo

from langchain_core.tools import StructuredTool
from langsmith import tracing_context
from pydantic import BaseModel, ConfigDict, Field

from .candidates import Candidates
from .codex_search import PROMPT_VERSION
from .codex_search import search as codex_search
from .exa import ENDPOINT
from .exa import search as exa_search
from .model_config import codex_settings
from .review import ReviewConflict
from .search_types import SearchObservation
from .storage import now


class SearchQuery(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    query: str = Field(min_length=1, max_length=1000)
    objective: str = Field(min_length=1, max_length=1500)


class SearchInput(SearchQuery):
    submission_id: str = Field(min_length=1, max_length=80)
    provider: str = Field(default="codex", pattern="^(codex|exa)$")
    refresh: bool = False


def search_tool(provider=exa_search):
    """Same tool interface can be invoked explicitly or by a future bounded agent."""

    def invoke(query: str, objective: str):
        return SearchObservation.model_validate(provider(query, objective)).model_dump(mode="json")

    return StructuredTool.from_function(
        invoke,
        name="search_practice_sources",
        description="Search public pages once. Returns up to five unverified snippets, not full articles.",
        args_schema=SearchQuery,
    )


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


class SourceSearch:
    def __init__(self, pipeline, *, provider=None):
        self.flow, self.store = pipeline, pipeline.store
        self.candidates = Candidates(pipeline)
        self.provider = provider
        self.tool = search_tool(provider or exa_search)
        self.store.db.executescript("""
            CREATE TABLE IF NOT EXISTS source_searches(
                id TEXT PRIMARY KEY, submission_id TEXT NOT NULL UNIQUE,
                created TEXT NOT NULL, finished TEXT, status TEXT NOT NULL,
                input TEXT NOT NULL, execution TEXT NOT NULL, output TEXT, error TEXT
            );
            CREATE TABLE IF NOT EXISTS source_search_choices(
                search_id TEXT NOT NULL REFERENCES source_searches(id),
                position INTEGER NOT NULL, decision TEXT NOT NULL,
                candidate_id TEXT REFERENCES candidates(id),
                PRIMARY KEY(search_id,position)
            );
            CREATE TABLE IF NOT EXISTS source_search_submissions(
                submission_id TEXT PRIMARY KEY,
                search_id TEXT NOT NULL REFERENCES source_searches(id),
                input TEXT NOT NULL, created TEXT NOT NULL
            );
        """)

    def search(self, payload):
        incoming = SearchInput.model_validate(payload).model_dump()
        timestamp = now()
        day = datetime.fromisoformat(timestamp).astimezone(ZoneInfo("America/New_York")).date().isoformat()
        settings = codex_settings()
        identity = {key: incoming[key] for key in ("provider", "query", "objective")}
        identity.update(as_of=day, rule="search-cache-v1", prompt_version=PROMPT_VERSION,
                        model=settings[0] if incoming["provider"] == "codex" else None,
                        effort=settings[1] if incoming["provider"] == "codex" else None)
        cache_key = hashlib.sha256(encoded(identity).encode()).hexdigest()
        with self.store.db:
            self.store.db.execute("BEGIN IMMEDIATE")
            previous = self.store.db.execute(
                """SELECT search_id AS id,input FROM source_search_submissions WHERE submission_id=?
                   UNION ALL SELECT id,input FROM source_searches WHERE submission_id=? LIMIT 1""",
                (incoming["submission_id"], incoming["submission_id"]),
            ).fetchone()
            if previous:
                if SearchInput.model_validate(json.loads(previous["input"])).model_dump() != incoming:
                    raise ReviewConflict("同一提交标识不能用于不同搜索。")
                return self.inspect(previous["id"])
            if not incoming["refresh"]:
                cached = self.store.db.execute(
                    """SELECT id FROM source_searches WHERE json_extract(execution,'$.cache.key')=?
                       AND (status='succeeded' OR (status='running' AND created>?))
                       ORDER BY rowid DESC LIMIT 1""",
                    (cache_key, (datetime.fromisoformat(timestamp) - timedelta(minutes=5)).isoformat()),
                ).fetchone()
                if cached:
                    self.store.db.execute(
                        "INSERT INTO source_search_submissions VALUES(?,?,?,?)",
                        (incoming["submission_id"], cached["id"], encoded(incoming), timestamp),
                    )
                    return self.inspect(cached["id"])
            identity = uuid4().hex
            execution = {
                "engine": "langchain_tool",
                "langchain_core": version("langchain-core"),
                "tool": self.tool.name,
                "tool_schema": self.tool.args_schema.model_json_schema(),
                "endpoint": ENDPOINT,
                "provider_tool": "web_search_exa",
                "provider_arguments": {
                    "query": incoming["query"],
                    "objective": incoming["objective"],
                    "numResults": 5,
                },
                "search_limit": 1,
                "search_attempts": 1,
                "model_calls": 0,
                "provider_internal_requests": "unknown",
                "automatic_retries": 0,
                "cloud_tracing": False,
                "cache": {"key": cache_key, "policy": "same-local-day", "as_of": day,
                          "timezone": "America/New_York", "refresh_requested": incoming["refresh"]},
            }
            use_codex = self.provider is None and incoming["provider"] == "codex"
            if use_codex:
                execution.update(endpoint="local app-server", provider_tool="Codex SDK / ChatGPT",
                                 model_calls=1, search_limit=None,
                                 search_limit_note="最多四次搜索为提示词约束，非硬预算",
                                 api_fallback=False, artifact_id=f"search-{identity}",
                                 model=settings[0], effort=settings[1], prompt_version=PROMPT_VERSION)
            elif self.provider is not None:
                execution["provider_tool"] = "injected search Adapter"
                execution["endpoint"] = "injected by caller"
            self.store.db.execute(
                """INSERT INTO source_searches
                (id,submission_id,created,status,input,execution) VALUES(?,?,?,'running',?,?)""",
                (identity, incoming["submission_id"], timestamp, encoded(incoming), encoded(execution)),
            )
            self.store.db.execute(
                "INSERT INTO source_search_submissions VALUES(?,?,?,?)",
                (incoming["submission_id"], identity, encoded(incoming), timestamp),
            )
        output, error = None, None
        try:
            tool = self.tool
            if use_codex:
                tool = search_tool(lambda query, objective: codex_search(
                    query, objective, root=self.store.root, trace_name=f"search-{identity}",
                    as_of=day, settings=settings))
            with tracing_context(enabled=False):
                output = tool.invoke(
                    {key: incoming[key] for key in ("query", "objective")}, config={"callbacks": []}
                )
            if output["status"] == "failed":
                error = output["limitations"]
            seen, unique = set(), []
            for hit in output["results"]:
                if hit["url"] not in seen:
                    seen.add(hit["url"])
                    unique.append(hit)
            output["duplicate_urls"] = len(output["results"]) - len(unique)
            output["results"] = unique
        except Exception as exc:  # noqa: BLE001 - preserve failed tool execution, never auto-retry
            error = f"搜索未完成：{type(exc).__name__}。本次没有可验证的搜索结果。"
        with self.store.db:
            self.store.db.execute(
                "UPDATE source_searches SET finished=?,status=?,output=?,error=? WHERE id=?",
                (now(), "failed" if error else "succeeded", encoded(output), error, identity),
            )
        return self.inspect(identity)

    def inspect(self, search_id):
        row = self.store.db.execute(
            "SELECT * FROM source_searches WHERE id=?", (search_id,)
        ).fetchone()
        if not row:
            raise ValueError("搜索记录不存在。")
        state = dict(row)
        for key in ("input", "execution", "output"):
            state[key] = json.loads(state[key]) if state[key] else None
        state["elapsed_seconds"] = (
            (
                datetime.fromisoformat(state["finished"]) - datetime.fromisoformat(state["created"])
            ).total_seconds()
            if state["finished"]
            else None
        )
        state["items"] = []
        for position, hit in enumerate((state["output"] or {}).get("results", [])):
            choice = self.store.db.execute(
                "SELECT * FROM source_search_choices WHERE search_id=? AND position=?",
                (search_id, position),
            ).fetchone()
            item = dict(
                hit,
                position=position,
                decision="pending",
                candidate_id=None,
                capture=None,
                already_saved=False,
            )
            if choice:
                item.update(decision=choice["decision"], candidate_id=choice["candidate_id"])
            if not item["candidate_id"]:
                existing = self.store.db.execute(
                    "SELECT id FROM candidates WHERE json_extract(input,'$.url')=? AND version_id IS NOT NULL ORDER BY created DESC LIMIT 1",
                    (hit["url"],),
                ).fetchone()
                if existing:
                    item.update(candidate_id=existing["id"], already_saved=True)
            if item["candidate_id"]:
                candidate = self.candidates.inspect(item["candidate_id"], include_trace=False)
                item["capture"] = {
                    "pipeline_id": candidate["pipeline_id"],
                    "version_id": candidate["version_id"],
                    **candidate["processing"],
                }
            state["items"].append(item)
        return state

    def list(self):
        return [
            dict(row) | {"input": json.loads(row["input"])}
            for row in self.store.db.execute(
                "SELECT id,created,finished,status,input,error FROM source_searches ORDER BY rowid DESC LIMIT 30"
            )
        ]

    def decide(self, search_id, position, action, submission_id=""):
        if action not in {"save", "ignore", "restore"}:
            raise ValueError("未知的搜索候选操作。")
        state = self.inspect(search_id)
        if state["status"] != "succeeded" or not 0 <= position < len(state["items"]):
            raise ValueError("只能选择成功搜索中的已保存结果。")
        if action == "save" and not 1 <= len(submission_id) <= 80:
            raise ValueError("保存全文需要提交标识。")
        item = state["items"][position]
        candidate_id = item["candidate_id"]
        if action == "save" and not candidate_id:
            candidate = self.candidates.import_link(
                {
                    "submission_id": f"search-{search_id}-{position}",
                    "url": item["url"],
                    "goal": state["input"]["objective"],
                    "origin": "web_search",
                    "source_note": (
                        f"{state['output']['provider']} 网页搜索；查询：{state['input']['query']}；"
                        f"搜索记录 {search_id}，结果 {position + 1}。搜索摘要未作为完整原文。"
                    ),
                    "as_of": state["created"][:10],
                    "max_age_days": 30,
                }
            )
            candidate_id = candidate["id"]
        decision = "ignored" if action == "ignore" else "selected" if candidate_id else "pending"
        with self.store.db:
            self.store.db.execute(
                """INSERT INTO source_search_choices VALUES(?,?,?,?)
                ON CONFLICT(search_id,position) DO UPDATE SET decision=excluded.decision,
                candidate_id=COALESCE(source_search_choices.candidate_id,excluded.candidate_id)""",
                (search_id, position, decision, candidate_id),
            )
        if action == "save":
            self.candidates.process(candidate_id, submission_id)
        return self.inspect(search_id)
