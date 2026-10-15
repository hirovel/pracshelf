"""One read-only Interface for saved work, ELT lineage and reading availability."""

from .article_insights import ArticleInsights
from .candidates import Candidates
from .curation import Curation
from .library import render_markdown
from .processing import ProcessingResults, check_result
from .translation import Translation

LABELS = {
    "completed": "已完成", "running": "处理中", "failed": "失败",
    "not_started": "未开始", "waiting": "等待前一步", "stopped": "已停止",
    "pending_review": "待审核",
}
RECOMMENDATIONS = {
    "read": "建议精读", "focus": "建议看重点", "skip": "不推荐", "uncertain": "信息不足",
}


def stage(name, title, status, summary, *, output=None, trace=None):
    return {
        "id": name, "title": title, "status": status, "label": LABELS[status],
        "summary": summary, "output": output, "trace": trace,
    }


class WorkflowView:
    def __init__(self, pipeline):
        self.flow = pipeline
        self.candidates = Candidates(pipeline)
        self.insights = ArticleInsights(pipeline)
        self.translation = Translation(pipeline.store)
        self.results = ProcessingResults(pipeline.store)
        self.curation = Curation(pipeline.store, origin=pipeline.reviews.origin)

    def list(self):
        return [
            {k: value[k] for k in (
                "run_id", "candidate_id", "title", "author", "published", "url",
                "version_id", "status", "summary", "steps", "elt",
            )}
            for value in (self._view(run, outputs=False, detail="summary") for run in self.flow.list())
        ]

    def inspect(self, run_id, *, detail="full"):
        return self._view(self.flow.inspect(run_id), outputs=True, detail=detail)

    def _view(self, run, *, outputs, detail="full"):
        version = self.flow.store.version(run["review"]["version_id"]) if run["review"]["available"] else None
        doc = version["document"] if version else None
        row = self.flow.store.db.execute(
            "SELECT id FROM candidates WHERE pipeline_id=? ORDER BY created DESC LIMIT 1",
            (run["id"],),
        ).fetchone()
        candidate = self.candidates.inspect(row["id"]) if row else None
        source = self._source(run, version, outputs)
        insights = self.insights.inspect(run["id"], detail=detail)
        screen = self._screen(candidate, version, run["id"], insights)
        translated, current_result = self._translated(run, version, screen, outputs, detail)
        reviewed = self._review(run, translated, current_result, insights)
        steps = [source, screen, translated, reviewed]
        active = next((s for s in steps if s["status"] in {"failed", "running", "stopped"}), None)
        if active is None:
            active = next((s for s in steps if s["status"] != "completed"), reviewed)
        default_step = "screen" if screen["output"] else active["id"]
        origin = candidate["input"] if candidate else None
        # List responses omit large artifacts. Both paths use the same status rules.
        brief = [{k: s[k] for k in ("id", "title", "status", "label", "summary")} for s in steps]
        elt = self._elt(run, version, screen, translated, insights)
        pipeline_view = self._pipeline_view(elt)
        if not outputs:
            elt = {k: elt[k] for k in ("schema", "status", "summary", "phases", "result")}
        return {
            "pipeline_view": pipeline_view,
            "run_id": run["id"], "candidate_id": candidate["id"] if candidate else None,
            "title": (doc["title"].rstrip("¶ ") if doc else run["url"]),
            "author": doc.get("author") if doc else None,
            "published": doc.get("published") if doc else None,
            "url": doc["url"] if doc else run["url"],
            "version_id": version["id"] if version else None,
            "provenance": origin, "created": run["created"],
            "status": active["status"], "summary": active["summary"],
            "default_step": default_step, "steps": steps if outputs else brief,
            "reader_url": "/read/" + run["id"] if doc else None,
            "read_only": True,
            "elt": elt,
            "insights": insights if outputs else None,
        }

    def _elt(self, run, version, screen, translated, insights):
        """Describe real artifacts; E/L share a capture attempt, optional T never gates reading."""
        fetch, clean = run["stages"][:2]
        latest_fetch = fetch["attempts"][-1] if fetch["attempts"] else None
        result = (latest_fetch or {}).get("output") or {}
        request = None
        if result.get("run_id"):
            request = self.flow.store.db.execute(
                "SELECT * FROM requests WHERE run_id=? ORDER BY id DESC LIMIT 1", (result["run_id"],)
            ).fetchone()
            request = dict(request) if request else None
        snapshot = self.flow.store.root / request["snapshot"] if request and request["snapshot"] else None
        stored = bool(snapshot and snapshot.is_file())
        complete = bool(stored and request["status"] == 200 and not request["truncated"] and not request["error"])
        capture_status = {"succeeded": "completed", "blocked": "waiting"}.get(fetch["status"], fetch["status"])
        storage_status = ("completed" if complete else "retained" if stored else
                          "failed" if latest_fetch and fetch["status"] in {"succeeded", "failed"} else "waiting")
        clean_status = "completed" if version else {"blocked": "waiting", "succeeded": "completed"}.get(clean["status"], clean["status"])
        doc = version["document"] if version else None
        source_id = version["id"] if version else None
        core = screen.get("output") or {}
        selected = (screen.get("trace") or {}).get("selected_attempt")
        full = bool(selected and core.get("kind") == "article_insights" and version
                    and selected["input"]["version_id"] == source_id
                    and selected["input"]["content_hash"] == version["content_hash"])
        # Running attempts have no output yet; their saved input determines the source.
        same_source = bool(selected and version and selected["input"]["version_id"] == source_id
                           and selected["input"].get("content_hash") == version["content_hash"])
        core_status = screen["status"] if full or (same_source and screen["status"] in {"failed", "running"}) else "not_started" if version else "waiting"
        core_ready = bool(full and selected["status"] == "completed" and core.get("core_available"))
        response = (selected or {}).get("response") or {}
        prepared = (selected or {}).get("input") or insights.get("input") or {}
        native = (translated.get("trace") or {}).get("native") or {}
        translation_plan = (translated.get("trace") or {}).get("plan") or {}
        translation_started = bool(native.get("attempts") or translation_plan.get("jobs") or (translated.get("trace") or {}).get("received_results"))
        translation_status = translated["status"] if translation_started else "not_enabled"
        labels = LABELS | {"retained": "已保留", "not_enabled": "未启用"}

        def operation(identifier, phase, title, status, summary, *, executor, group,
                      incoming, outgoing, required=True, detail=None):
            return {"id": identifier, "phase": phase, "title": title, "status": status,
                    "label": labels.get(status, status), "summary": summary, "executor": executor,
                    "execution_group": group, "required": required, "input": incoming,
                    "output": outgoing, "detail": detail}

        operations = [
            operation("fetch", "E", "获取网页", capture_status,
                      fetch.get("interruption_reason") or (f"HTTP {request['status']}" if request and request["status"] else result.get("error") or "尚未获取来源响应"),
                      executor="HTTP", group="capture", incoming={"url": run["url"]},
                      outgoing={k: request.get(k) for k in ("id", "final_url", "status", "content_type", "error")} if request else None,
                      detail={"attempts": len(fetch["attempts"]), "selected_attempt": latest_fetch}),
            operation("load", "L", "保存原始快照", storage_status,
                      "完整响应与采集记录已保存" if complete else "错误或不完整响应已保留，不能用于正文整理" if stored else "尚无可用快照",
                      executor="文件 / SQLite", group="capture", incoming={"request_id": request["id"] if request else None},
                      outgoing={"snapshot": request["snapshot"], "bytes": snapshot.stat().st_size,
                                "sha256": snapshot.stem, "captured_at": request["fetched"],
                                "complete": complete, "truncated": bool(request["truncated"]),
                                "url": f"/api/runs/{run['id']}/artifacts/raw"} if stored else None),
            operation("clean", "T", "整理正文", clean_status,
                      f"正文 v{source_id} · {len(doc['text']):,} 字符" if doc else
                      clean.get("interruption_reason") or ((clean["attempts"][-1].get("output") or {}).get("error") if clean["attempts"] else None) or "从已保存快照整理，不联网",
                      executor="Python", group="clean", incoming={"request_id": request["id"] if request else None},
                      outgoing={"version_id": source_id, "content_hash": version["content_hash"],
                                "characters": len(doc["text"]), "extraction": doc.get("extraction"),
                                "url": f"/api/runs/{run['id']}/artifacts/text"} if doc else None,
                      detail={"attempts": len(clean["attempts"]), "selected_attempt": clean["attempts"][-1] if clean["attempts"] else None}),
            operation("core", "T", "价值判断与核心提取", core_status,
                      screen["summary"] if full or (same_source and screen["status"] in {"failed", "running"}) else "保留历史抽样；尚未生成全文核心" if screen.get("output") else "尚未生成全文核心",
                      executor=(prepared.get("execution") or {}).get("adapter", "所选模型"), group="insights",
                      incoming={"version_id": source_id, "model": (prepared.get("request") or {}).get("model"),
                                "provider": response.get("provider") or (prepared.get("execution") or {}).get("provider"),
                                "source_characters": prepared.get("source_characters")},
                      outgoing={"ready": core_ready, "recommendation": core.get("effective_recommendation"),
                                "points": len(core.get("judgment", {}).get("points", [])),
                                "quote_alignment": core.get("quote_alignment"), "semantic_fidelity": core.get("semantic_fidelity"),
                                "usage": response.get("usage"), "attempt_id": selected["id"] if selected else None} if full else None,
                      detail={"attempts": len((screen.get("trace") or {}).get("history", [])),
                              "selected_attempt_id": selected["id"] if selected else None,
                              "event_count": response.get("event_count"), "terminal_status": response.get("terminal_status")}),
            operation("translate", "T", "中文副本", translation_status,
                      translated["summary"] if translation_started else "可选；未启用时不调用模型，不阻止阅读",
                      executor=native.get("provider") or (translated.get("output") or {}).get("origin") or "所选模型",
                      group="translation", incoming={"version_id": source_id, "model": native.get("model"),
                          "provider": native.get("provider"), "effort": native.get("effort") or native.get("model_configuration", {}).get("effort"),
                          "budget": translation_plan.get("budget")}, outgoing=translated.get("output"),
                      required=False, detail={"attempts": len(native.get("attempts", [])),
                          "plan": {k: v for k, v in translation_plan.items() if k != "native"}}),
        ]
        capture_action = {"kind": "stage", "stage": "fetch", "label": "重试采集" if capture_status == "failed" else "获取并保存来源"}
        clean_action = {"kind": "stage", "stage": "extract", "label": "重试正文整理" if clean_status == "failed" else "整理正文"}
        core_action = {"kind": "insights", "label": "重试核心提取" if insights["action"] == "retry" else "判断价值并提取核心"}
        actions = {
            "fetch": capture_action if capture_status in {"not_started", "failed"} else None,
            "clean": clean_action if complete and clean_status in {"not_started", "failed"} else None,
            "core": core_action if complete and insights["action"] else None,
            "translate": {"kind": "translation", "label": "继续未完成批次" if translation_plan.get("action") == "retry" else "生成中文副本"}
                         if core_ready and translation_plan.get("action") else None,
        }
        for operation in operations:
            operation["action"] = actions.get(operation["id"])
        operations[3]["input"].update(
            content_hash=prepared.get("content_hash"), prompt_version=prepared.get("prompt_version"),
            effort=(prepared.get("request") or {}).get("effort"), request_frozen=bool(selected),
        )
        required = operations[:4]
        failed = next((o for o in required if o["status"] == "failed"), None)
        running = next((o for o in required if o["status"] == "running"), None)
        if failed or running:
            status, summary = (failed or running)["status"], (failed or running)["summary"]
        elif core_status == "stopped":
            status, summary = "stopped", "筛选后停止；原文和判断原因已保留"
        elif core_ready:
            status, summary = "completed", "原文与核心可阅读"
            if translation_status == "running":
                summary += "；中文副本处理中"
            elif translation_status == "completed":
                summary += "；中文副本已保存"
            elif translation_status == "failed":
                summary += "；中文副本处理失败"
        elif doc:
            status, summary = "not_started", "原文可阅读；尚未生成全文核心"
        else:
            status, summary = "not_started", "等待保存和整理原文"
        phases = []
        for phase_id, title in (("E", "获取来源"), ("L", "原始入库"), ("T", "资料加工")):
            group = [o for o in operations if o["phase"] == phase_id and o["required"]]
            active = next((o for o in group if o["status"] in {"failed", "running", "stopped"}), None)
            phase_status = active["status"] if active else next((o["status"] for o in group if o["status"] != "completed"), "completed")
            phases.append({"id": phase_id, "title": title, "status": phase_status,
                           "label": labels.get(phase_status, phase_status), "operations": [o["id"] for o in operations if o["phase"] == phase_id]})
        return {"schema": "elt-v1", "status": status, "summary": summary, "phases": phases,
                "operations": operations, "capture_group_note": "E 与 L 共用一次采集尝试，分别展示响应与保存结果。",
                "result": {"original": bool(doc), "core": core_ready,
                           "translation": translated["status"] == "completed", "version_id": source_id,
                           "url": f"/read/{run['id']}" if doc else None,
                           "personal_notes": "optional; never generated by this view"}}

    @staticmethod
    def _pipeline_view(elt):
        """Present existing operations as three user steps; no new execution rules."""
        operations = elt["operations"]
        capture = operations[:3]
        focus = next((item for item in capture if item["status"] in {"failed", "running"}), None)
        if focus is None:
            focus = next((item for item in capture if item["status"] != "completed"), capture[-1])
        labels = LABELS | {"retained": "已保留", "not_enabled": "未启用"}
        original = {"id": "original", "title": "保存原文", "status": focus["status"],
                    "label": labels.get(focus["status"], focus["status"]),
                    "summary": focus["summary"], "operation_id": focus["id"],
                    "operation_ids": [item["id"] for item in capture], "optional": False}
        nodes = [original]
        for operation in operations[3:]:
            nodes.append({"id": operation["id"],
                          "title": "价值与核心" if operation["id"] == "core" else "中文副本",
                          "status": operation["status"], "label": operation["label"],
                          "summary": operation["summary"], "operation_id": operation["id"],
                          "operation_ids": [operation["id"]], "optional": not operation["required"]})
        return {"schema": "fixed-pipeline-v1", "nodes": nodes}

    def _source(self, run, version, outputs):
        attempts = [s for s in run["stages"] if s["id"] in {"fetch", "extract"}]
        active = next((s for s in attempts if s["status"] != "succeeded"), None)
        status = "completed" if version else (
            active["status"] if active and active["status"] in {"running", "failed"} else "not_started"
        )
        doc = version["document"] if version else None
        output = None
        if doc:
            output = {
                "version_id": version["id"], "content_hash": version["content_hash"],
                "characters": len(doc["text"]), "extraction": doc.get("extraction"),
                "source_html": render_markdown(doc["text"], doc["url"]) if outputs else None,
                "warnings": (doc.get("extraction") or {}).get("warnings", []),
            }
        error = (active.get("interruption_reason") or (active["attempts"][-1].get("output") or {}).get("error")) if active and active["attempts"] else None
        summary = f"原文 v{version['id']} · {len(doc['text']):,} 字符" if doc else error or "尚无保存的正文"
        return stage("source", "原文整理", status, summary, output=output, trace=run["stages"])

    def _screen(self, candidate, version, run_id, insights):
        if not version:
            return stage("screen", "价值筛选", "waiting", "等待原文整理完成")
        latest_insights = insights["latest"]
        if latest_insights:
            output = latest_insights["output"]
            if insights["expired"] and latest_insights["status"] in {"queued", "running"}:
                status, summary = "failed", insights["reason"]
            elif latest_insights["status"] in {"queued", "running"}:
                status, summary = "running", "全文分析等待执行" if latest_insights["status"] == "queued" else "全文分析进行中"
            elif latest_insights["status"] != "completed":
                status, summary = "failed", latest_insights["error"] or "全文分析未完成"
            else:
                recommendation = output["effective_recommendation"]
                status = "stopped" if recommendation in {"skip", "uncertain"} else "completed"
                summary = RECOMMENDATIONS[recommendation]
            return stage("screen", "核心提取", status, summary, output=output, trace={
                "selected_attempt_id": latest_insights["id"], "selected_attempt": latest_insights,
                "history": insights["history"], "legacy_screening_history": candidate["attempts"] if candidate else [],
                "rule": (output or {}).get("validation_version"),
            })
        if not candidate:
            return stage("screen", "价值筛选", "not_started", "没有此原文的筛选记录")
        preview = candidate.get("preview")
        latest = preview.get("latest") if preview else None
        # A previous source version must never become the current source's decision.
        if latest and latest["input"]["version_id"] != version["id"]:
            latest = None
        if not latest:
            return stage("screen", "价值筛选", "not_started", "尚未生成阅读建议")
        output = latest.get("current_output") or latest.get("output")
        if latest["status"] == "running":
            status, summary = "running", "筛选请求进行中"
        elif latest["status"] != "completed" or not output:
            status, summary = "failed", latest.get("error") or "筛选未完成"
        else:
            recommendation = output["effective_recommendation"]
            status = "stopped" if recommendation in {"skip", "uncertain"} else "completed"
            summary = RECOMMENDATIONS[recommendation]
        return stage(
            "screen", "价值筛选", status, summary,
            output=output, trace={
                "selected_attempt_id": latest["id"],
                "selected_attempt": latest,
                "history": candidate["attempts"],
                "rule": (output or {}).get("validation_version"),
                "scope": (output or {}).get("coverage"),
            },
        )

    def _translated(self, run, version, screen, outputs, detail):
        if not version:
            return stage("translate", "全文翻译", "waiting", "等待原文整理完成"), None
        plan = self.translation.plan(run["id"])
        native = plan["native"] if detail == "summary" else self.translation.inspect(run["id"], plan["native"].get("id"))
        results = self.results.inspect(run["id"])
        current = next((r for r in results["results"] if r["current"]), None)
        payload = current["payload"]["segments"] if current else None
        checks = current["checks"] if current else None
        # Native completed batches can be displayed without persisting a second artifact.
        if not current and native["status"] == "succeeded":
            payload = [{"index": p["index"], "text": p["translation"]} for p in native["segments"]]
            checks = check_result(results["task"]["segments"], payload)
        if plan["active"] and not plan["expired"]:
            status, summary = "running", f"中文副本处理中 · 已保存 {native['completed']}/{native['total']} 批"
        elif checks and checks["status"] != "passed":
            status, summary = "failed", f"译文有 {len(checks['issues'])} 项结构问题"
        elif checks:
            status, summary = "completed", "中文副本已生成 · 尚需阅读核对"
        elif plan["expired"]:
            status, summary = "failed", plan["reason"]
        elif plan["latest"] and plan["latest"]["status"] in {"failed", "interrupted"}:
            status, summary = "failed", plan["latest"]["error"] or "中文副本未完成"
        elif native["status"] in {"running", "failed"}:
            status = native["status"]
            error = next((a.get("error") for a in reversed(native["attempts"]) if a.get("error")), None)
            summary = error or f"已完成 {native['completed']}/{native['total']} 批"
        elif native["status"] == "partial":
            status, summary = "not_started", f"已保存 {native['completed']}/{native['total']} 批，尚未完成"
        elif screen["status"] == "stopped":
            status, summary = "not_started", "筛选未通过，未执行翻译"
        elif screen["status"] != "completed":
            status, summary = "waiting", "等待价值筛选完成"
        else:
            status, summary = "not_started", "尚未开始翻译"
        translation_html = None
        if payload and checks["status"] == "passed" and outputs:
            translation_html = render_markdown("".join(p["text"] for p in payload), run["url"])
        return stage(
            "translate", "全文翻译", status, summary,
            output={
                "translation_html": translation_html, "checks": checks,
                "completed": native["completed"], "total": native["total"],
                "origin": current["payload"]["origin"] if current else native["origin"],
                "model": current["payload"].get("model") if current else native["model"],
            },
            trace={"native": native, "plan": plan, "received_results": results["results"]},
        ), current

    def _review(self, run, translated, current, insight_state):
        insights = insight_state["latest"]
        if insights and insights["status"] == "completed" and insights["output"]["core_available"]:
            personal = self.curation.inspect(run["id"])
            return stage("review", "阅读与笔记", "pending_review", "核心与原文可阅读；个人笔记可选",
                         output={"curation": personal["latest"], "review": None},
                         trace={"curation_history": personal["history"]}) | {"label": "可阅读"}
        if translated["status"] != "completed":
            return stage("review", "最终审核", "waiting", "等待完整中文副本")
        personal = self.curation.inspect(run["id"])
        record = personal["latest"]
        reviews = current["reviews"] if current else []
        last = reviews[-1] if reviews else None
        accepted = bool(
            last and last["origin"] == "human" and last["payload"]["verdict"] == "accepted"
        )
        status = "completed" if accepted else "pending_review"
        return stage(
            "review", "最终审核", status,
            "已有本人审核记录" if accepted else "中文副本可阅读，尚无最终审核记录",
            output={"curation": record, "review": last},
            trace={"curation_history": personal["history"], "review_history": reviews},
        )
