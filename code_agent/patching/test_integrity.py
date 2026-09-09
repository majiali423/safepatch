"""Test-integrity heuristics enforced by PolicyValidator (not prompt-only)."""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass, field
from pathlib import Path

SKIP_CALL_RE = re.compile(r"\bpytest\.skip\s*\(")
SKIP_MARK_RE = re.compile(
    r"@pytest\.mark\.skip(?:if)?\b|@unittest\.skip(?:If|Unless)?\b"
)
ASSERT_TRUE_RE = re.compile(r"^\s*assert\s+True\b")
ASSERT_RE = re.compile(r"^\s*assert\b")
RAISES_RE = re.compile(r"\bpytest\.raises\s*\(")

CONFIG_NAMES = {
    "pytest.ini",
    "tox.ini",
    "setup.cfg",
    "pyproject.toml",
    "conftest.py",
}


@dataclass
class TestIntegrityResult:
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    new_test_files: list[str] = field(default_factory=list)
    modified_test_files: list[str] = field(default_factory=list)
    high_risk: bool = False


def is_pytest_test_module_name(name: str) -> bool:
    """Match pytest's default python_files: test_*.py and *_test.py."""
    return name.endswith(".py") and (
        name.startswith("test_") or name.endswith("_test.py")
    )


def is_test_path(path: str, collected_test_files: set[str] | None = None) -> bool:
    norm = path.replace("\\", "/")
    name = Path(norm).name
    if collected_test_files is not None:
        collected = {item.replace("\\", "/").removeprefix("./") for item in collected_test_files}
        if norm in collected or name in collected:
            return True
    if name == "conftest.py":
        return True
    if is_pytest_test_module_name(name):
        return True
    if "/tests/" in f"/{norm}" or norm.startswith("tests/"):
        if name.endswith(".py"):
            return True
    return False


def is_allowed_new_test_path(path: str) -> bool:
    """Only tests/test_*.py or tests/**/test_*.py."""
    norm = path.replace("\\", "/")
    if not norm.startswith("tests/"):
        return False
    return Path(norm).name.startswith("test_") and norm.endswith(".py")


def is_pytest_config_path(path: str) -> bool:
    name = Path(path.replace("\\", "/")).name.lower()
    return name in CONFIG_NAMES


def is_existing_root_test(path: str, workspace_root: Path) -> bool:
    norm = path.replace("\\", "/")
    name = Path(norm).name
    if "/" in norm or "\\" in path.replace("/", "\\"):
        # has directory component
        if norm.count("/") >= 1:
            return False
    if not (name.startswith("test_") and name.endswith(".py")):
        return False
    return (workspace_root / name).is_file()


def check_test_integrity(
    *,
    workspace_root: Path,
    file_pairs: list[tuple[str | None, str | None]],
    unified_diff: str,
    allow_test_changes: bool,
    allow_new_tests: bool,
    collected_test_files: set[str] | None = None,
) -> TestIntegrityResult:
    from code_agent.patching.hunk_engine import split_file_diffs

    result = TestIntegrityResult()
    bodies = {path: body for path, body in split_file_diffs(unified_diff)}
    collected = collected_test_files

    for old, new in file_pairs:
        if new is None and old is not None:
            # deletion — always reject test / config deletions here too
            if is_test_path(old, collected) or is_pytest_config_path(old):
                result.errors.append(
                    f"Deleting test/config files is forbidden: {old}"
                )
            continue
        if new is None:
            continue
        path = new.replace("\\", "/")
        is_new = old is None
        existed = (workspace_root / path).is_file()

        if is_pytest_config_path(path) or Path(path).name == "conftest.py":
            result.errors.append(
                f"Modifying pytest config/conftest is forbidden: {path}"
            )
            continue

        if is_new:
            _check_new_file(
                result,
                path=path,
                body=bodies.get(path, ""),
                allow_new_tests=allow_new_tests,
            )
            continue

        # Existing file modification
        if not existed:
            continue

        if is_test_path(path, collected) or _is_root_test_name(path):
            if not allow_test_changes:
                result.errors.append(
                    f"Modifying existing test file is forbidden "
                    f"(use --allow-test-changes): {path}"
                )
                continue
            result.modified_test_files.append(path)
            result.high_risk = True
            result.warnings.append(f"HIGH RISK: EXISTING TESTS MODIFIED: {path}")
            _check_modified_existing_test(
                result,
                workspace_root=workspace_root,
                path=path,
                body=bodies.get(path, ""),
            )

    result.new_test_files = sorted(set(result.new_test_files))
    result.modified_test_files = sorted(set(result.modified_test_files))
    return result


def _is_root_test_name(path: str) -> bool:
    norm = path.replace("\\", "/")
    if "/" in norm:
        return False
    return is_pytest_test_module_name(Path(norm).name)


def _check_new_file(
    result: TestIntegrityResult,
    *,
    path: str,
    body: str,
    allow_new_tests: bool,
) -> None:
    if not allow_new_tests:
        if is_test_path(path) or path.replace("\\", "/").startswith("tests/"):
            result.errors.append(f"New test files are disabled: {path}")
        return

    if path.replace("\\", "/").startswith("tests/"):
        if not is_allowed_new_test_path(path):
            result.errors.append(
                f"New files under tests/ must match tests/**/test_*.py: {path}"
            )
            return
        content = _content_from_new_diff(body)
        result.new_test_files.append(path)
        result.high_risk = True
        result.warnings.append(f"HIGH RISK: NEW TEST FILE: {path}")
        _check_evasion_in_text(result, path, content, context="new test")
        _check_new_test_has_tests_and_asserts(result, path, content)
        return

    # Root-level new test_*.py not allowed by default new-test rule
    if _is_root_test_name(path):
        result.errors.append(
            f"New root-level test_*.py is forbidden; use tests/**/test_*.py: {path}"
        )


def _check_modified_existing_test(
    result: TestIntegrityResult,
    *,
    workspace_root: Path,
    path: str,
    body: str,
) -> None:
    from code_agent.patching.hunk_engine import apply_hunks_to_text

    original = (workspace_root / path).read_text(encoding="utf-8")
    try:
        updated = apply_hunks_to_text(original, body, path)
    except Exception as exc:  # noqa: BLE001
        result.errors.append(f"Cannot analyze test patch for {path}: {exc}")
        return

    _check_evasion_in_text(result, path, updated, context="modified test")
    # Also flag evasion only in added lines
    for line in body.splitlines():
        if line.startswith("+") and not line.startswith("+++"):
            text = line[1:]
            if SKIP_CALL_RE.search(text) or SKIP_MARK_RE.search(text):
                result.errors.append(
                    f"skip/skipif is forbidden in test changes: {path}"
                )
            if ASSERT_TRUE_RE.search(text):
                result.errors.append(
                    f"assert True is forbidden in test changes: {path}"
                )

    old_asserts = _count_assertions(original)
    new_asserts = _count_assertions(updated)
    if old_asserts > 0 and new_asserts == 0:
        result.errors.append(
            f"Removing all assertions from existing test is forbidden: {path}"
        )


def _check_evasion_in_text(
    result: TestIntegrityResult, path: str, content: str, *, context: str
) -> None:
    if SKIP_CALL_RE.search(content) or SKIP_MARK_RE.search(content):
        result.errors.append(
            f"pytest.skip / mark.skip(skipif) forbidden in {context}: {path}"
        )
    for line in content.splitlines():
        if ASSERT_TRUE_RE.search(line):
            result.errors.append(f"assert True forbidden in {context}: {path}")
            break


def _check_new_test_has_tests_and_asserts(
    result: TestIntegrityResult, path: str, content: str
) -> None:
    has_test_fn = False
    has_assert_or_raises = False
    try:
        tree = ast.parse(content)
    except SyntaxError:
        # Still allow policy to catch evasion via text; syntax may fail apply later
        has_test_fn = bool(re.search(r"^\s*def\s+test_", content, re.M))
        has_test_fn = has_test_fn or bool(
            re.search(r"^\s*class\s+Test", content, re.M)
        )
        has_assert_or_raises = bool(ASSERT_RE.search(content)) or bool(
            RAISES_RE.search(content)
        )
    else:
        for node in tree.body:
            if isinstance(node, ast.FunctionDef) and node.name.startswith("test_"):
                has_test_fn = True
            if isinstance(node, ast.ClassDef) and node.name.startswith("Test"):
                has_test_fn = True
                for item in node.body:
                    if isinstance(item, ast.FunctionDef) and item.name.startswith(
                        "test_"
                    ):
                        has_test_fn = True
        has_assert_or_raises = _count_assertions(content) > 0

    if not has_test_fn:
        result.errors.append(
            f"New test file must define a test_* function or Test* class: {path}"
        )
    if not has_assert_or_raises:
        result.errors.append(
            f"New test file must contain assert or pytest.raises: {path}"
        )


def _count_assertions(content: str) -> int:
    n = 0
    for line in content.splitlines():
        if ASSERT_RE.search(line) or RAISES_RE.search(line):
            n += 1
    return n


def _content_from_new_diff(body: str) -> str:
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
        elif line.startswith(" "):
            out.append(line[1:])
    return "\n".join(out) + ("\n" if out else "")
