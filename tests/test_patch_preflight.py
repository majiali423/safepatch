"""v0.3 Patch Applicability acceptance tests."""

from __future__ import annotations

import json
from pathlib import Path

from code_agent.controller import TaskController
from code_agent.llm import LLMClient
from code_agent.patching.applier import apply_proposal
from code_agent.patching.hunk_engine import apply_hunks_to_text
from code_agent.patching.preflight import PatchPreflight, PreflightFailure, PreflightSuccess
from code_agent.state import ApprovalBinding, PatchProposal, SessionStatus, TestResult


class FailThenPassRunner:
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


def _mini_repo(tmp_path: Path) -> Path:
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


def _patch(old: str, new: str, *, diagnosis: str = "fix") -> dict:
    return {
        "tool": "propose_patch",
        "args": {
            "diagnosis": diagnosis,
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


def _mismatch_patch() -> dict:
    # Wrong context / wrong old line — cannot apply exactly.
    return _patch("999", "2", diagnosis="bad context")


def _good_patch() -> dict:
    return _patch("1", "2", diagnosis="correct")


def _load_trace(session) -> list[dict]:
    path = session.artifacts_dir / "trace.jsonl"
    events = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            events.append(json.loads(line))
    return events


def test_mismatch_then_good_patch_one_repair_attempt(tmp_path: Path):
    """1. mismatch → new valid patch → pytest once → repair_attempts=1."""
    repo = _mini_repo(tmp_path)
    approve_calls: list[str] = []

    def approve(binding: ApprovalBinding, attempt: int) -> bool:
        approve_calls.append(binding.patch_hash)
        return True

    controller = TaskController(
        llm=LLMClient(dry_run_script=[_mismatch_patch(), _good_patch()]),
        runner=FailThenPassRunner(),  # type: ignore[arg-type]
        approve=approve,
        say=lambda _m: None,
        session_base=tmp_path / "sessions",
    )
    session = controller.run(repo, "make f return 2")
    assert session.status == SessionStatus.SUCCEEDED
    assert session.attempts_used == 1
    assert session.total_patch_regeneration_retries == 1
    assert session.patch_preflight_failures == 1
    assert session.patch_preflight_successes == 1
    assert session.total_format_retries_used == 0
    assert len(approve_calls) == 1
    events = {e["event"] for e in _load_trace(session)}
    assert "patch_preflight_failed" in events
    assert "patch_regeneration_requested" in events
    assert "patch_preflight_succeeded" in events
    assert "patch_applied" in events


def test_preflight_ablation_applies_then_regenerates(tmp_path: Path):
    repo = _mini_repo(tmp_path)
    controller = TaskController(
        llm=LLMClient(dry_run_script=[_mismatch_patch(), _good_patch()]),
        runner=FailThenPassRunner(),  # type: ignore[arg-type]
        approve=lambda _binding, _attempt: True,
        say=lambda _m: None,
        session_base=tmp_path / "sessions",
        preflight_enabled=False,
    )

    session = controller.run(repo, "make f return 2")

    assert session.status == SessionStatus.SUCCEEDED
    assert session.attempts_used == 1
    assert session.patch_preflight_failures == 0
    assert session.patch_preflight_successes == 0
    assert session.patch_apply_failures_after_preflight == 0
    assert session.patch_apply_failures_without_preflight == 1
    events = [event["event"] for event in _load_trace(session)]
    assert events.count("patch_preflight_bypassed") == 2
    assert "patch_apply_failed_without_preflight" in events
    assert "patch_preflight_started" not in events


def test_three_mismatches_patch_not_applicable(tmp_path: Path):
    """2. three mismatch patches → PATCH_NOT_APPLICABLE, repair_attempts=0."""
    repo = _mini_repo(tmp_path)
    approve_calls = 0

    def approve(binding: ApprovalBinding, attempt: int) -> bool:
        nonlocal approve_calls
        approve_calls += 1
        return True

    script = [_mismatch_patch(), _mismatch_patch(), _mismatch_patch()]
    controller = TaskController(
        llm=LLMClient(dry_run_script=script),
        runner=FailThenPassRunner(),  # type: ignore[arg-type]
        approve=approve,
        say=lambda _m: None,
        session_base=tmp_path / "sessions",
    )
    session = controller.run(repo, "make f return 2")
    assert session.status == SessionStatus.PATCH_NOT_APPLICABLE
    assert session.attempts_used == 0
    assert session.status != SessionStatus.FAILED_MAX_ATTEMPTS
    assert approve_calls == 0  # 3. mismatch never reaches approval
    assert session.patch_preflight_failures == 3
    assert session.total_patch_regeneration_retries == 2
    assert session.first_patch_applicable is False
    # No successful pytest after patch
    assert not list(session.artifacts_dir.glob("attempt-*.log"))
    events = [e["event"] for e in _load_trace(session)]
    assert events.count("patch_preflight_failed") == 3
    assert events.count("patch_regeneration_requested") == 2
    assert "patch_applied" not in events


def test_new_patch_revalidates_policy_and_reapproves(tmp_path: Path):
    """4. regenerated patch re-runs policy + approval with a new patch_hash."""
    repo = _mini_repo(tmp_path)
    approve_hashes: list[str] = []

    def approve(binding: ApprovalBinding, attempt: int) -> bool:
        approve_hashes.append(binding.patch_hash)
        return True

    # First: mismatch. Second: tries to weaken a test (policy reject).
    # Third: good business-code patch.
    bad_test_patch = {
        "tool": "propose_patch",
        "args": {
            "diagnosis": "tamper tests",
            "affected_files": ["tests/test_mod.py"],
            "unified_diff": (
                "--- a/tests/test_mod.py\n"
                "+++ b/tests/test_mod.py\n"
                "@@ -1,4 +1,4 @@\n"
                " from mod import f\n"
                " def test_f():\n"
                "-    assert f() == 2\n"
                "+    assert True\n"
            ),
            "expected_behavior": "pass",
            "risk_notes": "high",
            "tests_to_run": ["tests/test_mod.py"],
        },
    }
    controller = TaskController(
        llm=LLMClient(dry_run_script=[_mismatch_patch(), bad_test_patch, _good_patch()]),
        runner=FailThenPassRunner(),  # type: ignore[arg-type]
        approve=approve,
        say=lambda _m: None,
        session_base=tmp_path / "sessions",
    )
    session = controller.run(repo, "make f return 2")
    assert session.status == SessionStatus.SUCCEEDED
    assert len(approve_hashes) == 1
    assert session.total_patch_regeneration_retries == 1
    assert session.attempts_used == 1


def test_approval_base_changed(tmp_path: Path):
    """5. mutating working tree during approval → PATCH_BASE_CHANGED."""
    repo = _mini_repo(tmp_path)

    def approve(binding: ApprovalBinding, attempt: int) -> bool:
        # Mutate after binding was computed.
        target = None
        # Find workspace via proposal path is awkward; mutate source repo is wrong.
        # Controller binds on workspace_root — mutate via binding side effect:
        # We need the working copy. Walk session base for mod.py that is not source.
        sessions = tmp_path / "sessions"
        for mod in sessions.rglob("mod.py"):
            if "working_copy" in mod.parts:
                target = mod
                break
        assert target is not None
        target.write_text("def f():\n    return 1\n# mutated\n", encoding="utf-8")
        return True

    controller = TaskController(
        llm=LLMClient(dry_run_script=[_good_patch()]),
        runner=FailThenPassRunner(),  # type: ignore[arg-type]
        approve=approve,
        say=lambda _m: None,
        session_base=tmp_path / "sessions",
    )
    session = controller.run(repo, "make f return 2")
    assert session.status == SessionStatus.PATCH_BASE_CHANGED
    assert session.attempts_used == 0


def test_preflight_and_apply_share_matcher(tmp_path: Path):
    """6. preflight and apply use the same hunk_engine matcher."""
    repo = _mini_repo(tmp_path)
    original = (repo / "mod.py").read_text(encoding="utf-8")
    body = "--- a/mod.py\n+++ b/mod.py\n@@ -1,2 +1,2 @@\n def f():\n-    return 1\n+    return 2\n"
    # Same matcher symbol used by both preflight and applier modules.
    from code_agent.patching import applier, hunk_engine, preflight

    assert preflight.apply_hunks_to_text is hunk_engine.apply_hunks_to_text
    assert applier.apply_hunks_to_text is hunk_engine.apply_hunks_to_text
    assert applier._apply_hunks is hunk_engine.apply_hunks_to_text

    patched = apply_hunks_to_text(original, body, "mod.py")
    assert "return 2" in patched

    proposal = PatchProposal(
        diagnosis="x",
        affected_files=["mod.py"],
        unified_diff=body,
        expected_behavior="y",
        risk_notes="z",
        tests_to_run=[],
    )
    pf = PatchPreflight.run(proposal, repo)
    assert isinstance(pf, PreflightSuccess)
    applied = apply_proposal(proposal, repo)
    assert applied.ok is True


def test_no_fuzzy_apply_one_line_off(tmp_path: Path):
    """7. one-line context mismatch must fail (no fuzzy)."""
    repo = _mini_repo(tmp_path)
    proposal = PatchProposal(
        diagnosis="x",
        affected_files=["mod.py"],
        unified_diff=(
            "--- a/mod.py\n"
            "+++ b/mod.py\n"
            "@@ -1,2 +1,2 @@\n"
            " def f():\n"
            "-    return 99\n"
            "+    return 2\n"
        ),
        expected_behavior="y",
        risk_notes="z",
        tests_to_run=[],
    )
    pf = PatchPreflight.run(proposal, repo)
    assert isinstance(pf, PreflightFailure)
    assert pf.error_kind == "context_mismatch" or pf.error_kind == "deletion_mismatch"
    assert pf.target_file == "mod.py"
    assert pf.file_excerpt
    assert pf.patch_hash
    assert pf.working_tree_hash
    applied = apply_proposal(proposal, repo)
    assert applied.ok is False


def test_format_regen_repair_counters_isolated(tmp_path: Path):
    """8. format / patch regeneration / repair counters do not mix."""
    repo = _mini_repo(tmp_path)
    script = [
        "not json",
        _mismatch_patch(),
        _good_patch(),
    ]
    controller = TaskController(
        llm=LLMClient(dry_run_script=script),
        runner=FailThenPassRunner(),  # type: ignore[arg-type]
        approve=lambda *_: True,
        say=lambda _m: None,
        session_base=tmp_path / "sessions",
    )
    session = controller.run(repo, "make f return 2")
    assert session.status == SessionStatus.SUCCEEDED
    assert session.total_format_retries_used == 1
    assert session.total_patch_regeneration_retries == 1
    assert session.attempts_used == 1
    summary = session.to_summary()
    assert summary["total_format_retries_used"] == 1
    assert summary["total_patch_regeneration_retries"] == 1
    assert summary["attempts_used"] == 1
    assert summary["first_patch_applicable"] is False


def test_apply_failed_after_preflight_trace(tmp_path: Path, monkeypatch):
    """Approve-time race: apply fails after successful preflight."""
    repo = _mini_repo(tmp_path)
    from code_agent import controller as controller_mod

    real_apply = controller_mod.apply_proposal

    def flaky_apply(proposal, workspace_root, **kwargs):
        # First post-approval apply fails; subsequent (if any) use real apply.
        if not getattr(flaky_apply, "_failed_once", False):
            flaky_apply._failed_once = True  # type: ignore[attr-defined]
            from code_agent.patching.applier import ApplyResult

            return ApplyResult(
                ok=False,
                error="context_mismatch: simulated race after preflight",
                error_kind="context_mismatch",
                target_file="mod.py",
                rollback_succeeded=True,
            )
        return real_apply(proposal, workspace_root, **kwargs)

    monkeypatch.setattr(controller_mod, "apply_proposal", flaky_apply)

    # After race: regenerate with good patch again.
    controller = TaskController(
        llm=LLMClient(dry_run_script=[_good_patch(), _good_patch()]),
        runner=FailThenPassRunner(),  # type: ignore[arg-type]
        approve=lambda *_: True,
        say=lambda _m: None,
        session_base=tmp_path / "sessions",
    )
    session = controller.run(repo, "make f return 2")
    assert session.status == SessionStatus.SUCCEEDED
    assert session.patch_apply_failures_after_preflight == 1
    assert session.attempts_used == 1
    events = {e["event"] for e in _load_trace(session)}
    assert "patch_apply_failed_after_preflight" in events
    assert "patch_regeneration_requested" in events
