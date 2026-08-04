from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from code_agent.patching.test_integrity import check_test_integrity, is_pytest_config_path
from code_agent.state import PatchProposal

FORBIDDEN_EXACT = {
    "dockerfile",
    "requirements.txt",
    "pyproject.toml",
    "poetry.lock",
    "pipfile",
    "pipfile.lock",
    "uv.lock",
    "setup.py",
    "setup.cfg",
}

FORBIDDEN_DIR_PREFIXES = (
    ".git/",
    ".github/",
    ".gitlab-ci",
)

MAX_FILES = 5
MAX_CHANGED_LINES = 300


@dataclass
class ValidationResult:
    ok: bool
    errors: list[str]
    files: list[str]
    added_lines: int
    deleted_lines: int
    new_test_files: list[str] = field(default_factory=list)
    modified_test_files: list[str] = field(default_factory=list)
    test_integrity_warnings: list[str] = field(default_factory=list)
    high_risk: bool = False

    def to_dict(self) -> dict:
        return {
            "ok": self.ok,
            "errors": self.errors,
            "files": self.files,
            "added_lines": self.added_lines,
            "deleted_lines": self.deleted_lines,
            "new_test_files": self.new_test_files,
            "modified_test_files": self.modified_test_files,
            "test_integrity_warnings": self.test_integrity_warnings,
            "high_risk": self.high_risk,
        }


class PolicyValidator:
    """Policy gate: path/size limits + TestIntegrityPolicy."""

    max_files = MAX_FILES
    max_changed_lines = MAX_CHANGED_LINES

    def __init__(
        self,
        *,
        allow_test_changes: bool = False,
        allow_new_tests: bool = True,
    ) -> None:
        self.allow_test_changes = allow_test_changes
        self.allow_new_tests = allow_new_tests

    def validate(
        self, proposal: PatchProposal, workspace_root: Path
    ) -> ValidationResult:
        return validate_proposal(
            proposal,
            workspace_root,
            allow_test_changes=self.allow_test_changes,
            allow_new_tests=self.allow_new_tests,
        )


def _parse_diff_files(diff_text: str) -> list[tuple[str | None, str | None]]:
    files: list[tuple[str | None, str | None]] = []
    old: str | None = None
    new: str | None = None
    for line in diff_text.splitlines():
        if line.startswith("--- "):
            old = line[4:].strip()
            if old.startswith("a/"):
                old = old[2:]
            elif old == "/dev/null":
                old = None
        elif line.startswith("+++ "):
            new = line[4:].strip()
            if new.startswith("b/"):
                new = new[2:]
            elif new == "/dev/null":
                new = None
            files.append((old, new))
            old, new = None, None
    return files


def _is_forbidden_path(path: str) -> str | None:
    lowered = path.replace("\\", "/").lower()
    name = Path(lowered).name

    if lowered == ".git" or lowered.startswith(".git/"):
        return f"Modifying forbidden path: {path}"
    if name == ".env" or name.startswith(".env."):
        return f"Modifying forbidden path: {path}"
    if name == "dockerfile" or name.startswith("dockerfile."):
        return f"Modifying forbidden path: {path}"
    for prefix in FORBIDDEN_DIR_PREFIXES:
        if lowered.startswith(prefix):
            return f"Modifying forbidden path: {path}"
    if name in FORBIDDEN_EXACT:
        return f"Modifying forbidden path: {path}"
    if name.startswith("requirements-") and name.endswith(".txt"):
        return f"Modifying forbidden path: {path}"
    if lowered.endswith(".gitlab-ci.yml"):
        return f"Modifying forbidden path: {path}"
    if is_pytest_config_path(path):
        return f"Modifying forbidden path: {path}"
    return None


def validate_proposal(
    proposal: PatchProposal,
    workspace_root: Path,
    *,
    allow_test_changes: bool = False,
    allow_new_tests: bool = True,
) -> ValidationResult:
    errors: list[str] = []
    diff = proposal.unified_diff
    pairs = _parse_diff_files(diff)
    if not pairs:
        errors.append("Could not parse any file headers from unified_diff")

    touched: list[str] = []
    for old, new in pairs:
        if old is None and new is None:
            errors.append("Invalid file header pair")
            continue
        if new is None:
            errors.append(f"Deleting files is forbidden: {old}")
            continue
        if old is not None and old != new:
            errors.append(f"Renaming files is forbidden: {old} -> {new}")
            continue

        path = new
        touched.append(path)

        if path.startswith("/") or ".." in Path(path).parts:
            errors.append(f"Path escapes workspace: {path}")
            continue

        forbidden_msg = _is_forbidden_path(path)
        if forbidden_msg:
            errors.append(forbidden_msg)
            continue

        # Config / non-py already handled; remaining edits must be .py
        if not path.endswith(".py"):
            errors.append(f"Only .py files may be modified: {path}")
            continue

        abs_path = (workspace_root / path).resolve()
        try:
            abs_path.relative_to(workspace_root.resolve())
        except ValueError:
            errors.append(f"Path escapes workspace: {path}")
            continue

        is_new = old is None
        if is_new:
            lowered = path.replace("\\", "/")
            if not lowered.startswith("tests/"):
                errors.append(f"New files only allowed under tests/: {path}")
            if abs_path.exists():
                errors.append(f"New file already exists: {path}")
        else:
            if not abs_path.exists():
                errors.append(f"Modified file does not exist: {path}")

    if len(set(touched)) > MAX_FILES:
        errors.append(f"Too many files changed (max {MAX_FILES})")

    added = sum(
        1
        for line in diff.splitlines()
        if line.startswith("+") and not line.startswith("+++")
    )
    deleted = sum(
        1
        for line in diff.splitlines()
        if line.startswith("-") and not line.startswith("---")
    )
    if added + deleted > MAX_CHANGED_LINES:
        errors.append(
            f"Too many changed lines: {added + deleted} (max {MAX_CHANGED_LINES})"
        )

    for f in proposal.affected_files:
        if f not in touched:
            errors.append(f"affected_files entry not in diff: {f}")

    touched_set = set(touched)
    for path, revision in proposal.base_revisions.items():
        normalized = path.replace("\\", "/").removeprefix("./")
        if normalized not in touched_set:
            errors.append(f"base_revisions entry not in diff: {path}")
        if re.fullmatch(r"sha256:[0-9a-f]{64}", revision) is None:
            errors.append(f"Invalid file revision for {path}")

    integrity = check_test_integrity(
        workspace_root=workspace_root,
        file_pairs=pairs,
        unified_diff=diff,
        allow_test_changes=allow_test_changes,
        allow_new_tests=allow_new_tests,
    )
    errors.extend(integrity.errors)

    return ValidationResult(
        ok=not errors,
        errors=errors,
        files=sorted(set(touched)),
        added_lines=added,
        deleted_lines=deleted,
        new_test_files=integrity.new_test_files,
        modified_test_files=integrity.modified_test_files,
        test_integrity_warnings=integrity.warnings,
        high_risk=integrity.high_risk,
    )
