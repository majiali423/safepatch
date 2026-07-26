from __future__ import annotations

import re
import shutil
from dataclasses import dataclass
from pathlib import Path

from code_agent.patching.validator import validate_proposal
from code_agent.state import PatchProposal

HUNK_HEADER = re.compile(
    r"^@@ -(?P<old_start>\d+)(?:,(?P<old_count>\d+))? "
    r"\+(?P<new_start>\d+)(?:,(?P<new_count>\d+))? @@"
)


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
        file_diffs = _split_file_diffs(proposal.unified_diff)
        for file_path, body in file_diffs:
            target = (workspace_root / file_path).resolve()
            target.relative_to(workspace_root.resolve())

            is_new = _is_new_file(body)
            if is_new:
                if target.exists():
                    raise RuntimeError(f"Refusing to overwrite existing file: {file_path}")
                content = _content_from_new_file_diff(body)
                target.parent.mkdir(parents=True, exist_ok=True)
                _write_text_raw(target, content)
                created.append(target)
            else:
                if not target.exists():
                    raise RuntimeError(f"Missing file for patch: {file_path}")
                original = _read_text_raw(target)
                backups[target] = original
                patched = _apply_hunks(original, body, file_path)
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


def _split_file_diffs(diff_text: str) -> list[tuple[str, str]]:
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


def _is_new_file(body: str) -> bool:
    for line in body.splitlines():
        if line.startswith("--- "):
            return line.strip().endswith("/dev/null")
    return False


def _content_from_new_file_diff(body: str) -> str:
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


def _detect_newline(text: str) -> str:
    if "\r\n" in text:
        return "\r\n"
    if "\r" in text:
        return "\r"
    return "\n"


def _apply_hunks(original: str, body: str, file_path: str) -> str:
    def split_keep(text: str) -> list[str]:
        if text == "":
            return []
        return text.splitlines(keepends=True)

    source = split_keep(original)
    result: list[str] = []
    src_index = 0  # 0-based

    hunks = _parse_hunks(body)
    if not hunks:
        raise RuntimeError(f"No hunks found for {file_path}")

    for hunk in hunks:
        old_start = hunk["old_start"] - 1  # 0-based
        if old_start < src_index:
            raise RuntimeError(f"Overlapping/out-of-order hunk in {file_path}")

        # copy unchanged prefix
        result.extend(source[src_index:old_start])
        src_index = old_start

        for tag, content in hunk["lines"]:
            if tag == " ":
                if src_index >= len(source):
                    raise RuntimeError(f"Context mismatch EOF in {file_path}")
                current = source[src_index].rstrip("\n\r")
                if current != content.rstrip("\n\r"):
                    raise RuntimeError(
                        f"Context mismatch in {file_path} at line {src_index + 1}: "
                        f"expected {content!r}, got {current!r}"
                    )
                result.append(source[src_index])
                src_index += 1
            elif tag == "-":
                if src_index >= len(source):
                    raise RuntimeError(f"Deletion mismatch EOF in {file_path}")
                current = source[src_index].rstrip("\n\r")
                if current != content.rstrip("\n\r"):
                    raise RuntimeError(
                        f"Deletion mismatch in {file_path} at line {src_index + 1}"
                    )
                src_index += 1
            elif tag == "+":
                newline = _detect_newline(original)
                bare = content.rstrip("\n\r")
                result.append(bare + newline)

    result.extend(source[src_index:])
    return "".join(result)


def _parse_hunks(body: str) -> list[dict]:
    hunks: list[dict] = []
    current: dict | None = None
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
