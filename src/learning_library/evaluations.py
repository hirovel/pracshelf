"""Freeze existing judgments; collect independent human labels and local metrics.

No model or network execution. A case pins the source and a specific attempt,
including failures. Changing prompts or re-extracting cannot change that case.
"""

import json
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from .candidates import Candidates
from .review import ReviewConflict
from .storage import now
from .value_preview import revalidate


class FreezeInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    screening_id: int = Field(gt=0, strict=True)
    split: Literal["development", "test"]


class LabelInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    submission_id: str = Field(min_length=1, max_length=80, pattern=r"^[a-zA-Z0-9_-]+$")
    previous_id: int | None = Field(..., gt=0, strict=True)
    reading: Literal["read", "skip", "uncertain"]
    fidelity: Literal["passed", "failed", "unknown"]
    conditions: Literal["passed", "failed", "unknown"]
    reason: str = Field(min_length=1, max_length=1500)


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


class Evaluations:
    def __init__(self, pipeline, *, origin="human"):
        if origin not in {"human", "demo"}:
            raise ValueError("Unsupported evaluation label origin")
        self.store, self.origin = pipeline.store, origin
        Candidates(pipeline)  # Owns the input tables; does not execute a request.
        self.store.db.executescript("""
            CREATE TABLE IF NOT EXISTS evaluation_cases (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                screening_id INTEGER NOT NULL UNIQUE REFERENCES candidate_screenings(id),
                document_key TEXT NOT NULL UNIQUE,
                split TEXT NOT NULL CHECK(split IN ('development','test')),
                created TEXT NOT NULL,
                snapshot TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS evaluation_labels (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                case_id INTEGER NOT NULL REFERENCES evaluation_cases(id),
                submission_id TEXT NOT NULL,
                created TEXT NOT NULL,
                origin TEXT NOT NULL CHECK(origin IN ('human','demo')),
                payload TEXT NOT NULL,
                UNIQUE(case_id, submission_id)
            );
        """)

    def available(self):
        """Only finalized attempts can be frozen; failures are valid evaluation data."""
        rows = self.store.db.execute("""
            SELECT s.*, e.id AS case_id FROM candidate_screenings s
            LEFT JOIN evaluation_cases e ON e.screening_id=s.id
            WHERE s.status!='running' ORDER BY s.id DESC
        """)
        results = []
        for row in rows:
            incoming = json.loads(row["input"])
            source = self.store.version(incoming["version_id"])
            results.append({
                "screening_id": row["id"], "status": row["status"],
                "started": row["started"], "version_id": source["id"],
                "title": source["document"]["title"], "case_id": row["case_id"],
            })
        return results

    def freeze(self, payload: FreezeInput):
        with self.store.db:
            self.store.db.execute("BEGIN IMMEDIATE")
            existing = self.store.db.execute(
                "SELECT id,split FROM evaluation_cases WHERE screening_id=?",
                (payload.screening_id,),
            ).fetchone()
            if existing:
                if existing["split"] != payload.split:
                    raise ReviewConflict("样本分组已固定，不能把调试资料改为独立测试资料。")
                return self.inspect(existing["id"])
            row = self.store.db.execute(
                "SELECT * FROM candidate_screenings WHERE id=?", (payload.screening_id,),
            ).fetchone()
            if not row or row["status"] == "running":
                raise ValueError("请选择一条已结束的价值判断记录。")
            attempt = dict(row)
            for key in ("input", "response", "output"):
                attempt[key] = json.loads(attempt[key]) if attempt[key] else None
            source = self.store.version(attempt["input"]["version_id"])
            if source["content_hash"] != attempt["input"]["content_hash"]:
                raise ValueError("判断输入与保存的原文哈希不一致，不能固定样本。")
            if self.store.db.execute(
                "SELECT 1 FROM evaluation_cases WHERE document_key=?", (source["document_key"],),
            ).fetchone():
                raise ReviewConflict("此资料已有固定样本；同一资料不能重复计数或跨入测试组。")
            candidate = self.store.db.execute(
                "SELECT input,pipeline_id FROM candidates WHERE id=?", (row["candidate_id"],),
            ).fetchone()
            snapshot = {
                "schema_version": 1, "source": source, "attempt": attempt,
                "provenance": json.loads(candidate["input"]),
                "pipeline_id": candidate["pipeline_id"],
                "material_origin": "demo"
                if json.loads(candidate["input"])["origin"] == "demo" else "real",
            }
            if attempt["output"]:
                snapshot["validation"] = revalidate(
                    attempt["input"], attempt["output"], source["document"]["text"],
                )
            case_id = self.store.db.execute(
                """INSERT INTO evaluation_cases(screening_id,document_key,split,created,snapshot)
                   VALUES(?,?,?,?,?)""",
                (payload.screening_id, source["document_key"], payload.split, now(), encoded(snapshot)),
            ).lastrowid
        return self.inspect(case_id)

    def inspect(self, case_id):
        row = self.store.db.execute(
            "SELECT * FROM evaluation_cases WHERE id=?", (case_id,),
        ).fetchone()
        if not row:
            raise ValueError("评测样本不存在。")
        history = [
            dict(label) | {"payload": json.loads(label["payload"])}
            for label in self.store.db.execute(
                "SELECT * FROM evaluation_labels WHERE case_id=? ORDER BY id", (case_id,),
            )
        ]
        snapshot = json.loads(row["snapshot"])
        attempt = snapshot["attempt"]
        current_validation = revalidate(
            attempt["input"], attempt["output"], snapshot["source"]["document"]["text"],
        ) if attempt["output"] else None
        return dict(row) | {
            "snapshot": snapshot, "current_validation": current_validation, "history": history,
            "latest": history[-1] if history else None,
        }

    def label(self, case_id, payload: LabelInput):
        text = encoded(payload.model_dump())
        with self.store.db:
            self.store.db.execute("BEGIN IMMEDIATE")
            state = self.inspect(case_id)
            existing = self.store.db.execute(
                "SELECT * FROM evaluation_labels WHERE case_id=? AND submission_id=?",
                (case_id, payload.submission_id),
            ).fetchone()
            if existing:
                if existing["payload"] != text or existing["origin"] != self.origin:
                    raise ReviewConflict("同一提交编号不能保存不同的评测标签。")
                return state
            if payload.previous_id != (state["latest"]["id"] if state["latest"] else None):
                raise ReviewConflict("评测标签已更新，请刷新后合并你的判断。")
            self.store.db.execute(
                """INSERT INTO evaluation_labels(case_id,submission_id,created,origin,payload)
                   VALUES(?,?,?,?,?)""",
                (case_id, payload.submission_id, now(), self.origin, text),
            )
        return self.inspect(case_id)

    def report(self, split: Literal["development", "test"] = "test"):
        if split not in {"development", "test"}:
            raise ValueError("Unsupported evaluation split")
        rows = [self.inspect(row[0]) for row in self.store.db.execute(
            "SELECT id FROM evaluation_cases WHERE split=? ORDER BY id", (split,),
        )]
        counts = dict.fromkeys((
            "included", "excluded_demo", "labeled", "unlabeled", "gold_uncertain",
            "true_positive", "false_positive", "true_negative", "false_negative",
            "abstained_positive", "abstained_negative", "failed_attempts",
            "model_abstentions",
            "quote_passed", "quote_failed", "quote_missing",
            "fidelity_passed", "fidelity_failed", "fidelity_unknown",
            "conditions_passed", "conditions_failed", "conditions_unknown",
        ), 0)
        usage = {"known_total_tokens": 0, "unknown_usage_cases": 0,
                 "application_requests": 0, "unknown_request_cases": 0,
                 "elapsed_seconds": 0, "unknown_duration_cases": 0}
        items, categories = [], {}
        for case in rows:
            snapshot, label = case["snapshot"], case["latest"]
            source, attempt = snapshot["source"], snapshot["attempt"]
            output = snapshot.get("validation", attempt["output"]) or {}
            response = attempt["response"] or {}
            prediction = output.get("effective_recommendation", "uncertain")
            category = output.get("judgment", {}).get("category", "unknown")
            included = snapshot["material_origin"] != "demo" and (
                not label or label["origin"] == "human"
            )
            items.append({
                "id": case["id"], "screening_id": case["screening_id"],
                "title": source["document"]["title"], "url": source["document"]["url"],
                "version_id": source["id"], "prediction": prediction,
                "reading": label["payload"]["reading"] if label else None,
                "category": category, "included": included,
                "material_origin": snapshot["material_origin"],
                "label_origin": label["origin"] if label else None,
            })
            if not included:
                counts["excluded_demo"] += 1
                continue
            counts["included"] += 1
            counts["model_abstentions"] += prediction not in {"read", "focus", "skip"}
            categories[category] = categories.get(category, 0) + 1
            counts["failed_attempts"] += attempt["status"] != "completed"
            alignment = output.get("quote_alignment")
            counts["quote_passed" if alignment == "passed" else
                   "quote_failed" if alignment == "failed" else "quote_missing"] += 1
            tokens = response.get("usage", {}).get("total_tokens") if response.get("usage") else None
            if isinstance(tokens, int) and not isinstance(tokens, bool) and tokens >= 0:
                usage["known_total_tokens"] += tokens
            else:
                usage["unknown_usage_cases"] += 1
            requests = response.get("request_count")
            if isinstance(requests, int) and not isinstance(requests, bool) and requests >= 0:
                usage["application_requests"] += requests
            else:
                usage["unknown_request_cases"] += 1
            try:
                usage["elapsed_seconds"] += max(0, (
                    datetime.fromisoformat(attempt["finished"]) -
                    datetime.fromisoformat(attempt["started"])
                ).total_seconds())
            except (ValueError, TypeError):
                usage["unknown_duration_cases"] += 1
            if not label:
                counts["unlabeled"] += 1
                counts["fidelity_unknown"] += 1
                counts["conditions_unknown"] += 1
                continue
            counts["labeled"] += 1
            gold = label["payload"]
            counts["fidelity_" + gold["fidelity"]] += 1
            counts["conditions_" + gold["conditions"]] += 1
            if gold["reading"] == "uncertain":
                counts["gold_uncertain"] += 1
            elif prediction in {"read", "focus"}:
                counts["true_positive" if gold["reading"] == "read" else "false_positive"] += 1
            elif prediction == "skip":
                counts["false_negative" if gold["reading"] == "read" else "true_negative"] += 1
            else:
                counts["abstained_positive" if gold["reading"] == "read" else "abstained_negative"] += 1
        tp, fp, fn = (counts[k] for k in ("true_positive", "false_positive", "false_negative"))
        gold_positive = tp + fn + counts["abstained_positive"]
        binary_labels = counts["labeled"] - counts["gold_uncertain"]
        decided = sum(counts[k] for k in (
            "true_positive", "false_positive", "true_negative", "false_negative",
        ))
        return {
            "split": split, "items": items, "counts": counts, "categories": categories,
            "metrics": {
                "recommendation_precision": tp / (tp + fp) if tp + fp else None,
                "fixed_set_recall": tp / gold_positive if gold_positive else None,
                "decision_coverage": decided / binary_labels if binary_labels else None,
            },
            "usage": usage | {"elapsed_seconds": round(usage["elapsed_seconds"], 3),
                              "reported_cost_usd": None},
            "limits": [
                "read 和 focus 都计为推荐；uncertain 为弃权，不计为跳过。",
                "召回率仅针对本组已标注样本；不是互联网搜索召回率。",
                "未标注和人工待定不计入推荐精度分母；演示资料或演示标签不计入正式统计。",
                "调试组与测试组分开；小样本和未完整标注不能证明整体推荐质量。",
                "逐字引文检查不证明理由忠实；语义与限定条件由人工分别判断。",
                "统计使用固定时的校验结果。当前规则复核单独展示，不改变已有样本和标签。",
                "同源检查使用保存的 document_key；不同 URL 的转载或格式副本仍需人工排查重复。",
                "用量是这些固定尝试的历史用量，查看或标注评测不会产生新请求；金额未计算。",
            ],
        }
