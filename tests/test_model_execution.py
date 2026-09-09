from __future__ import annotations

from pathlib import Path

from code_agent.llm import LLMClient


def test_logical_calls_are_not_transport_attempts():
    llm = LLMClient(dry_run_script=[{"tool": "finish", "args": {"reason": "x"}}])
    llm.complete([{"role": "user", "content": "hi"}])
    assert llm.logical_calls == 1
    assert llm.transport_attempts == 1


def test_session_deadline_stops_before_model(tmp_path: Path):
    from code_agent.controller import TaskController
    from code_agent.state import SessionStatus, TestResult

    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "mod.py").write_text("def f():\n    return 1\n", encoding="utf-8")
    (repo / "tests").mkdir()
    (repo / "tests" / "test_mod.py").write_text(
        "from mod import f\ndef test_f():\n    assert f() == 2\n", encoding="utf-8"
    )
    llm = LLMClient(dry_run_script=[{"tool": "finish", "args": {"reason": "late"}}])
    llm.requests = []  # type: ignore[attr-defined]
    original = llm.complete

    def counted(messages):
        llm.requests.append(messages)  # type: ignore[attr-defined]
        return original(messages)

    llm.complete = counted  # type: ignore[method-assign]

    class Runner:
        def run_pytest(self, workspace_root, *, log_path=None):
            result = TestResult(1, "FAILED", "", 0.01, failed_tests=["t"])
            if log_path:
                log_path.parent.mkdir(parents=True, exist_ok=True)
                log_path.write_text("x", encoding="utf-8")
            return result

    monotonic = {"t": 0.0}

    def fake_mono():
        monotonic["t"] += 10.0
        return monotonic["t"]

    session = TaskController(
        llm=llm,
        runner=Runner(),
        approve=lambda *_: True,
        session_base=tmp_path / "sessions",
        session_deadline_seconds=0.001,
        monotonic=fake_mono,
    ).run(repo, "fix")
    assert session.status == SessionStatus.ERROR
    assert session.stop_reason == "session_deadline"
    assert llm.requests == []
