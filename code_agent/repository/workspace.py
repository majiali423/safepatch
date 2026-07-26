from __future__ import annotations

import shutil
import uuid
from dataclasses import dataclass
from pathlib import Path

IGNORE_DIR_NAMES = {
    ".git",
    ".venv",
    "venv",
    "__pycache__",
    "node_modules",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    "dist",
    "build",
    ".eggs",
    ".tox",
    ".idea",
    ".vscode",
}

MAX_FILES = 500
MAX_TOTAL_BYTES = 5 * 1024 * 1024  # 5 MB


class WorkspaceError(ValueError):
    """Raised when workspace import or path safety checks fail."""


@dataclass
class ImportedWorkspace:
    session_id: str
    session_dir: Path
    workspace_root: Path
    artifacts_dir: Path
    file_count: int
    total_bytes: int


def create_session_dir(base_dir: Path | None = None) -> Path:
    root = Path(base_dir or Path.cwd() / ".code_agent_sessions")
    root.mkdir(parents=True, exist_ok=True)
    session_id = uuid.uuid4().hex[:12]
    session_dir = root / session_id
    session_dir.mkdir(parents=False, exist_ok=False)
    return session_dir


def import_repository(
    source_repo: Path,
    session_base: Path | None = None,
) -> ImportedWorkspace:
    source = Path(source_repo).expanduser().resolve()
    if not source.exists() or not source.is_dir():
        raise WorkspaceError(f"Repository path does not exist: {source}")

    session_dir = create_session_dir(session_base)
    workspace_root = session_dir / "working_copy"
    artifacts_dir = session_dir / "artifacts"
    artifacts_dir.mkdir(parents=True, exist_ok=True)

    file_count = 0
    total_bytes = 0

    def _ignore(dir_path: str, names: list[str]) -> set[str]:
        ignored: set[str] = set()
        for name in names:
            full = Path(dir_path) / name
            if name in IGNORE_DIR_NAMES:
                ignored.add(name)
                continue
            # Never copy symlinks: following them could pull host files into the copy.
            if full.is_symlink():
                ignored.add(name)
        return ignored

    shutil.copytree(source, workspace_root, ignore=_ignore, symlinks=False)

    for path in workspace_root.rglob("*"):
        if path.is_symlink():
            # Defense in depth: drop any symlink that still appears.
            path.unlink(missing_ok=True)
            continue
        if path.is_file():
            file_count += 1
            total_bytes += path.stat().st_size
            if file_count > MAX_FILES:
                shutil.rmtree(session_dir, ignore_errors=True)
                raise WorkspaceError(
                    f"Repository exceeds max file count ({MAX_FILES})"
                )
            if total_bytes > MAX_TOTAL_BYTES:
                shutil.rmtree(session_dir, ignore_errors=True)
                raise WorkspaceError(
                    f"Repository exceeds max size ({MAX_TOTAL_BYTES} bytes)"
                )

    return ImportedWorkspace(
        session_id=session_dir.name,
        session_dir=session_dir,
        workspace_root=workspace_root,
        artifacts_dir=artifacts_dir,
        file_count=file_count,
        total_bytes=total_bytes,
    )


def safe_resolve(workspace_root: Path, user_path: str) -> Path:
    """Resolve a user-provided path strictly inside workspace_root.

    Rejects absolute paths, '..' segments, and symlink escapes.
    """
    if not user_path or user_path.strip() == "":
        raise WorkspaceError("Empty path is not allowed")

    raw = user_path.strip().replace("\\", "/")
    if raw.startswith("/") or (len(raw) > 1 and raw[1] == ":"):
        raise WorkspaceError("Absolute paths are not allowed")
    if ".." in Path(raw).parts:
        raise WorkspaceError("Path traversal ('..') is not allowed")

    root = workspace_root.resolve()
    # Walk components without following the final symlink until checked.
    cursor = root
    for part in Path(raw).parts:
        if part in ("", "."):
            continue
        cursor = cursor / part
        if cursor.is_symlink():
            resolved = cursor.resolve()
            try:
                resolved.relative_to(root)
            except ValueError as exc:
                raise WorkspaceError(
                    "Symlink escapes workspace root"
                ) from exc
        if not cursor.exists():
            # Allow resolving not-yet-existing paths for tooling, still rooted.
            candidate = (root / raw).resolve()
            try:
                candidate.relative_to(root)
            except ValueError as exc:
                raise WorkspaceError("Path escapes workspace root") from exc
            return candidate

    candidate = cursor.resolve()
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise WorkspaceError("Path escapes workspace root") from exc
    return candidate


def list_py_files(workspace_root: Path) -> list[Path]:
    root = workspace_root.resolve()
    files: list[Path] = []
    for path in sorted(root.rglob("*.py")):
        if any(part in IGNORE_DIR_NAMES for part in path.parts):
            continue
        if path.is_symlink():
            continue
        try:
            path.resolve().relative_to(root)
        except ValueError:
            continue
        files.append(path)
    return files


def rel_posix(workspace_root: Path, path: Path) -> str:
    return path.resolve().relative_to(workspace_root.resolve()).as_posix()
