"""Exact unified-diff hunk parsing and matching.

Shared by PatchPreflight and PatchApplier. No fuzzy matching.
"""

from __future__ import annotations

import re
from typing import Any

HUNK_HEADER = re.compile(
    r"^@@ -(?P<old_start>\d+)(?:,(?P<old_count>\d+))? "
    r"\+(?P<new_start>\d+)(?:,(?P<new_count>\d+))? @@"
)


class HunkMatchError(Exception):
    """Exact hunk match failure with structured fields for preflight feedback."""

    def __init__(
        self,
        *,
        error_kind: str,
        target_file: str,
        hunk_index: int,
        line_no: int | None,
        first_unmatched_context: str,
        detail: str,
        actual_context: str = "",
        candidate_lines: list[int] | None = None,
    ) -> None:
        super().__init__(detail)
        self.error_kind = error_kind
        self.target_file = target_file
        self.hunk_index = hunk_index
        self.line_no = line_no
        self.first_unmatched_context = first_unmatched_context
        self.actual_context = actual_context
        self.candidate_lines = list(candidate_lines or [])
        self.detail = detail

    def __str__(self) -> str:
        return self.detail


def split_file_diffs(diff_text: str) -> list[tuple[str, str]]:
    lines = diff_text.splitlines()
    chunks: list[tuple[str, list[str]]] = []
    current_file: str | None = None
    current_lines: list[str] = []
    pending_old: str | None = None

    def flush() -> None:
        nonlocal current_file, current_lines
        if current_file is not None:
            chunks.append((current_file, current_lines))
        current_file = None
        current_lines = []

    for line in lines:
        if line.startswith("diff --git "):
            flush()
            continue
        if line.startswith("--- "):
            pending_old = line
            continue
        if line.startswith("+++ "):
            flush()
            new = line[4:].strip()
            if new.startswith("b/"):
                new = new[2:]
            current_file = new
            current_lines = []
            if pending_old:
                current_lines.append(pending_old)
                pending_old = None
            current_lines.append(line)
            continue
        if current_file is not None:
            current_lines.append(line)

    flush()
    return [(path, "\n".join(body) + "\n") for path, body in chunks if path != "/dev/null"]


def is_new_file(body: str) -> bool:
    for line in body.splitlines():
        if line.startswith("--- "):
            return line.strip().endswith("/dev/null")
    return False


def content_from_new_file_diff(body: str) -> str:
    out: list[str] = []
    in_hunk = False
    for line in body.splitlines():
        if line.startswith("@@"):
            in_hunk = True
            continue
        if not in_hunk:
            continue
        if line.startswith("+"):
            out.append(line[1:])
        elif line.startswith("\\"):
            continue
        elif line.startswith("-"):
            raise RuntimeError("New file diff unexpectedly contains deletions")
        elif line.startswith(" "):
            out.append(line[1:])
    return "\n".join(out) + ("\n" if out else "")


def detect_newline(text: str) -> str:
    if "\r\n" in text:
        return "\r\n"
    if "\r" in text:
        return "\r"
    return "\n"


def parse_hunks(body: str) -> list[dict[str, Any]]:
    hunks: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    for line in body.splitlines():
        m = HUNK_HEADER.match(line)
        if m:
            if current:
                hunks.append(current)
            current = {
                "old_start": int(m.group("old_start")),
                "old_count": int(m.group("old_count") or "1"),
                "new_start": int(m.group("new_start")),
                "new_count": int(m.group("new_count") or "1"),
                "lines": [],
            }
            continue
        if current is None:
            continue
        if line.startswith("+") and not line.startswith("+++"):
            current["lines"].append(("+", line[1:]))
        elif line.startswith("-") and not line.startswith("---"):
            current["lines"].append(("-", line[1:]))
        elif line.startswith(" "):
            current["lines"].append((" ", line[1:]))
        elif line.startswith("\\"):
            continue
    if current:
        hunks.append(current)
    return hunks


def _bare_lines(lines: list[str]) -> list[str]:
    return [line.rstrip("\n\r") for line in lines]


def _hunk_old_lines(hunk: dict[str, Any]) -> list[str]:
    return [content.rstrip("\n\r") for tag, content in hunk["lines"] if tag != "+"]


def _exact_block_matches(source: list[str], start: int, expected: list[str]) -> bool:
    if start < 0 or start + len(expected) > len(source):
        return False
    return _bare_lines(source[start : start + len(expected)]) == expected


def _exact_block_candidates(source: list[str], expected: list[str]) -> list[int]:
    if not expected:
        return []
    last_start = len(source) - len(expected)
    return [
        start
        for start in range(last_start + 1)
        if _exact_block_matches(source, start, expected)
    ]


def _first_hunk_mismatch(
    source: list[str], hunk: dict[str, Any], requested_start: int
) -> tuple[str, int, str, str]:
    source_index = max(requested_start, 0)
    for tag, content in hunk["lines"]:
        if tag == "+":
            continue
        expected = content.rstrip("\n\r")
        actual = (
            source[source_index].rstrip("\n\r")
            if 0 <= source_index < len(source)
            else "<EOF>"
        )
        if actual != expected:
            kind = "deletion_mismatch" if tag == "-" else "context_mismatch"
            return kind, source_index + 1, expected, actual
        source_index += 1
    return "context_mismatch", requested_start + 1, "", ""


def apply_hunks_to_text(
    original: str,
    body: str,
    file_path: str,
    *,
    relocations: list[dict[str, int]] | None = None,
) -> str:
    """Apply hunks with exact matching and safe unique-block relocation."""

    def split_keep(text: str) -> list[str]:
        if text == "":
            return []
        return text.splitlines(keepends=True)

    source = split_keep(original)
    result: list[str] = []
    src_index = 0  # 0-based

    hunks = parse_hunks(body)
    if not hunks:
        raise HunkMatchError(
            error_kind="no_hunks",
            target_file=file_path,
            hunk_index=-1,
            line_no=None,
            first_unmatched_context="",
            detail=f"No hunks found for {file_path}",
        )

    for hunk_index, hunk in enumerate(hunks):
        requested_start = hunk["old_start"] - 1  # 0-based
        expected_old = _hunk_old_lines(hunk)
        old_start = requested_start

        if expected_old and not _exact_block_matches(source, requested_start, expected_old):
            candidates = _exact_block_candidates(source, expected_old)
            usable = [candidate for candidate in candidates if candidate >= src_index]
            if len(candidates) == 1 and len(usable) == 1:
                old_start = usable[0]
                if relocations is not None:
                    relocations.append(
                        {
                            "hunk_index": hunk_index,
                            "requested_line": requested_start + 1,
                            "applied_line": old_start + 1,
                        }
                    )
            else:
                kind, line_no, expected, actual = _first_hunk_mismatch(
                    source, hunk, requested_start
                )
                if len(candidates) > 1:
                    kind = "ambiguous_context"
                    detail = (
                        f"Exact hunk context for {file_path} appears at multiple lines: "
                        f"{', '.join(str(line + 1) for line in candidates)}"
                    )
                else:
                    detail = (
                        f"{kind.replace('_', ' ').title()} in {file_path} at line "
                        f"{line_no}: expected {expected!r}, got {actual!r}"
                    )
                raise HunkMatchError(
                    error_kind=kind,
                    target_file=file_path,
                    hunk_index=hunk_index,
                    line_no=line_no,
                    first_unmatched_context=expected,
                    actual_context=actual,
                    candidate_lines=[line + 1 for line in candidates],
                    detail=detail,
                )

        if old_start < src_index:
            raise HunkMatchError(
                error_kind="overlapping_hunk",
                target_file=file_path,
                hunk_index=hunk_index,
                line_no=old_start + 1,
                first_unmatched_context="",
                detail=f"Overlapping/out-of-order hunk in {file_path}",
            )

        result.extend(source[src_index:old_start])
        src_index = old_start

        for tag, content in hunk["lines"]:
            if tag == " ":
                if src_index >= len(source):
                    raise HunkMatchError(
                        error_kind="context_mismatch",
                        target_file=file_path,
                        hunk_index=hunk_index,
                        line_no=src_index + 1,
                        first_unmatched_context=content,
                        detail=f"Context mismatch EOF in {file_path}",
                        actual_context="<EOF>",
                    )
                current = source[src_index].rstrip("\n\r")
                expected = content.rstrip("\n\r")
                if current != expected:
                    raise HunkMatchError(
                        error_kind="context_mismatch",
                        target_file=file_path,
                        hunk_index=hunk_index,
                        line_no=src_index + 1,
                        first_unmatched_context=content,
                        actual_context=current,
                        detail=(
                            f"Context mismatch in {file_path} at line {src_index + 1}: "
                            f"expected {content!r}, got {current!r}"
                        ),
                    )
                result.append(source[src_index])
                src_index += 1
            elif tag == "-":
                if src_index >= len(source):
                    raise HunkMatchError(
                        error_kind="deletion_mismatch",
                        target_file=file_path,
                        hunk_index=hunk_index,
                        line_no=src_index + 1,
                        first_unmatched_context=content,
                        detail=f"Deletion mismatch EOF in {file_path}",
                        actual_context="<EOF>",
                    )
                current = source[src_index].rstrip("\n\r")
                expected = content.rstrip("\n\r")
                if current != expected:
                    raise HunkMatchError(
                        error_kind="deletion_mismatch",
                        target_file=file_path,
                        hunk_index=hunk_index,
                        line_no=src_index + 1,
                        first_unmatched_context=content,
                        actual_context=current,
                        detail=(
                            f"Deletion mismatch in {file_path} at line {src_index + 1}"
                        ),
                    )
                src_index += 1
            elif tag == "+":
                newline = detect_newline(original)
                bare = content.rstrip("\n\r")
                result.append(bare + newline)

    result.extend(source[src_index:])
    return "".join(result)
