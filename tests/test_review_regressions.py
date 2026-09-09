"""Regression tests for review findings R1–R9 (assert corrected behavior)."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from code_agent.controller import TaskController
from code_agent.llm import LLMClient
from code_agent.patching import applier
from code_agent.patching.validator import validate_proposal
from code_agent.repository.workspace import WorkspaceError, import_repository
from code_agent.runtime.docker_pytest import _run_docker_cmd, _write_log
from code_agent.runtime.exit_classification import classify_runner_exit
from code_agent.state import AnalysisPhase, PatchProposal, SessionStatus, TaskSession, TestResult
from code_agent.tools.read_file import read_file, read_file_result
from code_agent.tools.request_evidence import execute_request_evidence
from code_agent.tracing.sanitize import REDACT_PLACEHOLDER, sanitize_text


class _Runner:
    def __init__(self, codes=(1, 0)):
        self.codes = codes
        self.calls = 0

    def run_pytest(self, workspace_root, *, log_path=None):
        code = self.codes[min(self.calls, len(self.codes) - 1)]
        self.calls += 1
        result = TestResult(
            exit_code=code,
            stdout="1 passed" if code == 0 else "FAILED tests/test_mod.py::test_f",
            stderr="",
            duration_sec=0.01,
            failed_tests=[] if code == 0 else ["tests/test_mod.py::test_f"],
        )
        if log_path:
            log_path.parent.mkdir(parents=True, exist_ok=True)
            log_path.write_text(result.stdout, encoding="utf-8")
        return result


class _CaptureLLM(LLMClient):
    def __init__(self, script):
        super().__init__(dry_run_script=script)
        self.requests = []

    def complete(self, messages):
        self.requests.append(list(messages))
        return super().complete(messages)


GOOD_PATCH = {
    "tool": "propose_patch",
    "args": {
        "diagnosis": "wrong value",
        "affected_files": ["mod.py"],
        "unified_diff": (
            "--- a/mod.py\n+++ b/mod.py\n@@ -1,2 +1,2 @@\n def f():\n-    return 1\n+    return 2\n"
        ),
        "expected_behavior": "return 2",
        "risk_notes": "low",
        "tests_to_run": ["tests/test_mod.py"],
    },
}


def _repo(base: Path) -> Path:
    repo = base / "repo"
    (repo / "tests").mkdir(parents=True)
    (repo / "mod.py").write_text("def f():\n    return 1\n", encoding="utf-8")
    (repo / "helpers.py").write_text(
        "CONTEXT_MARKER = 'SYNTHETIC_EVIDENCE_7421'\n", encoding="utf-8"
    )
    (repo / "tests" / "test_mod.py").write_text(
        "from mod import f\ndef test_f():\n    assert f() == 2\n",
        encoding="utf-8",
    )
    (repo / "pytest.ini").write_text("[pytest]\npythonpath = .\n", encoding="utf-8")
    return repo


def _proposal(diff: str, files: list[str]) -> PatchProposal:
    return PatchProposal(
        diagnosis="d",
        affected_files=files,
        unified_diff=diff,
        expected_behavior="e",
        risk_notes="r",
        tests_to_run=["tests/test_mod.py"],
    )


def test_r1_suffix_test_module_is_protected(tmp_path: Path):
    root = tmp_path / "work"
    root.mkdir()
    (root / "math_test.py").write_text("def test_math():\n    assert 1 == 2\n", encoding="utf-8")
    pkg = root / "pkg"
    pkg.mkdir()
    (pkg / "util_test.py").write_text("def test_util():\n    assert 1 == 2\n", encoding="utf-8")
    diff = (
        "--- a/math_test.py\n+++ b/math_test.py\n@@ -1,2 +1,2 @@\n"
        " def test_math():\n-    assert 1 == 2\n+    assert 1 == 1\n"
    )
    result = validate_proposal(_proposal(diff, ["math_test.py"]), root)
    assert result.ok is False
    assert result.modified_test_files == [] or any("existing test" in e.lower() for e in result.errors)

    custom = validate_proposal(
        _proposal(
            "--- a/pkg/util_test.py\n+++ b/pkg/util_test.py\n@@ -1,2 +1,2 @@\n"
            " def test_util():\n-    assert 1 == 2\n+    assert 1 == 1\n",
            ["pkg/util_test.py"],
        ),
        root,
        collected_test_files={"pkg/util_test.py"},
    )
    assert custom.ok is False


def test_r2_partial_new_file_write_is_rolled_back(tmp_path: Path):
    repo = _repo(tmp_path)
    proposal = PatchProposal.from_dict(
        {
            "diagnosis": "new test",
            "affected_files": ["tests/test_new.py"],
            "unified_diff": (
                "--- /dev/null\n+++ b/tests/test_new.py\n@@ -0,0 +1,2 @@\n"
                "+def test_new():\n+    assert 1 == 1\n"
            ),
            "expected_behavior": "test",
            "risk_notes": "low",
            "tests_to_run": ["tests/test_new.py"],
        }
    )
    original_write = applier._write_text_raw

    def fail_mid_write(path, content):
        if path.name == "test_new.py":
            original_write(path, content[:8])
            raise OSError("synthetic partial write failure")
        return original_write(path, content)

    before = {p: p.read_text(encoding="utf-8") for p in repo.rglob("*.py")}
    with patch.object(applier, "_write_text_raw", side_effect=fail_mid_write):
        result = applier.apply_proposal(proposal, repo)
    assert result.ok is False
    assert result.error_kind == "io_error"
    assert result.rollback_succeeded is True
    assert not (repo / "tests" / "test_new.py").exists()
    after = {p: p.read_text(encoding="utf-8") for p in repo.rglob("*.py")}
    assert after == before


def test_r3_invalid_proposals_exhaust_format_retries(tmp_path: Path):
    repo = _repo(tmp_path)
    bad = {"tool": "propose_patch", "args": {"diagnosis": "incomplete"}}
    llm = _CaptureLLM([bad, bad, bad, GOOD_PATCH])
    session = TaskController(
        llm=llm,
        runner=_Runner(),
        approve=lambda *_: True,
        session_base=tmp_path / "sessions",
    ).run(repo, "make f return 2")
    assert session.status == SessionStatus.MODEL_OUTPUT_INVALID
    assert len(llm.requests) == 3
    assert session.attempts_used == 0


def test_r4_unmodified_file_evidence_is_retained_across_rounds(tmp_path: Path):
    repo = _repo(tmp_path)
    llm = _CaptureLLM(
        [
            {"tool": "read_file", "args": {"path": "helpers.py", "start_line": 1, "end_line": 1}},
            GOOD_PATCH,
            {"tool": "finish", "args": {"reason": "probe complete"}},
        ]
    )
    session = TaskController(
        llm=llm,
        runner=_Runner((1, 1)),
        approve=lambda *_: True,
        session_base=tmp_path / "sessions",
    ).run(repo, "make f return 2")
    dumped = [json.dumps(item) for item in llm.requests]
    assert "SYNTHETIC_EVIDENCE_7421" in dumped[1]
    assert "SYNTHETIC_EVIDENCE_7421" in dumped[2]
    ranges = session.successful_read_ranges["helpers.py"]
    assert ranges == [(1, 1)]


def test_r5_default_read_records_actual_range_only(tmp_path: Path):
    repo = _repo(tmp_path)
    (repo / "long.py").write_text(
        "".join(f"x_{i} = {i}\n" for i in range(1, 201)), encoding="utf-8"
    )
    session = TaskSession("probe", repo, repo, repo, repo, "probe")
    result = read_file_result(repo, "long.py")
    assert result.start_line == 1
    assert result.end_line == 120
    header = read_file(repo, "long.py").splitlines()[0]
    assert "lines 1-120 of 200" in header
    from code_agent.tools.request_evidence import record_read_result

    record_read_result(session, result, source="read_file")
    session.analysis_phase = AnalysisPhase.SYNTHESIZE
    text = execute_request_evidence(
        session,
        {
            "path": "long.py",
            "start_line": 121,
            "end_line": 130,
            "unanswered_requirement": "unseen next lines",
            "reason": "need remaining code",
        },
    )
    assert "lines 121-130" in text


def test_r6_runner_exit_codes_are_classified():
    for code, kind in (
        (2, "runner"),
        (3, "runner"),
        (4, "runner"),
        (5, "runner"),
        (125, "environment"),
        (126, "environment"),
        (127, "environment"),
        (137, "environment"),
    ):
        classified = classify_runner_exit(code, "", "synthetic failure")
        assert classified.environment_error
        assert classified.error_kind == kind
        assert classified.valid_failure_oracle is False
        assert "OOM" not in (classified.environment_error or "") or code == 137


def test_r6_environment_baseline_does_not_call_model(tmp_path: Path):
    repo = _repo(tmp_path)
    llm = _CaptureLLM([GOOD_PATCH])

    class EnvRunner:
        def run_pytest(self, workspace_root, *, log_path=None):
            return TestResult(
                exit_code=127,
                stdout="",
                stderr="command not found",
                duration_sec=0.01,
                environment_error="TEST_ENVIRONMENT_ERROR: container command not found (exit 127)",
                error_kind="environment",
            )

    session = TaskController(
        llm=llm,
        runner=EnvRunner(),
        approve=lambda *_: True,
        session_base=tmp_path / "sessions",
    ).run(repo, "make f return 2")
    assert session.status == SessionStatus.TEST_ENVIRONMENT_ERROR
    assert llm.requests == []
    assert session.to_summary()["request_verified"] is False


def test_r7_artifacts_redact_synthetic_secrets(tmp_path: Path):
    secret = "sk-SYNTHETICFAKESECRETVALUE99"
    result = TestResult(1, secret, "", 0.01, failed_tests=["t"])
    log_path = tmp_path / "pytest.log"
    _write_log(log_path, result)
    assert secret not in log_path.read_text(encoding="utf-8")
    assert REDACT_PLACEHOLDER in log_path.read_text(encoding="utf-8")
    assert sanitize_text(f"error {secret}") == f"error {REDACT_PLACEHOLDER}"


def test_r7_does_not_rewrite_source_or_pending_diff(tmp_path: Path):
    repo = _repo(tmp_path)
    secret_line = 'TOKEN = "sk-SYNTHETICFAKESECRETVALUE99"\n'
    (repo / "mod.py").write_text("def f():\n    return 1\n" + secret_line, encoding="utf-8")
    original = (repo / "mod.py").read_bytes()
    proposal = _proposal(
        "--- a/mod.py\n+++ b/mod.py\n@@ -1,2 +1,2 @@\n def f():\n-    return 1\n+    return 2\n",
        ["mod.py"],
    )
    applied = applier.apply_proposal(proposal, repo)
    assert applied.ok is True
    text = (repo / "mod.py").read_text(encoding="utf-8")
    assert "sk-SYNTHETICFAKESECRETVALUE99" in text
    assert original != (repo / "mod.py").read_bytes()


def test_r8_secret_files_are_not_imported(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "a.py").write_text("x = 1\n", encoding="utf-8")
    (repo / ".env").write_text("OPENAI_API_KEY=sk-fake-not-real-0001\n", encoding="utf-8")
    (repo / ".env.example").write_text("OPENAI_API_KEY=\n", encoding="utf-8")
    imported = import_repository(repo, session_base=tmp_path / "sessions")
    assert (imported.workspace_root / "a.py").exists()
    assert not (imported.workspace_root / ".env").exists()
    assert (imported.workspace_root / ".env.example").exists()


def test_r9_in_repo_session_does_not_copy_itself(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "a.py").write_text("x = 1\n", encoding="utf-8")
    session_base = repo / ".safepatch_sessions"
    imported = import_repository(repo, session_base=session_base)
    assert (imported.workspace_root / "a.py").exists()
    assert not (imported.workspace_root / ".safepatch_sessions").exists()
    nested = list(imported.workspace_root.rglob("working_copy"))
    assert nested == []


def test_r9_rejects_session_inside_working_copy(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "a.py").write_text("x = 1\n", encoding="utf-8")
    nested = repo / "working_copy" / "sessions"
    nested.mkdir(parents=True)
    with pytest.raises(WorkspaceError, match="working_copy"):
        import_repository(repo, session_base=nested)


def test_docker_output_limit_terminates(monkeypatch):
    calls = {"killed": False}

    class FakeProc:
        stdout = None
        stderr = None
        returncode = 0

        def wait(self, timeout=None):
            return 0

        def kill(self):
            calls["killed"] = True

    def fake_popen(*_args, **_kwargs):
        proc = FakeProc()

        class Stream:
            def __init__(self):
                self._sent = False

            def read(self, _n):
                if self._sent:
                    return b""
                self._sent = True
                return b"x" * 200

            def close(self):
                return None

        proc.stdout = Stream()
        proc.stderr = Stream()
        return proc

    monkeypatch.setattr("code_agent.runtime.docker_pytest.subprocess.Popen", fake_popen)
    with pytest.raises(RuntimeError, match="exceeded"):
        _run_docker_cmd(["docker", "run"], timeout_seconds=5, max_bytes=50)
    assert calls["killed"] is True
