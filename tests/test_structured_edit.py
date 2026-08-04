from __future__ import annotations

import json
from pathlib import Path

import pytest

from code_agent.controller import TaskController
from code_agent.llm import LLMClient
from code_agent.patching.applier import apply_proposal
from code_agent.patching.hashes import file_revision
from code_agent.patching.preflight import PatchPreflight, PreflightSuccess
from code_agent.patching.structured_edit import StructuredEditError, build_structured_proposal
from code_agent.state import SessionStatus, TestResult
from code_agent.tools.read_file import read_file


class FailThenPassRunner:
    def __init__(self) -> None:
        self.calls = 0

    def run_pytest(self, workspace_root, *, log_path=None):
        self.calls += 1
        result = TestResult(
            exit_code=1 if self.calls == 1 else 0,
            stdout="failed" if self.calls == 1 else "passed",
            stderr="",
            duration_sec=0.01,
            failed_tests=["tests/test_behavior.py"] if self.calls == 1 else [],
        )
        if log_path:
            log_path.parent.mkdir(parents=True, exist_ok=True)
            log_path.write_text(result.stdout, encoding="utf-8")
        return result


def _repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "a.py").write_text("VALUE = 1\n", encoding="utf-8")
    (repo / "b.py").write_text("def value():\n    return 1\n", encoding="utf-8")
    return repo


def _args(repo: Path) -> dict:
    return {
        "diagnosis": "both implementations are stale",
        "edits": [
            {
                "path": "a.py",
                "old_text": "VALUE = 1",
                "new_text": "VALUE = 2",
                "base_revision": file_revision(repo / "a.py"),
            },
            {
                "path": "b.py",
                "old_text": "def value():\n    return 1",
                "new_text": "def value():\n    return 2",
                "base_revision": file_revision(repo / "b.py"),
            },
        ],
        "expected_behavior": "both return two",
        "risk_notes": "low",
        "tests_to_run": ["tests/test_behavior.py"],
    }


def test_structured_edit_builds_standard_multifile_proposal_and_applies(tmp_path: Path):
    repo = _repo(tmp_path)

    proposal = build_structured_proposal(_args(repo), repo)

    assert proposal.affected_files == ["a.py", "b.py"]
    assert proposal.base_revisions == {
        "a.py": file_revision(repo / "a.py"),
        "b.py": file_revision(repo / "b.py"),
    }
    assert "--- a/a.py" in proposal.unified_diff
    assert "--- a/b.py" in proposal.unified_diff
    checked = PatchPreflight.run(proposal, repo)
    assert isinstance(checked, PreflightSuccess)
    applied = apply_proposal(proposal, repo)
    assert applied.ok is True
    assert (repo / "a.py").read_text(encoding="utf-8") == "VALUE = 2\n"
    assert (repo / "b.py").read_text(encoding="utf-8") == (
        "def value():\n    return 2\n"
    )


def test_structured_edit_rejects_zero_or_multiple_exact_matches(tmp_path: Path):
    repo = _repo(tmp_path)
    missing = _args(repo)
    missing["edits"] = [
        {
            "path": "a.py",
            "old_text": "VALUE = 9",
            "new_text": "VALUE = 2",
            "base_revision": file_revision(repo / "a.py"),
        }
    ]
    with pytest.raises(StructuredEditError, match="not found exactly"):
        build_structured_proposal(missing, repo)

    (repo / "a.py").write_text("VALUE = 1\nVALUE = 1\n", encoding="utf-8")
    ambiguous = _args(repo)
    ambiguous["edits"] = [
        {
            "path": "a.py",
            "old_text": "VALUE = 1",
            "new_text": "VALUE = 2",
            "base_revision": file_revision(repo / "a.py"),
        }
    ]
    with pytest.raises(StructuredEditError, match="2 exact matches"):
        build_structured_proposal(ambiguous, repo)
    assert (repo / "a.py").read_text(encoding="utf-8") == "VALUE = 1\nVALUE = 1\n"


def test_structured_edit_treats_overlapping_matches_as_ambiguous(tmp_path: Path):
    repo = _repo(tmp_path)
    (repo / "a.py").write_text('TEXT = "aaa"\n', encoding="utf-8")
    args = _args(repo)
    args["edits"] = [
        {
            "path": "a.py",
            "old_text": "aa",
            "new_text": "b",
            "base_revision": file_revision(repo / "a.py"),
        }
    ]

    with pytest.raises(StructuredEditError, match="2 exact matches"):
        build_structured_proposal(args, repo)

    assert (repo / "a.py").read_text(encoding="utf-8") == 'TEXT = "aaa"\n'


def test_structured_edit_rejects_workspace_escape(tmp_path: Path):
    repo = _repo(tmp_path)
    outside = tmp_path / "outside.py"
    outside.write_text("VALUE = 1\n", encoding="utf-8")
    args = _args(repo)
    args["edits"] = [
        {
            "path": "../outside.py",
            "old_text": "VALUE = 1",
            "new_text": "VALUE = 2",
            "base_revision": file_revision(outside),
        }
    ]

    with pytest.raises(StructuredEditError, match="unsafe path"):
        build_structured_proposal(args, repo)

    assert outside.read_text(encoding="utf-8") == "VALUE = 1\n"


def test_controller_runs_structured_edit_through_normal_gates(tmp_path: Path):
    repo = _repo(tmp_path)
    controller = TaskController(
        llm=LLMClient(
            dry_run_script=[{"tool": "propose_edit", "args": _args(repo)}]
        ),
        runner=FailThenPassRunner(),  # type: ignore[arg-type]
        approve=lambda *_: True,
        say=lambda _message: None,
        session_base=tmp_path / "sessions",
    )

    session = controller.run(repo, "update both implementations")

    assert session.status == SessionStatus.SUCCEEDED
    assert session.changed_files == ["a.py", "b.py"]
    events = [
        json.loads(line)["event"]
        for line in (session.artifacts_dir / "trace.jsonl").read_text(
            encoding="utf-8"
        ).splitlines()
    ]
    assert "patch_preflight_succeeded" in events
    assert "patch_applied" in events


def test_read_file_returns_full_content_revision(tmp_path: Path):
    repo = _repo(tmp_path)

    result = read_file(repo, "a.py", 1, 1)

    assert f"revision={file_revision(repo / 'a.py')}" in result.splitlines()[0]


def test_structured_edit_rejects_stale_revision_even_when_old_text_still_exists(
    tmp_path: Path,
):
    repo = _repo(tmp_path)
    args = _args(repo)
    (repo / "a.py").write_text("VALUE = 1\n# changed after read\n", encoding="utf-8")

    with pytest.raises(StructuredEditError, match="STALE_FILE_REVISION"):
        build_structured_proposal(args, repo)


def test_preflight_and_apply_reject_proposal_if_file_changes_after_build(tmp_path: Path):
    repo = _repo(tmp_path)
    proposal = build_structured_proposal(_args(repo), repo)
    original_b = (repo / "b.py").read_text(encoding="utf-8")
    (repo / "b.py").write_text(original_b + "# concurrent change\n", encoding="utf-8")

    checked = PatchPreflight.run(proposal, repo)
    assert checked.error_kind == "stale_file_revision"
    assert checked.target_file == "b.py"
    assert "STALE_FILE_REVISION" in checked.feedback_message()

    applied = apply_proposal(proposal, repo)
    assert applied.ok is False
    assert applied.error_kind == "stale_file_revision"
    assert (repo / "a.py").read_text(encoding="utf-8") == "VALUE = 1\n"
    assert (repo / "b.py").read_text(encoding="utf-8") == original_b + (
        "# concurrent change\n"
    )
