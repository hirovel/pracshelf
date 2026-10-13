"""Keep response bytes, document versions and run history independently."""

import hashlib
import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path


def now():
    return datetime.now(UTC).isoformat()


class Store:
    def __init__(self, root: Path):
        self.root = root
        (root / "raw").mkdir(parents=True, exist_ok=True)
        (root / "human-notes").mkdir(exist_ok=True)
        self.db = sqlite3.connect(root / "library.sqlite3")
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA foreign_keys = ON")
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS runs (
                id INTEGER PRIMARY KEY, started TEXT, finished TEXT,
                kind TEXT, input TEXT, options TEXT, result TEXT
            );
            CREATE TABLE IF NOT EXISTS requests (
                id INTEGER PRIMARY KEY, run_id INTEGER REFERENCES runs(id),
                fetched TEXT, requested_url TEXT, final_url TEXT, status INTEGER,
                content_type TEXT, snapshot TEXT, truncated INTEGER, error TEXT
            );
            CREATE TABLE IF NOT EXISTS versions (
                id INTEGER PRIMARY KEY, document_key TEXT, content_hash TEXT,
                document TEXT, created TEXT,
                UNIQUE(document_key, content_hash)
            );
            CREATE TABLE IF NOT EXISTS documents (
                document_key TEXT PRIMARY KEY, latest_version INTEGER REFERENCES versions(id)
            );
            CREATE TABLE IF NOT EXISTS observations (
                request_id INTEGER REFERENCES requests(id), version_id INTEGER REFERENCES versions(id),
                UNIQUE(request_id, version_id)
            );
        """)

    def start(self, kind, value, options):
        with self.db:
            return self.db.execute(
                "INSERT INTO runs(started,kind,input,options) VALUES(?,?,?,?)",
                (now(), kind, value, json.dumps(options)),
            ).lastrowid

    def finish(self, run_id, result):
        with self.db:
            self.db.execute(
                "UPDATE runs SET finished=?, result=? WHERE id=?",
                (now(), json.dumps(result, ensure_ascii=False), run_id),
            )

    def request(
        self,
        run_id,
        url,
        *,
        body=None,
        final_url=None,
        status=None,
        content_type=None,
        truncated=False,
        error=None,
    ):
        snapshot = None
        if body is not None:
            digest = hashlib.sha256(body).hexdigest()
            snapshot = f"raw/{digest}.bin"
            target = self.root / snapshot
            if not target.exists():
                temporary = target.with_suffix(".tmp")
                temporary.write_bytes(body)
                temporary.replace(target)
        with self.db:
            request_id = self.db.execute(
                """INSERT INTO requests(run_id,fetched,requested_url,final_url,status,
                   content_type,snapshot,truncated,error) VALUES(?,?,?,?,?,?,?,?,?)""",
                (run_id, now(), url, final_url, status, content_type, snapshot, truncated, error),
            ).lastrowid
        return request_id

    def save(self, key, document, request_id):
        encoded = json.dumps(document, ensure_ascii=False, sort_keys=True)
        digest = hashlib.sha256(encoded.encode()).hexdigest()
        with self.db:
            cursor = self.db.execute(
                """INSERT OR IGNORE INTO versions(document_key,content_hash,document,created)
                   VALUES(?,?,?,?)""",
                (key, digest, encoded, now()),
            )
            added = cursor.rowcount
            version_id = self.db.execute(
                "SELECT id FROM versions WHERE document_key=? AND content_hash=?",
                (key, digest),
            ).fetchone()[0]
            self.db.execute(
                "INSERT OR REPLACE INTO documents VALUES(?,?)",
                (key, version_id),
            )
            self.db.execute(
                "INSERT OR IGNORE INTO observations VALUES(?,?)",
                (request_id, version_id),
            )
        return added

    def documents(self):
        return [
            dict(row) | {"document": json.loads(row["document"])}
            for row in self.db.execute(
                """SELECT d.document_key, v.id AS version_id, v.document FROM documents d
               JOIN versions v ON v.id=d.latest_version ORDER BY v.id DESC"""
            )
        ]

    def runs(self):
        return [dict(row) for row in self.db.execute("SELECT * FROM runs ORDER BY id DESC")]

    def version(self, version_id: int):
        """Read an exact immutable version, including its original response evidence."""
        row = self.db.execute("SELECT * FROM versions WHERE id=?", (version_id,)).fetchone()
        if row is None:
            raise ValueError(f"Unknown document version: {version_id}")
        return dict(row) | {
            "document": json.loads(row["document"]),
            "evidence": [
                dict(item)
                for item in self.db.execute(
                    """SELECT r.id AS request_id, r.fetched, r.snapshot, r.truncated
                   FROM observations o JOIN requests r ON r.id=o.request_id
                   WHERE o.version_id=? ORDER BY r.id""",
                    (version_id,),
                )
            ],
        }

    def captured_request(self, request_id: int):
        row = self.db.execute("SELECT * FROM requests WHERE id=?", (request_id,)).fetchone()
        if row is None:
            raise ValueError(f"Unknown request: {request_id}")
        return dict(row)

    def export(self):
        """Derived reader views can be rebuilt. Never write to human-notes/."""
        folder = self.root / "reading"
        folder.mkdir(exist_ok=True)
        index = [
            "# 采集阅读入口",
            "",
            "采集候选，不是当前实践推荐。正文完整性、当前适用性和实际效果尚未核实。",
            "",
        ]
        for row in self.documents():
            doc = row["document"]
            filename = f"{row['version_id']}.md"
            evidence = self.db.execute(
                """SELECT r.* FROM observations o JOIN requests r ON r.id=o.request_id
                   WHERE o.version_id=? ORDER BY r.id DESC LIMIT 1""",
                (row["version_id"],),
            ).fetchone()
            snapshot = Path("..") / evidence["snapshot"]
            lines = [
                f"# {doc['title']}",
                "",
                f"来源：{doc['url']}",
                f"作者：{doc.get('author', '')}",
                f"原文日期：{doc.get('published') or '未提取到'}",
                "当前适用性：未核实；效果：未验证。以下保存原文，不构成当前做法推荐。",
                f"类型：{doc['kind']}",
                f"保存状态：{doc['completeness']}",
                f"采集时间（UTC）：{evidence['fetched']}",
                f"原始响应：[文件]({snapshot.as_posix()})",
                "",
                "## 采集文本（未生成总结）",
                "",
                doc["text"],
                "",
            ]
            (folder / filename).write_text("\n".join(lines), encoding="utf-8")
            safe_title = doc["title"].replace("[", "(").replace("]", ")").replace("\n", " ")
            index.append(f"- [{safe_title}]({filename}) — {doc['kind']}")
        path = folder / "index.md"
        path.write_text("\n".join(index) + "\n", encoding="utf-8")
        return path

    def close(self):
        self.db.close()
