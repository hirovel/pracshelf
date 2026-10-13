"""Private reading views of saved sources; public snapshots are a separate Module."""

import json
import re
from html import escape
from urllib.parse import urljoin, urlparse

from markdown_it import MarkdownIt

from .article_classification import ArticleClassification
from .article_insights import ArticleInsights
from .curation import Curation
from .processing import ProcessingResults


def render_markdown(text, source_url=None):
    # Untrusted source HTML stays text. No remote image loads in the private reader.
    parser = MarkdownIt("commonmark", {"html": False}).enable("table")
    parser.renderer.rules["image"] = lambda tokens, idx, options, env: (
        '<span class="image-note">[图片：' + escape(tokens[idx].content) + "]</span>"
    )
    tokens = parser.parse(text)
    if source_url:
        for block in tokens:
            for token in block.children or []:
                if token.type == "link_open":
                    target = urljoin(source_url, token.attrGet("href") or "")
                    if parser.validateLink(target):
                        token.attrSet("href", parser.normalizeLink(target))
                        token.attrSet("rel", "noopener noreferrer")
                        token.attrSet("target", "_blank")
                    else:
                        token.attrs.pop("href", None)
    return parser.renderer.render(tokens, parser.options, {})


class Library:
    def __init__(self, pipeline):
        self.flow = pipeline
        self.results = ProcessingResults(pipeline.store)
        self.curation = Curation(pipeline.store, origin=pipeline.reviews.origin)
        self.insights = ArticleInsights(pipeline)
        self.classifications = ArticleClassification(pipeline.store)

    def _entry(self, run, result_id=None):
        if not run["review"]["available"]:
            return None
        version = self.flow.store.version(run["review"]["version_id"])
        doc = version["document"]
        state = self.results.inspect(run["id"])
        result = next((r for r in state["results"] if r["current"]), None)
        latest_id = result["id"] if result else None
        if result_id is not None:
            result = next((r for r in state["results"] if r["id"] == result_id and r["current"]), None)
            if result is None:
                raise ValueError("此副本不属于当前原文版本。")
        human = result["reviews"][-1] if result and result["reviews"] else None
        note_record = human or run["review"]["latest"]
        curation = self.curation.inspect(run["id"])
        personal = curation["latest"]
        accepted = bool(
            human and human["origin"] == "human" and human["payload"]["verdict"] == "accepted"
        )
        # A broken result stays observable in the workbench, never masquerades as full text.
        structurally_complete = bool(result and result["checks"]["status"] == "passed")
        paragraphs = [
            p.strip()
            for p in doc["text"].split("\n\n")
            if p.strip() and not p.lstrip().startswith(("#", "*", "`", "|", "["))
        ]
        excerpt = re.sub(r"\s+", " ", paragraphs[0] if paragraphs else doc["text"])[:220]
        return {
            "run_id": run["id"],
            "version_id": version["id"],
            "key": version["document_key"],
            "title": doc["title"].rstrip("¶ "),
            "author": doc.get("author"),
            "url": doc["url"],
            "domain": urlparse(doc["url"]).netloc,
            "saved": run["created"],
            "published": doc.get("published"),
            "classification": self.classifications.inspect(version['id']),
            "excerpt": excerpt,
            "has_translation": structurally_complete,
            "translation_checks": result["checks"] if result else None,
            "status": "演示资料"
            if run["review"]["origin"] == "demo"
            or (result and result["payload"]["origin"] == "demo")
            else "已验收副本"
            if accepted
            else "中文待核对"
            if structurally_complete
            else "已保存原文",
            "curation": {k: v for k, v in curation.items() if k != "history"},
            "needs_reassessment": bool(curation["history"] and not personal),
            "note": personal["payload"]["note"]
            if personal and personal["origin"] == "human"
            else "",
            "review_note": note_record["payload"]["note"]
            if note_record and note_record["origin"] == "human"
            else "",
            "result": result,
            "selected_result_id": result["id"] if result else None,
            "latest_result_id": latest_id,
            "copies": [{"id": r["id"], "created": r["created"], "origin": r["payload"]["origin"],
                        "base_result_id": r["execution"].get("base_result_id"), "checks": r["checks"]["status"]}
                       for r in state["results"] if r["current"]],
            "source": doc["text"],
            "task": state["task"],
        }

    def list(self):
        entries = {}
        for run in self.flow.list():
            item = self._entry(run)
            if not item:
                continue
            previous = entries.get(item["key"])
            # Newer source versions win. On the same version prefer a processed reading copy.
            if previous is None or (item["version_id"], bool(item["result"]), item["saved"]) > (
                previous["version_id"],
                bool(previous["result"]),
                previous["saved"],
            ):
                entries[item["key"]] = item
        return [
            {k: v for k, v in item.items() if k not in {"result", "source", "task", "key"}}
            for item in sorted(entries.values(), key=lambda x: x["saved"], reverse=True)
        ]

    def article(self, run_id, result_id=None):
        item = self._entry(self.flow.inspect(run_id), result_id)
        if not item:
            raise ValueError("这条任务尚无可阅读的正文，请先到工作台提取。")
        result = item.pop("result")
        source, task = item.pop("source"), item.pop("task")
        item.pop("key")
        item["source_html"] = render_markdown(source, item["url"])
        item["translation_html"] = None
        item["pairs"] = []
        item["translation_origin"] = result["payload"]["origin"] if result else None
        history = self.insights.inspect(run_id, detail="summary")["history"]
        saved = next((row for row in reversed(history)
                      if row["status"] == "completed"
                      and row["input"]["version_id"] == item["version_id"]), None)
        # Reading uses saved output for this source version, even if model settings changed.
        # It neither regenerates output nor treats quote alignment as semantic approval.
        item["insights"] = None if saved is None else {
            "id": saved["id"], "version_id": saved["input"]["version_id"],
            "started": saved["started"], "finished": saved["finished"],
            "current_settings": saved["current"], "output": saved["output"],
            "request_count": (saved["response"] or {}).get("request_count"),
            "observation": saved["observation"],
        }
        row = self.flow.store.db.execute(
            "SELECT id,input FROM candidates WHERE pipeline_id=? ORDER BY created DESC LIMIT 1",
            (run_id,),
        ).fetchone()
        item["discovery"] = None if row is None else {
            "candidate_id": row["id"], **json.loads(row["input"]),
        }
        item["editable"] = bool(item["has_translation"] and result["id"] == item["latest_result_id"])
        if item["has_translation"]:
            translated = sorted(result["payload"]["segments"], key=lambda p: p["index"])
            item["translation_html"] = render_markdown(
                "".join(p["text"] for p in translated), item["url"]
            )
            item["pairs"] = [
                {
                    "source": render_markdown(p["source"], item["url"]),
                    "translation": render_markdown(t["text"], item["url"]),
                    "index": p["index"], "kind": p["kind"], "text": t["text"],
                }
                for p, t in zip(task["segments"], translated, strict=True)
            ]
        return item
