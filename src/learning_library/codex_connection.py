"""Local onboarding: explicit SDK checks/login, separate from model execution."""

import copy
import importlib.metadata
import importlib.util
import json
import subprocess
import sys
import threading
import time
from pathlib import Path
from uuid import uuid4

from .codex_model import child_environment
from .model_config import codex_settings
from .reading_builds import build_environment
from .storage import now

ACTIVE = {"starting", "checking", "waiting_login"}


def local_environment(root):
    def version(package):
        try:
            return importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:
            return None

    sdk, runtime = version("openai-codex"), version("openai-codex-cli-bin")
    installed = bool(sdk and runtime and importlib.util.find_spec("openai_codex"))
    return {"python": sys.version.split()[0], "sdk": sdk, "runtime": runtime,
            "installed": installed, "data_directory": str(Path(root).resolve()),
            "python_supported": sys.version_info >= (3, 12),
            "reading_build": build_environment()}


class CodexConnection:
    """inspect is local-only; start explicitly performs check or official login.

    One operation at a time. Results/history are kept only for this server session.
    A new check never creates a conversation or consumes inference tokens.
    """

    def __init__(self, root, *, launcher=subprocess.Popen):
        self.root = Path(root).resolve()
        self.launcher = launcher
        self._lock = threading.RLock()
        self._state = {"status": "unchecked", "events": [], "result": None}
        self._history = []
        self._directory = None

    def inspect(self):
        with self._lock:
            model, effort = codex_settings()
            return copy.deepcopy({"environment": local_environment(self.root),
                "configuration": {"provider": "codex", "model": model, "effort": effort,
                                  "api_fallback": False},
                "operation": self._state, "history": self._history})

    def start(self, action):
        if action not in {"check", "login"}:
            raise ValueError("未知连接操作。")
        with self._lock:
            if self._directory is not None:
                return self.inspect()  # Repeated clicks reuse the live operation.
            environment = local_environment(self.root)
            if not environment["installed"] or not environment["python_supported"]:
                raise ValueError("需要 Python 3.12+ 和完整 SDK 依赖；在项目目录运行 uv sync。")
            model, effort = codex_settings()
            operation_id = uuid4().hex
            directory = self.root / "connection-runtime" / operation_id
            directory.mkdir(parents=True, exist_ok=False)
            self._directory = directory
            self._state = {"id": operation_id, "action": action, "status": "starting",
                "started_at": now(), "finished_at": None, "events": [], "result": None,
                "configuration": {"model": model, "effort": effort},
                "login_url": None, "error": None}
            threading.Thread(target=self._execute,
                args=(operation_id, directory, action, model, effort), daemon=True).start()
            return self.inspect()

    def cancel(self):
        with self._lock:
            if self._state["status"] in ACTIVE and self._directory:
                (self._directory / "cancel").touch()
                self._state["cancel_requested"] = True
            return self.inspect()

    def close(self):
        self.cancel()

    def _execute(self, operation_id, directory, action, model, effort):
        started = time.monotonic()
        try:
            process = self.launcher(
                [sys.executable, "-m", "learning_library.connection_worker", action,
                 str(directory), model, effort], env=child_environment(),
                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL,
                text=True, encoding="utf-8", creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            for line in process.stdout:
                packet = json.loads(line)
                with self._lock:
                    if self._state.get("id") != operation_id:
                        return
                    self._state.update(packet)
                    self._state["events"].append({"at": now(), "status": packet["status"]})
            process.wait()
            with self._lock:
                if self._state["status"] in ACTIVE:
                    self._state.update(status="failed", error="SDK 连接进程未返回完成状态。")
        except Exception as exc:  # noqa: BLE001 - never expose stderr or credentials
            with self._lock:
                self._state.update(status="failed", error=f"连接进程失败：{type(exc).__name__}。")
        finally:
            with self._lock:
                self._state.update(finished_at=now(), login_url=None,
                                   elapsed_seconds=round(time.monotonic() - started, 3))
                self._history.insert(0, copy.deepcopy(self._state))
                self._history = self._history[:8]
                self._directory = None
                # Only remove this operation's generated cancellation marker and empty directory.
                (directory / "cancel").unlink(missing_ok=True)
                directory.rmdir()
