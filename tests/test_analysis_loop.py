"""AnalysisLoop can run without constructing TaskController."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from code_agent.analysis_loop import AnalysisLoop
from code_agent.controller import TaskController
from code_agent.llm import LLMClient
from code_agent.patch_gate import PatchGate
from code_agent.patching.validator import PolicyValidator
from code_agent.repository.git_diff import snapshot_tree
from code_agent.repository.repo_map import build_repo_map
from code_agent.repository.workspace import import_repository
from code_agent.session_control import RegenerationBudget, RequiredReadTracker, SessionDeadline
from code_agent.state import (
    ApprovalBinding,
    SessionStatus,
    TaskSession,
    TestResult,
    TokenUsage,
)
from code_agent.tracing.recorder import TraceRecorder


class _Usage:
    def record_call_usage(self, session: TaskSession, usage: TokenUsage | None) -> None:
        session.observability.record_call_usage(usage)


class _FailThenPass:
    def __init__(self) -> None:
        self.calls = 0

    def run_pytest(self, workspace_root, *, log_path=None):
        self.calls += 1
        if self.calls == 1:
            result = TestResult(
                exit_code=1,
                stdout="FAILED tests/test_mod.py::test_f",
                stderr="",
                duration_sec=0.01,
                failed_tests=["tests/test_mod.py::test_f"],
            )
        else:
            result = TestResult(exit_code=0, stdout="1 passed", stderr="", duration_sec=0.01)
        if log_path:
            log_path.parent.mkdir(parents=True, exist_ok=True)
            log_path.write_text(result.stdout, encoding="utf-8")
        return result


def _repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "mod.py").write_text("def f():\n    return 1\n", encoding="utf-8")
    tests = repo / "tests"
    tests.mkdir()
    (tests / "test_mod.py").write_text(
        "from mod import f\ndef test_f():\n    assert f() == 2\n",
        encoding="utf-8",
    )
    (repo / "pytest.ini").write_text("[pytest]\npythonpath = .\n", encoding="utf-8")
    return repo


def _patch() -> dict:
    return {
        "tool": "propose_patch",
        "args": {
            "diagnosis": "fix",
            "affected_files": ["mod.py"],
            "unified_diff": (
                "--- a/mod.py\n+++ b/mod.py\n@@ -1,2 +1,2 @@\n def f():\n"
                "-    return 1\n+    return 2\n"
            ),
            "expected_behavior": "return 2",
            "risk_notes": "low",
            "tests_to_run": ["tests/test_mod.py"],
        },
    }


def _mismatch() -> dict:
    return {
        "tool": "propose_patch",
        "args": {
            "diagnosis": "bad",
            "affected_files": ["mod.py"],
            "unified_diff": (
                "--- a/mod.py\n+++ b/mod.py\n@@ -1,2 +1,2 @@\n def f():\n"
                "-    return 999\n+    return 2\n"
            ),
            "expected_behavior": "return 2",
            "risk_notes": "low",
            "tests_to_run": ["tests/test_mod.py"],
        },
    }


def _ready_session(tmp_path: Path, repo: Path) -> tuple[TaskSession, Path]:
    imported = import_repository(repo, tmp_path / "sessions")
    session = TaskSession(
        session_id=imported.session_id,
        source_repo=repo.resolve(),
        session_dir=imported.session_dir,
        workspace_root=imported.workspace_root,
        artifacts_dir=imported.artifacts_dir,
        bug_description="make f return 2",
        status=SessionStatus.BASELINE_TESTED,
    )
    text, data = build_repo_map(session.workspace_root)
    session.repo_map_text = text
    session.repo_map_data = data
    session.baseline = TestResult(
        exit_code=1,
        stdout="FAILED tests/test_mod.py::test_f",
        stderr="",
        duration_sec=0.01,
        failed_tests=["tests/test_mod.py::test_f"],
    )
    snapshot = session.session_dir / "baseline_snapshot"
    snapshot_tree(session.workspace_root, snapshot)
    return session, snapshot


def _loop(llm: LLMClient) -> AnalysisLoop:
    return AnalysisLoop(
        llm=llm,
        policy=PolicyValidator(),
        patch_gate=PatchGate(preflight_enabled=True, say=lambda _m: None),
        say=lambda _m: None,
        deadline=SessionDeadline(monotonic=lambda: 0.0, session_deadline_seconds=None),
        usage=_Usage(),
        required_reads=RequiredReadTracker(),
        regeneration=RegenerationBudget(),
        max_analysis_steps=20,
    )


def test_controller_no_longer_owns_analyze_phase():
    assert not hasattr(TaskController, "_analyze_phase")
    assert not hasattr(TaskController, "_build_user_prompt")
    assert not hasattr(TaskController, "_handle_format_error")


def test_analysis_loop_returns_binding_without_controller(tmp_path: Path):
    repo = _repo(tmp_path)
    session, snapshot = _ready_session(tmp_path, repo)
    llm = LLMClient(dry_run_script=[_patch()])
    loop = _loop(llm)
    trace = TraceRecorder(session.artifacts_dir / "trace.jsonl")
    binding = loop.run(session, trace, snapshot)
    assert isinstance(binding, ApprovalBinding)
    assert session.status is SessionStatus.PATCH_PROPOSED
    assert session.attempts_used == 0
    assert "return 1" in (session.workspace_root / "mod.py").read_text(encoding="utf-8")


def test_analysis_loop_format_exhaustion_is_terminal(tmp_path: Path):
    repo = _repo(tmp_path)
    session, snapshot = _ready_session(tmp_path, repo)
    llm = LLMClient(dry_run_script=["bad1", "bad2", "bad3", _patch()])
    loop = _loop(llm)
    trace = TraceRecorder(session.artifacts_dir / "trace.jsonl")
    binding = loop.run(session, trace, snapshot)
    assert binding is None
    assert session.status is SessionStatus.MODEL_OUTPUT_INVALID
    assert session.stop_reason == "model_output_invalid"
    assert session.attempts_used == 0
    assert session.total_format_retries_used == 2
    events = [
        json.loads(line)["event"]
        for line in (session.artifacts_dir / "trace.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert events.count("format_retry") == 2
    assert "retry_exhausted" in events


def test_analysis_loop_preflight_exhaustion_before_approval(tmp_path: Path):
    repo = _repo(tmp_path)
    session, snapshot = _ready_session(tmp_path, repo)
    read = {"tool": "read_file", "args": {"path": "mod.py", "start_line": 1, "end_line": 2}}
    llm = LLMClient(dry_run_script=[_mismatch(), read, _mismatch(), read, _mismatch()])
    loop = _loop(llm)
    trace = TraceRecorder(session.artifacts_dir / "trace.jsonl")
    binding = loop.run(session, trace, snapshot)
    assert binding is None
    assert session.status is SessionStatus.PATCH_NOT_APPLICABLE
    assert session.stop_reason == "patch_not_applicable"
    assert session.attempts_used == 0
    assert session.total_patch_regeneration_retries == 2
    assert session.patch_preflight_failures == 3


def test_analysis_loop_required_read_blocks_until_range_is_read(tmp_path: Path):
    repo = _repo(tmp_path)
    session, snapshot = _ready_session(tmp_path, repo)
    session.required_reads["mod.py"] = (1, 2)
    llm = LLMClient(dry_run_script=[_patch(), _read(), _patch()])
    loop = _loop(llm)
    trace = TraceRecorder(session.artifacts_dir / "trace.jsonl")
    binding = loop.run(session, trace, snapshot)
    assert isinstance(binding, ApprovalBinding)
    assert "mod.py" not in session.required_reads
    events = [
        json.loads(line)["event"]
        for line in (session.artifacts_dir / "trace.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert "proposal_blocked_required_read" in events
    assert "required_read_satisfied" in events
    assert session.attempts_used == 0


def _read() -> dict:
    return {"tool": "read_file", "args": {"path": "mod.py", "start_line": 1, "end_line": 2}}


def test_analysis_loop_binding_connects_to_controller_apply(tmp_path: Path):
    repo = _repo(tmp_path)
    session = TaskController(
        llm=LLMClient(dry_run_script=[_patch()]),
        runner=_FailThenPass(),
        approve=lambda *_: True,
        session_base=tmp_path / "sessions",
    ).run(repo, "make f return 2")
    assert session.status is SessionStatus.SUCCEEDED
    assert session.attempts_used == 1
    assert "return 2" in (session.workspace_root / "mod.py").read_text(encoding="utf-8")
    assert session.observability.post_apply_pytest_runs == 1


def test_model_timeout_restored_when_operator_cancels(tmp_path: Path, monkeypatch):
    repo = _repo(tmp_path)
    session, snapshot = _ready_session(tmp_path, repo)
    llm = LLMClient(dry_run_script=[_patch()], request_timeout_seconds=60.0)
    loop = _loop(llm)
    loop.deadline = SessionDeadline(monotonic=lambda: 0.0, session_deadline_seconds=5.0)
    loop.deadline.bind_start(0.0)

    def cancel(_messages):
        assert llm.request_timeout_seconds == 5.0
        raise KeyboardInterrupt

    monkeypatch.setattr(llm, "complete", cancel)
    with pytest.raises(KeyboardInterrupt):
        loop.run(session, TraceRecorder(session.artifacts_dir / "trace.jsonl"), snapshot)
    assert llm.request_timeout_seconds == 60.0
