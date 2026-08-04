"""Build ordinary PatchProposal objects from exact old_text/new_text edits."""

from __future__ import annotations

import difflib
import re
from pathlib import Path
from typing import Any

from code_agent.llm import FormatErrorKind, ModelOutputError
from code_agent.patching.hashes import file_revision
from code_agent.repository.workspace import WorkspaceError, safe_resolve
from code_agent.state import PatchProposal

REQUIRED_METADATA = (
    "diagnosis",
    "edits",
    "expected_behavior",
    "risk_notes",
    "tests_to_run",
)


class StructuredEditError(RuntimeError):
    """A schema-valid exact replacement is unsafe or cannot be located."""


def _read_text_raw(path: Path) -> str:
    with path.open("r", encoding="utf-8", newline="") as handle:
        return handle.read()


def _with_file_newlines(text: str, file_content: str) -> str:
    newline = "\r\n" if "\r\n" in file_content else "\r" if "\r" in file_content else "\n"
    return text.replace("\r\n", "\n").replace("\r", "\n").replace("\n", newline)


def _exact_occurrence_count(text: str, needle: str) -> int:
    """Count start positions, including overlaps; stop once ambiguity is known."""
    count = 0
    start = 0
    while True:
        found = text.find(needle, start)
        if found < 0:
            return count
        count += 1
        if count > 1:
            return count
        start = found + 1


def _schema_error(message: str, raw_text: str) -> ModelOutputError:
    return ModelOutputError(
        message,
        error_kind=FormatErrorKind.INVALID_PROPOSAL_SCHEMA,
        raw_text=raw_text,
    )


def _validate_args(data: dict[str, Any], raw_text: str) -> list[dict[str, str]]:
    missing = [field for field in REQUIRED_METADATA if data.get(field) in (None, "")]
    if missing:
        raise _schema_error(
            f"propose_edit missing fields: {', '.join(missing)}",
            raw_text,
        )
    if not isinstance(data["edits"], list) or not data["edits"]:
        raise _schema_error("edits must be a non-empty list", raw_text)
    if not isinstance(data["tests_to_run"], list):
        raise _schema_error("tests_to_run must be a list", raw_text)

    normalized: list[dict[str, str]] = []
    for index, edit in enumerate(data["edits"]):
        if not isinstance(edit, dict):
            raise _schema_error(f"edits[{index}] must be an object", raw_text)
        missing_edit = [
            field
            for field in ("path", "old_text", "new_text", "base_revision")
            if field not in edit
        ]
        if missing_edit:
            raise _schema_error(
                f"edits[{index}] missing fields: {', '.join(missing_edit)}",
                raw_text,
            )
        if not all(
            isinstance(edit[field], str)
            for field in ("path", "old_text", "new_text", "base_revision")
        ):
            raise _schema_error(
                f"edits[{index}] path, old_text, new_text, and base_revision must be strings",
                raw_text,
            )
        path = edit["path"].replace("\\", "/").removeprefix("./")
        old_text = edit["old_text"]
        new_text = edit["new_text"]
        base_revision = edit["base_revision"]
        if not path:
            raise _schema_error(f"edits[{index}].path must not be empty", raw_text)
        if not old_text:
            raise _schema_error(f"edits[{index}].old_text must not be empty", raw_text)
        if old_text == new_text:
            raise _schema_error(
                f"edits[{index}] old_text and new_text must differ",
                raw_text,
            )
        if re.fullmatch(r"sha256:[0-9a-f]{64}", base_revision) is None:
            raise _schema_error(
                f"edits[{index}].base_revision must be a full sha256 revision from read_file",
                raw_text,
            )
        normalized.append(
            {
                "path": path,
                "old_text": old_text,
                "new_text": new_text,
                "base_revision": base_revision,
            }
        )
    return normalized


def build_structured_proposal(
    data: dict[str, Any],
    workspace_root: Path,
    *,
    raw_text: str = "",
) -> PatchProposal:
    """Turn uniquely matching exact replacements into a standard unified diff."""
    if not isinstance(data, dict):
        raise _schema_error("propose_edit args must be an object", raw_text)
    edits = _validate_args(data, raw_text)

    originals: dict[str, str] = {}
    modified: dict[str, str] = {}
    base_revisions: dict[str, str] = {}
    for index, edit in enumerate(edits):
        path = edit["path"]
        try:
            target = safe_resolve(workspace_root, path)
        except WorkspaceError as exc:
            raise StructuredEditError(f"edits[{index}] unsafe path {path!r}: {exc}") from exc
        if not target.is_file():
            raise StructuredEditError(f"edits[{index}] target file does not exist: {path}")
        if target.suffix != ".py":
            raise StructuredEditError(f"edits[{index}] only .py files may be edited: {path}")

        current_revision = file_revision(target)
        expected_revision = edit["base_revision"]
        if current_revision != expected_revision:
            raise StructuredEditError(
                f"edits[{index}] STALE_FILE_REVISION for {path}: "
                f"expected {expected_revision}, current {current_revision}; "
                "call read_file again before editing"
            )
        previous_revision = base_revisions.get(path)
        if previous_revision is not None and previous_revision != expected_revision:
            raise StructuredEditError(
                f"edits[{index}] inconsistent base_revision values for {path}"
            )
        base_revisions[path] = expected_revision

        if path not in originals:
            originals[path] = _read_text_raw(target)
            modified[path] = originals[path]
        current = modified[path]
        old_text = _with_file_newlines(edit["old_text"], current)
        new_text = _with_file_newlines(edit["new_text"], current)
        occurrences = _exact_occurrence_count(current, old_text)
        if occurrences == 0:
            raise StructuredEditError(
                f"edits[{index}] old_text was not found exactly in {path}; "
                "read the current file and retry with a larger exact block"
            )
        if occurrences > 1:
            raise StructuredEditError(
                f"edits[{index}] old_text is ambiguous in {path}: "
                f"{occurrences} exact matches; include more surrounding context"
            )
        modified[path] = current.replace(old_text, new_text, 1)

    diff_parts: list[str] = []
    for path in originals:
        diff_parts.extend(
            difflib.unified_diff(
                originals[path].splitlines(keepends=True),
                modified[path].splitlines(keepends=True),
                fromfile=f"a/{path}",
                tofile=f"b/{path}",
            )
        )
    unified_diff = "".join(diff_parts)
    if not unified_diff:
        raise StructuredEditError("structured edits produced no file changes")

    return PatchProposal(
        diagnosis=str(data["diagnosis"]),
        affected_files=list(originals),
        unified_diff=unified_diff,
        expected_behavior=str(data["expected_behavior"]),
        risk_notes=str(data["risk_notes"]),
        tests_to_run=[str(item) for item in data["tests_to_run"]],
        base_revisions=base_revisions,
    )
