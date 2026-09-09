"""Apply validated unified diffs to working_copy (with rollback on failure)."""

from __future__ import annotations

import os
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path

from code_agent.patching.hashes import file_revision
from code_agent.patching.hunk_engine import (
    HunkMatchError,
    apply_hunks_to_text,
    content_from_new_file_diff,
    is_new_file,
    split_file_diffs,
)
from code_agent.patching.validator import validate_proposal
from code_agent.state import PatchProposal

# Re-export engine symbols for older internal imports (test_integrity, etc.).
_split_file_diffs = split_file_diffs
_apply_hunks = apply_hunks_to_text
_is_new_file = is_new_file
_content_from_new_file_diff = content_from_new_file_diff

# Recoverable exact-match failures → patch regeneration budget.
RECOVERABLE_APPLY_ERROR_KINDS = frozenset(
    {
        "context_mismatch",
        "deletion_mismatch",
        "no_hunks",
        "overlapping_hunk",
        "ambiguous_context",
        "stale_file_revision",
    }
)


@dataclass
class ApplyResult:
    ok: bool
    error: str | None = None
    files: list[str] | None = None
    error_kind: str | None = None
    target_file: str | None = None
    rollback_succeeded: bool | None = None
    relocations: list[dict[str, int | str]] | None = None
    line_no: int | None = None


class PatchApplier:
    """Applies a validated unified diff to working_copy (with rollback on failure)."""

    @staticmethod
    def apply(proposal: PatchProposal, workspace_root: Path) -> ApplyResult:
        return apply_proposal(proposal, workspace_root)


def classify_apply_error_kind(
    *,
    error_kind: str | None,
    error: str | None,
) -> str:
    """Normalize apply failure kind for controller branching."""
    if error_kind:
        return error_kind
    text = (error or "").lower()
    if "permissionerror" in text or "permission denied" in text:
        return "permission_error"
    if "timeout" in text and "io" in text:
        return "io_error"
    if any(
        token in text
        for token in (
            "errno",
            "oserror",
            "ioerror",
            "file exists",
            "no space",
            "disk",
            "readonly",
            "read-only",
        )
    ):
        return "io_error"
    if "base_changed" in text or "working tree" in text and "changed" in text:
        return "base_changed"
    if any(
        token in text
        for token in (
            "context mismatch",
            "deletion mismatch",
            "no hunks",
            "overlapping",
        )
    ):
        # Prefer specific kinds when the message embeds them.
        if "deletion mismatch" in text:
            return "deletion_mismatch"
        if "no hunks" in text:
            return "no_hunks"
        if "overlapping" in text:
            return "overlapping_hunk"
        return "context_mismatch"
    return "internal_error"


def is_recoverable_apply_error(error_kind: str) -> bool:
    return error_kind in RECOVERABLE_APPLY_ERROR_KINDS


def apply_proposal(
    proposal: PatchProposal,
    workspace_root: Path,
    *,
    allow_test_changes: bool = False,
    allow_new_tests: bool = True,
    collected_test_files: set[str] | None = None,
) -> ApplyResult:
    validation = validate_proposal(
        proposal,
        workspace_root,
        allow_test_changes=allow_test_changes,
        allow_new_tests=allow_new_tests,
        collected_test_files=collected_test_files,
    )
    if not validation.ok:
        return ApplyResult(
            ok=False,
            error="; ".join(validation.errors),
            error_kind="internal_error",
            target_file=None,
            rollback_succeeded=True,  # nothing written
        )

    # Backup touched existing files for rollback. Register create intent before write.
    backups: dict[Path, str | None] = {}
    created: list[Path] = []
    created_dirs: list[Path] = []
    pre_state: dict[Path, str | None] = {}
    current_file: str | None = None
    relocations: list[dict[str, int | str]] = []

    try:
        file_diffs = split_file_diffs(proposal.unified_diff)
        for file_path, body in file_diffs:
            if is_new_file(body):
                continue
            expected_revision = proposal.base_revisions.get(file_path)
            if expected_revision is None:
                continue
            target = (workspace_root / file_path).resolve()
            if not target.is_file():
                continue
            current_revision = file_revision(target)
            if current_revision != expected_revision:
                return ApplyResult(
                    ok=False,
                    error=(
                        f"STALE_FILE_REVISION for {file_path}: expected "
                        f"{expected_revision}, current {current_revision}"
                    ),
                    error_kind="stale_file_revision",
                    target_file=file_path,
                    rollback_succeeded=True,
                )
        for file_path, body in file_diffs:
            current_file = file_path
            target = (workspace_root / file_path).resolve()
            target.relative_to(workspace_root.resolve())

            if is_new_file(body):
                if target.exists():
                    raise RuntimeError(
                        f"Refusing to overwrite existing file: {file_path}"
                    )
                content = content_from_new_file_diff(body)
                pre_state[target] = None
                created.append(target)
                _ensure_parents(target, workspace_root.resolve(), created_dirs)
                _write_text_raw(target, content)
            else:
                if not target.exists():
                    raise RuntimeError(f"Missing file for patch: {file_path}")
                original = _read_text_raw(target)
                pre_state[target] = original
                backups[target] = original
                file_relocations: list[dict[str, int]] = []
                patched = apply_hunks_to_text(
                    original,
                    body,
                    file_path,
                    relocations=file_relocations,
                )
                relocations.extend(
                    {"file": file_path, **relocation}
                    for relocation in file_relocations
                )
                _write_text_raw(target, patched)

        return ApplyResult(ok=True, files=validation.files, relocations=relocations)
    except Exception as exc:  # noqa: BLE001
        rollback_ok = _safe_rollback(backups, created, created_dirs, pre_state)
        kind, target, line_no = _classify_exception(exc, current_file)
        return ApplyResult(
            ok=False,
            error=str(exc),
            error_kind=kind,
            target_file=target,
            rollback_succeeded=rollback_ok,
            line_no=line_no,
        )


def _classify_exception(
    exc: BaseException, current_file: str | None
) -> tuple[str, str | None, int | None]:
    if isinstance(exc, HunkMatchError):
        return exc.error_kind, exc.target_file or current_file, exc.line_no
    if isinstance(exc, PermissionError):
        return "permission_error", current_file, None
    if isinstance(exc, OSError):
        return "io_error", current_file, None
    return "internal_error", current_file, None


def _read_text_raw(path: Path) -> str:
    """Read text without newline translation (keeps CRLF on Windows)."""
    with path.open("r", encoding="utf-8", newline="") as fh:
        return fh.read()


def _write_text_raw(path: Path, content: str) -> None:
    """Write text without newline translation.

    Uses an exclusively created same-directory temp file plus ``os.replace``.
    Existing sibling files, including leftover ``*.safepatch.tmp`` names, are
    left untouched. Only the temp file created by this call is cleaned up.
    """
    original_mode = path.stat().st_mode if path.exists() else None
    fd, tmp_name = tempfile.mkstemp(
        prefix=f".{path.name}.safepatch.",
        suffix=".tmp",
        dir=str(path.parent),
    )
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as fh:
            fh.write(content)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
        if original_mode is not None:
            try:
                os.chmod(path, original_mode)
            except OSError:
                pass
    except Exception:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        raise


def _ensure_parents(
    path: Path, workspace_root: Path, created: list[Path] | None = None
) -> list[Path]:
    created = created if created is not None else []
    missing: list[Path] = []
    cursor = path.parent
    while cursor != workspace_root and not cursor.exists():
        missing.append(cursor)
        cursor = cursor.parent
        if len(cursor.parts) < len(workspace_root.parts):
            break
    for directory in reversed(missing):
        directory.mkdir(parents=False, exist_ok=True)
        created.append(directory)
    return created


def _rollback(
    backups: dict[Path, str | None],
    created: list[Path],
    created_dirs: list[Path] | None = None,
) -> None:
    errors: list[BaseException] = []
    for path, content in backups.items():
        try:
            if content is None:
                if path.exists():
                    path.unlink()
            else:
                _write_text_raw(path, content)
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)
    for path in created:
        try:
            if path.exists():
                path.unlink()
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)
    for directory in reversed(created_dirs or []):
        try:
            if directory.exists() and directory.is_dir() and not any(directory.iterdir()):
                shutil.rmtree(directory, ignore_errors=True)
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)
    if errors:
        raise errors[0]


def _snapshot_matches(pre_state: dict[Path, str | None]) -> bool:
    for path, content in pre_state.items():
        if content is None:
            if path.exists():
                return False
            continue
        if not path.exists() or _read_text_raw(path) != content:
            return False
    return True


def _safe_rollback(
    backups: dict[Path, str | None],
    created: list[Path],
    created_dirs: list[Path] | None = None,
    pre_state: dict[Path, str | None] | None = None,
) -> bool:
    try:
        _rollback(backups, created, created_dirs)
    except Exception:  # noqa: BLE001
        return False
    if pre_state is not None and not _snapshot_matches(pre_state):
        return False
    return True
