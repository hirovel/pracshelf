"""Run the fixed reading flow, retaining successful work and stopping on failure.

begin() claims a durable submission before the HTTP response; run() executes it
in a server background task with its own Store. GET/inspect never does work.
"""

import re
from datetime import UTC, datetime, timedelta

from .candidates import Candidates
from .model_config import selected_model
from .processing import ProcessingResults
from .review import ReviewConflict
from .storage import now
from .translation import Translation, segment_source
from .workflow_view import WorkflowView

MAX_MODEL_CALLS = 20


class Workflows:
    def __init__(self, pipeline, *, transport=None):
        self.flow, self.store = pipeline, pipeline.store
        self.candidates = Candidates(pipeline, transport=transport)
        self.translation = Translation(self.store, transport=transport)
        self.results = ProcessingResults(self.store)
        self.view = WorkflowView(pipeline)
        self.store.db.executescript("""
            CREATE TABLE IF NOT EXISTS workflow_runs(
                id INTEGER PRIMARY KEY, pipeline_id TEXT NOT NULL REFERENCES pipelines(id),
                submission_id TEXT NOT NULL, status TEXT NOT NULL, stage TEXT NOT NULL,
                started TEXT NOT NULL, updated TEXT NOT NULL, finished TEXT,
                version_id INTEGER, model TEXT NOT NULL, reserved_calls INTEGER NOT NULL DEFAULT 0,
                error TEXT, UNIQUE(pipeline_id,submission_id)
            );
            CREATE UNIQUE INDEX IF NOT EXISTS one_active_workflow
                ON workflow_runs(pipeline_id) WHERE status IN ('queued','running');
        """)

    def inspect(self, run_id, *, detail="full"):
        value = self.view.inspect(run_id, detail=detail)
        history = [dict(r) for r in self.store.db.execute(
            "SELECT * FROM workflow_runs WHERE pipeline_id=? ORDER BY id", (run_id,),
        )]
        latest = history[-1] if history else None
        active = bool(latest and latest["status"] in {"queued", "running"})
        expired = bool(active and datetime.fromisoformat(latest["updated"]) < datetime.now(UTC) - timedelta(minutes=5))
        steps = {s["id"]: s for s in value["steps"]}
        screening = steps["screen"]
        native = (steps["translate"].get("trace") or {}).get("native", {})
        remaining = None
        if value["version_id"]:
            text = self.store.version(value["version_id"])["document"]["text"]
            parts = native.get("segments") or segment_source(text)
            remaining = sum(p["kind"] == "text" and bool(p["source"].strip()) and p.get("status") != "succeeded" for p in parts)
        screen_calls = int(screening["status"] not in {"completed", "stopped"})
        if screening["status"] == "stopped":
            remaining = 0
        choice = selected_model(self.store.root)
        model = choice.model
        action, reason = "start", "自动处理到最终审核；不会填写人工记录。"
        if active and not expired:
            action, reason = None, "后端正在处理，关闭页面不会停止；刷新只读取记录。"
        elif screening["status"] == "stopped":
            action, reason = None, "筛选未通过，已停止后续模型调用。"
        elif steps["translate"]["status"] == "completed" and self._registered(run_id):
            action, reason = None, "结果可阅读，最终审核由你完成。"
        elif native.get("status") == "succeeded" and steps["translate"]["status"] == "failed":
            action, reason = None, "完整译文未通过结构检查，需修改结果；重跑不会修复已有文本。"
        elif not value["candidate_id"]:
            action, reason = None, "资料缺少候选来源记录。"
        elif not choice.ready:
            action, reason = None, choice.reason
        elif remaining is not None and remaining + screen_calls > MAX_MODEL_CALLS:
            action, reason = None, f"待调用批数超过单次 {MAX_MODEL_CALLS} 次上限，请拆分资料。"
        elif expired or (latest and latest["status"] in {"failed", "interrupted"}) or any(s["status"] == "failed" for s in value["steps"]):
            action = "retry"
        value["execution"] = {
            "latest": latest, "history": history, "action": action, "reason": reason,
            "expired": expired, "model": model,
            "current_status": value["status"] if steps["translate"]["status"] == "completed" else latest["status"] if latest else "ready",
            "budget": {"screen_calls": screen_calls, "translation_calls": remaining,
                       "max_model_calls": MAX_MODEL_CALLS, "translation_max_output_tokens_per_call": 8192},
        }
        return value

    def begin(self, run_id, submission_id):
        if not isinstance(submission_id, str) or not re.fullmatch(r"[a-zA-Z0-9_-]{1,80}", submission_id):
            raise ValueError("需要有效的处理提交编号。")
        with self.store.db:
            self.store.db.execute("BEGIN IMMEDIATE")
            existing = self.store.db.execute(
                "SELECT id FROM workflow_runs WHERE pipeline_id=? AND submission_id=?", (run_id, submission_id),
            ).fetchone()
            if existing:
                return {"id": existing["id"], "launch": False}
            if self.store.db.execute(
                "SELECT 1 FROM article_insights WHERE pipeline_id=? AND status IN ('queued','running')", (run_id,),
            ).fetchone():
                raise ReviewConflict("全文分析仍在运行，不能同时启动旧处理流程。")
            state = self.inspect(run_id)
            execution = state["execution"]
            if execution["action"] is None:
                if execution["latest"] and execution["latest"]["status"] in {"queued", "running"}:
                    raise ReviewConflict(execution["reason"])
                raise ValueError(execution["reason"])
            if execution["expired"]:
                self.store.db.execute(
                    "UPDATE workflow_runs SET status='interrupted',finished=?,error=? WHERE id=?",
                    (now(), "处理超过五分钟未更新，结果未知；已有结果保留，新的提交从未完成部分继续。", execution["latest"]["id"]),
                )
            timestamp = now()
            identifier = self.store.db.execute(
                """INSERT INTO workflow_runs(pipeline_id,submission_id,status,stage,started,updated,version_id,model)
                   VALUES(?,?,'queued','source',?,?,?,?)""",
                (run_id, submission_id, timestamp, timestamp, state["version_id"], execution["model"]),
            ).lastrowid
        return {"id": identifier, "launch": True}

    def run(self, identifier):
        with self.store.db:
            claimed = self.store.db.execute(
                "UPDATE workflow_runs SET status='running',updated=? WHERE id=? AND status='queued'", (now(), identifier),
            ).rowcount
        if not claimed:
            return
        job = dict(self.store.db.execute("SELECT * FROM workflow_runs WHERE id=?", (identifier,)).fetchone())
        run_id = job["pipeline_id"]
        current_stage = "source"
        try:
            candidate_id = self.view.inspect(run_id)["candidate_id"]
            state = self.candidates.process(candidate_id, f"flow-{identifier}-source")
            version_id = self.flow.inspect(run_id)["review"]["version_id"]
            if not version_id:
                raise ValueError(state["processing"]["error"] or "原文整理未完成。")
            if job["version_id"] is not None and job["version_id"] != version_id:
                raise ReviewConflict("原文版本已改变，旧任务停止；请从当前原文重新开始。")
            # Move the candidate pointer, never rewrite the old screening records.
            with self.store.db:
                self.store.db.execute("UPDATE candidates SET version_id=? WHERE id=?", (version_id, candidate_id))
                self.store.db.execute("UPDATE workflow_runs SET version_id=? WHERE id=?", (version_id, identifier))
            job["version_id"] = version_id
            budget = self.inspect(run_id)["execution"]["budget"]
            if budget["screen_calls"] + budget["translation_calls"] > MAX_MODEL_CALLS:
                raise ValueError(f"待调用批数超过单次 {MAX_MODEL_CALLS} 次上限，未开始模型调用。")
            current_stage = "screen"
            self._touch(job, current_stage)
            preview = self.candidates.inspect(candidate_id)["preview"]
            if preview["action"] is not None:
                self._count(job)
                self.candidates.review(candidate_id, f"flow-{identifier}-screen")
            screen = self.view.inspect(run_id)["steps"][1]
            if screen["status"] == "stopped":
                self._finish(identifier, "stopped", current_stage)
                return
            if screen["status"] != "completed":
                raise ValueError(screen["summary"])
            current_stage = "translate"
            self._touch(job, current_stage)
            if not self._registered(run_id):
                while True:
                    self._touch(job, current_stage)
                    translated = self.translation.inspect(run_id)
                    if translated["status"] == "succeeded":
                        break
                    self._count(job)
                    translated = self.translation.advance(run_id)
                    if translated["status"] == "failed":
                        error = next(a["error"] for a in reversed(translated["attempts"]) if a["status"] == "failed")
                        raise ValueError(error)
                self._touch(job, current_stage)
                self.results.receive(run_id, translation_id=translated["id"])
            if self.view.inspect(run_id)["steps"][2]["status"] != "completed":
                raise ValueError("完整译文未通过结构检查，请查看翻译结果。")
            self._finish(identifier, "pending_review", "review")
        except (ValueError, ReviewConflict) as exc:
            self._finish(identifier, "failed", current_stage, str(exc))
        except Exception as exc:  # noqa: BLE001 - preserve a durable error, never leak transport secrets
            self._finish(identifier, "failed", current_stage, f"处理内部错误（{type(exc).__name__}）；已有结果保留。")

    def _registered(self, run_id):
        if not self.flow.reviews.inspect(run_id)["available"]:
            return False
        return any(r["current"] and r["checks"]["status"] == "passed" for r in self.results.inspect(run_id)["results"])

    def _touch(self, job, stage):
        active = self.store.db.execute("SELECT status FROM workflow_runs WHERE id=?", (job["id"],)).fetchone()
        if active["status"] != "running":
            raise ReviewConflict("本次处理已中断，不再继续调用。")
        if self.flow.reviews.inspect(job["pipeline_id"])["version_id"] != job["version_id"]:
            raise ReviewConflict("处理期间原文版本改变，已停止；请从当前原文重新开始。")
        if selected_model(self.store.root).model != job["model"]:
            raise ReviewConflict("处理期间模型配置改变，已停止；请重新开始。")
        with self.store.db:
            self.store.db.execute("UPDATE workflow_runs SET stage=?,updated=? WHERE id=? AND status='running'", (stage, now(), job["id"]))

    def _count(self, job):
        if job["reserved_calls"] >= MAX_MODEL_CALLS:
            raise ValueError("达到单次模型调用上限，已有结果保留。")
        job["reserved_calls"] += 1
        with self.store.db:
            self.store.db.execute("UPDATE workflow_runs SET reserved_calls=? WHERE id=? AND status='running'", (job["reserved_calls"], job["id"]))

    def _finish(self, identifier, status, stage, error=None):
        with self.store.db:
            self.store.db.execute(
                "UPDATE workflow_runs SET status=?,stage=?,updated=?,finished=?,error=? WHERE id=? AND status='running'",
                (status, stage, now(), now(), error, identifier),
            )
