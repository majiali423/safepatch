from __future__ import annotations

from pathlib import Path

from code_agent.patching.hashes import file_revision
from code_agent.repository.workspace import WorkspaceError, safe_resolve

MAX_LINES_PER_READ = 120


def read_file(
    workspace_root: Path,
    path: str,
    start_line: int = 1,
    end_line: int | None = None,
) -> str:
    target = safe_resolve(workspace_root, path)
    if not target.exists() or not target.is_file():
        raise WorkspaceError(f"File not found: {path}")
    if target.suffix != ".py":
        raise WorkspaceError("Only .py files can be read in v1")

    try:
        lines = target.read_text(encoding="utf-8").splitlines()
    except UnicodeDecodeError as exc:
        raise WorkspaceError("File is not valid UTF-8") from exc

    if start_line < 1:
        raise WorkspaceError("start_line must be >= 1")
    if end_line is None:
        end_line = min(len(lines), start_line + MAX_LINES_PER_READ - 1)
    if end_line < start_line:
        raise WorkspaceError("end_line must be >= start_line")
    if end_line - start_line + 1 > MAX_LINES_PER_READ:
        raise WorkspaceError(
            f"Cannot read more than {MAX_LINES_PER_READ} lines at once"
        )

    end_line = min(end_line, len(lines))
    chunk = lines[start_line - 1 : end_line]
    numbered = [f"{i:>4}|{line}" for i, line in enumerate(chunk, start=start_line)]
    header = (
        f"# {path} lines {start_line}-{end_line} of {len(lines)} "
        f"revision={file_revision(target)}"
    )
    return header + "\n" + "\n".join(numbered)
