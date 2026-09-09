"""P0: Policy must reject path-escape diffs before preflight/approval/apply."""

from __future__ import annotations

import json
from pathlib import Path

from code_agent.controller import TaskController
from code_agent.llm import LLMClient
from code_agent.patching.applier import apply_proposal
from code_agent.patching.validator import PolicyValidator
from code_agent.state import PatchProposal, SessionStatus, TestResult


def _proposal(diff: str, files: list[str]) -> PatchProposal:
    return PatchProposal(
        diagnosis="escape",
        affected_files=files,
        unified_diff=diff,
        expected_behavior="x",
        risk_notes="x",
        tests_to_run=[],
    )


def _diff_for(path: str, body: str = "+secret=1\n") -> str:
    return (
        f"--- a/{path}\n"
        f"+++ b/{path}\n"
        "@@ -0,0 +1 @@\n"
        f"{body}"
    )


def test_policy_rejects_relative_traversal_path(tmp_path: Path):
    root = tmp_path / "work"
    root.mkdir()
    (root / "a.py").write_text("x=1\n", encoding="utf-8")
    outside = tmp_path / "outside.py"
    outside.write_text("KEEP=1\n", encoding="utf-8")
    path = "../outside.py"
    result = PolicyValidator().validate(
        _proposal(_diff_for(path), [path]), root
    )
    assert result.ok is False
    assert any("escape" in e.lower() or ".." in e for e in result.errors)
    assert outside.read_text(encoding="utf-8") == "KEEP=1\n"
    assert apply_proposal(_proposal(_diff_for(path), [path]), root).ok is False
    assert outside.read_text(encoding="utf-8") == "KEEP=1\n"


def test_policy_rejects_unix_absolute_path(tmp_path: Path):
    root = tmp_path / "work"
    root.mkdir()
    (root / "a.py").write_text("x=1\n", encoding="utf-8")
    path = "/tmp/code_agent_p0_escape_outside.py"
    outside = Path(path)
    # Best-effort: only create when host can write this path.
    created = False
    try:
        outside.parent.mkdir(parents=True, exist_ok=True)
        outside.write_text("KEEP=1\n", encoding="utf-8")
        created = True
    except OSError:
        pass
    try:
        result = PolicyValidator().validate(
            _proposal(_diff_for(path), [path]), root
        )
        assert result.ok is False
        assert any("escape" in e.lower() or path in e for e in result.errors)
        if created:
            assert outside.read_text(encoding="utf-8") == "KEEP=1\n"
    finally:
        if created:
            outside.unlink(missing_ok=True)


def test_policy_rejects_windows_absolute_path(tmp_path: Path):
    root = tmp_path / "work"
    root.mkdir()
    (root / "a.py").write_text("x=1\n", encoding="utf-8")
    for path in ("C:/code_agent_p0_escape_outside.py", r"C:\code_agent_p0_escape_outside.py"):
        result = PolicyValidator().validate(
            _proposal(_diff_for(path), [path]), root
        )
        assert result.ok is False, (path, result.errors)
        assert any(
            "escape" in e.lower() or "C:" in e or path.replace("\\", "/") in e.replace("\\", "/")
            for e in result.errors
        ), result.errors


def test_policy_rejects_normalized_escape_path(tmp_path: Path):
    root = tmp_path / "work"
    root.mkdir()
    (root / "a.py").write_text("x=1\n", encoding="utf-8")
    (root / "sub").mkdir()
    outside = tmp_path / "outside.py"
    outside.write_text("KEEP=1\n", encoding="utf-8")
    path = "sub/../../outside.py"
    result = PolicyValidator().validate(
        _proposal(_diff_for(path), [path]), root
    )
    assert result.ok is False
    assert any("escape" in e.lower() or ".." in e for e in result.errors)
    assert outside.read_text(encoding="utf-8") == "KEEP=1\n"


def test_controller_path_escape_never_reaches_preflight_or_approval(tmp_path: Path):
    """Escaping propose_patch is policy feedback only — no preflight/approval/apply."""
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
    outside = tmp_path / "outside.py"
    outside.write_text("KEEP=1\n", encoding="utf-8")

    escape_patch = {
        "tool": "propose_patch",
        "args": {
            "diagnosis": "escape",
            "affected_files": ["../outside.py"],
            "unified_diff": _diff_for("../outside.py"),
            "expected_behavior": "x",
            "risk_notes": "x",
            "tests_to_run": ["tests/test_mod.py"],
        },
    }
    good_patch = {
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

    approve_calls = 0

    def approve(*_a, **_k):
        nonlocal approve_calls
        approve_calls += 1
        return True

    class Runner:
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
                    exit_code=0, stdout="1 passed", stderr="", duration_sec=0.01
                )
            if log_path:
                log_path.parent.mkdir(parents=True, exist_ok=True)
                log_path.write_text(result.stdout, encoding="utf-8")
            return result

    controller = TaskController(
        llm=LLMClient(dry_run_script=[escape_patch, good_patch]),
        runner=Runner(),  # type: ignore[arg-type]
        approve=approve,
        say=lambda _m: None,
        session_base=tmp_path / "sessions",
    )
    session = controller.run(repo, "make f return 2")
    assert session.status == SessionStatus.SUCCEEDED
    assert approve_calls == 1
    assert outside.read_text(encoding="utf-8") == "KEEP=1\n"

    events = [
        json.loads(line)
        for line in (session.artifacts_dir / "trace.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
        if line.strip()
    ]
    # Escape attempt: policy tool_result failure, never preflight.
    policy_fails = [
        e
        for e in events
        if e["event"] == "tool_result"
        and e["payload"].get("tool") == "propose_patch"
        and e["payload"].get("ok") is False
    ]
    assert policy_fails
    assert any("escape" in (e["payload"].get("error") or "").lower() for e in policy_fails)
    # Only the good patch should preflight/apply.
    assert sum(1 for e in events if e["event"] == "patch_preflight_started") == 1
    assert sum(1 for e in events if e["event"] == "patch_preflight_succeeded") == 1
    assert "patch_applied" in {e["event"] for e in events}
