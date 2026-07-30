"""Apply validated unified diffs to working_copy (with rollback on failure)."""

from __future__ import annotations

import shutil
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
) -> ApplyResult:
    validation = validate_proposal(
        proposal,
        workspace_root,
        allow_test_changes=allow_test_changes,
        allow_new_tests=allow_new_tests,
    )
    if not validation.ok:
        return ApplyResult(
            ok=False,
            error="; ".join(validation.errors),
            error_kind="internal_error",
            target_file=None,
            rollback_succeeded=True,  # nothing written
        )

    # Backup touched existing files for rollback
    backups: dict[Path, str | None] = {}
    created: list[Path] = []
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
                target.parent.mkdir(parents=True, exist_ok=True)
                _write_text_raw(target, content)
                created.append(target)
            else:
                if not target.exists():
                    raise RuntimeError(f"Missing file for patch: {file_path}")
                original = _read_text_raw(target)
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
        rollback_ok = _safe_rollback(backups, created)
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
    """Write text without newline translation."""
    with path.open("w", encoding="utf-8", newline="") as fh:
        fh.write(content)


def _rollback(backups: dict[Path, str | None], created: list[Path]) -> None:
    for path, content in backups.items():
        if content is None:
            if path.exists():
                path.unlink()
        else:
            _write_text_raw(path, content)
    for path in created:
        if path.exists():
            path.unlink()
            # clean empty parents lightly
            parent = path.parent
            if parent.exists() and not any(parent.iterdir()):
                shutil.rmtree(parent, ignore_errors=True)


def _safe_rollback(backups: dict[Path, str | None], created: list[Path]) -> bool:
    try:
        _rollback(backups, created)
        return True
    except Exception:  # noqa: BLE001
        return False
