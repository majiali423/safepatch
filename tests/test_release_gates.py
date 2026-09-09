"""Pre-release scenarios tied to Controller/Applier v0.3.1 changes."""

from __future__ import annotations

import json
from io import StringIO
from pathlib import Path

from rich.console import Console

from code_agent.controller import TaskController
from code_agent.llm import LLMClient
from code_agent.patching.applier import ApplyResult, apply_proposal
from code_agent.patching.hashes import working_tree_hash
from code_agent.patching.preflight import PatchPreflight, PreflightSuccess
from code_agent.patching.validator import PolicyValidator
from code_agent.state import PatchProposal, SessionStatus, TestResult


def _mini_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir(parents=True, exist_ok=True)
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


def _new_test_file_patch() -> dict:
    return {
        "tool": "propose_patch",
        "args": {
            "diagnosis": "add coverage",
            "affected_files": ["tests/test_extra.py"],
            "unified_diff": (
                "--- /dev/null\n"
                "+++ b/tests/test_extra.py\n"
                "@@ -0,0 +1,4 @@\n"
                "+from mod import f\n"
                "+\n"
                "+def test_extra():\n"
                "+    assert f() == 1\n"
            ),
            "expected_behavior": "extra test",
            "risk_notes": "new test",
            "tests_to_run": ["tests/test_extra.py"],
        },
    }


def _load_events(session) -> list[dict]:
    return [
        json.loads(line)
        for line in (session.artifacts_dir / "trace.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
        if line.strip()
    ]


class _BaselineFailRunner:
    def __init__(self) -> None:
        self.calls = 0

    def preflight(self):
        return True, "ok"

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


def test_cli_permission_apply_failure_exit_1_not_6_or_7(tmp_path: Path, monkeypatch):
    """Permission apply failure: ERROR → CLI exit 1 (not 6/7), no regen/repair/pytest."""
    from code_agent import cli as cli_mod
    from code_agent import controller as controller_mod

    repo = _mini_repo(tmp_path)
    session_base = tmp_path / "sess"
    buf = StringIO()
    monkeypatch.setattr(cli_mod, "console", Console(file=buf, force_terminal=True))
    monkeypatch.setattr(cli_mod, "DockerPytestRunner", lambda: _BaselineFailRunner())
    monkeypatch.setattr(
        cli_mod,
        "LLMClient",
        lambda **kwargs: LLMClient(dry_run_script=[_good_patch()]),
    )

    def permission_denied(proposal, workspace_root, **kwargs):
        return ApplyResult(
            ok=False,
            error="PermissionError: [Errno 13] Permission denied: 'mod.py'",
            error_kind="permission_error",
            target_file="mod.py",
            rollback_succeeded=True,
        )

    monkeypatch.setattr(controller_mod, "apply_proposal", permission_denied)

    code = cli_mod.main(
        [
            str(repo),
            "make f return 2",
            "--yes",
            "--session-base",
            str(session_base),
        ]
    )
    assert code == 1
    assert code not in {6, 7}

    summaries = list(session_base.rglob("summary.json"))
    assert summaries
    artifacts = max(summaries, key=lambda p: p.stat().st_mtime).parent
    summary = json.loads((artifacts / "summary.json").read_text(encoding="utf-8"))
    assert summary["status"] == "ERROR"
    assert "permission_error" in summary["stop_reason"]
    assert summary["attempts_used"] == 0
    assert summary["total_patch_regeneration_retries"] == 0
    assert not list(artifacts.glob("attempt-*.log"))

    events = [
        json.loads(line)
        for line in (artifacts / "trace.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    kinds = [e["event"] for e in events]
    assert "patch_regeneration_requested" not in kinds
    assert "patch_applied" not in kinds
    fail = next(e for e in events if e["event"] == "patch_apply_failed_after_preflight")
    assert fail["payload"]["error_kind"] == "permission_error"
    out = buf.getvalue()
    assert "ERROR" in out
    assert "permission_error" in out


def test_cli_io_apply_failure_exit_1_not_6_or_7(tmp_path: Path, monkeypatch):
    """I/O apply failure follows the same ERROR / exit 1 contract."""
    from code_agent import cli as cli_mod
    from code_agent import controller as controller_mod

    repo = _mini_repo(tmp_path)
    session_base = tmp_path / "sess"
    buf = StringIO()
    monkeypatch.setattr(cli_mod, "console", Console(file=buf, force_terminal=True))
    monkeypatch.setattr(cli_mod, "DockerPytestRunner", lambda: _BaselineFailRunner())
    monkeypatch.setattr(
        cli_mod,
        "LLMClient",
        lambda **kwargs: LLMClient(dry_run_script=[_good_patch()]),
    )

    def io_fail(proposal, workspace_root, **kwargs):
        return ApplyResult(
            ok=False,
            error="OSError: [Errno 28] No space left on device",
            error_kind="io_error",
            target_file="mod.py",
            rollback_succeeded=True,
        )

    monkeypatch.setattr(controller_mod, "apply_proposal", io_fail)

    code = cli_mod.main(
        [
            str(repo),
            "make f return 2",
            "--yes",
            "--session-base",
            str(session_base),
        ]
    )
    assert code == 1
    assert code not in {6, 7}
    summary = json.loads(
        max(session_base.rglob("summary.json"), key=lambda p: p.stat().st_mtime).read_text(
            encoding="utf-8"
        )
    )
    assert summary["status"] == "ERROR"
    assert summary["stop_reason"] == "patch_apply_io_error"
    assert summary["attempts_used"] == 0
    assert summary["total_patch_regeneration_retries"] == 0


def test_patch_hash_changed_after_approval_invalidates(tmp_path: Path):
    """Approving patch A then swapping diff to B before apply must not land B."""
    repo = _mini_repo(tmp_path)
    original = (repo / "mod.py").read_text(encoding="utf-8")
    bound_hashes: list[str] = []

    def approve_then_swap_diff(binding, attempt: int) -> bool:
        bound_hashes.append(binding.patch_hash)
        # Mutate the approved proposal object so re-hash diverges.
        binding.proposal.unified_diff = (
            "--- a/mod.py\n"
            "+++ b/mod.py\n"
            "@@ -1,2 +1,2 @@\n"
            " def f():\n"
            "-    return 1\n"
            "+    return 99\n"
        )
        return True

    runner = _BaselineFailRunner()
    controller = TaskController(
        llm=LLMClient(dry_run_script=[_good_patch()]),
        runner=runner,  # type: ignore[arg-type]
        approve=approve_then_swap_diff,
        say=lambda _m: None,
        session_base=tmp_path / "sessions",
    )
    session = controller.run(repo, "make f return 2")

    assert session.status == SessionStatus.PATCH_BASE_CHANGED
    assert session.stop_reason == "patch_base_changed"
    assert session.attempts_used == 0
    assert runner.calls == 1  # baseline only
    assert not list(session.artifacts_dir.glob("attempt-*.log"))
    assert (session.workspace_root / "mod.py").read_text(encoding="utf-8") == original
    assert "return 99" not in (session.workspace_root / "mod.py").read_text(encoding="utf-8")

    assert session.last_error
    assert "bound_patch=" in session.last_error
    assert "now_patch=" in session.last_error
    assert bound_hashes
    assert bound_hashes[0] in session.last_error

    events = _load_events(session)
    kinds = [e["event"] for e in events]
    assert "approval_decision" in kinds
    assert "patch_applied" not in kinds
    # Hash invalidation is recorded on the terminal path.
    finished = next(e for e in events if e["event"] == "session_finished")
    assert finished["payload"]["status"] == "PATCH_BASE_CHANGED"
    summary = json.loads(
        (session.artifacts_dir / "summary.json").read_text(encoding="utf-8")
    )
    assert summary["status"] == "PATCH_BASE_CHANGED"
    assert "error" in summary
    assert "bound_patch=" in summary["error"]
    assert "now_patch=" in summary["error"]


def test_new_file_preflight_and_apply_success(tmp_path: Path):
    """Legal new test file passes policy → preflight → apply (unit) and controller."""
    root = tmp_path / "unit"
    root.mkdir()
    (root / "mod.py").write_text("def f():\n    return 1\n", encoding="utf-8")
    tests = root / "tests"
    tests.mkdir()
    (tests / "test_mod.py").write_text(
        "from mod import f\n"
        "def test_f():\n"
        "    assert f() == 1\n",
        encoding="utf-8",
    )
    proposal = PatchProposal(
        diagnosis="add coverage",
        affected_files=["tests/test_extra.py"],
        unified_diff=(
            "--- /dev/null\n"
            "+++ b/tests/test_extra.py\n"
            "@@ -0,0 +1,4 @@\n"
            "+from mod import f\n"
            "+\n"
            "+def test_extra():\n"
            "+    assert f() == 1\n"
        ),
        expected_behavior="extra",
        risk_notes="new test",
        tests_to_run=["tests/test_extra.py"],
    )
    assert PolicyValidator().validate(proposal, root).ok
    pf = PatchPreflight.run(proposal, root)
    assert isinstance(pf, PreflightSuccess)
    before = working_tree_hash(root)
    result = apply_proposal(proposal, root)
    assert result.ok
    assert (root / "tests" / "test_extra.py").is_file()
    assert working_tree_hash(root) != before

    repo = _mini_repo(tmp_path / "ctrl")

    class Runner:
        def __init__(self) -> None:
            self.calls = 0

        def run_pytest(self, workspace_root, *, log_path=None):
            self.calls += 1
            result = TestResult(
                exit_code=0 if self.calls > 1 else 1,
                stdout="ok" if self.calls > 1 else "FAILED",
                stderr="",
                duration_sec=0.01,
                failed_tests=[] if self.calls > 1 else ["tests/test_mod.py::test_f"],
            )
            if log_path:
                log_path.write_text(result.stdout, encoding="utf-8")
            return result

    controller = TaskController(
        llm=LLMClient(dry_run_script=[_new_test_file_patch()]),
        runner=Runner(),  # type: ignore[arg-type]
        approve=lambda *_: True,
        say=lambda _m: None,
        session_base=tmp_path / "sessions",
    )
    session = controller.run(repo, "add extra test")
    assert session.status == SessionStatus.SUCCEEDED
    assert session.attempts_used == 1
    assert (session.workspace_root / "tests" / "test_extra.py").is_file()


def test_new_file_apply_failure_rolls_back_partial_create(tmp_path: Path):
    """If a later hunk fails, a newly created file must not remain."""
    root = tmp_path / "work"
    root.mkdir()
    (root / "mod.py").write_text("def f():\n    return 1\n", encoding="utf-8")
    tests = root / "tests"
    tests.mkdir()
    (tests / "test_mod.py").write_text(
        "from mod import f\n"
        "def test_f():\n"
        "    assert f() == 1\n",
        encoding="utf-8",
    )
    before_hash = working_tree_hash(root)
    # First: create new test file. Second: modify mod.py with wrong context.
    proposal = PatchProposal(
        diagnosis="partial",
        affected_files=["tests/test_extra.py", "mod.py"],
        unified_diff=(
            "--- /dev/null\n"
            "+++ b/tests/test_extra.py\n"
            "@@ -0,0 +1,3 @@\n"
            "+from mod import f\n"
            "+def test_extra():\n"
            "+    assert f() == 1\n"
            "--- a/mod.py\n"
            "+++ b/mod.py\n"
            "@@ -1,2 +1,2 @@\n"
            " def f():\n"
            "-    return 999\n"
            "+    return 2\n"
        ),
        expected_behavior="x",
        risk_notes="x",
        tests_to_run=["tests/test_extra.py"],
    )
    # Policy may reject multi-file with new test + modify — if so, apply still must
    # not leave partial files when validation is bypassed via direct engine path.
    # Use apply_proposal which validates first; if policy blocks, nothing written.
    result = apply_proposal(proposal, root)
    assert result.ok is False
    assert not (root / "tests" / "test_extra.py").exists()
    assert (root / "mod.py").read_text(encoding="utf-8") == "def f():\n    return 1\n"
    assert working_tree_hash(root) == before_hash
