"""Subscription execution Interface, using synthetic workers and SDK events only."""

import asyncio
import json
import os
from types import SimpleNamespace as NS

import httpx
import pytest
from fastapi.testclient import TestClient
from openai_codex.models import Notification
from pydantic import BaseModel, ConfigDict

from learning_library.codex_model import call, child_environment
from learning_library.codex_worker import execute
from learning_library.model_config import ModelChoice, choose_model, selected_model
from learning_library.structured_model import call_json
from learning_library.web import create_app


class Answer(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str


REQUEST = {"provider": "codex", "model": "gpt-6.1-sol", "effort": "medium",
           "web_search": "disabled", "messages": [{"role": "user", "content": "Synthetic data."}]}


def test_default_does_not_read_key_and_api_is_explicit(tmp_path, monkeypatch):
    monkeypatch.delenv("LIBRARY_MODEL_PROVIDER")
    monkeypatch.delenv("LIBRARY_CODEX_MODEL", raising=False)
    monkeypatch.delenv("LIBRARY_CODEX_EFFORT", raising=False)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "synthetic-parent-secret")
    # Invalid UTF-8 proves merely having a key file cannot trigger a read.
    (tmp_path / "deepseek-api-key.txt").write_bytes(b"\xff")
    choice = selected_model(tmp_path)
    assert (choice.model, choice.effort) == ('gpt-6.1-sol', 'high')
    assert choice.provider == "codex" and choice.api_key == ""
    assert "synthetic-parent-secret" not in json.dumps(choice.public())
    explicit = choose_model(tmp_path, ModelChoice(provider="deepseek"))
    assert explicit["provider"] == "deepseek" and explicit["api_fallback"] is False
    choose_model(tmp_path, ModelChoice(provider="codex"))
    assert selected_model(tmp_path).provider == "codex"


def test_child_environment_is_copy_not_global_mutation(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "synthetic-openai")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "synthetic-deepseek")
    child = child_environment()
    assert "OPENAI_API_KEY" not in child and "DEEPSEEK_API_KEY" not in child
    assert os.environ["OPENAI_API_KEY"] == "synthetic-openai"
    assert os.environ["DEEPSEEK_API_KEY"] == "synthetic-deepseek"


def test_completed_worker_is_locally_validated_and_trace_preserved(tmp_path):
    def worker(argv, **kwargs):
        from pathlib import Path
        directory = Path(argv[-1])
        assert "DEEPSEEK_API_KEY" not in kwargs["env"]
        packet = json.loads((directory / "input.json").read_text("utf-8"))
        assert packet["schema"]["additionalProperties"] is False
        (directory / "events.jsonl").write_text('{"method":"turn/completed"}\n', encoding="utf-8")
        (directory / "result.json").write_text(json.dumps({"response": {"terminal_status": "completed"},
            "text": '{"text":"synthetic"}', "error": None}), encoding="utf-8")
        return NS(returncode=0)
    result = call(REQUEST, Answer, root=tmp_path, runner=worker)
    assert result["error"] is None and result["output"] == {"text": "synthetic"}
    assert result["response"]["request_count"] is None
    assert result["response"]["events"][0]["method"] == "turn/completed"


def test_no_api_fallback_from_codex_failure(tmp_path, monkeypatch):
    from learning_library import codex_model
    calls = []
    def failed(request, schema, **kwargs):
        calls.append(request)
        return {"response": {"provider": "codex"}, "output": None, "error": "Synthetic failure"}
    monkeypatch.setattr(codex_model, "call", failed)
    result = call_json(REQUEST, Answer, api_key="present-but-must-not-use", root=tmp_path,
                       transport=httpx.MockTransport(lambda r: pytest.fail("API fallback forbidden")))
    assert result["error"] == "Synthetic failure" and len(calls) == 1


class SDK:
    def __init__(self, events, auth="chatgpt"):
        self.events, self.auth, self.closed, self.turns = events, auth, False, []
        self.metadata = NS()
        self.metadata.model_dump = lambda **kw: {"synthetic": True}
    async def __aenter__(self):
        return self
    async def close(self):
        self.closed = True
    async def account(self, **kwargs):
        return NS(account=NS(root=NS(type=self.auth)))
    async def models(self):
        return NS(data=[NS(model=REQUEST["model"], supported_reasoning_efforts=[NS(reasoning_effort=NS(value="medium"))])])
    async def thread_start(self, **kwargs):
        assert kwargs["ephemeral"] and "All tools are disabled" in kwargs["developer_instructions"]
        return self
    id = "synthetic-id"
    async def read(self):
        return NS(thread=NS(model=REQUEST["model"], model_provider="openai", cli_version="synthetic"))
    async def turn(self, prompt, **kwargs):
        self.turns.append(prompt)
        return self
    async def stream(self):
        for method, params in self.events:
            yield Notification(method=method, payload=params)


@pytest.mark.parametrize("terminal,auth,error", [("completed", "chatgpt", False),
    (None, "chatgpt", True), ("failed", "chatgpt", True), ("completed", "apiKey", True)])
def test_json_is_not_completion_and_subscription_required(tmp_path, terminal, auth, error):
    events = [("item/completed", {"item": {"type": "agentMessage", "text": '{"text":"synthetic"}'}})]
    if terminal:
        events.append(("turn/completed", {"turn": {"status": terminal}}))
    sdk = SDK(events, auth)
    result = asyncio.run(execute(tmp_path, {"request": REQUEST, "schema": None},
                                factory=lambda cfg: sdk, inspect_config=False))
    assert bool(result["error"]) is error and sdk.closed
    assert result["response"]["api_fallback"] is False
    if auth == "apiKey":
        assert not sdk.turns
    else:
        assert result["text"] == '{"text":"synthetic"}'
        assert result["response"]["terminal_status"] == terminal


def test_web_provider_controls_are_read_only_until_save(tmp_path, monkeypatch):
    monkeypatch.delenv("LIBRARY_MODEL_PROVIDER")
    with TestClient(create_app(tmp_path)) as client:
        assert client.get("/api/model-configuration").json()["provider"] == "codex"
        assert not (tmp_path / "model-provider.json").exists()
        assert not (tmp_path / "model-runs").exists()
        assert client.post("/api/model-configuration", json={"provider": "other"}).status_code == 422
        assert client.post("/api/model-configuration", json={"provider": "deepseek"}).json()["provider"] == "deepseek"
        assert not (tmp_path / "model-runs").exists()
