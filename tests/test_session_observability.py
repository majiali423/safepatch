"""Per-session observability card — deterministic offline tests."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from code_agent.controller import TaskController
from code_agent.llm import LLMClient, LLMError, normalize_token_usage
from code_agent.patching.applier import ApplyResult
from code_agent.state import (
    SUMMARY_SCHEMA_VERSION,
    ApprovalBinding,
    SessionStatus,
    TestResult,
)


class FakeClock:
    """Wall + monotonic clocks. Elapsed appears between start() and finish()."""

    def __init__(
        self,
        wall: float = 1_700_000_000.0,
        mono: float = 100.0,
        *,
        elapsed_ms: float = 25.0,
    ) -> None:
        self.wall = wall
        self.mono = mono
        self.elapsed_ms = elapsed_ms
        self._mono_reads = 0
        self._wall_reads = 0

    def time(self) -> float:
        self._wall_reads += 1
        if self._wall_reads == 1:
            return self.wall
        return self.wall + self.elapsed_ms / 1000.0

    def perf(self) -> float:
        self._mono_reads += 1
        if self._mono_reads == 1:
            return self.mono
        return self.mono + self.elapsed_ms / 1000.0


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
            result = TestResult(
                exit_code=0, stdout="1 passed", stderr="", duration_sec=0.01
            )
        if log_path:
            log_path.parent.mkdir(parents=True, exist_ok=True)
            log_path.write_text(result.stdout, encoding="utf-8")
        return result


class AlwaysFailRunner:
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


def _patch(old: str, new: str, **extra: Any) -> dict:
    item: dict[str, Any] = {
        "tool": "propose_patch",
        "args": {
            "diagnosis": "fix",
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
    if "usage" in extra:
        item["usage"] = extra["usage"]
    return item


def _good_patch(**extra: Any) -> dict:
    return _patch("1", "2", **extra)


def _mismatch_patch(**extra: Any) -> dict:
    return _patch("999", "2", **extra)


def _read_tool() -> dict:
    return {
        "tool": "read_file",
        "args": {"path": "mod.py", "start_line": 1, "end_line": 10},
    }


def _run(
    tmp_path: Path,
    script: list[Any],
    *,
    runner=None,
    approve=None,
    clock: FakeClock | None = None,
) -> Any:
    clock = clock or FakeClock()
    controller = TaskController(
        llm=LLMClient(dry_run_script=script, model="test-model"),
        runner=runner or FailThenPassRunner(),  # type: ignore[arg-type]
        approve=approve or (lambda *_: True),
        say=lambda _m: None,
        session_base=tmp_path / "sessions",
        wall_time=clock.time,
        monotonic=clock.perf,
    )
    session = controller.run(
        _mini_repo(tmp_path),
        "f should return 2",
    )
    return session


def _obs(session) -> dict:
    summary = json.loads(
        (session.artifacts_dir / "summary.json").read_text(encoding="utf-8")
    )
    return summary, summary["observability"]


def _assert_call_usage_invariant(obs: dict) -> None:
    """model.calls == calls_with_usage + calls_without_usage (exclusive buckets)."""
    calls = obs["model"]["calls"]
    usage = obs["model"]["usage"]
    with_u = usage["calls_with_usage"]
    without_u = usage["calls_without_usage"]
    assert with_u + without_u == calls
    assert with_u >= 0 and without_u >= 0
    if calls == 0:
        assert with_u == 0 and without_u == 0
        assert usage["available"] is False
        assert usage["complete"] is False
        assert usage["prompt_tokens"] is None
        assert usage["completion_tokens"] is None
        assert usage["total_tokens"] is None
        assert usage["cached_tokens"] is None
    else:
        assert usage["complete"] is (without_u == 0 and with_u == calls)


def test_successful_session_counts(tmp_path: Path):
    clock = FakeClock()
    session = _run(
        tmp_path,
        [_read_tool(), _good_patch()],
        clock=clock,
    )
    assert session.status == SessionStatus.SUCCEEDED
    summary, obs = _obs(session)

    assert summary["summary_schema_version"] == SUMMARY_SCHEMA_VERSION
    assert summary["attempts_used"] == 1
    assert summary["status"] == "SUCCEEDED"
    # Legacy fields preserved.
    assert "total_format_retries_used" in summary
    assert "changed_files" in summary

    assert obs["started_at"]
    assert obs["finished_at"]
    assert obs["duration_ms"] == 25
    assert obs["model"]["provider"] == "dry_run"
    assert obs["model"]["name"] == "test-model"
    assert obs["model"]["tool_calling_protocol"] == "custom_json"
    assert obs["model"]["calls"] == 2
    assert obs["tools"]["read_calls"] == 1
    assert obs["tools"]["proposal_calls"] == 1
    assert obs["tools"]["total_calls"] == 2
    assert obs["retries"]["format"] == 0
    assert obs["retries"]["patch_regeneration"] == 0
    assert obs["retries"]["repair_attempts"] == 1
    assert obs["tests"]["baseline_runs"] == 1
    assert obs["tests"]["post_apply_runs"] == 1
    assert obs["tests"]["total_runs"] == 2
    assert obs["model"]["usage"]["available"] is False
    assert obs["model"]["usage"]["prompt_tokens"] is None
    assert obs["model"]["usage"]["complete"] is False
    assert obs["model"]["usage"]["calls_with_usage"] == 0
    assert obs["model"]["usage"]["calls_without_usage"] == 2
    _assert_call_usage_invariant(obs)


def test_invariant_success_with_provider_usage(tmp_path: Path):
    """1. Success + provider usage → all calls in calls_with_usage."""
    script = [
        {
            **_good_patch(),
            "usage": {"prompt_tokens": 5, "completion_tokens": 2, "total_tokens": 7},
        }
    ]
    session = _run(tmp_path, script)
    assert session.status == SessionStatus.SUCCEEDED
    _, obs = _obs(session)
    assert obs["model"]["calls"] == 1
    assert obs["model"]["usage"]["calls_with_usage"] == 1
    assert obs["model"]["usage"]["calls_without_usage"] == 0
    assert obs["model"]["usage"]["complete"] is True
    _assert_call_usage_invariant(obs)


def test_invariant_success_without_provider_usage(tmp_path: Path):
    """2. Success without usage → all calls in calls_without_usage."""
    session = _run(tmp_path, [_good_patch()])
    assert session.status == SessionStatus.SUCCEEDED
    _, obs = _obs(session)
    assert obs["model"]["calls"] == 1
    assert obs["model"]["usage"]["calls_with_usage"] == 0
    assert obs["model"]["usage"]["calls_without_usage"] == 1
    assert obs["model"]["usage"]["complete"] is False
    _assert_call_usage_invariant(obs)


def test_provider_success_counts_one_model_call(tmp_path: Path):
    session = _run(tmp_path, [_good_patch()])
    assert session.status == SessionStatus.SUCCEEDED
    _, obs = _obs(session)
    assert obs["model"]["calls"] == 1
    _assert_call_usage_invariant(obs)


def test_invalid_json_still_counts_model_call(tmp_path: Path):
    session = _run(tmp_path, ["not-json{", _good_patch()])
    assert session.status == SessionStatus.SUCCEEDED
    _, obs = _obs(session)
    assert obs["model"]["calls"] == 2
    assert obs["retries"]["format"] == 1
    assert obs["retries"]["format"] != obs["model"]["calls"]
    _assert_call_usage_invariant(obs)


def test_invariant_invalid_json_with_usage(tmp_path: Path):
    """4. Illegal JSON after provider return that included usage → calls_with_usage."""
    script = [
        {
            "raw": "not-a-json-object",
            "usage": {"prompt_tokens": 9, "completion_tokens": 1, "total_tokens": 10},
        },
        {
            **_good_patch(),
            "usage": {"prompt_tokens": 3, "completion_tokens": 1, "total_tokens": 4},
        },
    ]
    session = _run(tmp_path, script)
    assert session.status == SessionStatus.SUCCEEDED
    _, obs = _obs(session)
    assert obs["model"]["calls"] == 2
    assert obs["retries"]["format"] == 1
    assert obs["model"]["usage"]["calls_with_usage"] == 2
    assert obs["model"]["usage"]["calls_without_usage"] == 0
    assert obs["model"]["usage"]["prompt_tokens"] == 12
    assert obs["model"]["usage"]["complete"] is True
    _assert_call_usage_invariant(obs)


def test_invariant_invalid_json_without_usage(tmp_path: Path):
    """5. Illegal JSON without usage → calls_without_usage for that attempt."""
    session = _run(tmp_path, [{"raw": "{not json"}, _good_patch()])
    assert session.status == SessionStatus.SUCCEEDED
    _, obs = _obs(session)
    assert obs["model"]["calls"] == 2
    assert obs["retries"]["format"] == 1
    assert obs["model"]["usage"]["calls_with_usage"] == 0
    assert obs["model"]["usage"]["calls_without_usage"] == 2
    _assert_call_usage_invariant(obs)


def test_invariant_schema_validation_failure(tmp_path: Path):
    """6. Tool/proposal schema failure after provider return (with usage)."""
    bad_schema = {
        "tool": "propose_patch",
        "args": {"diagnosis": "incomplete"},  # missing required proposal fields
        "usage": {"prompt_tokens": 4, "completion_tokens": 1, "total_tokens": 5},
    }
    script = [
        bad_schema,
        {
            **_good_patch(),
            "usage": {"prompt_tokens": 2, "completion_tokens": 1, "total_tokens": 3},
        },
    ]
    session = _run(tmp_path, script)
    assert session.status == SessionStatus.SUCCEEDED
    _, obs = _obs(session)
    assert obs["retries"]["format"] == 1
    assert obs["model"]["calls"] == 2
    # complete() succeeded for both; usage recorded once each (no double-count on
    # subsequent parse_proposal ModelOutputError).
    assert obs["model"]["usage"]["calls_with_usage"] == 2
    assert obs["model"]["usage"]["calls_without_usage"] == 0
    assert obs["model"]["usage"]["prompt_tokens"] == 6
    _assert_call_usage_invariant(obs)


def test_provider_transport_exception_counts_model_call(tmp_path: Path):
    """3. Transport exception after invocation started."""
    class BoomClient(LLMClient):
        def __init__(self) -> None:
            super().__init__(api_key="sk-test", model="boom-model")
            self._dry_run_script = []  # force live path

        def _fetch_raw(self, messages):
            self._emit_provider_invocation()
            raise LLMError("Connection timeout", provider_invoked=True)

    clock = FakeClock()
    controller = TaskController(
        llm=BoomClient(),
        runner=FailThenPassRunner(),  # type: ignore[arg-type]
        approve=lambda *_: True,
        say=lambda _m: None,
        session_base=tmp_path / "sessions",
        wall_time=clock.time,
        monotonic=clock.perf,
    )
    session = controller.run(_mini_repo(tmp_path), "f should return 2")
    assert session.status == SessionStatus.ERROR
    _, obs = _obs(session)
    assert obs["model"]["calls"] == 1
    assert obs["model"]["usage"]["calls_with_usage"] == 0
    assert obs["model"]["usage"]["calls_without_usage"] == 1
    assert obs["finished_at"]
    assert obs["duration_ms"] == 25
    _assert_call_usage_invariant(obs)


def test_local_config_error_before_provider_counts_zero(tmp_path: Path, monkeypatch):
    """11. Local config error before provider → all counters stay 0."""
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("CODE_AGENT_API_KEY", raising=False)
    llm = LLMClient(api_key=None, model="no-key-model")
    llm.api_key = None
    llm._dry_run_script = []

    clock = FakeClock(elapsed_ms=30)
    controller = TaskController(
        llm=llm,
        runner=FailThenPassRunner(),  # type: ignore[arg-type]
        approve=lambda *_: True,
        say=lambda _m: None,
        session_base=tmp_path / "sessions",
        wall_time=clock.time,
        monotonic=clock.perf,
    )
    session = controller.run(_mini_repo(tmp_path), "f should return 2")
    assert session.status == SessionStatus.ERROR
    assert "API key" in session.last_error
    _, obs = _obs(session)
    assert obs["model"]["calls"] == 0
    assert obs["model"]["usage"]["calls_with_usage"] == 0
    assert obs["model"]["usage"]["calls_without_usage"] == 0
    assert obs["finished_at"]
    assert obs["duration_ms"] == 30
    _assert_call_usage_invariant(obs)


def test_format_retry_increments_model_and_format_not_repair(tmp_path: Path):
    """7. Format retry then success."""
    script = ["not-json", _good_patch()]
    session = _run(tmp_path, script)
    assert session.status == SessionStatus.SUCCEEDED
    _, obs = _obs(session)
    assert obs["model"]["calls"] == 2
    assert obs["retries"]["format"] == 1
    assert obs["retries"]["repair_attempts"] == 1
    assert session.attempts_used == 1
    _assert_call_usage_invariant(obs)


def test_patch_regeneration_counts_without_repair(tmp_path: Path):
    """8. Patch regeneration then success."""
    session = _run(tmp_path, [_mismatch_patch(), _read_tool(), _good_patch()])
    assert session.status == SessionStatus.SUCCEEDED
    _, obs = _obs(session)
    assert obs["model"]["calls"] == 3
    assert obs["tools"]["proposal_calls"] == 2
    assert obs["retries"]["patch_regeneration"] == 1
    assert obs["retries"]["repair_attempts"] == 1
    assert obs["tests"]["post_apply_runs"] == 1
    _assert_call_usage_invariant(obs)


def test_repair_attempt_only_after_apply_and_pytest(tmp_path: Path):
    session = _run(tmp_path, [_good_patch()])
    _, obs = _obs(session)
    assert obs["retries"]["repair_attempts"] == 1
    assert obs["tests"]["post_apply_runs"] == 1
    assert session.attempts_used == 1


def test_preflight_permanent_failure_zero_post_apply_pytest(tmp_path: Path):
    """9. PATCH_NOT_APPLICABLE."""
    script = [
        _mismatch_patch(),
        _read_tool(),
        _mismatch_patch(),
        _read_tool(),
        _mismatch_patch(),
    ]
    session = _run(tmp_path, script)
    assert session.status == SessionStatus.PATCH_NOT_APPLICABLE
    _, obs = _obs(session)
    assert obs["retries"]["repair_attempts"] == 0
    assert obs["tests"]["post_apply_runs"] == 0
    assert obs["tests"]["baseline_runs"] == 1
    assert obs["retries"]["patch_regeneration"] == 2
    assert obs["finished_at"]
    assert isinstance(obs["duration_ms"], int)
    assert obs["model"]["calls"] == 5
    _assert_call_usage_invariant(obs)


def test_permission_apply_error_no_repair_or_pytest(tmp_path: Path, monkeypatch):
    from code_agent import controller as controller_mod

    def boom(proposal, workspace_root, **kwargs):
        return ApplyResult(
            ok=False,
            error="PermissionError: Permission denied: 'mod.py'",
            error_kind="permission_error",
            target_file="mod.py",
            rollback_succeeded=True,
        )

    monkeypatch.setattr(controller_mod, "apply_proposal", boom)
    session = _run(tmp_path, [_good_patch()])
    assert session.status == SessionStatus.ERROR
    _, obs = _obs(session)
    assert obs["retries"]["repair_attempts"] == 0
    assert obs["tests"]["post_apply_runs"] == 0
    assert obs["tests"]["baseline_runs"] == 1
    assert obs["finished_at"]


def test_baseline_and_post_apply_pytest_separated(tmp_path: Path):
    session = _run(tmp_path, [_good_patch()], runner=FailThenPassRunner())
    _, obs = _obs(session)
    assert obs["tests"]["baseline_runs"] == 1
    assert obs["tests"]["post_apply_runs"] == 1
    assert obs["tests"]["total_runs"] == 2


def test_token_usage_accumulates_across_calls(tmp_path: Path):
    script = [
        {
            **_read_tool(),
            "usage": {
                "prompt_tokens": 10,
                "completion_tokens": 4,
                "total_tokens": 14,
                "cached_tokens": 2,
            },
        },
        {
            **_good_patch(),
            "usage": {
                "prompt_tokens": 20,
                "completion_tokens": 6,
                "total_tokens": 26,
            },
        },
    ]
    session = _run(tmp_path, script)
    _, obs = _obs(session)
    usage = obs["model"]["usage"]
    assert usage["available"] is True
    assert usage["complete"] is True
    assert usage["source"] == "provider"
    assert usage["prompt_tokens"] == 30
    assert usage["completion_tokens"] == 10
    assert usage["total_tokens"] == 40
    assert usage["cached_tokens"] == 2
    assert usage["calls_with_usage"] == 2
    assert usage["calls_without_usage"] == 0
    _assert_call_usage_invariant(obs)


def test_partial_token_usage_complete_false(tmp_path: Path):
    script = [
        {
            **_read_tool(),
            "usage": {"prompt_tokens": 100, "completion_tokens": 20, "total_tokens": 120},
        },
        _read_tool(),  # no usage
        {
            **_good_patch(),
            "usage": {"prompt_tokens": 50, "completion_tokens": 10, "total_tokens": 60},
        },
    ]
    session = _run(tmp_path, script)
    assert session.status == SessionStatus.SUCCEEDED
    _, obs = _obs(session)
    usage = obs["model"]["usage"]
    assert obs["model"]["calls"] == 3
    assert usage["prompt_tokens"] == 150
    assert usage["completion_tokens"] == 30
    assert usage["total_tokens"] == 180
    assert usage["source"] == "provider_partial"
    assert usage["available"] is True
    assert usage["complete"] is False
    assert usage["calls_with_usage"] == 2
    assert usage["calls_without_usage"] == 1
    _assert_call_usage_invariant(obs)


def test_missing_token_usage_is_null_and_session_succeeds(tmp_path: Path):
    session = _run(tmp_path, [_good_patch()])
    assert session.status == SessionStatus.SUCCEEDED
    _, obs = _obs(session)
    usage = obs["model"]["usage"]
    assert usage["prompt_tokens"] is None
    assert usage["completion_tokens"] is None
    assert usage["total_tokens"] is None
    assert usage["cached_tokens"] is None
    assert usage["available"] is False
    assert usage["complete"] is False
    assert usage["source"] == "unavailable"
    assert usage["calls_with_usage"] == 0
    assert usage["calls_without_usage"] == 1
    _assert_call_usage_invariant(obs)


def test_all_calls_with_usage_complete_true(tmp_path: Path):
    script = [
        {
            **_good_patch(),
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        }
    ]
    session = _run(tmp_path, script)
    _, obs = _obs(session)
    usage = obs["model"]["usage"]
    assert usage["complete"] is True
    assert usage["source"] == "provider"
    assert usage["calls_with_usage"] == 1
    assert usage["calls_without_usage"] == 0
    _assert_call_usage_invariant(obs)


def test_terminal_paths_write_duration(tmp_path: Path):
    """9+10. PATCH_NOT_APPLICABLE and PATCH_BASE_CHANGED keep duration + invariant."""
    clock = FakeClock()
    session = _run(
        tmp_path,
        [
            _mismatch_patch(),
            _read_tool(),
            _mismatch_patch(),
            _read_tool(),
            _mismatch_patch(),
        ],
        clock=clock,
    )
    assert session.status == SessionStatus.PATCH_NOT_APPLICABLE
    _, obs = _obs(session)
    assert obs["duration_ms"] == 25
    assert obs["finished_at"]

    repo = _mini_repo(tmp_path / "base")
    clock3 = FakeClock(elapsed_ms=40)

    def approve_mutate(binding: ApprovalBinding, _attempt: int) -> bool:
        sessions = list((tmp_path / "base_sessions").rglob("working_copy/mod.py"))
        assert sessions
        sessions[0].write_text("def f():\n    return 9\n", encoding="utf-8")
        return True

    controller = TaskController(
        llm=LLMClient(dry_run_script=[_good_patch()]),
        runner=FailThenPassRunner(),  # type: ignore[arg-type]
        approve=approve_mutate,
        say=lambda _m: None,
        session_base=tmp_path / "base_sessions",
        wall_time=clock3.time,
        monotonic=clock3.perf,
    )
    session2 = controller.run(repo, "fix")
    assert session2.status == SessionStatus.PATCH_BASE_CHANGED
    _, obs2 = _obs(session2)
    assert obs2["duration_ms"] == 40
    assert obs2["finished_at"]
    assert obs2["tests"]["post_apply_runs"] == 0
    assert obs2["model"]["calls"] == 1
    _assert_call_usage_invariant(obs)
    _assert_call_usage_invariant(obs2)


def test_observability_has_no_secrets(tmp_path: Path):
    script = [
        {
            **_good_patch(),
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        }
    ]
    session = _run(tmp_path, script)
    text = (session.artifacts_dir / "summary.json").read_text(encoding="utf-8")
    assert "sk-" not in text
    assert "OPENAI_API_KEY" not in text
    assert "api_key" not in text.lower() or "***" in text
    blob = json.loads(text)
    dumped = json.dumps(blob["observability"])
    assert "raw_text" not in dumped
    assert "choices" not in dumped


def test_legacy_summary_fields_unchanged(tmp_path: Path):
    session = _run(tmp_path, [_good_patch()])
    summary, _ = _obs(session)
    for key in (
        "status",
        "attempts_used",
        "changed_files",
        "baseline_tests_passed",
        "final_tests_passed",
        "stop_reason",
        "consecutive_format_retries",
        "total_format_retries_used",
        "consecutive_patch_regeneration_retries",
        "total_patch_regeneration_retries",
        "patch_preflight_failures",
        "patch_preflight_successes",
        "patch_preflight_success_rate",
        "patch_apply_failures_after_preflight",
        "first_patch_applicable",
    ):
        assert key in summary
    assert summary["summary_schema_version"] == 1
    assert "observability" in summary


def test_normalize_token_usage_zero_vs_missing():
    zero = normalize_token_usage(
        {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
        source="provider",
    )
    assert zero.prompt_tokens == 0
    assert zero.available is True
    missing = normalize_token_usage(None)
    assert missing.prompt_tokens is None
    assert missing.available is False


def test_normalize_token_usage_deepseek_cache_hit_tokens():
    usage = normalize_token_usage(
        {
            "prompt_tokens": 120,
            "completion_tokens": 30,
            "total_tokens": 150,
            "prompt_cache_hit_tokens": 80,
            "prompt_cache_miss_tokens": 40,
        }
    )

    assert usage.prompt_tokens == 120
    assert usage.cached_tokens == 80


def test_failed_max_attempts_pytest_matches_repair(tmp_path: Path):
    # After the first apply, working copy has `return 2`; later patches must
    # match that baseline or preflight/regen will fire instead of repair.
    second = {
        "tool": "propose_patch",
        "args": {
            "diagnosis": "tweak",
            "affected_files": ["mod.py"],
            "unified_diff": (
                "--- a/mod.py\n"
                "+++ b/mod.py\n"
                "@@ -1,2 +1,3 @@\n"
                " def f():\n"
                "+    # attempt2\n"
                "     return 2\n"
            ),
            "expected_behavior": "still return 2",
            "risk_notes": "low",
            "tests_to_run": ["tests/test_mod.py"],
        },
    }
    third = {
        "tool": "propose_patch",
        "args": {
            "diagnosis": "tweak2",
            "affected_files": ["mod.py"],
            "unified_diff": (
                "--- a/mod.py\n"
                "+++ b/mod.py\n"
                "@@ -1,3 +1,4 @@\n"
                " def f():\n"
                "+    # attempt3\n"
                "     # attempt2\n"
                "     return 2\n"
            ),
            "expected_behavior": "still return 2",
            "risk_notes": "low",
            "tests_to_run": ["tests/test_mod.py"],
        },
    }
    script = [_good_patch(), second, third]
    session = _run(tmp_path, script, runner=AlwaysFailRunner())
    assert session.status == SessionStatus.FAILED_MAX_ATTEMPTS
    _, obs = _obs(session)
    assert obs["retries"]["repair_attempts"] == 3
    assert obs["tests"]["post_apply_runs"] == 3
    assert obs["retries"]["patch_regeneration"] == 0
