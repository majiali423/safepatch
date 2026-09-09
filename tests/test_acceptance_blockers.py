"""Acceptance blockers A1–A9: fail closed on the 2026-09-07 report."""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from code_agent.controller import TaskController
from code_agent.evidence import EvidenceItem, EvidenceStore
from code_agent.llm import LLMClient
from code_agent.patching import applier
from code_agent.patching.test_collection import discover_protected_test_files
from code_agent.patching.validator import validate_proposal
from code_agent.repository.context_selector import select_context_files
from code_agent.repository.repo_map import build_repo_map
from code_agent.runtime.docker_pytest import _run_docker_cmd
from code_agent.state import AnalysisPhase, PatchProposal, SessionStatus, TaskSession, TestResult
from code_agent.tools.read_file import read_file_result
from code_agent.tools.request_evidence import (
    execute_request_evidence,
    record_read_result,
)
from code_agent.tracing.sanitize import REDACT_PLACEHOLDER, sanitize

FAKE_SECRET = "sk-ACCEPTANCE_FAKE_SECRET_9x7"
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


def _mini_repo(tmp_path: Path, *, pytest_ini: str | None = None) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "mod.py").write_text("def f():\n    return 1\n", encoding="utf-8")
    (repo / "tests").mkdir()
    (repo / "tests" / "test_mod.py").write_text(
        "from mod import f\ndef test_f():\n    assert f() == 2\n",
        encoding="utf-8",
    )
    (repo / "pytest.ini").write_text(
        pytest_ini or "[pytest]\npythonpath = .\n",
        encoding="utf-8",
    )
    return repo


def test_a1_traceback_cannot_read_outside_workspace(tmp_path: Path):
    outside = tmp_path / "outside.py"
    outside.write_text("import os\nHOST_SECRET = 'do-not-read'\n", encoding="utf-8")
    workspace = tmp_path / "work"
    workspace.mkdir()
    (workspace / "mod.py").write_text("VALUE = 1\n", encoding="utf-8")
    (workspace / "pkg").mkdir()
    (workspace / "pkg" / "mod.py").write_text("VALUE = 2\n", encoding="utf-8")
    _text, data = build_repo_map(workspace)
    opened: list[str] = []
    original_read = Path.read_text

    def guarded(self, *args, **kwargs):
        opened.append(str(self.resolve()))
        return original_read(self, *args, **kwargs)

    with patch.object(Path, "read_text", guarded):
        ranked = select_context_files(
            workspace_root=workspace,
            repo_map_data=data,
            traceback_summary=(
                'File "../outside.py", line 1\nFile "/etc/passwd.py", line 1\n'
                'File "mod.py"'
            ),
            bug_description="fix VALUE",
        )
    outside_posix = outside.resolve().as_posix()
    assert all(Path(p).resolve().as_posix() != outside_posix for p in opened)
    assert "outside.py" not in ranked


def test_a2_existing_safepatch_tmp_is_not_replaced(tmp_path: Path):
    repo = _mini_repo(tmp_path)
    leftover = repo / ".mod.py.safepatch.tmp"
    leftover.write_text("unrelated leftover\n", encoding="utf-8")
    proposal = PatchProposal.from_dict(GOOD_PATCH["args"])
    result = applier.apply_proposal(proposal, repo)
    assert result.ok is True
    assert leftover.exists()
    assert leftover.read_text(encoding="utf-8") == "unrelated leftover\n"
    assert "return 2" in (repo / "mod.py").read_text(encoding="utf-8")


def test_a2_parent_mkdir_failure_rolls_back(tmp_path: Path):
    root = tmp_path / "work"
    root.mkdir()
    target = root / "a" / "b" / "c.py"
    created: list[Path] = []
    original = Path.mkdir

    def boom(self, *args, **kwargs):
        if self.name == "b":
            raise OSError("synthetic mkdir failure")
        return original(self, *args, **kwargs)

    with patch.object(Path, "mkdir", boom):
        with pytest.raises(OSError, match="synthetic mkdir failure"):
            applier._ensure_parents(target, root, created)
    assert created
    assert all(path.exists() for path in created)
    applier._rollback({}, [], created)
    assert not (root / "a").exists()


def test_a3_patch_proposed_trace_redacts_secret_like_diff(tmp_path: Path):
    repo = _mini_repo(tmp_path)
    secret_patch = {
        "tool": "propose_patch",
        "args": {
            **GOOD_PATCH["args"],
            "unified_diff": (
                "--- a/mod.py\n+++ b/mod.py\n@@ -1,2 +1,2 @@\n def f():\n"
                f"-    return 1\n+    return '{FAKE_SECRET}'\n"
            ),
        },
    }
    llm = LLMClient(dry_run_script=[secret_patch], api_key="sk-controllerkey123456")
    session = TaskController(
        llm=llm,
        runner=_Runner((1, 1)),
        approve=lambda *_: False,
        session_base=tmp_path / "sessions",
    ).run(repo, "make f return 2")
    trace = (session.artifacts_dir / "trace.jsonl").read_text(encoding="utf-8")
    assert FAKE_SECRET not in trace
    assert "sk-controllerkey123456" not in trace
    assert REDACT_PLACEHOLDER in trace
    payload = sanitize({"unified_diff": f"token {FAKE_SECRET}"})
    assert FAKE_SECRET not in json.dumps(payload)


def test_a4_docker_gate_counts_call_skip_and_requires_pass(monkeypatch):
    import tests.conftest as gate

    monkeypatch.setenv("SAFEPATCH_REQUIRE_DOCKER_E2E", "1")
    saved_selected = gate._docker_selected
    saved_outcomes = dict(gate._docker_outcomes)
    try:
        _a4_docker_gate_body(gate)
    finally:
        gate._docker_selected = saved_selected
        gate._docker_outcomes = saved_outcomes


def _a4_docker_gate_body(gate) -> None:
    gate.pytest_sessionstart(None)
    item = SimpleNamespace(
        nodeid="tests/test_docker_e2e_negative.py::test_x",
        get_closest_marker=lambda name: object() if name == "docker_e2e" else None,
    )
    gate.pytest_collection_modifyitems(None, [item])
    gate.pytest_runtest_logreport(
        SimpleNamespace(
            when="call",
            skipped=True,
            passed=False,
            failed=False,
            keywords={"docker_e2e": True},
            nodeid=item.nodeid,
        )
    )
    session = SimpleNamespace(
        exitstatus=0,
        config=SimpleNamespace(pluginmanager=SimpleNamespace(get_plugin=lambda _n: None)),
    )
    gate.pytest_sessionfinish(session, 0)
    assert session.exitstatus == 1

    gate.pytest_sessionstart(None)
    gate.pytest_collection_modifyitems(None, [])
    session.exitstatus = 0
    gate.pytest_sessionfinish(session, 0)
    assert session.exitstatus == 1

    gate.pytest_sessionstart(None)
    gate.pytest_collection_modifyitems(None, [item])
    gate.pytest_runtest_logreport(
        SimpleNamespace(
            when="call",
            skipped=False,
            passed=True,
            failed=False,
            keywords={"docker_e2e": True},
            nodeid=item.nodeid,
        )
    )
    session.exitstatus = 0
    gate.pytest_sessionfinish(session, 0)
    assert session.exitstatus == 0


def test_a5_omitted_serialized_evidence_can_be_reread(tmp_path: Path):
    repo = _mini_repo(tmp_path)
    lines = [f"{i:04d}:" + ("x" * 3980) for i in range(6)]
    (repo / "big.py").write_text("\n".join(lines) + "\n", encoding="utf-8")
    session = TaskSession("probe", repo, repo, repo, repo, "probe")
    session.analysis_phase = AnalysisPhase.SYNTHESIZE
    read = read_file_result(repo, "big.py", 1, 6)
    record_read_result(session, read, source="read_file")
    prompt = session.evidence.format_for_prompt()
    assert "0000:" not in prompt or "may be re-read" in prompt
    assert session.evidence.covers("big.py", 1, 6, read.revision) is False
    text = execute_request_evidence(
        session,
        {
            "path": "big.py",
            "start_line": 1,
            "end_line": 6,
            "unanswered_requirement": "need the large helper",
            "reason": "previous window omitted the serialized block",
        },
    )
    assert "EVIDENCE_ACCEPTED" in text


def test_a5_truncated_lines_are_not_full_coverage():
    store = EvidenceStore()
    store.record(
        EvidenceItem(
            path="wide.py",
            revision="rev",
            start_line=1,
            end_line=1,
            content="1|trunc",
            source="read_file",
            total_lines=1,
            complete=False,
        )
    )
    store.format_for_prompt(max_chars=10_000)
    assert store.covers("wide.py", 1, 1, "rev") is False


def test_a6_custom_checks_py_is_protected_in_controller(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "mod.py").write_text("def f():\n    return 1\n", encoding="utf-8")
    (repo / "checks.py").write_text(
        "from mod import f\ndef test_f():\n    assert f() == 2\n",
        encoding="utf-8",
    )
    (repo / "pytest.ini").write_text(
        "[pytest]\npythonpath = .\npython_files = checks.py\n",
        encoding="utf-8",
    )
    protected = discover_protected_test_files(repo)
    assert "checks.py" in protected
    diff = (
        "--- a/checks.py\n+++ b/checks.py\n@@ -1,3 +1,3 @@\n from mod import f\n"
        " def test_f():\n-    assert f() == 2\n+    assert True\n"
    )
    result = validate_proposal(
        PatchProposal.from_dict(
            {
                "diagnosis": "weaken",
                "affected_files": ["checks.py"],
                "unified_diff": diff,
                "expected_behavior": "pass",
                "risk_notes": "high",
                "tests_to_run": ["checks.py"],
            }
        ),
        repo,
        collected_test_files=protected,
    )
    assert result.ok is False

    weaken = {
        "tool": "propose_patch",
        "args": {
            "diagnosis": "weaken oracle",
            "affected_files": ["checks.py"],
            "unified_diff": diff,
            "expected_behavior": "pass",
            "risk_notes": "high",
            "tests_to_run": ["checks.py"],
        },
    }
    session = TaskController(
        llm=LLMClient(
            dry_run_script=[weaken, {"tool": "finish", "args": {"reason": "stop"}}]
        ),
        runner=_Runner((1, 0)),
        approve=lambda *_: True,
        session_base=tmp_path / "sessions",
    ).run(repo, "make tests pass")
    assert session.status != SessionStatus.SUCCEEDED
    assert "assert f() == 2" in (session.workspace_root / "checks.py").read_text(
        encoding="utf-8"
    )


def test_a7_output_cap_kills_live_child_before_timeout():
    script = (
        "import sys, time\n"
        "sys.stdout.buffer.write(b'x' * 65536)\n"
        "sys.stdout.flush()\n"
        "time.sleep(30)\n"
    )
    started = time.perf_counter()
    with pytest.raises(RuntimeError, match="exceeded"):
        _run_docker_cmd([sys.executable, "-c", script], timeout_seconds=5, max_bytes=1024)
    elapsed = time.perf_counter() - started
    assert elapsed < 2.5


def test_a7_keyboard_interrupt_kills_proc(monkeypatch):
    calls = {"killed": False}

    class FakeProc:
        stdout = None
        stderr = None
        returncode = None

        def wait(self, timeout=None):
            raise KeyboardInterrupt

        def kill(self):
            calls["killed"] = True

    monkeypatch.setattr(
        "code_agent.runtime.docker_pytest.subprocess.Popen",
        lambda *_a, **_k: FakeProc(),
    )
    with pytest.raises(KeyboardInterrupt):
        _run_docker_cmd(["docker", "run"], timeout_seconds=5, max_bytes=50)
    assert calls["killed"] is True


def test_a8_deadline_after_model_blocks_apply(tmp_path: Path):
    repo = _mini_repo(tmp_path)
    llm = LLMClient(dry_run_script=[GOOD_PATCH])
    clock = {"t": 0.0}

    def fake_mono():
        return clock["t"]

    original = llm.complete

    def late(messages):
        clock["t"] = 10.0
        return original(messages)

    llm.complete = late  # type: ignore[method-assign]
    session = TaskController(
        llm=llm,
        runner=_Runner(),
        approve=lambda *_: True,
        session_base=tmp_path / "sessions",
        session_deadline_seconds=1.0,
        monotonic=fake_mono,
    ).run(repo, "make f return 2")
    assert session.stop_reason == "session_deadline"
    assert session.status == SessionStatus.ERROR
    assert session.attempts_used == 0
    assert "return 1" in (session.workspace_root / "mod.py").read_text(encoding="utf-8")


def test_a9_summary_keeps_transport_attempts(tmp_path: Path):
    repo = _mini_repo(tmp_path)
    calls = {"n": 0}

    def create(**_kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("transient transport failure")
        message = SimpleNamespace(content=json.dumps(GOOD_PATCH))
        return SimpleNamespace(choices=[SimpleNamespace(message=message)], usage=None)

    llm = LLMClient(api_key="sk-not-a-real-key-aaaa", max_transport_retries=1)
    llm._get_client = lambda: SimpleNamespace(  # type: ignore[method-assign]
        chat=SimpleNamespace(completions=SimpleNamespace(create=create))
    )
    session = TaskController(
        llm=llm,
        runner=_Runner(),
        approve=lambda *_: True,
        session_base=tmp_path / "sessions",
    ).run(repo, "make f return 2")
    summary = json.loads((session.artifacts_dir / "summary.json").read_text(encoding="utf-8"))
    assert llm.logical_calls == 1
    assert llm.transport_attempts == 2
    assert summary["observability"]["model"]["logical_calls"] == 1
    assert summary["observability"]["model"]["transport_attempts"] == 2
    assert summary["observability"]["model"]["calls"] == 1


def test_b1_addopts_explicit_checks_py_is_not_weakened(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "pytest.ini").write_text("[pytest]\naddopts = checks.py\n", encoding="utf-8")
    original = "def test_f():\n    assert 1 == 2\n"
    (repo / "checks.py").write_text(original, encoding="utf-8")
    protected = discover_protected_test_files(repo)
    assert "checks.py" in protected
    diff = (
        "--- a/checks.py\n+++ b/checks.py\n@@ -1,2 +1,2 @@\n def test_f():\n"
        "-    assert 1 == 2\n+    assert 1 == 1\n"
    )
    result = validate_proposal(
        PatchProposal.from_dict(
            {
                "diagnosis": "weaken",
                "affected_files": ["checks.py"],
                "unified_diff": diff,
                "expected_behavior": "pass",
                "risk_notes": "high",
                "tests_to_run": ["checks.py"],
            }
        ),
        repo,
        collected_test_files=protected,
    )
    assert result.ok is False
    session = TaskController(
        llm=LLMClient(
            dry_run_script=[
                {
                    "tool": "propose_patch",
                    "args": {
                        "diagnosis": "weaken oracle",
                        "affected_files": ["checks.py"],
                        "unified_diff": diff,
                        "expected_behavior": "pass",
                        "risk_notes": "high",
                        "tests_to_run": ["checks.py"],
                    },
                },
                {"tool": "finish", "args": {"reason": "blocked"}},
            ]
        ),
        runner=_Runner((1, 0)),
        approve=lambda *_: True,
        session_base=tmp_path / "sessions",
    ).run(repo, "fix test")
    assert session.status != SessionStatus.SUCCEEDED
    assert session.attempts_used == 0
    assert (session.workspace_root / "checks.py").read_text(encoding="utf-8") == original


def test_b1_container_nodeid_maps_and_escapes_are_rejected(tmp_path: Path):
    from code_agent.patching.test_collection import map_pytest_nodeid_to_workspace

    work = tmp_path / "work"
    work.mkdir()
    (work / "checks.py").write_text("def test_f():\n    assert 1 == 2\n", encoding="utf-8")
    assert (
        map_pytest_nodeid_to_workspace("/work/checks.py::test_f", work) == "checks.py"
    )
    assert map_pytest_nodeid_to_workspace("checks.py::test_f", work) == "checks.py"
    assert map_pytest_nodeid_to_workspace("../outside.py::test_f", work) is None
    assert map_pytest_nodeid_to_workspace("/etc/passwd.py::test_x", work) is None
    assert map_pytest_nodeid_to_workspace(r"C:\Windows\checks.py::t", work) is None
    inventory = discover_protected_test_files(
        work, isolated_nodeids=["/work/checks.py::test_f", "../secret.py::t"]
    )
    assert "checks.py" in inventory


def test_b1_unparseable_python_files_fail_closed(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "mod.py").write_text("VALUE = 1\n", encoding="utf-8")
    (repo / "pytest.ini").write_text(
        "[pytest]\npython_files = **/*.py\n", encoding="utf-8"
    )
    protected = discover_protected_test_files(repo)
    assert "mod.py" in protected


def _overflow_child(nbytes: int, stream: str) -> list[str]:
    if stream == "stdout":
        body = (
            f"import sys,time;sys.stdout.buffer.write(b'x'*{nbytes});"
            "sys.stdout.flush();time.sleep(30)"
        )
    elif stream == "stderr":
        body = (
            f"import sys,time;sys.stderr.buffer.write(b'x'*{nbytes});"
            "sys.stderr.flush();time.sleep(30)"
        )
    else:
        body = (
            f"import sys,time;sys.stdout.buffer.write(b'x'*{nbytes // 2});"
            f"sys.stderr.buffer.write(b'y'*{nbytes - nbytes // 2});"
            "sys.stdout.flush();sys.stderr.flush();time.sleep(30)"
        )
    return [sys.executable, "-u", "-c", body]


@pytest.mark.parametrize("stream", ["stdout", "stderr", "mixed"])
def test_b2_limit_plus_one_byte_raises_before_timeout(stream: str):
    import subprocess

    from code_agent.runtime.docker_pytest import MAX_CAPTURE_BYTES

    held: dict[str, object] = {}
    real_popen = subprocess.Popen

    def wrap(*args, **kwargs):
        proc = real_popen(*args, **kwargs)
        held["proc"] = proc
        return proc

    started = time.perf_counter()
    with patch("code_agent.runtime.docker_pytest.subprocess.Popen", wrap):
        with pytest.raises(RuntimeError, match="exceeded"):
            _run_docker_cmd(_overflow_child(1025, stream), timeout_seconds=8, max_bytes=1024)
    elapsed = time.perf_counter() - started
    assert elapsed < 2.5
    proc = held["proc"]
    assert proc.poll() is not None

    started = time.perf_counter()
    with patch("code_agent.runtime.docker_pytest.subprocess.Popen", wrap):
        with pytest.raises(RuntimeError, match="exceeded"):
            _run_docker_cmd(
                _overflow_child(MAX_CAPTURE_BYTES + 1, stream),
                timeout_seconds=8,
                max_bytes=MAX_CAPTURE_BYTES,
            )
    elapsed = time.perf_counter() - started
    assert elapsed < 3.0
    assert held["proc"].poll() is not None


def test_b2_timeout_is_distinct_from_output_cap():
    import subprocess

    with pytest.raises(subprocess.TimeoutExpired):
        _run_docker_cmd(
            [sys.executable, "-u", "-c", "import time; time.sleep(5)"],
            timeout_seconds=0.2,
            max_bytes=1024,
        )
