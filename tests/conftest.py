"""Historical synthetic HTTP tests explicitly select the optional API Adapter.

Codex Interface tests override this selection and inject the SDK/worker Seam.
Production defaults to Codex. No test may start a real model task.
"""

import pytest


@pytest.fixture(autouse=True)
def explicit_legacy_api_adapter(monkeypatch):
    monkeypatch.setenv("LIBRARY_MODEL_PROVIDER", "deepseek")
