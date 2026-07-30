"""Read-only patch preflight using the same exact hunk matcher as PatchApplier."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from code_agent.patching.hashes import file_revision, patch_hash, working_tree_hash
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
    relocations: list[dict[str, int | str]] | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class PreflightFailure:
    ok: bool = False
    error_kind: str = "unknown"
    target_file: str = ""
    hunk_index: int = -1
    first_unmatched_context: str = ""
    actual_context: str = ""
    candidate_lines: list[int] | None = None
    requested_line: int | None = None
    suggested_read_start: int | None = None
    suggested_read_end: int | None = None
    file_excerpt: str = ""
    patch_hash: str = ""
    working_tree_hash: str = ""
    detail: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def feedback_message(self) -> str:
        read_range = (
            f"{self.suggested_read_start}-{self.suggested_read_end}"
            if self.suggested_read_start is not None
            and self.suggested_read_end is not None
            else "the relevant region"
        )
        return (
            "PATCH_PREFLIGHT_FAILED: unified diff cannot be applied to the current file.\n"
            f"error_kind={self.error_kind}\n"
            f"target_file={self.target_file}\n"
            f"hunk_index={self.hunk_index}\n"
            f"requested_line={self.requested_line}\n"
            f"first_unmatched_context={self.first_unmatched_context!r}\n"
            f"actual_content={self.actual_context!r}\n"
            f"exact_match_candidate_lines={self.candidate_lines or []}\n"
            f"patch_hash={self.patch_hash}\n"
            f"working_tree_hash={self.working_tree_hash}\n"
            f"detail={self.detail}\n"
            "current_file_excerpt:\n"
            f"{self.file_excerpt}\n"
            f"Required next action: call read_file(path={self.target_file!r}, "
            f"start_line={self.suggested_read_start}, end_line={self.suggested_read_end}) "
            f"to refresh lines {read_range}. Then build a new patch from the returned "
            "content. Do not reuse stale line numbers or invent context lines."
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


def _suggested_read_range(path: Path, line_no: int | None) -> tuple[int | None, int | None]:
    if not path.is_file() or line_no is None:
        return None, None
    line_count = len(_read_text_raw(path).splitlines())
    if line_count == 0:
        return 1, 1
    return max(1, line_no - EXCERPT_RADIUS), min(line_count, line_no + EXCERPT_RADIUS)


class PatchPreflight:
    """Simulate exact apply in memory; never writes the working tree."""

    @staticmethod
    def run(proposal: PatchProposal, workspace_root: Path) -> PreflightResult:
        p_hash = patch_hash(proposal.unified_diff)
        wt_hash = working_tree_hash(workspace_root)
        root = workspace_root.resolve()
        files: list[str] = []
        relocations: list[dict[str, int | str]] = []

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
                    expected_revision = proposal.base_revisions.get(file_path)
                    if expected_revision is not None:
                        current_revision = file_revision(target)
                        if current_revision != expected_revision:
                            read_end = min(
                                max(len(_read_text_raw(target).splitlines()), 1),
                                120,
                            )
                            return PreflightFailure(
                                error_kind="stale_file_revision",
                                target_file=file_path,
                                hunk_index=-1,
                                first_unmatched_context="",
                                actual_context="",
                                requested_line=1,
                                suggested_read_start=1,
                                suggested_read_end=read_end,
                                file_excerpt=_file_excerpt(target, 1),
                                patch_hash=p_hash,
                                working_tree_hash=wt_hash,
                                detail=(
                                    f"STALE_FILE_REVISION for {file_path}: expected "
                                    f"{expected_revision}, current {current_revision}"
                                ),
                            )
                    original = _read_text_raw(target)
                    file_relocations: list[dict[str, int]] = []
                    apply_hunks_to_text(
                        original,
                        body,
                        file_path,
                        relocations=file_relocations,
                    )
                    relocations.extend(
                        {"file": file_path, **relocation}
                        for relocation in file_relocations
                    )

            return PreflightSuccess(
                patch_hash=p_hash,
                working_tree_hash=wt_hash,
                files=files,
                relocations=relocations,
            )
        except HunkMatchError as exc:
            target = root / exc.target_file
            read_start, read_end = _suggested_read_range(target, exc.line_no)
            return PreflightFailure(
                error_kind=exc.error_kind,
                target_file=exc.target_file,
                hunk_index=exc.hunk_index,
                first_unmatched_context=exc.first_unmatched_context,
                actual_context=exc.actual_context,
                candidate_lines=exc.candidate_lines,
                requested_line=exc.line_no,
                suggested_read_start=read_start,
                suggested_read_end=read_end,
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
