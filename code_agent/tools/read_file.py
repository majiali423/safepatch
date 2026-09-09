from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from code_agent.patching.hashes import file_revision
from code_agent.repository.workspace import WorkspaceError, safe_resolve

MAX_LINES_PER_READ = 120
MAX_LINE_CHARS = 4000


@dataclass(frozen=True)
class ReadResult:
    path: str
    start_line: int
    end_line: int
    total_lines: int
    revision: str
    lines: tuple[str, ...]
    truncated: bool = False
    empty: bool = False

    @property
    def content(self) -> str:
        numbered = [
            f"{i:>4}|{line}"
            for i, line in enumerate(self.lines, start=self.start_line)
        ]
        return "\n".join(numbered)

    def format_text(self) -> str:
        header = (
            f"# {self.path} lines {self.start_line}-{self.end_line} of "
            f"{self.total_lines} revision={self.revision}"
        )
        if self.empty:
            return header + "\n"
        body = self.content
        if self.truncated:
            return header + "\n" + body + "\n# truncated: line length exceeded cap"
        return header + "\n" + body


def read_file_result(
    workspace_root: Path,
    path: str,
    start_line: int = 1,
    end_line: int | None = None,
) -> ReadResult:
    target = safe_resolve(workspace_root, path)
    if not target.exists() or not target.is_file():
        raise WorkspaceError(f"File not found: {path}")
    if target.suffix != ".py":
        raise WorkspaceError("Only .py files can be read in v1")

    try:
        raw_lines = target.read_text(encoding="utf-8").splitlines()
    except UnicodeDecodeError as exc:
        raise WorkspaceError("File is not valid UTF-8") from exc

    if start_line < 1:
        raise WorkspaceError("start_line must be >= 1")
    total = len(raw_lines)
    if total == 0:
        return ReadResult(
            path=path.replace("\\", "/").removeprefix("./"),
            start_line=1,
            end_line=0,
            total_lines=0,
            revision=file_revision(target),
            lines=(),
            empty=True,
        )

    if end_line is None:
        actual_end = min(total, start_line + MAX_LINES_PER_READ - 1)
    else:
        if end_line < start_line:
            raise WorkspaceError("end_line must be >= start_line")
        if end_line - start_line + 1 > MAX_LINES_PER_READ:
            raise WorkspaceError(
                f"Cannot read more than {MAX_LINES_PER_READ} lines at once"
            )
        actual_end = min(end_line, total)
    if start_line > total:
        return ReadResult(
            path=path.replace("\\", "/").removeprefix("./"),
            start_line=start_line,
            end_line=start_line - 1,
            total_lines=total,
            revision=file_revision(target),
            lines=(),
            empty=True,
        )

    chunk = list(raw_lines[start_line - 1 : actual_end])
    truncated = False
    clipped: list[str] = []
    for line in chunk:
        if len(line) > MAX_LINE_CHARS:
            clipped.append(line[:MAX_LINE_CHARS])
            truncated = True
        else:
            clipped.append(line)
    return ReadResult(
        path=path.replace("\\", "/").removeprefix("./"),
        start_line=start_line,
        end_line=actual_end,
        total_lines=total,
        revision=file_revision(target),
        lines=tuple(clipped),
        truncated=truncated,
    )


def read_file(
    workspace_root: Path,
    path: str,
    start_line: int = 1,
    end_line: int | None = None,
) -> str:
    return read_file_result(workspace_root, path, start_line, end_line).format_text()
