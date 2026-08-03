"""Every run_pytest must allocate a unique Docker container name."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from code_agent.runtime.docker_config import DockerRunConfig
from code_agent.runtime.docker_pytest import (
    DockerPytestRunner,
    allocate_container_name,
)


def _repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    (root / "mod.py").write_text("X = 1\n", encoding="utf-8")
    tests = root / "tests"
    tests.mkdir()
    (tests / "test_mod.py").write_text("def test_ok():\n    assert True\n", encoding="utf-8")
    return root


def test_allocate_container_name_default_format_and_uniqueness() -> None:
    first = allocate_container_name(None)
    second = allocate_container_name(None)
    assert first.startswith("code-agent-pytest-")
    assert second.startswith("code-agent-pytest-")
    assert first != second
    assert len(first) <= 63


def test_allocate_container_name_preserves_explicit_safe_name() -> None:
    """Documented behavior: Docker-safe explicit names are preserved."""
    assert allocate_container_name("code-agent-test-abc123") == "code-agent-test-abc123"


def test_allocate_container_name_replaces_unsafe_explicit() -> None:
    generated = allocate_container_name("bad name with spaces")
    assert generated.startswith("code-agent-pytest-")
    assert " " not in generated


def test_default_config_run_command_includes_name(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = _repo(tmp_path)
    runner = DockerPytestRunner()
    monkeypatch.setattr(runner, "preflight", lambda: (True, "ok"))
    monkeypatch.setattr(runner, "_resolve_image", lambda: "code-agent-pytest:local")
    seen: list[list[str]] = []

    def fake_run(cmd: list[str], *, timeout_seconds: int):
        seen.append(cmd)
        return "ok", "", 0

    monkeypatch.setattr("code_agent.runtime.docker_pytest._run_docker_cmd", fake_run)
    result = runner.run_pytest(workspace)
    assert result.passed
    assert "--name" in seen[0]
    name = seen[0][seen[0].index("--name") + 1]
    assert name.startswith("code-agent-pytest-")
    assert runner.last_run_config is not None
    assert runner.last_run_config.container_name == name
    assert runner.last_run_config.container_name is not None


def test_consecutive_runs_get_different_names(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = _repo(tmp_path)
    runner = DockerPytestRunner()
    monkeypatch.setattr(runner, "preflight", lambda: (True, "ok"))
    monkeypatch.setattr(runner, "_resolve_image", lambda: "code-agent-pytest:local")
    names: list[str] = []

    def fake_run(cmd: list[str], *, timeout_seconds: int):
        names.append(cmd[cmd.index("--name") + 1])
        return "ok", "", 0

    monkeypatch.setattr("code_agent.runtime.docker_pytest._run_docker_cmd", fake_run)
    runner.run_pytest(workspace)
    runner.run_pytest(workspace)
    assert len(names) == 2
    assert names[0] != names[1]


def test_explicit_safe_name_is_preserved_on_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = _repo(tmp_path)
    runner = DockerPytestRunner()
    monkeypatch.setattr(runner, "preflight", lambda: (True, "ok"))
    monkeypatch.setattr(runner, "_resolve_image", lambda: "code-agent-pytest:local")
    seen: list[str] = []

    def fake_run(cmd: list[str], *, timeout_seconds: int):
        seen.append(cmd[cmd.index("--name") + 1])
        return "ok", "", 0

    monkeypatch.setattr("code_agent.runtime.docker_pytest._run_docker_cmd", fake_run)
    cfg = DockerRunConfig(image="code-agent-pytest:local", container_name="code-agent-test-pinned")
    runner.run_pytest(workspace, config=cfg)
    assert seen == ["code-agent-test-pinned"]
    assert runner.last_run_config is not None
    assert runner.last_run_config.container_name == "code-agent-test-pinned"


def test_timeout_cleanup_targets_allocated_name_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = _repo(tmp_path)
    runner = DockerPytestRunner()
    monkeypatch.setattr(runner, "preflight", lambda: (True, "ok"))
    monkeypatch.setattr(runner, "_resolve_image", lambda: "code-agent-pytest:local")
    removed: list[str | None] = []

    def timeout(cmd: list[str], *, timeout_seconds: int):
        raise subprocess.TimeoutExpired(cmd, timeout_seconds)

    def capture_rm(name: str | None) -> None:
        removed.append(name)

    monkeypatch.setattr("code_agent.runtime.docker_pytest._run_docker_cmd", timeout)
    monkeypatch.setattr("code_agent.runtime.docker_pytest._force_remove_container", capture_rm)
    result = runner.run_pytest(workspace)
    assert result.error_kind == "timeout"
    assert runner.last_run_config is not None
    assert runner.last_run_config.container_name is not None
    assert removed == [runner.last_run_config.container_name]
