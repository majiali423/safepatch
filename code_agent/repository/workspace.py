from __future__ import annotations

import os
import shutil
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from code_agent.repository.import_policy import (
    IGNORE_DIR_NAMES,
    ImportFilter,
    resolve_session_base,
    validate_session_location,
)

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
    excluded_counts: dict[str, int] = field(default_factory=dict)


def create_session_dir(base_dir: Path | None = None) -> Path:
    root = resolve_session_base(base_dir)
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

    planned_base = resolve_session_base(session_base)
    try:
        validate_session_location(source, planned_base)
    except ValueError as exc:
        raise WorkspaceError(str(exc)) from exc

    # Exclude the session root even before it exists so in-repo defaults are safe.
    try:
        session_root = planned_base.resolve()
    except OSError:
        session_root = planned_base
    import_filter = ImportFilter(source=source, session_root=session_root)

    file_count, total_bytes = _measure_importable_tree(source, import_filter)

    session_dir = create_session_dir(session_base)
    workspace_root = session_dir / "working_copy"
    artifacts_dir = session_dir / "artifacts"
    artifacts_dir.mkdir(parents=True, exist_ok=True)

    # Also exclude the concrete session directory created under the source tree.
    import_filter.extra_exclude_roots = (session_dir.resolve(),)
    destination = workspace_root.resolve()
    import_filter.extra_exclude_roots = (session_dir.resolve(), destination)

    def _ignore(dir_path: str, names: list[str]) -> set[str]:
        directory = Path(dir_path)
        ignored: set[str] = set()
        try:
            if directory.resolve() == destination:
                # Never walk into the copy destination if it is inside the source.
                return set(names)
        except OSError:
            pass
        for name in names:
            full = directory / name
            if full.is_symlink():
                import_filter.note("symlink")
                ignored.add(name)
                continue
            if import_filter.should_ignore_name(directory, name):
                ignored.add(name)
        return ignored

    shutil.copytree(source, workspace_root, ignore=_ignore, symlinks=False)

    copied_file_count = 0
    copied_total_bytes = 0
    for path in workspace_root.rglob("*"):
        if path.is_symlink():
            path.unlink(missing_ok=True)
            continue
        if not path.is_file():
            continue
        rel = path.relative_to(workspace_root)
        source_equiv = source / rel
        decision = import_filter.decide_file(source_equiv if source_equiv.exists() else path)
        if not decision.include:
            path.unlink(missing_ok=True)
            import_filter.note(decision.category or "excluded")
            continue
        copied_file_count += 1
        copied_total_bytes += path.stat().st_size
        if copied_file_count > MAX_FILES:
            shutil.rmtree(session_dir, ignore_errors=True)
            raise WorkspaceError(f"Repository exceeds max file count ({MAX_FILES})")
        if copied_total_bytes > MAX_TOTAL_BYTES:
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
        excluded_counts=dict(import_filter.excluded_counts),
    )


def _measure_importable_tree(source: Path, import_filter: ImportFilter) -> tuple[int, int]:
    file_count = 0
    total_bytes = 0

    for root_text, dir_names, file_names in os.walk(source, topdown=True, followlinks=False):
        root = Path(root_text)
        keep_dirs: list[str] = []
        for name in dir_names:
            path = root / name
            if path.is_symlink() or import_filter.should_ignore_name(root, name):
                continue
            keep_dirs.append(name)
        dir_names[:] = keep_dirs
        for name in file_names:
            path = root / name
            if path.is_symlink() or not path.is_file():
                continue
            if import_filter.should_ignore_name(root, name):
                continue
            file_count += 1
            total_bytes += path.stat().st_size
            if file_count > MAX_FILES:
                raise WorkspaceError(f"Repository exceeds max file count ({MAX_FILES})")
            if total_bytes > MAX_TOTAL_BYTES:
                raise WorkspaceError(
                    f"Repository exceeds max size ({MAX_TOTAL_BYTES} bytes)"
                )

    return file_count, total_bytes


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
                raise WorkspaceError("Symlink escapes workspace root") from exc
        if not cursor.exists():
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
