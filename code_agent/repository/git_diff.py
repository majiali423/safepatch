from __future__ import annotations

import difflib
from pathlib import Path

from code_agent.repository.workspace import IGNORE_DIR_NAMES, rel_posix


def _iter_text_files(root: Path) -> list[Path]:
    files: list[Path] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        if any(part in IGNORE_DIR_NAMES for part in path.parts):
            continue
        if path.suffix.lower() not in {".py", ".txt", ".md", ".toml", ".cfg", ".ini"}:
            if path.suffix.lower() != ".py":
                continue
        files.append(path)
    return files


def _logical_lines(path: Path) -> list[str]:
    """Read file as newline-agnostic logical lines (no terminators).

    splitlines() without keepends treats LF, CRLF, and CR uniformly, so
    difflib output joined with '\\n' cannot grow blank-line noise from '\\r'.
    """
    return path.read_text(encoding="utf-8").splitlines()


def unified_diff_between(original_root: Path, current_root: Path) -> str:
    """Generate a unified diff between two directory trees (py files)."""
    orig_files = {
        rel_posix(original_root, p): p for p in _iter_text_files(original_root)
    }
    curr_files = {
        rel_posix(current_root, p): p for p in _iter_text_files(current_root)
    }
    all_keys = sorted(set(orig_files) | set(curr_files))
    chunks: list[str] = []

    for key in all_keys:
        left = orig_files.get(key)
        right = curr_files.get(key)
        left_lines = _logical_lines(left) if left else []
        right_lines = _logical_lines(right) if right else []
        if left_lines == right_lines:
            continue
        # lineterm="" => difflib yields bare lines; we own the final LF join.
        diff_lines = list(
            difflib.unified_diff(
                left_lines,
                right_lines,
                fromfile=f"a/{key}",
                tofile=f"b/{key}",
                lineterm="",
            )
        )
        if diff_lines:
            chunks.append("\n".join(diff_lines))

    if not chunks:
        return ""
    return "\n".join(chunks) + "\n"


def write_unified_diff(path: Path, diff_text: str) -> None:
    """Write final.diff with LF newlines only (no Windows translation)."""
    normalized = diff_text.replace("\r\n", "\n").replace("\r", "\n")
    path.write_text(normalized, encoding="utf-8", newline="\n")


def snapshot_tree(src: Path, dest: Path) -> None:
    """Copy py-focused tree for baseline comparison."""
    import shutil

    if dest.exists():
        shutil.rmtree(dest)
    shutil.copytree(
        src,
        dest,
        ignore=shutil.ignore_patterns(*IGNORE_DIR_NAMES),
    )


def current_diff_from_snapshot(snapshot_root: Path, workspace_root: Path) -> str:
    return unified_diff_between(snapshot_root, workspace_root)


def changed_files_from_diff(diff_text: str) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for line in diff_text.splitlines():
        if line.startswith("+++ b/"):
            path = line[6:]
            if path != "/dev/null" and path not in seen:
                seen.add(path)
                ordered.append(path)
    return ordered
