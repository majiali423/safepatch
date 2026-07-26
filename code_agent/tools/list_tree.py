from __future__ import annotations

from pathlib import Path

from code_agent.repository.workspace import IGNORE_DIR_NAMES, WorkspaceError, safe_resolve


def list_tree(workspace_root: Path, path: str = ".", *, max_entries: int = 200) -> str:
    target = safe_resolve(workspace_root, path or ".")
    if not target.exists():
        raise WorkspaceError(f"Path not found: {path}")
    if not target.is_dir():
        raise WorkspaceError(f"Not a directory: {path}")

    lines: list[str] = []
    count = 0
    root = workspace_root.resolve()

    for item in sorted(target.rglob("*")):
        if any(part in IGNORE_DIR_NAMES for part in item.parts):
            continue
        rel = item.resolve().relative_to(root).as_posix()
        suffix = "/" if item.is_dir() else ""
        lines.append(rel + suffix)
        count += 1
        if count >= max_entries:
            lines.append(f"... truncated at {max_entries} entries")
            break
    return "\n".join(lines) if lines else "(empty)"
