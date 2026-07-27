"""Read-only patch preflight using the same exact hunk matcher as PatchApplier."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from code_agent.patching.hashes import patch_hash, working_tree_hash
from code_agent.patching.hunk_engine import (
    HunkMatchError,
    apply_hunks_to_text,
    content_from_new_file_diff,
    is_new_file,
    split_file_diffs,
)
from code_agent.state import PatchProposal

EXCERPT_RADIUS = 8
EXCERPT_MAX_CHARS = 4000


@dataclass
class PreflightSuccess:
    ok: bool = True
    patch_hash: str = ""
    working_tree_hash: str = ""
    files: list[str] | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class PreflightFailure:
    ok: bool = False
    error_kind: str = "unknown"
    target_file: str = ""
    hunk_index: int = -1
    first_unmatched_context: str = ""
    file_excerpt: str = ""
    patch_hash: str = ""
    working_tree_hash: str = ""
    detail: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def feedback_message(self) -> str:
        return (
            "PATCH_PREFLIGHT_FAILED: unified diff cannot be applied exactly.\n"
            f"error_kind={self.error_kind}\n"
            f"target_file={self.target_file}\n"
            f"hunk_index={self.hunk_index}\n"
            f"first_unmatched_context={self.first_unmatched_context!r}\n"
            f"patch_hash={self.patch_hash}\n"
            f"working_tree_hash={self.working_tree_hash}\n"
            f"detail={self.detail}\n"
            "file_excerpt:\n"
            f"{self.file_excerpt}\n"
            "You MUST call read_file on the target file, then propose_patch again "
            "with a corrected unified diff that matches the current file contents. "
            "Do not invent context lines."
        )


PreflightResult = PreflightSuccess | PreflightFailure


def _read_text_raw(path: Path) -> str:
    with path.open("r", encoding="utf-8", newline="") as fh:
        return fh.read()


def _file_excerpt(path: Path, line_no: int | None) -> str:
    if not path.is_file():
        return "(file missing)"
    text = _read_text_raw(path)
    lines = text.splitlines()
    if not lines:
        return "(empty file)"
    if line_no is None:
        start = 0
        end = min(len(lines), EXCERPT_RADIUS * 2)
    else:
        idx = max(line_no - 1, 0)
        start = max(0, idx - EXCERPT_RADIUS)
        end = min(len(lines), idx + EXCERPT_RADIUS + 1)
    numbered = [f"{i + 1:>4}|{lines[i]}" for i in range(start, end)]
    excerpt = "\n".join(numbered)
    if len(excerpt) > EXCERPT_MAX_CHARS:
        excerpt = excerpt[: EXCERPT_MAX_CHARS] + "\n... (truncated)"
    return excerpt


class PatchPreflight:
    """Simulate exact apply in memory; never writes the working tree."""

    @staticmethod
    def run(proposal: PatchProposal, workspace_root: Path) -> PreflightResult:
        p_hash = patch_hash(proposal.unified_diff)
        wt_hash = working_tree_hash(workspace_root)
        root = workspace_root.resolve()
        files: list[str] = []

        try:
            file_diffs = split_file_diffs(proposal.unified_diff)
            if not file_diffs:
                return PreflightFailure(
                    error_kind="empty_diff",
                    target_file="",
                    hunk_index=-1,
                    first_unmatched_context="",
                    file_excerpt="",
                    patch_hash=p_hash,
                    working_tree_hash=wt_hash,
                    detail="No file hunks found in unified_diff",
                )

            for file_path, body in file_diffs:
                target = (root / file_path).resolve()
                target.relative_to(root)
                files.append(file_path)

                if is_new_file(body):
                    if target.exists():
                        return PreflightFailure(
                            error_kind="refuse_overwrite",
                            target_file=file_path,
                            hunk_index=-1,
                            first_unmatched_context="",
                            file_excerpt=_file_excerpt(target, 1),
                            patch_hash=p_hash,
                            working_tree_hash=wt_hash,
                            detail=f"Refusing to overwrite existing file: {file_path}",
                        )
                    # Validate new-file body parses; discard result.
                    content_from_new_file_diff(body)
                else:
                    if not target.exists():
                        return PreflightFailure(
                            error_kind="missing_file",
                            target_file=file_path,
                            hunk_index=-1,
                            first_unmatched_context="",
                            file_excerpt="(file missing)",
                            patch_hash=p_hash,
                            working_tree_hash=wt_hash,
                            detail=f"Missing file for patch: {file_path}",
                        )
                    original = _read_text_raw(target)
                    apply_hunks_to_text(original, body, file_path)

            return PreflightSuccess(
                patch_hash=p_hash,
                working_tree_hash=wt_hash,
                files=files,
            )
        except HunkMatchError as exc:
            target = root / exc.target_file
            return PreflightFailure(
                error_kind=exc.error_kind,
                target_file=exc.target_file,
                hunk_index=exc.hunk_index,
                first_unmatched_context=exc.first_unmatched_context,
                file_excerpt=_file_excerpt(target, exc.line_no),
                patch_hash=p_hash,
                working_tree_hash=wt_hash,
                detail=exc.detail,
            )
        except Exception as exc:  # noqa: BLE001
            return PreflightFailure(
                error_kind="preflight_error",
                target_file="",
                hunk_index=-1,
                first_unmatched_context="",
                file_excerpt="",
                patch_hash=p_hash,
                working_tree_hash=wt_hash,
                detail=str(exc),
            )
