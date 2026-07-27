"""P0: CLI exit-code contract for v0.3 terminals (5 / 6 / 7)."""

from __future__ import annotations

import json
from io import StringIO
from pathlib import Path

from rich.console import Console

from code_agent.llm import LLMClient
from code_agent.state import TestResult


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


def _mismatch_patch() -> dict:
    return {
        "tool": "propose_patch",
        "args": {
            "diagnosis": "bad context",
            "affected_files": ["mod.py"],
            "unified_diff": (
                "--- a/mod.py\n"
                "+++ b/mod.py\n"
                "@@ -1,2 +1,2 @@\n"
                " def f():\n"
                "-    return 999\n"
                "+    return 2\n"
            ),
            "expected_behavior": "return 2",
            "risk_notes": "low",
            "tests_to_run": ["tests/test_mod.py"],
        },
    }


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


class _BaselineFailRunner:
    def preflight(self):
        return True, "ok"

    def run_pytest(self, workspace_root, *, log_path=None):
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


def _patch_cli(monkeypatch, *, script, runner_factory=None):
    from code_agent import cli as cli_mod

    buf = StringIO()
    monkeypatch.setattr(cli_mod, "console", Console(file=buf, force_terminal=True))
    monkeypatch.setattr(
        cli_mod,
        "DockerPytestRunner",
        runner_factory or (lambda: _BaselineFailRunner()),
    )
    monkeypatch.setattr(
        cli_mod,
        "LLMClient",
        lambda **kwargs: LLMClient(dry_run_script=script),
    )
    return cli_mod, buf


def _find_summary(session_base: Path) -> dict:
    summaries = list(session_base.rglob("summary.json"))
    assert summaries, f"no summary.json under {session_base}"
    # Prefer the newest session artifact.
    path = max(summaries, key=lambda p: p.stat().st_mtime)
    return json.loads(path.read_text(encoding="utf-8")), path.parent


def test_cli_exit_code_6_patch_not_applicable(tmp_path: Path, monkeypatch):
    """PATCH_NOT_APPLICABLE must map to exit 6 (not generic 1)."""
    repo = _mini_repo(tmp_path)
    session_base = tmp_path / "sess"
    cli_mod, buf = _patch_cli(
        monkeypatch, script=[_mismatch_patch(), _mismatch_patch(), _mismatch_patch()]
    )
    code = cli_mod.main(
        [
            str(repo),
            "make f return 2",
            "--yes",
            "--session-base",
            str(session_base),
        ]
    )
    assert code == 6
    assert code != 1
    summary, artifacts = _find_summary(session_base)
    assert summary["status"] == "PATCH_NOT_APPLICABLE"
    assert summary["stop_reason"] == "patch_not_applicable"
    assert (artifacts / "summary.json").is_file()
    assert (artifacts / "trace.jsonl").is_file()
    out = buf.getvalue()
    assert "patch_not_applicable" in out
    assert "PATCH_NOT_APPLICABLE" in out


def test_cli_exit_code_7_patch_base_changed(tmp_path: Path, monkeypatch):
    """PATCH_BASE_CHANGED must map to exit 7 (not generic 1)."""
    repo = _mini_repo(tmp_path)
    session_base = tmp_path / "sess"
    cli_mod, buf = _patch_cli(monkeypatch, script=[_good_patch()])

    def mutate_then_approve(binding, attempt: int) -> bool:
        for mod in session_base.rglob("mod.py"):
            if "working_copy" in mod.parts:
                mod.write_text("def f():\n    return 1\n# mutated\n", encoding="utf-8")
                break
        else:
            raise AssertionError("working_copy mod.py not found during approval")
        return True

    monkeypatch.setattr(cli_mod, "_prompt_approval", mutate_then_approve)
    code = cli_mod.main(
        [
            str(repo),
            "make f return 2",
            "--session-base",
            str(session_base),
        ]
    )
    assert code == 7
    assert code != 1
    summary, artifacts = _find_summary(session_base)
    assert summary["status"] == "PATCH_BASE_CHANGED"
    assert summary["stop_reason"] == "patch_base_changed"
    assert (artifacts / "summary.json").is_file()
    assert (artifacts / "trace.jsonl").is_file()
    out = buf.getvalue()
    assert "patch_base_changed" in out
    assert "PATCH_BASE_CHANGED" in out


def test_cli_exit_code_5_model_output_invalid_no_regression(tmp_path: Path, monkeypatch):
    """Existing exit 5 contract must not regress alongside 6/7."""
    repo = _mini_repo(tmp_path)
    session_base = tmp_path / "sess"
    cli_mod, buf = _patch_cli(monkeypatch, script=["bad1", "bad2", "bad3"])
    code = cli_mod.main(
        [
            str(repo),
            "fix it",
            "--yes",
            "--session-base",
            str(session_base),
        ]
    )
    assert code == 5
    assert code != 1
    summary, artifacts = _find_summary(session_base)
    assert summary["status"] == "MODEL_OUTPUT_INVALID"
    assert summary["stop_reason"] == "model_output_invalid"
    assert (artifacts / "summary.json").is_file()
    out = buf.getvalue()
    assert "model_output_invalid" in out
    assert "MODEL_OUTPUT_INVALID" in out
