from __future__ import annotations

from enum import Enum
from typing import Any

from code_agent.evidence import EvidenceItem
from code_agent.repository.workspace import WorkspaceError
from code_agent.state import AnalysisPhase, TaskSession
from code_agent.tools.read_file import MAX_LINES_PER_READ, read_file_result


class EvidenceErrorKind(str, Enum):
    PARAMETER = "parameter_error"
    NO_PROGRESS = "no_progress"
    HARD_VIOLATION = "hard_violation"


class EvidenceRequestError(RuntimeError):
    def __init__(self, message: str, *, kind: EvidenceErrorKind) -> None:
        super().__init__(message)
        self.kind = kind


def normalized_path(path: object) -> str:
    return str(path).replace("\\", "/").removeprefix("./")


def record_read_result(session: TaskSession, result: Any, *, source: str) -> None:
    if getattr(result, "empty", False):
        return
    start = int(result.start_line)
    end = int(result.end_line)
    if end < start:
        return
    session.evidence.record(
        EvidenceItem(
            path=normalized_path(result.path),
            revision=str(result.revision),
            start_line=start,
            end_line=end,
            content=result.content,
            source=source,
            total_lines=int(result.total_lines),
            complete=not bool(getattr(result, "truncated", False)),
        )
    )


def record_successful_read_range(
    session: TaskSession,
    arguments: dict[str, Any],
    *,
    actual_start: int | None = None,
    actual_end: int | None = None,
    revision: str | None = None,
    content: str = "",
    total_lines: int = 0,
    source: str = "read_file",
) -> None:
    path = normalized_path(arguments.get("path", ""))
    try:
        start_line = int(
            actual_start if actual_start is not None else arguments.get("start_line", 1)
        )
        if actual_end is not None:
            end_line = int(actual_end)
        else:
            raw_end = arguments.get("end_line")
            end_line = int(raw_end) if raw_end is not None else start_line
    except (TypeError, ValueError):
        return
    if not path or start_line < 1 or end_line < start_line:
        return
    session.evidence.record(
        EvidenceItem(
            path=path,
            revision=revision or "",
            start_line=start_line,
            end_line=end_line,
            content=content,
            source=source,
            total_lines=total_lines,
        )
    )


def execute_request_evidence(
    session: TaskSession,
    arguments: dict[str, Any],
) -> str:
    if session.analysis_phase != AnalysisPhase.SYNTHESIZE:
        raise EvidenceRequestError(
            "EVIDENCE_HARD_VIOLATION: request_evidence is available only in the "
            "SYNTHESIZE phase. Use normal inspection tools during EXPLORE.",
            kind=EvidenceErrorKind.HARD_VIOLATION,
        )
    if session.evidence_requests_used >= session.max_evidence_requests:
        raise EvidenceRequestError(
            "EVIDENCE_HARD_VIOLATION: The structured evidence allowance is "
            "exhausted. Submit propose_edit, propose_patch, or finish.",
            kind=EvidenceErrorKind.HARD_VIOLATION,
        )

    path = normalized_path(arguments.get("path", ""))
    unanswered = str(arguments.get("unanswered_requirement", "")).strip()
    reason = str(arguments.get("reason", "")).strip()
    try:
        start_line = int(arguments["start_line"])
        end_line = int(arguments["end_line"])
    except (KeyError, TypeError, ValueError) as exc:
        raise EvidenceRequestError(
            "EVIDENCE_PARAMETER_ERROR: path, integer start_line, integer end_line, "
            "unanswered_requirement, and reason are required.",
            kind=EvidenceErrorKind.PARAMETER,
        ) from exc

    if not path or not unanswered or not reason:
        raise EvidenceRequestError(
            "EVIDENCE_PARAMETER_ERROR: path, unanswered_requirement, and reason "
            "must be non-empty.",
            kind=EvidenceErrorKind.PARAMETER,
        )
    if start_line < 1 or end_line < start_line:
        raise EvidenceRequestError(
            "EVIDENCE_PARAMETER_ERROR: Require 1 <= start_line <= end_line.",
            kind=EvidenceErrorKind.PARAMETER,
        )
    line_count = end_line - start_line + 1
    if line_count > MAX_LINES_PER_READ:
        corrected_end = start_line + MAX_LINES_PER_READ - 1
        raise EvidenceRequestError(
            "EVIDENCE_PARAMETER_ERROR: A request may contain at most "
            f"{MAX_LINES_PER_READ} lines; requested {line_count}. Retry with "
            f"end_line <= {corrected_end}.",
            kind=EvidenceErrorKind.PARAMETER,
        )

    try:
        result = read_file_result(
            session.workspace_root,
            path,
            start_line,
            end_line,
        )
    except WorkspaceError as exc:
        raise EvidenceRequestError(
            f"EVIDENCE_PARAMETER_ERROR: {exc}",
            kind=EvidenceErrorKind.PARAMETER,
        ) from exc

    if session.evidence.covers(path, start_line, end_line, result.revision):
        raise EvidenceRequestError(
            "EVIDENCE_NO_PROGRESS: The requested range is already fully covered "
            "by an earlier successful read. Use existing evidence, request a "
            "different missing range, propose a change, or finish.",
            kind=EvidenceErrorKind.NO_PROGRESS,
        )

    session.evidence_requests_used += 1
    session.read_actions_used += 1
    record_read_result(session, result, source="request_evidence")
    remaining = session.max_evidence_requests - session.evidence_requests_used
    return (
        f"{result.format_text()}\n\n"
        "EVIDENCE_ACCEPTED: Return to SYNTHESIZE. Do not resume free exploration.\n"
        f"unanswered_requirement={unanswered}\n"
        f"evidence_requests_remaining={remaining}\n"
        "Next choose propose_edit, propose_patch, another justified "
        "request_evidence if allowance remains, or finish."
    )
