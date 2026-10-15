"""Persist capture steps and source checks; content screening belongs to Candidates."""

import json
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from .collector import Collector, web_url
from .review import OriginalReview
from .storage import Store, now

STAGES = [
    ("fetch", "获取网页"),
    ("extract", "提取正文"),
    ("validate", "原文基础检查"),
]
INTERRUPTED_CAPTURE_REASON = "原文处理超过五分钟未完成，结果未知；可显式重试，已保存阶段保留。"


def capture_expired(started):
    return datetime.fromisoformat(started) < datetime.now(UTC) - timedelta(minutes=5)


class Pipeline:
    def __init__(self, store: Store, client, *, review_origin="human"):
        self.store = store
        self.collector = Collector(store, client)
        store.db.executescript("""
            CREATE TABLE IF NOT EXISTS pipelines(id TEXT PRIMARY KEY, url TEXT, created TEXT);
            CREATE TABLE IF NOT EXISTS pipeline_attempts(
                id INTEGER PRIMARY KEY, pipeline_id TEXT REFERENCES pipelines(id),
                stage TEXT, status TEXT, started TEXT, finished TEXT, input TEXT, output TEXT
            );
            CREATE UNIQUE INDEX IF NOT EXISTS one_active_pipeline_step
                ON pipeline_attempts(pipeline_id) WHERE status='running';
        """)
        self.reviews = OriginalReview(store, origin=review_origin)

    def create(self, url: str):
        url = web_url(url)
        pipeline_id = uuid4().hex
        with self.store.db:
            self.store.db.execute("INSERT INTO pipelines VALUES(?,?,?)", (pipeline_id, url, now()))
        return self.inspect(pipeline_id)

    def list(self):
        return [
            self.inspect(row[0])
            for row in self.store.db.execute("SELECT id FROM pipelines ORDER BY created DESC")
        ]

    def reextract(self, pipeline_id):
        """Create a new attempt chain from an existing successful fetch, offline.

        The old chain and its document version remain unchanged. Extraction is
        still an explicit next step, so creating this chain does not claim success.
        """
        source = self.inspect(pipeline_id)
        fetch = source["stages"][0]
        if fetch["status"] != "succeeded":
            raise ValueError("A successful saved response is required")
        previous = fetch["attempts"][-1]
        new = self.create(source["url"])
        with self.store.db:
            self.store.db.execute(
                """INSERT INTO pipeline_attempts(
                    pipeline_id,stage,status,started,finished,input,output
                ) VALUES(?,?,?,?,?,?,?)""",
                (
                    new["id"],
                    "fetch",
                    "succeeded",
                    now(),
                    now(),
                    json.dumps({"url": source["url"], "reused_from_pipeline_id": pipeline_id}),
                    json.dumps(previous["output"], ensure_ascii=False),
                ),
            )
        return self.inspect(new["id"])

    def inspect(self, pipeline_id):
        row = self.store.db.execute("SELECT * FROM pipelines WHERE id=?", (pipeline_id,)).fetchone()
        if row is None:
            raise ValueError("Unknown pipeline")
        stages = []
        for name, label in STAGES:
            history = [
                dict(item)
                for item in self.store.db.execute(
                    "SELECT * FROM pipeline_attempts WHERE pipeline_id=? AND stage=? ORDER BY id",
                    (pipeline_id, name),
                )
            ]
            for attempt in history:
                attempt["input"] = json.loads(attempt["input"])
                attempt["output"] = json.loads(attempt["output"]) if attempt["output"] else None
            status = history[-1]["status"] if history else "not_started"
            expired = bool(history and status == "running" and capture_expired(history[-1]["started"]))
            if expired:
                status = "failed"
            if (
                not history
                and name in {"extract", "validate"}
                and stages[-1]["status"] != "succeeded"
            ):
                status = "blocked"
            stages.append({"id": name, "label": label, "status": status, "attempts": history,
                           "expired": expired, "interruption_reason": INTERRUPTED_CAPTURE_REASON if expired else None})
        return dict(row) | {"stages": stages, "review": self.reviews.inspect(pipeline_id)}

    def run(self, pipeline_id, stage):
        if stage not in {"fetch", "extract", "validate"}:
            raise ValueError("Unknown capture stage")
        state = self.inspect(pipeline_id)
        position = [s[0] for s in STAGES].index(stage)
        selected = state["stages"][position]
        if selected["status"] not in {"not_started", "failed"}:
            raise ValueError(
                "Stage is blocked, active or already finished; create a new pipeline for a fresh capture"
            )
        if position and state["stages"][position - 1]["status"] != "succeeded":
            raise ValueError("Previous stage has not succeeded")
        incoming = (
            {"url": state["url"]}
            if not position
            else state["stages"][position - 1]["attempts"][-1]["output"]
        )
        with self.store.db:
            if selected["expired"]:
                self.store.db.execute(
                    "UPDATE pipeline_attempts SET status='failed',finished=?,output=? WHERE id=? AND status='running'",
                    (now(), json.dumps({"error": INTERRUPTED_CAPTURE_REASON}, ensure_ascii=False), selected["attempts"][-1]["id"]),
                )
            cursor = self.store.db.execute(
                """INSERT INTO pipeline_attempts(pipeline_id,stage,status,started,input)
                   SELECT ?,?,?,?,? WHERE NOT EXISTS(
                       SELECT 1 FROM pipeline_attempts WHERE pipeline_id=? AND stage=?
                       AND status IN ('running','succeeded','needs_review'))""",
                (pipeline_id, stage, "running", now(), json.dumps(incoming), pipeline_id, stage),
            )
            if not cursor.rowcount:
                raise ValueError("Stage was already started or completed by another request")
            attempt_id = cursor.lastrowid
        try:
            if stage == "fetch":
                output = self.collector.collect("fetch", incoming["url"])
                if output.get("request_id"):
                    output["request"] = self.store.captured_request(output["request_id"])
            elif stage == "extract":
                output = self.collector.collect("extract", str(incoming["request_id"]))
                if output.get("version_ids"):
                    doc = self.store.version(output["version_ids"][0])["document"]
                    output["metadata"] = {
                        k: doc.get(k)
                        for k in ("title", "author", "url", "published", "completeness")
                    }
                    output["text_characters"] = len(doc["text"])
            else:
                doc = self.store.version(incoming["version_ids"][0])["document"]
                output = {
                    "status": "needs_review",
                    "version_id": incoming["version_ids"][0],
                    "checks": {
                        "body_present": bool(doc["text"].strip()),
                        "title_present": bool(doc["title"]),
                        "date_extracted": bool(doc.get("published")),
                        "full_text_matches_webpage": "needs_human_review",
                        "published_date_confirmed": "needs_human_review",
                    },
                    "extraction": doc.get("extraction", {"version": "legacy; not reprocessed"}),
                    "note": "自动检查不证明全文完整；对照来源页面与保存正文逐项验收。",
                }
            status = "succeeded" if output["status"] == "ok" else output["status"]
        except Exception as error:  # noqa: BLE001 - persist unexpected stage failures for inspection
            # Persist the stage failure, including parser bugs, so the UI never reports success.
            output, status = {"error": f"{type(error).__name__}: {error}"}, "failed"
        with self.store.db:
            self.store.db.execute(
                "UPDATE pipeline_attempts SET status=?,finished=?,output=? WHERE id=? AND status='running'",
                (status, now(), json.dumps(output, ensure_ascii=False), attempt_id),
            )
        return self.inspect(pipeline_id)

    def artifact(self, pipeline_id, name):
        state = self.inspect(pipeline_id)
        if name == "raw":
            attempts = state["stages"][0]["attempts"]
            if not attempts or not attempts[-1]["output"]:
                raise ValueError("No raw response yet")
            output = attempts[-1]["output"]
            rows = self.store.db.execute(
                "SELECT * FROM requests WHERE run_id=? ORDER BY id DESC", (output.get("run_id"),)
            ).fetchall()
            if not rows or not rows[0]["snapshot"]:
                raise ValueError("No raw response saved")
            return (
                (self.store.root / rows[0]["snapshot"])
                .read_bytes()
                .decode("utf-8", errors="replace")
            )
        if name in {"text", "document"}:
            attempts = state["stages"][1]["attempts"]
            ids = (attempts[-1]["output"] or {}).get("version_ids") if attempts else None
            if not ids:
                raise ValueError("No extracted document yet")
            document = self.store.version(ids[0])
            return (
                document["document"]["text"]
                if name == "text"
                else json.dumps(document, ensure_ascii=False, indent=2)
            )
        raise ValueError("Unknown artifact")
