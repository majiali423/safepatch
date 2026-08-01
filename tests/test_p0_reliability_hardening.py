from __future__ import annotations

import os
import stat
import subprocess
from pathlib import Path

import pytest

from code_agent.controller import TaskController
from code_agent.llm import LLMClient
from code_agent.patching.validator import validate_proposal
from code_agent.runtime.docker_pytest import DockerPytestRunner
from code_agent.state import PatchProposal, SessionStatus, TestResult


def _repo(tmp_path: Path, *, expected: int = 2) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir(parents=True)
    (repo / "mod.py").write_text("def f():\n    return 1\n", encoding="utf-8")
    (repo / "other.py").write_text("VALUE = 1\n", encoding="utf-8")
    tests = repo / "tests"
    tests.mkdir()
    (tests / "test_mod.py").write_text(
        f"from mod import f\n\ndef test_f():\n    assert f() == {expected}\n",
        encoding="utf-8",
    )
    return repo


def _proposal(*, affected_files: list[str] | None = None, include_other: bool = False) -> dict:
    diff = (
        "--- a/mod.py\n+++ b/mod.py\n@@ -1,2 +1,2 @@\n"
        " def f():\n-    return 1\n+    return 2\n"
    )
    if include_other:
        diff += "--- a/other.py\n+++ b/other.py\n@@ -1 +1 @@\n-VALUE = 1\n+VALUE = 2\n"
    return {
        "tool": "propose_patch",
        "args": {
            "diagnosis": "fix",
            "affected_files": affected_files or ["mod.py"],
            "unified_diff": diff,
            "expected_behavior": "return 2",
            "risk_notes": "low",
            "tests_to_run": ["tests/test_mod.py"],
        },
    }


class _SequenceRunner:
    def __init__(self, passed: list[bool]) -> None:
        self.passed = list(passed)
        self.calls = 0

    def run_pytest(self, workspace_root: Path, *, log_path: Path | None = None) -> TestResult:
        passed = self.passed[self.calls]
        self.calls += 1
        result = TestResult(
            exit_code=0 if passed else 1,
            stdout="passed" if passed else "FAILED tests/test_mod.py::test_f",
            stderr="",
            duration_sec=0.01,
            failed_tests=[] if passed else ["tests/test_mod.py::test_f"],
        )
        if log_path is not None:
            log_path.parent.mkdir(parents=True, exist_ok=True)
            log_path.write_text(result.stdout, encoding="utf-8")
        return result


def test_missing_approval_handler_fails_closed(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    runner = _SequenceRunner([False])
    session = TaskController(
        llm=LLMClient(dry_run_script=[_proposal()]),
        runner=runner,  # type: ignore[arg-type]
        session_base=tmp_path / "sessions",
    ).run(repo, "make f return 2")

    assert session.status == SessionStatus.REJECTED
    assert session.stop_reason == "approval_handler_missing"
    assert session.attempts_used == 0
    assert runner.calls == 1
    assert (session.workspace_root / "mod.py").read_text(encoding="utf-8").endswith("return 1\n")
    trace = (session.artifacts_dir / "trace.jsonl").read_text(encoding="utf-8")
    assert '"decision": "handler_missing"' in trace
    assert '"event": "patch_applied"' not in trace
    assert not list(session.artifacts_dir.glob("attempt-*.log"))


def _validation(tmp_path: Path, *, affected_files: list[str], include_other: bool = False):
    repo = _repo(tmp_path)
    args = _proposal(affected_files=affected_files, include_other=include_other)["args"]
    proposal = PatchProposal.from_dict(args)
    return validate_proposal(proposal, repo)


def test_declared_one_file_but_diff_edits_two_is_rejected(tmp_path: Path) -> None:
    result = _validation(tmp_path, affected_files=["mod.py"], include_other=True)
    assert not result.ok
    assert any("undeclared" in error for error in result.errors)


def test_declared_extra_file_absent_from_diff_is_rejected(tmp_path: Path) -> None:
    result = _validation(tmp_path, affected_files=["mod.py", "other.py"])
    assert not result.ok
    assert any("not in diff" in error for error in result.errors)


def test_declared_file_order_is_irrelevant(tmp_path: Path) -> None:
    result = _validation(
        tmp_path,
        affected_files=["other.py", "mod.py"],
        include_other=True,
    )
    assert result.ok, result.errors


def test_duplicate_and_cross_platform_declarations_cannot_bypass_check(tmp_path: Path) -> None:
    duplicate = _validation(tmp_path, affected_files=["mod.py", ".\\a\\mod.py"])
    assert not duplicate.ok
    assert any("Duplicate" in error for error in duplicate.errors)
    normalized = _validation(tmp_path / "normalized", affected_files=[".\\b\\mod.py"])
    assert normalized.ok, normalized.errors


def test_baseline_failure_resolved_is_verified(tmp_path: Path) -> None:
    runner = _SequenceRunner([False, True])
    session = TaskController(
        llm=LLMClient(dry_run_script=[_proposal()]),
        runner=runner,  # type: ignore[arg-type]
        approve=lambda *_: True,
        session_base=tmp_path / "sessions",
    ).run(_repo(tmp_path), "make f return 2")
    assert session.status == SessionStatus.SUCCEEDED
    assert session.to_summary()["request_verified"] is True
    assert session.to_summary()["verification_basis"] == "baseline_failure_resolved"


def test_green_baseline_patch_is_not_independently_verified(tmp_path: Path) -> None:
    runner = _SequenceRunner([True, True])
    session = TaskController(
        llm=LLMClient(dry_run_script=[_proposal()]),
        runner=runner,  # type: ignore[arg-type]
        approve=lambda *_: True,
        session_base=tmp_path / "sessions",
    ).run(_repo(tmp_path, expected=1), "feature request")
    summary = session.to_summary()
    assert session.status == SessionStatus.TESTS_PASSED_UNVERIFIED
    assert summary["final_tests_passed"] is True
    assert summary["request_verified"] is False
    assert summary["verification_basis"] == "green_baseline_no_independent_oracle"
    trace = (session.artifacts_dir / "trace.jsonl").read_text(encoding="utf-8")
    assert "TESTS_PASSED_UNVERIFIED" in trace


def test_docker_runner_uses_and_removes_writable_test_copy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = _repo(tmp_path)
    original = (workspace / "mod.py").read_bytes()
    runner = DockerPytestRunner()
    monkeypatch.setattr(runner, "preflight", lambda: (True, "ok"))
    monkeypatch.setattr(runner, "_resolve_image", lambda: "code-agent-pytest:local")
    observed: dict[str, Path] = {}

    def fake_run(cmd: list[str], *, timeout_seconds: int):
        mount = cmd[cmd.index("-v") + 1]
        test_copy = Path(mount.removesuffix(":/work:rw"))
        observed["test_copy"] = test_copy
        if os.name != "nt":
            assert test_copy.stat().st_mode & stat.S_IWOTH
            assert (test_copy / "mod.py").stat().st_mode & stat.S_IWOTH
        (test_copy / "mod.py").write_text("mutated by pytest\n", encoding="utf-8")
        (test_copy / "ordinary.tmp").write_text("allowed\n", encoding="utf-8")
        return "ok", "", 0

    monkeypatch.setattr("code_agent.runtime.docker_pytest._run_docker_cmd", fake_run)
    result = runner.run_pytest(workspace)
    assert result.passed
    assert (workspace / "mod.py").read_bytes() == original
    assert not (workspace / "ordinary.tmp").exists()
    assert not observed["test_copy"].parent.exists()


def test_docker_timeout_removes_test_copy(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    workspace = _repo(tmp_path)
    runner = DockerPytestRunner()
    monkeypatch.setattr(runner, "preflight", lambda: (True, "ok"))
    monkeypatch.setattr(runner, "_resolve_image", lambda: "code-agent-pytest:local")
    observed: dict[str, Path] = {}

    def timeout(cmd: list[str], *, timeout_seconds: int):
        mount = cmd[cmd.index("-v") + 1]
        observed["test_copy"] = Path(mount.removesuffix(":/work:rw"))
        raise subprocess.TimeoutExpired(cmd, timeout_seconds)

    monkeypatch.setattr("code_agent.runtime.docker_pytest._run_docker_cmd", timeout)
    result = runner.run_pytest(workspace)
    assert result.error_kind == "timeout"
    assert not observed["test_copy"].parent.exists()
