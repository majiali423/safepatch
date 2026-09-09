from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Callable

from code_agent.repository.git_diff import current_diff_from_snapshot
from code_agent.state import AnalysisPhase, TaskSession
from code_agent.tools.list_tree import list_tree
from code_agent.tools.read_file import read_file_result
from code_agent.tools.request_evidence import (
    normalized_path,
    record_read_result,
)
from code_agent.tools.search_symbol import search_symbol
from code_agent.tools.search_text import search_text


class ToolErrorKind(str, Enum):
    UNKNOWN_OR_FORBIDDEN = "unknown_or_forbidden_tool"
    EXECUTION_ERROR = "tool_execution_error"


class ToolError(RuntimeError):
    def __init__(self, message: str, *, error_kind: ToolErrorKind) -> None:
        super().__init__(message)
        self.error_kind = error_kind


class ReadBudgetExceeded(RuntimeError):
    """The model requested a general read after its read budget reached zero."""


READ_BUDGET_WARNING_THRESHOLD = 3


READ_TOOLS = {"list_tree", "read_file", "search_text", "search_symbol", "get_repo_map"}
AVAILABLE_TOOLS = (
    "list_tree", "read_file", "search_text", "search_symbol", "get_repo_map",
    "get_current_diff", "request_evidence", "propose_patch", "propose_edit", "finish",
)


def _budget_feedback(session: TaskSession) -> str:
    remaining = max(
        session.max_read_actions - session.exploration_read_actions_used,
        0,
    )
    lines = [
        (
            "READ_BUDGET: "
            f"used={session.exploration_read_actions_used}/"
            f"{session.max_read_actions}, remaining={remaining}"
        )
    ]
    if remaining == 0:
        lines.extend(
            [
                "ANALYSIS_PHASE: SYNTHESIZE. Free exploration is complete.",
                "Choose one next action:",
                "1. Submit propose_edit using content and revisions already returned.",
                "2. Submit propose_patch using context already returned.",
                "3. Submit request_evidence for one explicit missing code range.",
                "4. Call finish and explain why no safe patch can be proposed.",
                "Do not call general read/search/tree/map tools in SYNTHESIZE.",
                "A read explicitly required by PATCH_PREFLIGHT_FAILED remains allowed.",
            ]
        )
    elif remaining <= READ_BUDGET_WARNING_THRESHOLD:
        lines.append(
            "READ_BUDGET_WARNING: Exploration is nearly complete. Use remaining reads "
            "for essential context and prepare to synthesize a proposal or finish."
        )
    return "\n".join(lines)


@dataclass
class ToolRegistry:
    session: TaskSession
    snapshot_root: Path

    def available_tools(self) -> list[str]:
        return list(AVAILABLE_TOOLS)


def execute_tool(
    registry: ToolRegistry,
    name: str,
    arguments: dict[str, Any],
) -> tuple[str, bool]:
    """
    Execute a tool.

    Returns (result_text, is_terminal_action).
    propose_patch and finish are terminal for the analysis loop.
    """
    session = registry.session
    root = session.workspace_root

    if name in READ_TOOLS:
        required_recovery_read = (
            name == "read_file"
            and normalized_path(arguments.get("path", "")) in session.required_reads
        )
        if required_recovery_read:
            session.required_recovery_reads_used += 1
        else:
            if session.analysis_phase != AnalysisPhase.EXPLORE:
                raise ReadBudgetExceeded(
                    "READ_BUDGET_EXCEEDED: General inspection tools are disabled in "
                    "SYNTHESIZE. Choose propose_edit, propose_patch, a structured "
                    "request_evidence, or finish."
                )
            if session.exploration_read_actions_used >= session.max_read_actions:
                raise ReadBudgetExceeded(
                    "READ_BUDGET_EXCEEDED: The fixed exploration budget is exhausted. "
                    "Enter SYNTHESIZE and choose propose_edit, propose_patch, a "
                    "structured request_evidence, or finish."
                )
            session.exploration_read_actions_used += 1
        session.read_actions_used += 1

    handlers: dict[str, Callable[[], str]] = {
        "list_tree": lambda: list_tree(root, str(arguments.get("path", "."))),
        "read_file": lambda: read_file_result(
            root,
            str(arguments.get("path", "")),
            int(arguments.get("start_line", 1)),
            int(arguments["end_line"]) if arguments.get("end_line") is not None else None,
        ),
        "search_text": lambda: search_text(root, str(arguments.get("query", ""))),
        "search_symbol": lambda: search_symbol(
            root, str(arguments.get("symbol", ""))
        ),
        "get_repo_map": lambda: session.repo_map_text or "(empty repo map)",
        "get_current_diff": lambda: current_diff_from_snapshot(
            registry.snapshot_root, root
        )
        or "(no changes yet)",
    }

    if name in handlers:
        try:
            result = handlers[name]()
            if name in READ_TOOLS:
                if name == "read_file":
                    record_read_result(session, result, source="read_file")
                    result = result.format_text()
                result = f"{result}\n\n{_budget_feedback(session)}"
            return result, False
        except Exception as exc:  # noqa: BLE001 - surface tool errors to model
            detail = str(exc)
            if name in READ_TOOLS:
                detail = f"{detail}\n\n{_budget_feedback(session)}"
            raise ToolError(detail, error_kind=ToolErrorKind.EXECUTION_ERROR) from exc

    if name in {"propose_patch", "propose_edit"}:
        # Controller handles validation; return marker payload.
        return "__PROPOSE_CHANGE__", True

    if name == "finish":
        reason = str(arguments.get("reason", "finished"))
        return f"__FINISH__:{reason}", True

    raise ToolError(
        f"Unknown or forbidden tool: {name}",
        error_kind=ToolErrorKind.UNKNOWN_OR_FORBIDDEN,
    )
