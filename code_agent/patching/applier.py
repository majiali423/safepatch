"""Apply validated unified diffs to working_copy (with rollback on failure)."""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path

from code_agent.patching.hunk_engine import (
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


@dataclass
class ApplyResult:
    ok: bool
    error: str | None = None
    files: list[str] | None = None


class PatchApplier:
    """Applies a validated unified diff to working_copy (with rollback on failure)."""

    @staticmethod
    def apply(proposal: PatchProposal, workspace_root: Path) -> ApplyResult:
        return apply_proposal(proposal, workspace_root)


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
        return ApplyResult(ok=False, error="; ".join(validation.errors))

    # Backup touched existing files for rollback
    backups: dict[Path, str | None] = {}
    created: list[Path] = []

    try:
        file_diffs = split_file_diffs(proposal.unified_diff)
        for file_path, body in file_diffs:
            target = (workspace_root / file_path).resolve()
            target.relative_to(workspace_root.resolve())

            if is_new_file(body):
                if target.exists():
                    raise RuntimeError(f"Refusing to overwrite existing file: {file_path}")
                content = content_from_new_file_diff(body)
                target.parent.mkdir(parents=True, exist_ok=True)
                _write_text_raw(target, content)
                created.append(target)
            else:
                if not target.exists():
                    raise RuntimeError(f"Missing file for patch: {file_path}")
                original = _read_text_raw(target)
                backups[target] = original
                patched = apply_hunks_to_text(original, body, file_path)
                _write_text_raw(target, patched)

        return ApplyResult(ok=True, files=validation.files)
    except Exception as exc:  # noqa: BLE001
        _rollback(backups, created)
        return ApplyResult(ok=False, error=str(exc))


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
