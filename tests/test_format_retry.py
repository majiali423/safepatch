from __future__ import annotations

import json
from pathlib import Path

from code_agent.controller import TaskController
from code_agent.llm import (
    FormatErrorKind,
    LLMClient,
    ModelOutputError,
    parse_tool_call,
    raw_preview,
)
from code_agent.state import SessionStatus, TestResult


class FakeRunner:
    def run_pytest(self, workspace_root, *, log_path=None):
        # Baseline + post-patch: always pass so we exercise LLM path only when needed.
        result = TestResult(
            exit_code=0,
            stdout="1 passed",
            stderr="",
            duration_sec=0.01,
        )
        if log_path:
            log_path.parent.mkdir(parents=True, exist_ok=True)
            log_path.write_text("ok\n", encoding="utf-8")
        return result


class FailThenPassRunner:
    """Baseline fails once conceptually — always return fail for baseline simplicity.

    Controller always runs baseline then analyze. For format-retry-only tests we
    want baseline to succeed-or-fail without blocking; fail is fine.
    """

    def __init__(self):
        self.calls = 0

    def run_pytest(self, workspace_root, *, log_path=None):
        self.calls += 1
        # First call = baseline fail; later = pass if patch applied.
        if self.calls == 1:
            result = TestResult(
                exit_code=1,
                stdout="FAILED tests/test_x.py::test_x",
                stderr="",
                duration_sec=0.01,
                failed_tests=["tests/test_x.py::test_x"],
            )
        else:
            result = TestResult(
                exit_code=0, stdout="1 passed", stderr="", duration_sec=0.01
            )
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
        "from mod import f\n"
        "def test_f():\n"
        "    assert f() == 2\n",
        encoding="utf-8",
    )
    (repo / "pytest.ini").write_text("[pytest]\npythonpath = .\n", encoding="utf-8")
    return repo


def _valid_patch_tool() -> dict:
    return {
        "tool": "propose_patch",
        "args": {
            "diagnosis": "return wrong value",
            "affected_files": ["mod.py"],
            "unified_diff": (
                "--- a/mod.py\n"
                "+++ b/mod.py\n"
                "@@ -1,2 +1,2 @@\n"
                " def f():\n"
                "-    return 1\n"
                "+    return 2\n"
            ),
            "expected_behavior": "f returns 2",
            "risk_notes": "low",
            "tests_to_run": ["tests/test_mod.py"],
        },
    }


def test_parse_tool_call_kinds():
    try:
        parse_tool_call("not json at all")
        assert False
    except ModelOutputError as exc:
        assert exc.error_kind == FormatErrorKind.JSON_DECODE_ERROR
        assert "not json" in exc.raw_text

    try:
        parse_tool_call('{"args":{}}')
        assert False
    except ModelOutputError as exc:
        assert exc.error_kind == FormatErrorKind.INVALID_TOOL_SCHEMA

    try:
        parse_tool_call('{"tool":"read_file","args":[]}')
        assert False
    except ModelOutputError as exc:
        assert exc.error_kind == FormatErrorKind.INVALID_TOOL_SCHEMA


def test_raw_preview_redacts_and_truncates():
    text = "header sk-test-secret-key-12345678 footer " + ("x" * 3000)
    preview = raw_preview(text, max_len=100)
    assert "sk-test-secret-key-12345678" not in preview
    assert "truncated" in preview or len(preview) <= 120


def test_format_retry_then_success(tmp_path: Path):
    repo = _mini_repo(tmp_path)
    script = [
        "THIS IS NOT JSON",
        '{"tool": "read_file", "args": {"path": "mod.py", "start_line": 1, "end_line": 20}}',
        _valid_patch_tool(),
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
    assert session.attempts_used == 1
    assert session.total_format_retries_used == 1
    assert session.consecutive_format_retries == 0

    events = [
        json.loads(line)["event"]
        for line in (session.artifacts_dir / "trace.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
        if line.strip()
    ]
    assert "parse_failed" in events
    assert "format_retry" in events
    assert "retry_exhausted" not in events


def test_format_retry_exhausted_model_output_invalid(tmp_path: Path):
    repo = _mini_repo(tmp_path)
    # initial + 2 retries = 3 bad outputs → MODEL_OUTPUT_INVALID
    script = ["bad1", "bad2", "bad3", _valid_patch_tool()]
    controller = TaskController(
        llm=LLMClient(dry_run_script=script),
        runner=FailThenPassRunner(),  # type: ignore[arg-type]
        approve=lambda *_: True,
        say=lambda _m: None,
        session_base=tmp_path / "sessions",
    )
    session = controller.run(repo, "make f return 2")
    assert session.status == SessionStatus.MODEL_OUTPUT_INVALID
    assert session.stop_reason == "model_output_invalid"
    assert session.attempts_used == 0
    assert session.total_format_retries_used == 2
    assert session.consecutive_format_retries == 2

    summary = json.loads(
        (session.artifacts_dir / "summary.json").read_text(encoding="utf-8")
    )
    assert summary["status"] == "MODEL_OUTPUT_INVALID"
    assert summary["attempts_used"] == 0

    records = [
        json.loads(line)
        for line in (session.artifacts_dir / "trace.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
        if line.strip()
    ]
    kinds = [r["event"] for r in records]
    assert kinds.count("parse_failed") == 3
    assert kinds.count("format_retry") == 2
    assert "retry_exhausted" in kinds
    # raw_preview present on parse_failed
    pf = next(r for r in records if r["event"] == "parse_failed")
    assert "raw_preview" in pf["payload"]
    assert pf["payload"]["raw_preview"]


def test_invalid_proposal_schema_uses_format_retry(tmp_path: Path):
    repo = _mini_repo(tmp_path)
    script = [
        {
            "tool": "propose_patch",
            "args": {"diagnosis": "incomplete"},  # missing required fields
        },
        _valid_patch_tool(),
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
    events = [
        json.loads(line)
        for line in (session.artifacts_dir / "trace.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
        if line.strip()
    ]
    pf = next(e for e in events if e["event"] == "parse_failed")
    assert pf["payload"]["error_kind"] == "invalid_proposal_schema"


def test_policy_failure_does_not_count_format_retry(tmp_path: Path):
    repo = _mini_repo(tmp_path)
    # Forbidden path → policy feedback, not format retry; then a valid patch.
    bad_policy = {
        "tool": "propose_patch",
        "args": {
            "diagnosis": "touch env",
            "affected_files": [".env"],
            "unified_diff": (
                "--- a/.env\n+++ b/.env\n@@ -0,0 +1 @@\n+SECRET=1\n"
            ),
            "expected_behavior": "x",
            "risk_notes": "x",
            "tests_to_run": ["tests/test_mod.py"],
        },
    }
    script = [bad_policy, _valid_patch_tool()]
    controller = TaskController(
        llm=LLMClient(dry_run_script=script),
        runner=FailThenPassRunner(),  # type: ignore[arg-type]
        approve=lambda *_: True,
        say=lambda _m: None,
        session_base=tmp_path / "sessions",
    )
    session = controller.run(repo, "make f return 2")
    assert session.status == SessionStatus.SUCCEEDED
    assert session.total_format_retries_used == 0
    events = {
        json.loads(line)["event"]
        for line in (session.artifacts_dir / "trace.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
        if line.strip()
    }
    assert "parse_failed" not in events
    assert "format_retry" not in events


def test_cli_exit_code_5_for_model_output_invalid(tmp_path: Path, monkeypatch):
    from code_agent import cli as cli_mod

    repo = _mini_repo(tmp_path)
    script = ["bad1", "bad2", "bad3"]

    class Runner:
        def preflight(self):
            return True, "ok"

        def run_pytest(self, workspace_root, *, log_path=None):
            r = TestResult(
                exit_code=1,
                stdout="F",
                stderr="",
                duration_sec=0.01,
                failed_tests=["t"],
            )
            if log_path:
                log_path.write_text("F\n", encoding="utf-8")
            return r

    monkeypatch.setattr(cli_mod, "DockerPytestRunner", lambda: Runner())
    monkeypatch.setattr(
        cli_mod,
        "LLMClient",
        lambda **kwargs: LLMClient(dry_run_script=script),
    )
    code = cli_mod.main(
        [
            str(repo),
            "fix it",
            "--yes",
            "--session-base",
            str(tmp_path / "sess"),
        ]
    )
    assert code == 5
