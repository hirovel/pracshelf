"""RSS selection state; Collector owns fetching, Candidates owns full-text capture."""

import json
from html.parser import HTMLParser

from .candidates import Candidates, date_check
from .collector import web_url
from .storage import now


class _PlainText(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts = []

    def handle_data(self, data):
        self.parts.append(data)


def plain_text(value):
    parser = _PlainText()
    parser.feed(value or "")
    return " ".join(" ".join(parser.parts).split())


class FeedInbox:
    def __init__(self, pipeline):
        self.flow, self.store = pipeline, pipeline.store
        self.candidates = Candidates(pipeline)
        self.store.db.executescript("""
            CREATE TABLE IF NOT EXISTS inbox_feeds (
                url TEXT PRIMARY KEY, created TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS inbox_items (
                id INTEGER PRIMARY KEY, feed_url TEXT NOT NULL REFERENCES inbox_feeds(url),
                url TEXT NOT NULL, created TEXT NOT NULL, seen TEXT NOT NULL,
                version_id INTEGER NOT NULL REFERENCES versions(id),
                decision TEXT NOT NULL DEFAULT 'pending',
                candidate_id TEXT REFERENCES candidates(id), UNIQUE(feed_url,url)
            );
            CREATE TABLE IF NOT EXISTS inbox_decisions (
                id INTEGER PRIMARY KEY, item_id INTEGER NOT NULL REFERENCES inbox_items(id),
                action TEXT NOT NULL, created TEXT NOT NULL
            );
        """)

    def refresh(self, url):
        url = web_url(url.strip())
        with self.store.db:
            self.store.db.execute("INSERT OR IGNORE INTO inbox_feeds VALUES(?,?)", (url, now()))
        result = self.flow.collector.collect("feed", url, limit=30)
        for version_id in result.get("version_ids", []):
            document = self.store.version(version_id)["document"]
            with self.store.db:
                self.store.db.execute(
                    """
                    INSERT INTO inbox_items(feed_url,url,created,seen,version_id)
                    VALUES(?,?,?,?,?) ON CONFLICT(feed_url,url) DO UPDATE SET
                        seen=excluded.seen, version_id=excluded.version_id
                """,
                    (url, document["url"], now(), now(), version_id),
                )
        return result

    def inspect(self, item_id):
        row = self.store.db.execute("SELECT * FROM inbox_items WHERE id=?", (item_id,)).fetchone()
        if not row:
            raise ValueError("RSS 候选不存在。")
        item = dict(row)
        document = self.store.version(row["version_id"])["document"]
        summary = plain_text(document["text"])
        item.update(
            title=plain_text(document["title"]),
            author=plain_text(document.get("author")),
            summary=summary[:600],
            summary_truncated=len(summary) > 600,
            date_raw=document.get("published"),
            date_kind=document.get("date_kind"),
            date_iso=document.get("date_iso"),
            date_check=date_check({"published": document.get("date_iso")}, now()[:10], 90),
            decisions=[
                dict(r)
                for r in self.store.db.execute(
                    "SELECT action,created FROM inbox_decisions WHERE item_id=? ORDER BY id",
                    (item_id,),
                )
            ],
        )
        item["capture"] = None
        item["preview"] = None
        if item["candidate_id"]:
            candidate = self.candidates.inspect(item["candidate_id"], include_trace=False)
            item["preview"] = candidate["preview"]
            item["capture"] = {
                "pipeline_id": candidate["pipeline_id"],
                "version_id": candidate["version_id"],
                **candidate["processing"],
            }
        return item

    def list(self, *, state="pending", q="", freshness="all", feed=""):
        if state not in {"all", "pending", "ignored", "selected"}:
            raise ValueError("未知的收件箱状态。")
        if freshness not in {
            "all",
            "within_window",
            "outside_window",
            "unknown_date",
            "future_date",
        }:
            raise ValueError("未知的日期条件。")
        items = [
            self.inspect(r[0])
            for r in self.store.db.execute("SELECT id FROM inbox_items ORDER BY id DESC")
        ]
        items.sort(key=lambda item: item["date_iso"] or "", reverse=True)
        query = q.strip().casefold()
        return [
            item
            for item in items
            if (state == "all" or item["decision"] == state)
            and (not feed or item["feed_url"] == feed)
            and (freshness == "all" or item["date_check"]["status"] == freshness)
            and (
                not query
                or query
                in " ".join(
                    [item["title"], item["summary"], item["author"], item["url"]]
                ).casefold()
            )
        ]

    def feeds(self):
        result = []
        for row in self.store.db.execute("SELECT * FROM inbox_feeds ORDER BY created"):
            attempts = []
            for run in self.store.db.execute(
                "SELECT id,started,finished,result FROM runs WHERE kind='feed' AND input=? ORDER BY id DESC LIMIT 10",
                (row["url"],),
            ):
                attempts.append(
                    dict(run) | {"result": json.loads(run["result"]) if run["result"] else None}
                )
            result.append(dict(row) | {"attempts": attempts})
        return result

    def decide(self, item_id, action, submission_id=""):
        if action not in {"save", "ignore", "restore"}:
            raise ValueError("未知的候选操作。")
        if action == "save" and not 1 <= len(submission_id) <= 80:
            raise ValueError("保存全文需要提交标识。")
        item = self.inspect(item_id)
        if action != "save":
            with self.store.db:
                self.store.db.execute("BEGIN IMMEDIATE")
                current = self.inspect(item_id)
                decision = (
                    "ignored"
                    if action == "ignore"
                    else "selected"
                    if current["candidate_id"]
                    else "pending"
                )
                if current["decision"] != decision:
                    self.store.db.execute(
                        "UPDATE inbox_items SET decision=? WHERE id=?", (decision, item_id)
                    )
                    self.store.db.execute(
                        "INSERT INTO inbox_decisions(item_id,action,created) VALUES(?,?,?)",
                        (item_id, action, now()),
                    )
            return self.inspect(item_id)
        if not item["candidate_id"]:
            candidate = self.candidates.import_link(
                {
                    "submission_id": f"rss-item-{item_id}",
                    "url": item["url"],
                    "origin": "rss",
                    "source_note": f"RSS: {item['feed_url']}",
                    "goal": "学习高级开发者使用 AI 的实践、工作流与适用条件。",
                    "as_of": item["created"][:10],
                    "max_age_days": 90,
                }
            )
            with self.store.db:
                changed = self.store.db.execute(
                    "UPDATE inbox_items SET decision='selected',candidate_id=? WHERE id=? AND candidate_id IS NULL",
                    (candidate["id"], item_id),
                ).rowcount
                if changed:
                    self.store.db.execute(
                        "INSERT INTO inbox_decisions(item_id,action,created) VALUES(?,?,?)",
                        (item_id, "save", now()),
                    )
            item = self.inspect(item_id)
        self.candidates.process(item["candidate_id"], submission_id)
        return self.inspect(item_id)
