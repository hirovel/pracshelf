"""Onboarding crosses its public Interface. All login/model tests are synthetic."""

import asyncio
import io
import json
import threading
import time
from types import SimpleNamespace as NS

import pytest
from fastapi.testclient import TestClient

from learning_library.codex_connection import CodexConnection
from learning_library.connection_worker import connect
from learning_library.web import create_app


class SDK:
    def __init__(self, auth="chatgpt", models=None, login=None):
        self.auth = auth
        self.catalog = models if models is not None else [NS(model="gpt-6.1-sol",
            supported_reasoning_efforts=[NS(reasoning_effort=NS(value="high"))])]
        self.login = login
        self.calls = []
        self.closed = False

    async def __aenter__(self):
        return self

    async def close(self):
        self.closed = True

    async def account(self, *, refresh_token):
        assert refresh_token is False
        self.calls.append("account")
        return NS(account=NS(root=NS(type=self.auth, email="private@example.test"))
                  if self.auth else None)

    async def models(self):
        self.calls.append("models")
        return NS(data=self.catalog)

    async def login_chatgpt(self):
        self.calls.append("login")
        return self.login

    async def thread_start(self, **kwargs):
        pytest.fail("Onboarding must never create a model conversation")


def run(sdk, tmp_path, *, action="check", **kwargs):
    packets = []
    asyncio.run(connect(action, tmp_path, "gpt-6.1-sol", "high",
        factory=lambda config: sdk, emit=packets.append, **kwargs))
    assert sdk.closed
    assert "private@example.test" not in json.dumps(packets)
    assert packets[-1]["result"]["model_tasks"] == 0
    assert packets[-1]["result"]["api_fallback"] is False
    return packets


def test_check_is_not_inference_and_existing_login_is_reused(tmp_path):
    sdk = SDK()
    result = run(sdk, tmp_path, action="login")[-1]["result"]
    assert sdk.calls == ["account", "models"]
    assert result["auth_type"] == "chatgpt"
    assert result["model_found"] and result["effort_found"]
    assert result["calls"] == ["account/read", "model/list"]


@pytest.mark.parametrize("auth,kind", [(None, "missing"), ("apiKey", "apiKey")])
def test_missing_and_api_login_do_not_read_model_catalog_or_fallback(tmp_path, auth, kind):
    sdk = SDK(auth)
    result = run(sdk, tmp_path)[-1]["result"]
    assert result["auth_type"] == kind and result["models"] is None
    assert result["model_found"] is None and sdk.calls == ["account"]


@pytest.mark.parametrize("models,model_found", [([], False),
    ([NS(model="gpt-6.1-sol", supported_reasoning_efforts=[])], True)])
def test_catalog_missing_model_or_effort_never_selects_a_substitute(tmp_path, models, model_found):
    result = run(SDK(models=models), tmp_path)[-1]["result"]
    assert result["model"] == "gpt-6.1-sol" and result["effort"] == "high"
    assert result["model_found"] is model_found and result["effort_found"] is False


class Login:
    auth_url = "https://auth.openai.com/authorize?state=synthetic"

    def __init__(self, sdk, *, finish=False):
        self.sdk, self.finish, self.cancelled = sdk, finish, False

    async def wait(self):
        if not self.finish:
            await asyncio.Event().wait()
        self.sdk.auth = "chatgpt"
        return NS(success=True)

    async def cancel(self):
        self.cancelled = True


def test_login_waits_for_official_completion_then_checks_account_and_catalog(tmp_path):
    sdk = SDK(None)
    sdk.login = Login(sdk, finish=True)
    packets = run(sdk, tmp_path, action="login")
    assert any(packet["status"] == "waiting_login" for packet in packets)
    assert packets[-1]["login_url"] is None
    assert packets[-1]["status"] == "completed" and not sdk.login.cancelled
    assert sdk.calls == ["account", "login", "account", "models"]


def test_cancelling_login_cancels_only_live_handle_and_closes_runtime(tmp_path):
    sdk = SDK(None)
    sdk.login = Login(sdk)
    packets = []

    def emit(packet):
        packets.append(packet)
        if packet["status"] == "waiting_login":
            (tmp_path / "cancel").touch()

    asyncio.run(connect("login", tmp_path, "gpt-6.1-sol", "high",
        factory=lambda config: sdk, emit=emit))
    assert packets[-1]["status"] == "cancelled"
    assert sdk.login.cancelled and sdk.closed
    assert sdk.calls == ["account", "login"]


def test_login_timeout_has_no_raw_sdk_error_or_credentials(tmp_path):
    sdk = SDK(None)
    sdk.login = Login(sdk)
    last = run(sdk, tmp_path, action="login", login_timeout=0.01)[-1]
    assert last["status"] == "failed" and "超时" in last["error"]
    assert sdk.login.cancelled and last["login_url"] is None


def test_sdk_exception_is_safe_and_partial_evidence_remains(tmp_path):
    class Broken(SDK):
        async def models(self):
            raise RuntimeError("private@example.test Bearer secret-private-token")

    last = run(Broken(), tmp_path)[-1]
    assert last["status"] == "failed" and "RuntimeError" in last["error"]
    assert "secret-private-token" not in json.dumps(last)
    assert last["result"]["auth_type"] == "chatgpt"


def wait_done(connection):
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        value = connection.inspect()
        if value["operation"].get("finished_at"):
            return value
        time.sleep(0.01)
    pytest.fail("Synthetic connection did not finish")


def test_page_reads_never_launch_worker_and_duplicate_start_reuses_operation(tmp_path, monkeypatch):
    monkeypatch.delenv("LIBRARY_MODEL_PROVIDER", raising=False)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "synthetic-secret")
    release, launched = threading.Event(), []

    class Process:
        def __init__(self):
            self.stdout = self.lines()

        def lines(self):
            release.wait(2)
            yield json.dumps({"status": "completed", "result": {"model_tasks": 0}})

        def wait(self):
            return 0

    def launcher(args, **kwargs):
        launched.append(args)
        assert "DEEPSEEK_API_KEY" not in kwargs["env"]
        assert "OPENAI_API_KEY" not in kwargs["env"]
        return Process()

    connection = CodexConnection(tmp_path, launcher=launcher)
    client = TestClient(create_app(tmp_path, connection=connection), base_url="http://127.0.0.1")
    for _ in range(3):
        assert client.get("/api/connection").json()["operation"]["status"] == "unchecked"
    assert launched == []
    first = client.post("/api/connection", json={"action": "check"}).json()
    second = client.post("/api/connection", json={"action": "login"}).json()
    assert first["operation"]["id"] == second["operation"]["id"]
    release.set()
    value = wait_done(connection)
    assert len(launched) == 1 and len(value["history"]) == 1
    assert "synthetic-secret" not in json.dumps(value)
    assert not list(tmp_path.glob("**/cancel"))


def test_external_origin_cannot_start_or_cancel_local_login(tmp_path):
    connection = CodexConnection(tmp_path, launcher=lambda *args, **kwargs: pytest.fail("No launch"))
    client = TestClient(create_app(tmp_path, connection=connection), base_url="http://127.0.0.1")
    assert client.post("/api/connection", json={"action": "login"},
        headers={"origin": "https://external.example"}).status_code == 403
    assert client.post("/api/connection/cancel",
        headers={"origin": "https://external.example"}).status_code == 403


def test_missing_worker_terminal_is_a_visible_failure(tmp_path):
    connection = CodexConnection(tmp_path,
        launcher=lambda *args, **kwargs: NS(stdout=io.StringIO(""), wait=lambda: 1))
    connection.start("check")
    result = wait_done(connection)
    assert result["operation"]["status"] == "failed"
    assert "未返回完成状态" in result["operation"]["error"]
