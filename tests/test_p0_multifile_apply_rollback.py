"""P0: Multi-file apply must roll back on mid-patch failure (real applier)."""

from __future__ import annotations

import json
from pathlib import Path

from code_agent.controller import TaskController
from code_agent.llm import LLMClient
from code_agent.patching.applier import apply_proposal
from code_agent.patching.hashes import working_tree_hash
from code_agent.state import PatchProposal, SessionStatus, TestResult


def _workspace(tmp_path: Path) -> Path:
    root = tmp_path / "work"
    root.mkdir()
    (root / "a.py").write_text("def a():\n    return 1\n", encoding="utf-8")
    (root / "b.py").write_text("def b():\n    return 1\n", encoding="utf-8")
    return root


def _multifile_diff(*, b_old: str) -> str:
    """First hunk applies to a.py; second targets b.py with controllable old line."""
    return (
        "--- a/a.py\n"
        "+++ b/a.py\n"
        "@@ -1,2 +1,2 @@\n"
        " def a():\n"
        "-    return 1\n"
        "+    return 2\n"
        "--- a/b.py\n"
        "+++ b/b.py\n"
        "@@ -1,2 +1,2 @@\n"
        " def b():\n"
        f"-    return {b_old}\n"
        "+    return 2\n"
    )


def test_multifile_apply_rolls_back_first_file_on_second_mismatch(tmp_path: Path):
    """Real PatchApplier: file A would change, file B mismatches → full rollback."""
    root = _workspace(tmp_path)
    before_a = (root / "a.py").read_text(encoding="utf-8")
    before_b = (root / "b.py").read_text(encoding="utf-8")
    before_hash = working_tree_hash(root)

    proposal = PatchProposal(
        diagnosis="multi",
        affected_files=["a.py", "b.py"],
        unified_diff=_multifile_diff(b_old="999"),  # mismatch on b
        expected_behavior="both return 2",
        risk_notes="low",
        tests_to_run=[],
    )
    result = apply_proposal(proposal, root)
    assert result.ok is False
    assert result.error
    assert "mismatch" in result.error.lower() or "Mismatch" in result.error

    assert (root / "a.py").read_text(encoding="utf-8") == before_a
    assert (root / "b.py").read_text(encoding="utf-8") == before_b
    assert working_tree_hash(root) == before_hash


def test_multifile_apply_success_baseline(tmp_path: Path):
    """Control: same two-file patch succeeds when both contexts match."""
    root = _workspace(tmp_path)
    proposal = PatchProposal(
        diagnosis="multi",
        affected_files=["a.py", "b.py"],
        unified_diff=_multifile_diff(b_old="1"),
        expected_behavior="both return 2",
        risk_notes="low",
        tests_to_run=[],
    )
    result = apply_proposal(proposal, root)
    assert result.ok is True, result.error
    assert "return 2" in (root / "a.py").read_text(encoding="utf-8")
    assert "return 2" in (root / "b.py").read_text(encoding="utf-8")


def test_controller_multifile_apply_failure_does_not_count_repair(
    tmp_path: Path, monkeypatch
):
    """Controller: apply-after-preflight multifile failure → no attempts_used bump."""
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "a.py").write_text("def a():\n    return 1\n", encoding="utf-8")
    (repo / "b.py").write_text("def b():\n    return 1\n", encoding="utf-8")
    tests = repo / "tests"
    tests.mkdir()
    (tests / "test_ab.py").write_text(
        "from a import a\nfrom b import b\n"
        "def test_ab():\n"
        "    assert a() == 2 and b() == 2\n",
        encoding="utf-8",
    )
    (repo / "pytest.ini").write_text("[pytest]\npythonpath = .\n", encoding="utf-8")

    # Preflight sees matching tree. After approval, mutate b.py so real apply
    # fails on the second file and rolls back a.py.
    from code_agent import controller as controller_mod

    real_apply = controller_mod.apply_proposal

    def apply_with_b_drift(proposal, workspace_root, **kwargs):
        target = workspace_root / "b.py"
        original_b = target.read_text(encoding="utf-8")
        target.write_text("def b():\n    return 0\n", encoding="utf-8")
        result = real_apply(proposal, workspace_root, **kwargs)
        # Applier rolls back to the drifted b.py it observed; restore preflight tree
        # so the session can continue cleanly if regeneration were attempted.
        # For this test we only need the first apply failure outcome.
        assert result.ok is False
        # After failed apply, a.py must not retain a partial edit.
        assert (workspace_root / "a.py").read_text(encoding="utf-8") == (
            "def a():\n    return 1\n"
        )
        # Restore b for determinism of artifact inspection.
        target.write_text(original_b, encoding="utf-8")
        return result

    monkeypatch.setattr(controller_mod, "apply_proposal", apply_with_b_drift)

    good = {
        "tool": "propose_patch",
        "args": {
            "diagnosis": "multi",
            "affected_files": ["a.py", "b.py"],
            "unified_diff": _multifile_diff(b_old="1"),
            "expected_behavior": "both return 2",
            "risk_notes": "low",
            "tests_to_run": ["tests/test_ab.py"],
        },
    }
    # Second proposal unused if we stop at NA after regen budget — provide recovery
    # path that never runs if first apply fails and we exhaust... actually first
    # failure consumes regen and continues analyzing; need more script entries or
    # finish. Provide a second identical patch that will also fail the same way,
    # then a third, then stop via finish to avoid hanging — better: exhaust to NA
    # with three apply failures (same as group 5). Here we only need one failure
    # side effects: use finish after one failed apply cycle via regeneration then finish.

    finish = {"tool": "finish", "args": {"reason": "stop after observing apply failure"}}

    class Runner:
        def __init__(self) -> None:
            self.calls = 0

        def run_pytest(self, workspace_root, *, log_path=None):
            self.calls += 1
            result = TestResult(
                exit_code=1,
                stdout="FAILED",
                stderr="",
                duration_sec=0.01,
                failed_tests=["tests/test_ab.py::test_ab"],
            )
            if log_path:
                log_path.parent.mkdir(parents=True, exist_ok=True)
                log_path.write_text(result.stdout, encoding="utf-8")
            return result

    runner = Runner()
    controller = TaskController(
        llm=LLMClient(dry_run_script=[good, finish]),
        runner=runner,  # type: ignore[arg-type]
        approve=lambda *_: True,
        say=lambda _m: None,
        session_base=tmp_path / "sessions",
    )
    session = controller.run(repo, "make a and b return 2")

    assert session.attempts_used == 0
    assert session.patch_apply_failures_after_preflight >= 1
    assert runner.calls == 1  # baseline only — no attempt pytest
    assert not list(session.artifacts_dir.glob("attempt-*.log"))
    events = [
        json.loads(line)
        for line in (session.artifacts_dir / "trace.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
        if line.strip()
    ]
    assert "patch_apply_failed_after_preflight" in {e["event"] for e in events}
    # Product does not emit a dedicated "rollback" event; failure event is the audit signal.
    assert "patch_applied" not in {e["event"] for e in events}
    # Working copy must not keep partial a.py edit.
    assert (session.workspace_root / "a.py").read_text(encoding="utf-8") == (
        "def a():\n    return 1\n"
    )
    assert (session.workspace_root / "b.py").read_text(encoding="utf-8") == (
        "def b():\n    return 1\n"
    )
    # Session may end ERROR (finish without patch) or still analyzing path — not SUCCEEDED.
    assert session.status != SessionStatus.SUCCEEDED
    assert session.status != SessionStatus.FAILED_MAX_ATTEMPTS
