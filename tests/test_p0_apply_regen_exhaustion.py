"""P0/v0.3.1: Apply-after-preflight failure classification and regen budget."""

from __future__ import annotations

import json
from pathlib import Path

from code_agent.controller import TaskController
from code_agent.llm import LLMClient
from code_agent.patching.applier import ApplyResult
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


def _patch(old: str, new: str) -> dict:
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
                f"-    return {old}\n"
                f"+    return {new}\n"
            ),
            "expected_behavior": f"return {new}",
            "risk_notes": "low",
            "tests_to_run": ["tests/test_mod.py"],
        },
    }


def _good_patch() -> dict:
    return _patch("1", "2")


def _load_events(session) -> list[dict]:
    return [
        json.loads(line)
        for line in (session.artifacts_dir / "trace.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
        if line.strip()
    ]


class _BaselineOnlyRunner:
    def __init__(self) -> None:
        self.calls = 0

    def run_pytest(self, workspace_root, *, log_path=None):
        self.calls += 1
        result = TestResult(
            exit_code=1,
            stdout="FAILED tests/test_mod.py::test_f",
            stderr="",
            duration_sec=0.01,
            failed_tests=["tests/test_mod.py::test_f"],
        )
        if log_path:
            log_path.parent.mkdir(parents=True, exist_ok=True)
            log_path.write_text(result.stdout, encoding="utf-8")
        return result


class _FailUntilPassRunner:
    """Baseline fails; each post-apply pytest fails until ``pass_on`` call index."""

    def __init__(self, *, pass_on: int) -> None:
        self.calls = 0
        self.pass_on = pass_on

    def run_pytest(self, workspace_root, *, log_path=None):
        self.calls += 1
        if self.calls >= self.pass_on:
            result = TestResult(
                exit_code=0, stdout="1 passed", stderr="", duration_sec=0.01
            )
        else:
            result = TestResult(
                exit_code=1,
                stdout="FAILED tests/test_mod.py::test_f",
                stderr="",
                duration_sec=0.01,
                failed_tests=["tests/test_mod.py::test_f"],
            )
        if log_path:
            log_path.parent.mkdir(parents=True, exist_ok=True)
            log_path.write_text(result.stdout, encoding="utf-8")
        return result


def test_type_a_context_mismatch_apply_failures_exhaust_to_patch_not_applicable(
    tmp_path: Path, monkeypatch
):
    """Type A: exact-apply context mismatch exhausts to PATCH_NOT_APPLICABLE."""
    repo = _mini_repo(tmp_path)
    from code_agent import controller as controller_mod

    real_apply = controller_mod.apply_proposal
    apply_calls = 0

    def apply_with_context_drift(proposal, workspace_root, **kwargs):
        nonlocal apply_calls
        apply_calls += 1
        mod = workspace_root / "mod.py"
        before = mod.read_text(encoding="utf-8")
        mod.write_text("def f():\n    return 99\n", encoding="utf-8")
        result = real_apply(proposal, workspace_root, **kwargs)
        mod.write_text(before, encoding="utf-8")
        assert result.ok is False
        assert result.error_kind in {"context_mismatch", "deletion_mismatch"}
        assert result.rollback_succeeded is True
        return result

    monkeypatch.setattr(controller_mod, "apply_proposal", apply_with_context_drift)

    approve_count = 0

    def approve(*_a, **_k):
        nonlocal approve_count
        approve_count += 1
        return True

    runner = _BaselineOnlyRunner()
    script = [_good_patch() for _ in range(6)]
    controller = TaskController(
        llm=LLMClient(dry_run_script=script),
        runner=runner,  # type: ignore[arg-type]
        approve=approve,
        say=lambda _m: None,
        session_base=tmp_path / "sessions",
    )
    session = controller.run(repo, "make f return 2")

    assert session.status == SessionStatus.PATCH_NOT_APPLICABLE
    assert session.stop_reason == "patch_not_applicable"
    assert session.attempts_used == 0
    assert session.patch_apply_failures_after_preflight == 3
    assert session.total_patch_regeneration_retries == 2
    assert approve_count == 3
    assert apply_calls == 3
    assert runner.calls == 1
    assert not list(session.artifacts_dir.glob("attempt-*.log"))

    events = _load_events(session)
    kinds = [e["event"] for e in events]
    assert kinds.count("patch_apply_failed_after_preflight") == 3
    assert kinds.count("patch_regeneration_requested") == 2
    assert "patch_applied" not in kinds
    fail_payloads = [
        e["payload"]
        for e in events
        if e["event"] == "patch_apply_failed_after_preflight"
    ]
    assert all(p.get("error_kind") in {"context_mismatch", "deletion_mismatch"} for p in fail_payloads)
    assert all(p.get("rollback_succeeded") is True for p in fail_payloads)
    assert all("patch_hash" in p and "working_tree_hash" in p for p in fail_payloads)


def test_type_b_io_failure_must_not_use_patch_not_applicable_budget(
    tmp_path: Path, monkeypatch
):
    """Type B: permission/I/O apply failures → ERROR, no regen budget."""
    repo = _mini_repo(tmp_path)
    from code_agent import controller as controller_mod

    apply_calls = 0

    def apply_permission_denied(proposal, workspace_root, **kwargs):
        nonlocal apply_calls
        apply_calls += 1
        return ApplyResult(
            ok=False,
            error="PermissionError: [Errno 13] Permission denied: 'mod.py'",
            error_kind="permission_error",
            target_file="mod.py",
            rollback_succeeded=True,
        )

    monkeypatch.setattr(controller_mod, "apply_proposal", apply_permission_denied)

    approve_count = 0

    def approve(*_a, **_k):
        nonlocal approve_count
        approve_count += 1
        return True

    runner = _BaselineOnlyRunner()
    script = [_good_patch() for _ in range(6)]
    controller = TaskController(
        llm=LLMClient(dry_run_script=script),
        runner=runner,  # type: ignore[arg-type]
        approve=approve,
        say=lambda _m: None,
        session_base=tmp_path / "sessions",
    )
    session = controller.run(repo, "make f return 2")

    assert session.status == SessionStatus.ERROR
    assert session.stop_reason == "patch_apply_permission_error"
    assert "permission_error" in (session.last_error or "")
    assert session.attempts_used == 0
    assert session.total_patch_regeneration_retries == 0
    assert apply_calls == 1
    assert approve_count == 1
    assert runner.calls == 1
    assert not list(session.artifacts_dir.glob("attempt-*.log"))

    events = _load_events(session)
    kinds = [e["event"] for e in events]
    assert "patch_regeneration_requested" not in kinds
    assert "patch_applied" not in kinds
    fail = next(e for e in events if e["event"] == "patch_apply_failed_after_preflight")
    assert fail["payload"]["error_kind"] == "permission_error"
    assert fail["payload"]["rollback_succeeded"] is True
    assert fail["payload"]["target_file"] == "mod.py"


def test_regeneration_budget_resets_after_successful_apply(
    tmp_path: Path, monkeypatch
):
    """After a successful apply, the next repair window gets a full regen budget."""
    repo = _mini_repo(tmp_path)
    from code_agent import controller as controller_mod

    real_apply = controller_mod.apply_proposal
    # Call plan:
    # 1) mismatch → regen
    # 2) apply success (wrong value 3) → repair attempt 1, pytest fail, budget reset
    # 3) mismatch → regen again (proves fresh budget)
    # 4) apply success (value 2) → repair attempt 2, pytest pass
    script = [
        _patch("1", "2"),  # will mismatch via hook
        _patch("1", "3"),  # applies; tests still fail
        _patch("3", "2"),  # will mismatch via hook
        _patch("3", "2"),  # applies; tests pass
    ]

    state = {"n": 0}

    def selective_apply(proposal, workspace_root, **kwargs):
        state["n"] += 1
        # 1st and 3rd post-approval applies: force context mismatch then restore.
        if state["n"] in {1, 3}:
            mod = workspace_root / "mod.py"
            before = mod.read_text(encoding="utf-8")
            mod.write_text("def f():\n    return 99\n", encoding="utf-8")
            result = real_apply(proposal, workspace_root, **kwargs)
            mod.write_text(before, encoding="utf-8")
            assert result.ok is False
            return result
        return real_apply(proposal, workspace_root, **kwargs)

    monkeypatch.setattr(controller_mod, "apply_proposal", selective_apply)

    runner = _FailUntilPassRunner(pass_on=3)  # baseline + attempt1 fail; attempt2 pass
    controller = TaskController(
        llm=LLMClient(dry_run_script=script),
        runner=runner,  # type: ignore[arg-type]
        approve=lambda *_: True,
        say=lambda _m: None,
        session_base=tmp_path / "sessions",
    )
    session = controller.run(repo, "make f return 2")

    assert session.status == SessionStatus.SUCCEEDED
    assert session.attempts_used == 2
    # Two recoverable apply failures each consumed one regen; budget reset after
    # first successful apply so both are allowed.
    assert session.total_patch_regeneration_retries == 2
    assert session.patch_apply_failures_after_preflight == 2
    assert session.consecutive_patch_regeneration_retries == 0

    events = [e["event"] for e in _load_events(session)]
    assert events.count("patch_regeneration_requested") == 2
    assert events.count("patch_applied") == 2
    assert events.count("baseline_test_finished") == 1
    assert events.count("pytest_finished") == 2
