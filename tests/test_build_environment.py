"""First-use build prerequisites; no installer, SDK login or model is run."""

import pytest

from learning_library import reading_builds


def source_layout(root):
    for name in ("package-lock.json", "scripts/reading.mjs", "reading/astro.config.mjs"):
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("synthetic source", encoding="utf-8")


def test_missing_source_prevents_compiler_launch(tmp_path, monkeypatch):
    # Reproduce an installed wheel location; a source checkout is unavailable.
    monkeypatch.setattr(reading_builds, "__file__", str(tmp_path / "Lib/site-packages/learning_library/reading_builds.py"))
    monkeypatch.setattr(reading_builds.subprocess, "Popen", lambda *a, **k: pytest.fail("Compiler launched without source"))
    state = reading_builds.build_environment()
    assert not state["source_available"] and not state["ready"]
    with pytest.raises(reading_builds.BuildFailure, match="完整项目源码"):
        reading_builds.run_reading_build(tmp_path / "input", tmp_path / "output")


def test_node_and_frontend_installation_are_reported_separately(tmp_path, monkeypatch):
    source_layout(tmp_path)
    monkeypatch.setattr(reading_builds.shutil, "which", lambda name: None)
    missing_node = reading_builds.build_environment(tmp_path)
    assert missing_node["source_available"] and not missing_node["node_available"]
    assert "Node.js 22.12" in missing_node["error"]
    monkeypatch.setattr(reading_builds.shutil, "which", lambda name: "synthetic-node")
    missing_packages = reading_builds.build_environment(tmp_path)
    assert missing_packages["node_available"] and not missing_packages["ready"]
    assert "npm ci" in missing_packages["error"]
    cli = tmp_path / "node_modules/astro/bin/astro.mjs"
    cli.parent.mkdir(parents=True)
    cli.write_text("synthetic compiler", encoding="utf-8")
    installed = reading_builds.build_environment(tmp_path)
    assert installed["ready"] and installed["node_version_checked"] is False
