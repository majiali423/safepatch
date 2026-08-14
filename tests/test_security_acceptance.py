from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

import pytest

from code_agent.controller import TaskController
from code_agent.llm import LLMClient
from code_agent.patching.applier import PatchApplier
from code_agent.patching.validator import MAX_CHANGED_LINES, PolicyValidator
from code_agent.repository.workspace import WorkspaceError, import_repository, safe_resolve
from code_agent.runtime.docker_pytest import DockerPytestRunner
from code_agent.state import PatchProposal, SessionStatus, TestResult
from code_agent.tools.read_file import read_file
from code_agent.tracing.recorder import TraceRecorder


def _proposal(diff: str, files: list[str] | None = None) -> PatchProposal:
    return PatchProposal(
        diagnosis="d",
        affected_files=files or ["a.py"],
        unified_diff=diff,
        expected_behavior="e",
        risk_notes="r",
        tests_to_run=["tests/test_a.py"],
    )


def test_path_traversal_relative_fails(tmp_path: Path):
    root = tmp_path / "work"
    root.mkdir()
    (root / "a.py").write_text("x=1\n", encoding="utf-8")
    with pytest.raises(WorkspaceError):
        safe_resolve(root, "../../outside.txt")


def test_absolute_path_read_fails(tmp_path: Path):
    root = tmp_path / "work"
    root.mkdir()
    outside = tmp_path / "outside.py"
    outside.write_text("secret=1\n", encoding="utf-8")
    with pytest.raises(WorkspaceError):
        read_file(root, str(outside.resolve()))


def test_symlink_escape_fails(tmp_path: Path):
    root = tmp_path / "work"
    root.mkdir()
    outside = tmp_path / "outside.py"
    outside.write_text("SECRET=1\n", encoding="utf-8")
    link = root / "leak.py"
    try:
        link.symlink_to(outside)
    except OSError:
        pytest.skip("symlink creation not permitted on this host")
    with pytest.raises(WorkspaceError, match="Symlink|escapes"):
        safe_resolve(root, "leak.py")


def test_forbid_env_git_dockerfile(tmp_path: Path):
    (tmp_path / "ok.py").write_text("x=1\n", encoding="utf-8")
    cases = [
        (".env", ".env"),
        (".git/config", ".git/config"),
        ("Dockerfile", "Dockerfile"),
    ]
    for path, label in cases:
        diff = (
            f"--- a/{path}\n"
            f"+++ b/{path}\n"
            "@@ -0,0 +1 @@\n"
            "+x=1\n"
        )
        result = PolicyValidator().validate(_proposal(diff, files=[path]), tmp_path)
        assert not result.ok, label
        assert any("forbidden" in e.lower() for e in result.errors), result.errors


def test_too_many_files_fails(tmp_path: Path):
    files = [f"f{i}.py" for i in range(6)]
    for name in files:
        (tmp_path / name).write_text("x=1\n", encoding="utf-8")
    chunks = []
    for name in files:
        chunks.append(
            f"--- a/{name}\n+++ b/{name}\n@@ -1 +1 @@\n-x=1\n+y=1\n"
        )
    result = PolicyValidator().validate(
        _proposal("\n".join(chunks), files=files), tmp_path
    )
    assert not result.ok
    assert any("Too many files" in e for e in result.errors)


def test_too_many_lines_fails(tmp_path: Path):
    (tmp_path / "a.py").write_text("x=1\n", encoding="utf-8")
    added = "\n".join(["+line"] * (MAX_CHANGED_LINES + 1))
    diff = f"--- a/a.py\n+++ b/a.py\n@@ -1,0 +1,{MAX_CHANGED_LINES + 1} @@\n{added}\n"
    result = PolicyValidator().validate(_proposal(diff), tmp_path)
    assert not result.ok
    assert any("Too many changed lines" in e for e in result.errors)


def test_reject_leaves_working_copy_unchanged(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "calculator.py").write_text(
        "def divide(a, b):\n    return a / b\n", encoding="utf-8"
    )
    tests = repo / "tests"
    tests.mkdir()
    (tests / "test_calculator.py").write_text(
        "from calculator import divide\n"
        "def test_ok():\n"
        "    assert divide(4, 2) == 2\n",
        encoding="utf-8",
    )
    (repo / "pytest.ini").write_text("[pytest]\npythonpath = .\n", encoding="utf-8")

    diff = (
        "--- a/calculator.py\n"
        "+++ b/calculator.py\n"
        "@@ -1,2 +1,4 @@\n"
        " def divide(a, b):\n"
        "+    if b == 0:\n"
        "+        raise ValueError('x')\n"
        "     return a / b\n"
    )
    script = [
        {
            "tool": "propose_patch",
            "args": {
                "diagnosis": "d",
                "affected_files": ["calculator.py"],
                "unified_diff": diff,
                "expected_behavior": "e",
                "risk_notes": "r",
                "tests_to_run": ["tests/test_calculator.py"],
            },
        }
    ]

    class LocalRunner:
        def run_pytest(self, workspace_root, *, log_path=None):
            return TestResult(
                exit_code=0,
                stdout="1 passed",
                stderr="",
                duration_sec=0.01,
            )

    original = (repo / "calculator.py").read_text(encoding="utf-8")
    controller = TaskController(
        llm=LLMClient(dry_run_script=script),
        runner=LocalRunner(),  # type: ignore[arg-type]
        approve=lambda *_: False,
        say=lambda _m: None,
        session_base=tmp_path / "sessions",
    )
    session = controller.run(repo, "fix divide")
    assert session.status == SessionStatus.REJECTED
    assert (session.workspace_root / "calculator.py").read_text(
        encoding="utf-8"
    ) == original
    assert (repo / "calculator.py").read_text(encoding="utf-8") == original


def test_tampered_source_after_approval_apply_fails(tmp_path: Path):
    (tmp_path / "a.py").write_text("def f():\n    return 1\n", encoding="utf-8")
    diff = (
        "--- a/a.py\n"
        "+++ b/a.py\n"
        "@@ -1,2 +1,3 @@\n"
        " def f():\n"
        "+    # note\n"
        "     return 1\n"
    )
    proposal = _proposal(diff)
    assert PolicyValidator().validate(proposal, tmp_path).ok
    # Replace file content so hunk context no longer matches.
    (tmp_path / "a.py").write_text("def f():\n    return 99\n", encoding="utf-8")
    result = PatchApplier.apply(proposal, tmp_path)
    assert not result.ok
    assert "mismatch" in (result.error or "").lower() or "Mismatch" in (
        result.error or ""
    )


def test_docker_unavailable_returns_environment_error(monkeypatch, tmp_path: Path):
    runner = DockerPytestRunner()
    monkeypatch.setattr(
        runner, "preflight", lambda: (False, "docker daemon unavailable")
    )
    result = runner.run_pytest(tmp_path)
    assert result.error_kind == "environment"
    assert result.environment_error is not None
    assert result.environment_error.startswith("TEST_ENVIRONMENT_ERROR")
    assert result.passed is False


def test_pytest_timeout_returns_timeout_kind(monkeypatch, tmp_path: Path):
    runner = DockerPytestRunner()
    monkeypatch.setattr(runner, "preflight", lambda: (True, "ok"))
    monkeypatch.setattr(runner, "_resolve_image", lambda: "safepatch-pytest:local")

    class FakeProc:
        def communicate(self, timeout=None):
            raise subprocess.TimeoutExpired(cmd=["docker"], timeout=timeout or 120)

        def kill(self):
            return None

    monkeypatch.setattr(subprocess, "Popen", lambda *_a, **_k: FakeProc())
    # Best-effort cleanup path also calls subprocess.run — keep it no-op.
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *_a, **_k: subprocess.CompletedProcess(args=[], returncode=0),
    )
    result = runner.run_pytest(tmp_path)
    assert result.error_kind == "timeout"
    assert result.environment_error is not None
    assert result.environment_error.startswith("TEST_TIMEOUT")
    assert "120s" in result.environment_error


def test_failed_max_attempts(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "mod.py").write_text("def f():\n    return 1\n", encoding="utf-8")
    tests = repo / "tests"
    tests.mkdir()
    (tests / "test_mod.py").write_text(
        "from mod import f\n"
        "def test_f():\n"
        "    assert f() == 2\n",
        encoding="utf-8",
    )
    (repo / "pytest.ini").write_text("[pytest]\npythonpath = .\n", encoding="utf-8")

    def make_patch(old_return: str, new_return: str) -> dict:
        diff = (
            "--- a/mod.py\n"
            "+++ b/mod.py\n"
            "@@ -1,2 +1,2 @@\n"
            " def f():\n"
            f"-    return {old_return}\n"
            f"+    return {new_return}\n"
        )
        return {
            "tool": "propose_patch",
            "args": {
                "diagnosis": "still wrong",
                "affected_files": ["mod.py"],
                "unified_diff": diff,
                "expected_behavior": "return 2",
                "risk_notes": "n",
                "tests_to_run": ["tests/test_mod.py"],
            },
        }

    # Keep returning wrong values: 1 -> 3 -> 4 -> 5, never 2.
    script = [make_patch("1", "3"), make_patch("3", "4"), make_patch("4", "5")]

    class LocalRunner:
        def run_pytest(self, workspace_root, *, log_path=None):
            proc = subprocess.run(
                [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider"],
                cwd=workspace_root,
                capture_output=True,
                text=True,
                check=False,
            )
            failed = []
            for line in (proc.stdout or "").splitlines():
                if line.startswith("FAILED "):
                    failed.append(line.split()[1])
            if log_path:
                log_path.write_text(proc.stdout or "", encoding="utf-8")
            return TestResult(
                exit_code=proc.returncode,
                stdout=proc.stdout or "",
                stderr=proc.stderr or "",
                duration_sec=0.01,
                failed_tests=failed,
                traceback_summary=proc.stdout or "",
            )

    controller = TaskController(
        llm=LLMClient(dry_run_script=script),
        runner=LocalRunner(),  # type: ignore[arg-type]
        approve=lambda *_: True,
        say=lambda _m: None,
        session_base=tmp_path / "sessions",
    )
    session = controller.run(repo, "make f return 2")
    assert session.status == SessionStatus.FAILED_MAX_ATTEMPTS
    assert session.attempts_used == 3


def test_trace_redacts_api_key(tmp_path: Path):
    trace_path = tmp_path / "trace.jsonl"
    rec = TraceRecorder(trace_path)
    rec.emit(
        "model_request",
        api_key="sk-test-secret-key-123456",
        note="header Authorization: Bearer sk-test-secret-key-123456",
    )
    text = trace_path.read_text(encoding="utf-8")
    assert "sk-test-secret-key-123456" not in text
    assert "***REDACTED***" in text


def test_source_repo_not_modified_on_success(tmp_path: Path):
    root = Path(__file__).resolve().parents[1]
    repo = root / "examples" / "buggy_calculator"
    before = (repo / "calculator.py").read_text(encoding="utf-8")
    script = json.loads(
        (root / "examples" / "dry_run_fix_divide.json").read_text(encoding="utf-8")
    )

    class LocalRunner:
        def run_pytest(self, workspace_root, *, log_path=None):
            started = time.time()
            proc = subprocess.run(
                [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider"],
                cwd=workspace_root,
                capture_output=True,
                text=True,
                check=False,
            )
            if log_path:
                log_path.write_text((proc.stdout or "") + (proc.stderr or ""), encoding="utf-8")
            return TestResult(
                exit_code=proc.returncode,
                stdout=proc.stdout or "",
                stderr=proc.stderr or "",
                duration_sec=time.time() - started,
            )

    controller = TaskController(
        llm=LLMClient(dry_run_script=script),
        runner=LocalRunner(),  # type: ignore[arg-type]
        approve=lambda *_: True,
        say=lambda _m: None,
        session_base=tmp_path / "sessions",
    )
    session = controller.run(repo, "fix divide")
    assert session.status == SessionStatus.SUCCEEDED
    assert (repo / "calculator.py").read_text(encoding="utf-8") == before
    assert "ValueError" in (
        session.workspace_root / "calculator.py"
    ).read_text(encoding="utf-8")


def test_import_skips_git_and_only_working_copy_used(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "a.py").write_text("x=1\n", encoding="utf-8")
    (repo / ".git").mkdir()
    (repo / ".git" / "config").write_text("secret", encoding="utf-8")
    imported = import_repository(repo, session_base=tmp_path / "sessions")
    assert not (imported.workspace_root / ".git").exists()
    assert (imported.workspace_root / "a.py").exists()
