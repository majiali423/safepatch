"""Write session artifacts and close observability without deciding apply/test."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

from code_agent.repository.git_diff import (
    changed_files_from_diff,
    current_diff_from_snapshot,
    write_unified_diff,
)
from code_agent.state import SessionStatus, TaskSession, TokenUsage
from code_agent.tracing.recorder import TraceRecorder
from code_agent.tracing.sanitize import sanitize_text


class SessionFinalizer:
    def __init__(
        self,
        *,
        runner: object,
        llm: object,
        wall_time: Callable[[], float],
        monotonic: Callable[[], float],
    ) -> None:
        self.runner = runner
        self.llm = llm
        self._wall_time = wall_time
        self._monotonic = monotonic

    def init_observability(self, session: TaskSession) -> None:
        obs = session.observability
        obs.start(wall_time=self._wall_time, monotonic=self._monotonic)
        obs.model_provider = getattr(self.llm, "provider_name", "")
        obs.model_name = getattr(self.llm, "model", "")
        obs.tool_calling_protocol = getattr(self.llm, "tool_calling_protocol", "")
        obs.temperature = getattr(self.llm, "temperature", None)
        self.llm.on_provider_invocation = obs.note_provider_invocation  # type: ignore[attr-defined]
        self.sync_llm_counts(session)

    def finish_observability(self, session: TaskSession) -> None:
        self.sync_llm_counts(session)
        if session.observability.finished_at is None:
            session.observability.finish(
                wall_time=self._wall_time, monotonic=self._monotonic
            )

    def record_call_usage(self, session: TaskSession, usage: TokenUsage | None) -> None:
        session.observability.record_call_usage(usage)
        self.sync_llm_counts(session)

    def sync_llm_counts(self, session: TaskSession) -> None:
        obs = session.observability
        if hasattr(self.llm, "logical_calls"):
            obs.logical_calls = int(getattr(self.llm, "logical_calls") or 0)
        if hasattr(self.llm, "transport_attempts"):
            obs.transport_attempts = int(getattr(self.llm, "transport_attempts") or 0)

    def docker_config_payload(self) -> dict:
        config = getattr(self.runner, "last_run_config", None)
        if config is None:
            return {}
        return {"docker_run_config": config.to_dict()}

    def write_artifacts(self, session: TaskSession, snapshot_root: Path) -> None:
        artifacts = session.artifacts_dir
        artifacts.mkdir(parents=True, exist_ok=True)
        diff = current_diff_from_snapshot(snapshot_root, session.workspace_root)
        write_unified_diff(artifacts / "final.diff", diff)
        if diff:
            session.changed_files = sorted(
                set(session.changed_files) | set(changed_files_from_diff(diff))
            )
        summary = session.to_summary()
        if session.last_error and session.status in {
            SessionStatus.ERROR,
            SessionStatus.TEST_ENVIRONMENT_ERROR,
            SessionStatus.TEST_TIMEOUT,
            SessionStatus.MODEL_OUTPUT_INVALID,
            SessionStatus.READ_BUDGET_EXHAUSTED,
            SessionStatus.PATCH_NOT_APPLICABLE,
            SessionStatus.PATCH_BASE_CHANGED,
        }:
            summary["error"] = sanitize_text(session.last_error)
        (artifacts / "summary.json").write_text(
            json.dumps(summary, indent=2, ensure_ascii=False),
            encoding="utf-8",
            newline="\n",
        )

    def finalize(
        self,
        session: TaskSession,
        trace: TraceRecorder,
        snapshot_root: Path,
        say: Callable[[str], None],
    ) -> None:
        self.finish_observability(session)
        self.write_artifacts(session, snapshot_root)
        trace.emit(
            "session_finished",
            status=session.status.value,
            summary=session.to_summary(),
        )
        say(f"Session finished: {session.status.value}")
        say(f"Artifacts: {session.artifacts_dir}")
