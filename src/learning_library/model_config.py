"""Explicit provider selection. Keys are read only for a selected API provider."""

import importlib.util
import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict

DEFAULT_CODEX_MODEL = "gpt-6.1-sol"
DEFAULT_CODEX_EFFORT = "high"


def codex_settings():
    return (os.environ.get("LIBRARY_CODEX_MODEL", DEFAULT_CODEX_MODEL),
            os.environ.get("LIBRARY_CODEX_EFFORT", DEFAULT_CODEX_EFFORT))


class ModelChoice(BaseModel):
    model_config = ConfigDict(extra="forbid")
    provider: Literal["codex", "deepseek"]


@dataclass(frozen=True)
class SelectedModel:
    provider: str
    model: str
    effort: str
    ready: bool
    reason: str
    api_key: str = field(default="", repr=False)

    def public(self):
        search_model, search_effort = codex_settings()
        return {"provider": self.provider, "model": self.model, "effort": self.effort,
                "ready": self.ready, "reason": self.reason, "api_fallback": False,
                "codex": {"model": search_model, "effort": search_effort},
                "billing": "ChatGPT subscription" if self.provider == "codex" else "DeepSeek API"}


def selected_model(root=None):
    path = Path(root) / "model-provider.json" if root is not None else None
    saved = json.loads(path.read_text("utf-8")) if path and path.is_file() else {}
    provider = os.environ.get("LIBRARY_MODEL_PROVIDER", saved.get("provider", "codex"))
    if provider == "codex":
        ready = importlib.util.find_spec("openai_codex") is not None
        model, effort = codex_settings()
        return SelectedModel("codex", model, effort, ready,
                             "执行前检查 ChatGPT 登录；不转 API。" if ready else "尚未安装 Codex SDK。")
    if provider != "deepseek":
        raise ValueError("未知的模型提供方；未调用任何模型。")
    key, model = deepseek_config(root)
    installed = importlib.util.find_spec("langchain_deepseek") is not None
    return SelectedModel("deepseek", model, "disabled", bool(key) and installed,
                         "手动选择 DeepSeek API。" if key and installed else
                         "未配置 DeepSeek 密钥。" if not key else "尚未安装 DeepSeek 可选依赖。", key)


def choose_model(root, choice):
    if os.environ.get("LIBRARY_MODEL_PROVIDER"):
        raise ValueError("提供方由 LIBRARY_MODEL_PROVIDER 环境变量固定，请先移除该覆盖。")
    path = Path(root) / "model-provider.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(choice.model_dump()), encoding="utf-8")
    temp.replace(path)
    return selected_model(root).public()


def deepseek_config(root):
    key = os.environ.get("DEEPSEEK_API_KEY", "").strip()
    path = Path(root) / "deepseek-api-key.txt" if root is not None else None
    if not key and path and path.is_file():
        key = path.read_text(encoding="utf-8-sig").strip()
    return key, os.environ.get("DEEPSEEK_MODEL", "deepseek-flash")
