from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from code_agent.repository.git_diff import current_diff_from_snapshot
from code_agent.state import TaskSession
from code_agent.tools.list_tree import list_tree
from code_agent.tools.read_file import read_file
from code_agent.tools.search_symbol import search_symbol
from code_agent.tools.search_text import search_text


class ToolError(RuntimeError):
    pass


READ_TOOLS = {"list_tree", "read_file", "search_text", "search_symbol", "get_repo_map"}


@dataclass
class ToolRegistry:
    session: TaskSession
    snapshot_root: Path

    def available_tools(self) -> list[str]:
        return [
            "list_tree",
            "read_file",
            "search_text",
            "search_symbol",
            "get_repo_map",
            "get_current_diff",
            "propose_patch",
            "finish",
        ]


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
        if session.read_actions_used >= session.max_read_actions:
            raise ToolError(
                f"Read action limit reached ({session.max_read_actions})"
            )
        session.read_actions_used += 1

    handlers: dict[str, Callable[[], str]] = {
        "list_tree": lambda: list_tree(root, str(arguments.get("path", "."))),
        "read_file": lambda: read_file(
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
            return handlers[name](), False
        except Exception as exc:  # noqa: BLE001 - surface tool errors to model
            raise ToolError(str(exc)) from exc

    if name == "propose_patch":
        # Controller handles validation; return marker payload.
        return "__PROPOSE_PATCH__", True

    if name == "finish":
        reason = str(arguments.get("reason", "finished"))
        return f"__FINISH__:{reason}", True

    raise ToolError(f"Unknown or forbidden tool: {name}")
