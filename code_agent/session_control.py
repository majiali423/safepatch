"""Shared session deadline, regeneration budget, and required-read tracking.

TaskSession remains the only store for counters. These helpers mutate that
session; AnalysisLoop and RepairExecutor must not keep parallel copies.
"""

from __future__ import annotations

from collections.abc import Callable

from code_agent.patching.preflight import PreflightFailure
from code_agent.state import SessionStatus, TaskSession
from code_agent.tracing.recorder import TraceRecorder

MessageFn = Callable[[str], None]


class SessionDeadline:
    def __init__(
        self,
        *,
        monotonic: Callable[[], float],
        session_deadline_seconds: float | None,
    ) -> None:
        self._monotonic = monotonic
        self.session_deadline_seconds = session_deadline_seconds
        self._session_mono_start: float | None = None

    def bind_start(self, start: float | None) -> None:
        self._session_mono_start = start

    def remaining_deadline_seconds(self) -> float | None:
        if self.session_deadline_seconds is None or self._session_mono_start is None:
            return None
        return self.session_deadline_seconds - (self._monotonic() - self._session_mono_start)

    def exceeded(self) -> bool:
        remaining = self.remaining_deadline_seconds()
        return remaining is not None and remaining <= 0

    def abort_if_deadline(self, session: TaskSession, stage: str) -> bool:
        if not self.exceeded():
            return False
        session.status = SessionStatus.ERROR
        session.stop_reason = "session_deadline"
        session.last_error = (
            f"SESSION_DEADLINE: {stage} exceeded the session time budget "
            "(approval wait counts toward the budget)"
        )
        return True


class RegenerationBudget:
    def exhausted(self, session: TaskSession) -> bool:
        return (
            session.consecutive_patch_regeneration_retries
            >= session.max_patch_regeneration_retries
        )

    def consume(self, session: TaskSession, trace: TraceRecorder, *, reason: str) -> None:
        session.consecutive_patch_regeneration_retries += 1
        session.total_patch_regeneration_retries += 1
        trace.emit(
            "patch_regeneration_requested",
            retry=session.consecutive_patch_regeneration_retries,
            max_patch_regeneration_retries=session.max_patch_regeneration_retries,
            total_patch_regeneration_retries=session.total_patch_regeneration_retries,
            reason=reason[:2000],
        )


class RequiredReadTracker:
    @staticmethod
    def normalize_tool_path(path: object) -> str:
        return str(path).replace("\\", "/").removeprefix("./")

    @staticmethod
    def payload(session: TaskSession) -> list[dict[str, int | str]]:
        return [
            {"path": path, "start_line": bounds[0], "end_line": bounds[1]}
            for path, bounds in sorted(session.required_reads.items())
        ]

    def feedback(self, session: TaskSession) -> str:
        requests = self.payload(session)
        lines = [
            "REQUIRED_READ_MISSING: the previous patch used stale file context.",
            "Before proposing any new patch, perform every required read below:",
        ]
        lines.extend(
            f"- read_file(path={item['path']!r}, start_line={item['start_line']}, "
            f"end_line={item['end_line']})"
            for item in requests
        )
        lines.append(
            "A search result or a read of a different range does not satisfy this gate."
        )
        return "\n".join(lines)

    def require_failure_region_read(
        self,
        session: TaskSession,
        trace: TraceRecorder,
        failure: PreflightFailure,
    ) -> None:
        if (
            not failure.target_file
            or failure.suggested_read_start is None
            or failure.suggested_read_end is None
        ):
            return
        path = self.normalize_tool_path(failure.target_file)
        self.register(
            session,
            trace,
            path=path,
            start_line=failure.suggested_read_start,
            end_line=failure.suggested_read_end,
            error_kind=failure.error_kind,
        )

    def require_apply_failure_region_read(
        self,
        session: TaskSession,
        trace: TraceRecorder,
        target_file: str | None,
        line_no: int | None,
        error_kind: str,
    ) -> None:
        if not target_file:
            return
        path = self.normalize_tool_path(target_file)
        target = (session.workspace_root / path).resolve()
        try:
            target.relative_to(session.workspace_root.resolve())
        except ValueError:
            return
        if not target.is_file():
            return
        try:
            line_count = max(len(target.read_text(encoding="utf-8").splitlines()), 1)
        except (OSError, UnicodeDecodeError):
            return
        center = min(max(line_no or 1, 1), line_count)
        self.register(
            session,
            trace,
            path=path,
            start_line=max(1, center - 8),
            end_line=min(line_count, center + 8),
            error_kind=error_kind,
        )

    @staticmethod
    def register(
        session: TaskSession,
        trace: TraceRecorder,
        *,
        path: str,
        start_line: int,
        end_line: int,
        error_kind: str,
    ) -> None:
        session.required_reads[path] = (start_line, end_line)
        trace.emit(
            "required_read_registered",
            path=path,
            start_line=start_line,
            end_line=end_line,
            error_kind=error_kind,
        )

    def record_if_satisfied(
        self,
        session: TaskSession,
        trace: TraceRecorder,
        arguments: dict[str, object],
    ) -> None:
        path = self.normalize_tool_path(arguments.get("path", ""))
        required = session.required_reads.get(path)
        if required is None:
            return
        try:
            raw_start = arguments.get("start_line", 1)
            raw_end = arguments.get("end_line")
            if not isinstance(raw_start, (int, str)):
                return
            start = int(raw_start)
            if raw_end is None:
                end = start + 119
            elif isinstance(raw_end, (int, str)):
                end = int(raw_end)
            else:
                return
        except (TypeError, ValueError):
            return
        if start <= required[0] and end >= required[1]:
            del session.required_reads[path]
            trace.emit(
                "required_read_satisfied",
                path=path,
                requested_start_line=start,
                requested_end_line=end,
                required_start_line=required[0],
                required_end_line=required[1],
            )
