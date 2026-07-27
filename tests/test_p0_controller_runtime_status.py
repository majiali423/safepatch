"""P0: TaskController must wire runner TEST_* results into session terminals."""

from __future__ import annotations

import json
from pathlib import Path

from code_agent.controller import TaskController
from code_agent.llm import LLMClient
from code_agent.state import SessionStatus, TestResult


def _mini_repo(tmp_path: Path) -> Path:
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
    return repo


def _good_patch() -> dict:
    return {
        "tool": "propose_patch",
        "args": {
            "diagnosis": "fix",
            "affected_files": ["mod.py"],
            "unified_diff": (
                "--- a/mod.py\n"
                "+++ b/mod.py\n"
                "@@ -1,2 +1,2 @@\n"
                " def f():\n"
                "-    return 1\n"
                "+    return 2\n"
            ),
            "expected_behavior": "return 2",
            "risk_notes": "low",
            "tests_to_run": ["tests/test_mod.py"],
        },
    }


def _load_trace(session) -> list[dict]:
    events = []
    for line in (session.artifacts_dir / "trace.jsonl").read_text(
        encoding="utf-8"
    ).splitlines():
        if line.strip():
            events.append(json.loads(line))
    return events


class _CountingLLM(LLMClient):
    """Dry-run LLM that counts complete() calls."""

    def __init__(self, script: list):
        super().__init__(dry_run_script=script)
        self.complete_calls = 0

    def complete(self, messages: list[dict[str, str]]):  # type: ignore[override]
        self.complete_calls += 1
        return super().complete(messages)


def test_controller_baseline_timeout_sets_test_timeout(tmp_path: Path):
    """Baseline pytest timeout must become session TEST_TIMEOUT (not repair loop)."""
    repo = _mini_repo(tmp_path)
    llm = _CountingLLM([_good_patch()])

    class TimeoutRunner:
        def run_pytest(self, workspace_root, *, log_path=None):
            result = TestResult(
                exit_code=-1,
                stdout="",
                stderr="pytest timed out in Docker",
                duration_sec=120.0,
                environment_error="TEST_TIMEOUT: pytest exceeded 120s",
                error_kind="timeout",
            )
            if log_path:
                log_path.parent.mkdir(parents=True, exist_ok=True)
                log_path.write_text(result.environment_error or "", encoding="utf-8")
            return result

    controller = TaskController(
        llm=llm,
        runner=TimeoutRunner(),  # type: ignore[arg-type]
        approve=lambda *_: True,
        say=lambda _m: None,
        session_base=tmp_path / "sessions",
    )
    session = controller.run(repo, "make f return 2")

    assert session.status == SessionStatus.TEST_TIMEOUT
    assert session.stop_reason == "pytest_timeout"
    assert session.attempts_used == 0
    assert llm.complete_calls == 0  # no model loop after baseline abort
    assert not list(session.artifacts_dir.glob("attempt-*.log"))
    events = [e["event"] for e in _load_trace(session)]
    assert "baseline_test_finished" in events
    assert "session_finished" in events
    assert "patch_proposed" not in events
    assert "patch_applied" not in events
    baseline_ev = next(e for e in _load_trace(session) if e["event"] == "baseline_test_finished")
    assert baseline_ev["payload"]["result"]["error_kind"] == "timeout"


def test_controller_baseline_environment_error_sets_status(tmp_path: Path):
    """Baseline environment failure must become TEST_ENVIRONMENT_ERROR."""
    repo = _mini_repo(tmp_path)
    llm = _CountingLLM([_good_patch()])

    class EnvFailRunner:
        def run_pytest(self, workspace_root, *, log_path=None):
            result = TestResult(
                exit_code=-1,
                stdout="",
                stderr="docker daemon unavailable",
                duration_sec=0.0,
                environment_error="TEST_ENVIRONMENT_ERROR: docker daemon unavailable",
                error_kind="environment",
            )
            if log_path:
                log_path.parent.mkdir(parents=True, exist_ok=True)
                log_path.write_text(result.environment_error or "", encoding="utf-8")
            return result

    controller = TaskController(
        llm=llm,
        runner=EnvFailRunner(),  # type: ignore[arg-type]
        approve=lambda *_: True,
        say=lambda _m: None,
        session_base=tmp_path / "sessions",
    )
    session = controller.run(repo, "make f return 2")

    assert session.status == SessionStatus.TEST_ENVIRONMENT_ERROR
    assert session.stop_reason == "test_environment_error"
    assert session.attempts_used == 0
    assert llm.complete_calls == 0
    assert session.status != SessionStatus.FAILED_MAX_ATTEMPTS
    events = [e["event"] for e in _load_trace(session)]
    assert "baseline_test_finished" in events
    assert "patch_applied" not in events
    baseline_ev = next(e for e in _load_trace(session) if e["event"] == "baseline_test_finished")
    assert baseline_ev["payload"]["result"]["error_kind"] == "environment"


def test_controller_post_apply_timeout_sets_test_timeout(tmp_path: Path):
    """Post-apply pytest timeout is TEST_TIMEOUT after a counted repair attempt."""
    repo = _mini_repo(tmp_path)
    llm = _CountingLLM([_good_patch()])

    class BaselineThenTimeout:
        def __init__(self) -> None:
            self.calls = 0

        def run_pytest(self, workspace_root, *, log_path=None):
            self.calls += 1
            if self.calls == 1:
                result = TestResult(
                    exit_code=1,
                    stdout="FAILED",
                    stderr="",
                    duration_sec=0.01,
                    failed_tests=["tests/test_mod.py::test_f"],
                )
            else:
                result = TestResult(
                    exit_code=-1,
                    stdout="",
                    stderr="timeout",
                    duration_sec=120.0,
                    environment_error="TEST_TIMEOUT: pytest exceeded 120s",
                    error_kind="timeout",
                )
            if log_path:
                log_path.parent.mkdir(parents=True, exist_ok=True)
                log_path.write_text(
                    result.environment_error or result.stdout, encoding="utf-8"
                )
            return result

    runner = BaselineThenTimeout()
    controller = TaskController(
        llm=llm,
        runner=runner,  # type: ignore[arg-type]
        approve=lambda *_: True,
        say=lambda _m: None,
        session_base=tmp_path / "sessions",
    )
    session = controller.run(repo, "make f return 2")

    assert session.status == SessionStatus.TEST_TIMEOUT
    assert session.stop_reason == "pytest_timeout"
    assert session.attempts_used == 1  # apply succeeded before timeout
    assert runner.calls == 2
    assert llm.complete_calls == 1  # one analyze window, then stop
    events = [e["event"] for e in _load_trace(session)]
    assert "patch_applied" in events
    assert "pytest_finished" in events
    pytest_ev = next(e for e in _load_trace(session) if e["event"] == "pytest_finished")
    assert pytest_ev["payload"]["result"]["error_kind"] == "timeout"
