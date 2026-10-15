"""Build a local reading site from current, human-approved publication snapshots.

The interface owns selection validation, immutable inputs, execution records and
artifact access. The command runner is an internal seam for process-failure tests.
"""

import hashlib
import json
import logging
import os
import shutil
import subprocess
import uuid
from datetime import UTC, datetime, timedelta
from functools import partial
from pathlib import Path, PurePosixPath

from pydantic import BaseModel, ConfigDict, Field

from .publication import Publication, encode
from .review import ReviewConflict
from .storage import now


class BuildInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    submission_id: uuid.UUID
    preview_ids: list[int] = Field(max_length=100)
    allow_empty: bool = False


class BuildFailure(Exception):
    def __init__(self, message, log=""):
        super().__init__(message)
        self.log = log


def build_environment(frontend=None):
    """Local file/PATH inspection only; never installs or starts a process."""
    frontend = Path(frontend) if frontend else Path(__file__).resolve().parents[2] / "frontend"
    source = all((frontend / name).is_file() for name in (
        "package-lock.json", "scripts/reading.mjs", "reading/astro.config.mjs"
    ))
    node = shutil.which("node")
    dependencies = (frontend / "node_modules/astro/bin/astro.mjs").is_file()
    error = (
        "博客构建需要完整项目源码中的 frontend 目录；当前安装包只提供本地资料界面。"
        if not source else
        "博客构建需要 Node.js 22.12 或更高版本；请安装后重新启动工作台。"
        if not node else
        "前端构建依赖未安装；在项目 frontend 目录运行 npm ci，再刷新页面。"
        if not dependencies else None
    )
    return {"ready": error is None, "source_available": source,
            "node_available": bool(node), "dependencies_installed": dependencies,
            "required_node": ">=22.12.0", "node_version_checked": False,
            "error": error, "frontend_directory": str(frontend.resolve())}


def run_reading_build(input_dir: Path, build_root: Path, *, demo=False):
    environment = build_environment()
    if not environment["ready"]:
        raise BuildFailure(environment["error"])
    frontend = Path(__file__).resolve().parents[2] / "frontend"
    node = shutil.which("node")
    if not node:
        raise BuildFailure("缺少 Node.js；请安装后重新构建。")
    command = [
        node,
        str(frontend / "scripts" / "reading.mjs"),
        "build",
        "--input",
        str(input_dir),
        "--output-root",
        str(build_root.parent),
        "--build-id",
        build_root.name,
        "--no-activate",
    ]
    if demo:
        command.append("--demo")
    # Run only this fixed local script; no request field is interpreted as a command.
    process = subprocess.Popen(
        command,
        cwd=frontend,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        start_new_session=os.name != "nt",
    )
    try:
        output, _ = process.communicate(timeout=100)
    except subprocess.TimeoutExpired:
        if os.name == "nt":
            subprocess.run(
                ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                capture_output=True,
                check=False,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
        else:
            import signal

            os.killpg(process.pid, signal.SIGKILL)
        output, _ = process.communicate()
        raise BuildFailure("构建超过 100 秒，进程已终止。", output.decode("utf-8", "replace"))
    log = output.decode("utf-8", "replace")[-64000:]
    report_path = build_root / "report.json"
    report = json.loads(report_path.read_text(encoding="utf-8")) if report_path.exists() else {}
    if process.returncode or report.get("status") != "succeeded":
        raise BuildFailure(report.get("error") or f"构建进程退出，代码 {process.returncode}。", log)
    return report, log


class ReadingBuilds:
    def __init__(self, store, *, runner=None, demo=False):
        self.store = store
        self.demo = demo
        self.mode = "demo" if demo else "real"
        self.runner = runner or partial(run_reading_build, demo=demo)
        self.publication = Publication(store, origin="demo" if demo else "human")
        self.root = (store.root / "reading-builds" / ("web-demo" if demo else "web")).resolve()
        store.db.executescript("""
            CREATE TABLE IF NOT EXISTS reading_builds (
                id TEXT PRIMARY KEY, submission_id TEXT UNIQUE NOT NULL,
                request TEXT NOT NULL, created TEXT NOT NULL, finished TEXT,
                status TEXT NOT NULL, stage TEXT NOT NULL,
                inputs TEXT NOT NULL DEFAULT '[]', events TEXT NOT NULL DEFAULT '[]',
                log TEXT NOT NULL DEFAULT '', report TEXT, error TEXT
            );
            CREATE UNIQUE INDEX IF NOT EXISTS reading_one_running
                ON reading_builds(status) WHERE status='running';
        """)
        # A killed server cannot finish its request. The subprocess deadline is 100 s.
        cutoff = (datetime.now(UTC) - timedelta(seconds=180)).isoformat()
        with store.db:
            store.db.execute(
                """UPDATE reading_builds SET status='failed', finished=?,
                error='构建记录超过 180 秒未完成；工作台可能中断。可重新构建。'
                WHERE status='running' AND created < ?""",
                (now(), cutoff),
            )

    def _selection(self, preview_id):
        row = self.store.db.execute(
            "SELECT pipeline_id FROM publication_previews WHERE id=?", (preview_id,)
        ).fetchone()
        if not row:
            raise ValueError(f"找不到快照版本 {preview_id}。")
        state = self.publication.inspect(row["pipeline_id"])
        p = next(p for p in state["history"] if p["id"] == preview_id)
        reasons = list(p["blockers"])
        if not p["current"]:
            reasons.append("原文、笔记或预览已有变化，需要重新确认。")
        if not p["approval"]:
            reasons.append("这一版尚未人工确认。")
        elif (
            p["approval"]["origin"] != ("demo" if self.demo else "human")
            or p["approval"]["content_hash"] != p["content_hash"]
        ):
            reasons.append("确认来源或内容哈希不符合本次构建模式。")
        if p["public_note"]["content_origin"] != ("demo" if self.demo else "human"):
            reasons.append(
                "演示站只接受标为 demo 的测试笔记。"
                if self.demo else "此内容不是人工笔记，不能进入真实阅读站。"
            )
        return {
            "preview_id": preview_id,
            "run_id": row["pipeline_id"],
            "title": p["public_note"]["title"],
            "content_hash": p["content_hash"],
            "eligible": not reasons,
            "reasons": reasons,
        }

    def _job(self, row):
        job = dict(row)
        for key in ("request", "inputs", "events", "report"):
            job[key] = json.loads(job[key]) if job[key] else None
        job["mode"] = job["request"].get("mode", "real")
        job["preview_url"] = (
            f"/reading-preview/{job['id']}/" if job["status"] == "succeeded" else None
        )
        return job

    def inspect(self):
        candidates = [
            self._selection(r[0])
            for r in self.store.db.execute(
                "SELECT MAX(id) FROM publication_previews GROUP BY document_key ORDER BY MAX(id) DESC"
            ).fetchall()
        ]
        jobs = [
            self._job(r)
            for r in self.store.db.execute(
                "SELECT * FROM reading_builds ORDER BY created DESC LIMIT 30"
            )
        ]
        return {
            "candidates": candidates,
            "jobs": [job for job in jobs if job["mode"] == self.mode],
            "mode": self.mode,
            "environment": build_environment(),
            "model_calls": 0,
            "deployed": False,
        }

    def _record(self, id, stage, message, **values):
        events = json.loads(
            self.store.db.execute("SELECT events FROM reading_builds WHERE id=?", (id,)).fetchone()[
                0
            ]
        )
        events.append({"time": now(), "stage": stage, "message": message})
        values.update(stage=stage, events=encode(events))
        with self.store.db:
            self.store.db.execute(
                "UPDATE reading_builds SET " + ",".join(f"{k}=?" for k in values) + " WHERE id=?",
                (*values.values(), id),
            )

    def build(self, payload: BuildInput):
        ids = sorted(set(payload.preview_ids))
        if any(type(i) is not int or i <= 0 for i in ids) or len(ids) != len(payload.preview_ids):
            raise ValueError("快照编号必须为互不重复的正整数。")
        if not ids and not payload.allow_empty:
            raise ValueError("请选中已确认快照；构建空站需要明确选择。")
        request = encode({"preview_ids": ids, "allow_empty": payload.allow_empty, "mode": self.mode})
        id = str(uuid.uuid4())
        with self.store.db:
            self.store.db.execute("BEGIN IMMEDIATE")
            existing = self.store.db.execute(
                "SELECT * FROM reading_builds WHERE submission_id=?", (str(payload.submission_id),)
            ).fetchone()
            if existing:
                old_request = json.loads(existing["request"])
                old_request.setdefault("mode", "real")
                if old_request != json.loads(request):
                    raise ReviewConflict("同一提交编号对应不同选择，请刷新后重试。")
                return self._job(existing)
            if self.store.db.execute(
                "SELECT 1 FROM reading_builds WHERE status='running'"
            ).fetchone():
                raise ReviewConflict("已有构建正在运行，请等待结果。")
            self.store.db.execute(
                """INSERT INTO reading_builds
                (id,submission_id,request,created,status,stage) VALUES(?,?,?,?,'running','validate')""",
                (id, str(payload.submission_id), request, now()),
            )
        stage = "validate"
        log = ""
        try:
            self._record(id, stage, "正在检查所选快照的确认记录与当前版本。")
            inputs = [self._selection(i) for i in ids]
            for item in inputs:
                if not item["eligible"]:
                    raise ValueError(f"版本 {item['preview_id']}：" + " ".join(item["reasons"]))
            stage = "export"
            self._record(
                id, stage, f"将 {len(inputs)} 个已确认版本写入本次构建输入。", inputs=encode(inputs)
            )
            build_root = self.root / id
            directory = build_root / "snapshots"
            directory.mkdir(parents=True)
            for item in inputs:
                body = self.publication.export(item["run_id"], item["preview_id"], mode="approved")
                envelope = json.loads(body)
                if envelope["demo"] != self.demo or envelope["content_hash"] != item["content_hash"]:
                    raise ValueError("导出内容与选定快照或构建模式不一致。")
                (directory / f"snapshot-{item['preview_id']}.json").write_text(
                    body, encoding="utf-8"
                )
            stage = "build"
            self._record(id, stage, "正在运行 Astro 静态构建和产物检查。")
            report, log = self.runner(directory, build_root)
            stage = "verify"
            self._record(
                id,
                stage,
                "正在复核快照版本；构建期间的内容修改会使本次结果失效。",
                log=log[-64000:],
            )
            with self.store.db:
                self.store.db.execute("BEGIN IMMEDIATE")
                for item in inputs:
                    if not self._selection(item["preview_id"])["eligible"]:
                        raise ValueError("构建期间资料已变化，请重新预览并确认。")
                if report.get("status") != "succeeded" or report.get("notes") != len(inputs):
                    raise ValueError("构建报告状态或资料数量不符合输入。")
                if report.get("mode", "real") != self.mode:
                    raise ValueError("构建报告模式与本次输入不一致。")
                self._record(
                    id,
                    "complete",
                    f"构建完成，包含 {len(inputs)} 篇笔记。尚未部署。",
                    status="succeeded",
                    finished=now(),
                    report=encode(report),
                )
        except Exception as error:
            logging.getLogger(__name__).exception("Reading build %s failed at %s", id, stage)
            self._record(
                id,
                stage,
                "本次构建失败，历史成功产物保持不变。",
                status="failed",
                finished=now(),
                error=str(error),
                log=getattr(error, "log", log)[-64000:],
            )
        return self._job(
            self.store.db.execute("SELECT * FROM reading_builds WHERE id=?", (id,)).fetchone()
        )

    def artifact(self, id, name="index.html"):
        if str(uuid.UUID(id)) != id:
            raise ValueError("构建编号无效。")
        row = self.store.db.execute("SELECT * FROM reading_builds WHERE id=?", (id,)).fetchone()
        if not row or row["status"] != "succeeded":
            raise ValueError("没有可预览的成功构建。")
        if json.loads(row["request"]).get("mode", "real") != self.mode:
            raise ValueError("此产物不属于当前构建模式。")
        name = name or "index.html"
        if name.endswith("/"):
            name += "index.html"
        parts = PurePosixPath(name).parts
        if not parts or any(p in {"..", "."} for p in parts) or "\\" in name:
            raise ValueError("产物路径无效。")
        allowed = {a["file"]: a for a in json.loads(row["report"])["artifacts"]}
        if name not in allowed or (
            Path(name).suffix not in {".html", ".css", ".woff2"} and name != "FONT_LICENSES.txt"
        ):
            raise ValueError("此文件不属于公开阅读产物。")
        root = self.root / id / "site"
        path = root.joinpath(*parts)
        if root.is_symlink() or any(
            p.is_symlink() for p in [path, *path.parents] if p != self.root.parent
        ):
            raise ValueError("产物不可通过符号链接访问。")
        if not path.resolve().is_relative_to(root.resolve()) or not path.is_file():
            raise ValueError("产物不存在。")
        if hashlib.sha256(path.read_bytes()).hexdigest() != allowed[name]["sha256"]:
            raise ValueError("产物校验失败，文件在构建后被修改。")
        return path
