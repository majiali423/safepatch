from __future__ import annotations

import json
from pathlib import Path

from code_agent.controller import TaskController
from code_agent.llm import LLMClient
from code_agent.state import AnalysisPhase, SessionStatus, TestResult


class BaselineFailThenPassRunner:
    def __init__(self) -> None:
        self.calls = 0

    def run_pytest(self, workspace_root, *, log_path=None):
        self.calls += 1
        passed = self.calls > 1
        result = TestResult(
            exit_code=0 if passed else 1,
            stdout="1 passed" if passed else "1 failed",
            stderr="",
            duration_sec=0.01,
            failed_tests=[] if passed else ["tests/test_mod.py::test_f"],
        )
        if log_path:
            log_path.parent.mkdir(parents=True, exist_ok=True)
            log_path.write_text(result.stdout, encoding="utf-8")
        return result


def _repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "mod.py").write_text("def f():\n    return 1\n", encoding="utf-8")
    (repo / "other.py").write_text(
        "".join(f"value_{line} = {line}\n" for line in range(1, 501)),
        encoding="utf-8",
    )
    return repo


def _read() -> dict:
    return {
        "tool": "read_file",
        "args": {"path": "mod.py", "start_line": 1, "end_line": 2},
    }


def _evidence(start_line: int, end_line: int) -> dict:
    return {
        "tool": "request_evidence",
        "args": {
            "path": "other.py",
            "start_line": start_line,
            "end_line": end_line,
            "unanswered_requirement": "where the state is cleaned up",
            "reason": "existing evidence covers use but not cleanup",
        },
    }


def _patch(old: str = "1") -> dict:
    return {
        "tool": "propose_patch",
        "args": {
            "diagnosis": "fix return value",
            "affected_files": ["mod.py"],
            "unified_diff": (
                "--- a/mod.py\n"
                "+++ b/mod.py\n"
                "@@ -1,2 +1,2 @@\n"
                " def f():\n"
                f"-    return {old}\n"
                "+    return 2\n"
            ),
            "expected_behavior": "return two",
            "risk_notes": "low",
            "tests_to_run": ["tests/test_mod.py"],
        },
    }


def _events(session) -> list[dict]:
    return [
        json.loads(line)
        for line in (session.artifacts_dir / "trace.jsonl").read_text(
            encoding="utf-8"
        ).splitlines()
    ]


def _controller(tmp_path: Path, script: list[dict]) -> TaskController:
    return TaskController(
        llm=LLMClient(dry_run_script=script),
        runner=BaselineFailThenPassRunner(),  # type: ignore[arg-type]
        approve=lambda *_: True,
        say=lambda _message: None,
        session_base=tmp_path / "sessions",
    )


def test_fixed_exploration_budget_transitions_to_synthesis(tmp_path: Path):
    script = [_read() for _ in range(14)]
    session = _controller(tmp_path, script).run(_repo(tmp_path), "make f return 2")

    assert session.status == SessionStatus.READ_BUDGET_EXHAUSTED
    assert session.stop_reason == "synthesis_no_progress"
    assert session.analysis_phase == AnalysisPhase.SYNTHESIZE
    assert session.exploration_read_actions_used == 12
    assert session.read_actions_used == 12
    assert session.total_no_progress_actions == 2
    assert session.observability.model_calls == 14
    assert session.to_summary()["read_budget"] == {
        "used": 12,
        "max": 12,
        "remaining": 0,
        "total_read_actions": 12,
        "required_recovery_reads": 0,
        "consecutive_violations": 2,
        "total_violations": 2,
    }

    events = _events(session)
    assert sum(event["event"] == "synthesis_started" for event in events) == 1
    assert sum(event["event"] == "synthesis_no_progress" for event in events) == 2


def test_proposal_after_one_synthesis_no_progress_action_is_allowed(tmp_path: Path):
    script = [_read() for _ in range(12)] + [_read(), _patch()]
    session = _controller(tmp_path, script).run(_repo(tmp_path), "make f return 2")

    assert session.status == SessionStatus.SUCCEEDED
    assert session.analysis_phase == AnalysisPhase.PROPOSE
    assert session.total_no_progress_actions == 1
    assert session.consecutive_no_progress_actions == 0


def test_structured_evidence_returns_to_synthesis_then_patch(tmp_path: Path):
    script = [_read() for _ in range(12)] + [_evidence(360, 460), _patch()]
    session = _controller(tmp_path, script).run(_repo(tmp_path), "make f return 2")

    assert session.status == SessionStatus.SUCCEEDED
    assert session.evidence_requests_used == 1
    assert session.read_actions_used == 13
    events = _events(session)
    accepted = [event for event in events if event["event"] == "evidence_requested"]
    assert len(accepted) == 1
    assert accepted[0]["payload"]["remaining"] == 1


def test_third_evidence_request_is_a_hard_violation(tmp_path: Path):
    script = (
        [_read() for _ in range(12)]
        + [_evidence(1, 100), _evidence(121, 220), _evidence(241, 340)]
    )
    session = _controller(tmp_path, script).run(_repo(tmp_path), "make f return 2")

    assert session.status == SessionStatus.READ_BUDGET_EXHAUSTED
    assert session.stop_reason == "evidence_hard_violation"
    assert session.evidence_requests_used == 2
    assert session.hard_policy_violations == 1


def test_one_evidence_parameter_error_gets_free_correction(tmp_path: Path):
    script = [_read() for _ in range(12)] + [_evidence(1, 121), _evidence(1, 120), _patch()]
    session = _controller(tmp_path, script).run(_repo(tmp_path), "make f return 2")

    assert session.status == SessionStatus.SUCCEEDED
    assert session.evidence_parameter_corrections_used == 1
    assert session.evidence_requests_used == 1
    assert session.total_no_progress_actions == 0
    rejected = [
        event
        for event in _events(session)
        if event["event"] == "evidence_request_rejected"
    ]
    assert rejected[0]["payload"]["kind"] == "parameter_error"
    assert rejected[0]["payload"]["correction_granted"] is True


def test_later_parameter_error_is_promoted_to_no_progress(tmp_path: Path):
    script = (
        [_read() for _ in range(12)]
        + [_evidence(1, 121), _evidence(121, 241), _evidence(121, 220), _patch()]
    )
    session = _controller(tmp_path, script).run(_repo(tmp_path), "make f return 2")

    assert session.status == SessionStatus.SUCCEEDED
    assert session.evidence_parameter_corrections_used == 1
    assert session.total_no_progress_actions == 1
    assert session.consecutive_no_progress_actions == 0
    assert session.evidence_requests_used == 1


def test_repeated_evidence_is_no_progress_and_eventually_terminates(tmp_path: Path):
    script = (
        [_read() for _ in range(12)]
        + [_evidence(1, 100), _evidence(20, 80), _evidence(20, 80)]
    )
    session = _controller(tmp_path, script).run(_repo(tmp_path), "make f return 2")

    assert session.status == SessionStatus.READ_BUDGET_EXHAUSTED
    assert session.stop_reason == "synthesis_no_progress"
    assert session.evidence_requests_used == 1
    assert session.total_no_progress_actions == 2


def test_required_context_recovery_read_remains_allowed_in_synthesis(tmp_path: Path):
    script = [_read() for _ in range(12)] + [_patch(old="999"), _read(), _patch()]
    session = _controller(tmp_path, script).run(_repo(tmp_path), "make f return 2")

    assert session.status == SessionStatus.SUCCEEDED
    assert session.exploration_read_actions_used == 12
    assert session.read_actions_used == 13
    assert session.required_recovery_reads_used == 1
    assert session.evidence_requests_used == 0
    required_states = [
        event
        for event in _events(session)
        if event["event"] == "read_budget_state"
        and event["payload"].get("required_recovery_read") is True
    ]
    assert len(required_states) == 1
    assert required_states[0]["payload"]["remaining"] == 0


def test_finish_records_explicit_finish_phase(tmp_path: Path):
    script = [
        {
            "tool": "finish",
            "args": {"reason": "insufficient evidence for a safe patch"},
        }
    ]
    session = _controller(tmp_path, script).run(_repo(tmp_path), "make f return 2")

    assert session.status == SessionStatus.ERROR
    assert session.analysis_phase == AnalysisPhase.FINISH
    assert session.stop_reason == "agent_finished_without_patch"
    assert session.last_error == "insufficient evidence for a safe patch"
