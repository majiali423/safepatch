"""Patch path normalization must preserve real a/ and b/ directories."""

from __future__ import annotations

from pathlib import Path

from code_agent.patching.validator import (
    _normalize_patch_path,
    _parse_diff_files,
    validate_proposal,
)
from code_agent.state import PatchProposal


def _repo_with_ab(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    (repo / "a").mkdir(parents=True)
    (repo / "b").mkdir(parents=True)
    (repo / "a" / "mod.py").write_text("A = 1\n", encoding="utf-8")
    (repo / "b" / "mod.py").write_text("B = 1\n", encoding="utf-8")
    (repo / "mod.py").write_text("ROOT = 1\n", encoding="utf-8")
    return repo


def _proposal(affected_files: list[str], unified_diff: str) -> PatchProposal:
    return PatchProposal.from_dict(
        {
            "diagnosis": "fix",
            "affected_files": affected_files,
            "unified_diff": unified_diff,
            "expected_behavior": "ok",
            "risk_notes": "low",
            "tests_to_run": [],
        }
    )


def test_normalize_does_not_strip_real_a_or_b_directories() -> None:
    assert _normalize_patch_path("a/mod.py") == "a/mod.py"
    assert _normalize_patch_path("b/mod.py") == "b/mod.py"
    assert _normalize_patch_path("a/b/mod.py") == "a/b/mod.py"
    assert _normalize_patch_path("./a/mod.py") == "a/mod.py"
    assert _normalize_patch_path(".\\a\\mod.py") == "a/mod.py"
    assert _normalize_patch_path("  b/mod.py  ") == "b/mod.py"


def test_parse_diff_files_strips_synthetic_prefix_once() -> None:
    pairs = _parse_diff_files(
        "--- a/a/mod.py\n+++ b/a/mod.py\n@@ -1 +1 @@\n-A = 1\n+A = 2\n"
        "--- a/b/mod.py\n+++ b/b/mod.py\n@@ -1 +1 @@\n-B = 1\n+B = 2\n"
    )
    assert pairs == [("a/mod.py", "a/mod.py"), ("b/mod.py", "b/mod.py")]


def test_modifying_real_a_mod_py(tmp_path: Path) -> None:
    repo = _repo_with_ab(tmp_path)
    diff = "--- a/a/mod.py\n+++ b/a/mod.py\n@@ -1 +1 @@\n-A = 1\n+A = 2\n"
    result = validate_proposal(_proposal(["a/mod.py"], diff), repo)
    assert result.ok, result.errors
    assert result.files == ["a/mod.py"]


def test_modifying_real_b_mod_py(tmp_path: Path) -> None:
    repo = _repo_with_ab(tmp_path)
    diff = "--- a/b/mod.py\n+++ b/b/mod.py\n@@ -1 +1 @@\n-B = 1\n+B = 2\n"
    result = validate_proposal(_proposal(["b/mod.py"], diff), repo)
    assert result.ok, result.errors
    assert result.files == ["b/mod.py"]


def test_same_diff_can_modify_both_a_and_b_mod_py(tmp_path: Path) -> None:
    repo = _repo_with_ab(tmp_path)
    diff = (
        "--- a/a/mod.py\n+++ b/a/mod.py\n@@ -1 +1 @@\n-A = 1\n+A = 2\n"
        "--- a/b/mod.py\n+++ b/b/mod.py\n@@ -1 +1 @@\n-B = 1\n+B = 2\n"
    )
    result = validate_proposal(_proposal(["a/mod.py", "b/mod.py"], diff), repo)
    assert result.ok, result.errors
    assert result.files == ["a/mod.py", "b/mod.py"]


def test_declared_a_mod_vs_actual_mod_are_not_equal(tmp_path: Path) -> None:
    repo = _repo_with_ab(tmp_path)
    diff = "--- a/mod.py\n+++ b/mod.py\n@@ -1 +1 @@\n-ROOT = 1\n+ROOT = 2\n"
    result = validate_proposal(_proposal(["a/mod.py"], diff), repo)
    assert not result.ok
    assert any("not in diff" in error for error in result.errors)
    assert any("undeclared" in error for error in result.errors)


def test_declared_mod_vs_actual_a_mod_are_not_equal(tmp_path: Path) -> None:
    repo = _repo_with_ab(tmp_path)
    diff = "--- a/a/mod.py\n+++ b/a/mod.py\n@@ -1 +1 @@\n-A = 1\n+A = 2\n"
    result = validate_proposal(_proposal(["mod.py"], diff), repo)
    assert not result.ok
    assert any("not in diff" in error for error in result.errors)
    assert any("undeclared" in error for error in result.errors)


def test_dot_slash_a_mod_equals_a_mod(tmp_path: Path) -> None:
    repo = _repo_with_ab(tmp_path)
    diff = "--- a/a/mod.py\n+++ b/a/mod.py\n@@ -1 +1 @@\n-A = 1\n+A = 2\n"
    result = validate_proposal(_proposal(["./a/mod.py"], diff), repo)
    assert result.ok, result.errors


def test_windows_separator_normalization(tmp_path: Path) -> None:
    repo = _repo_with_ab(tmp_path)
    diff = "--- a/a/mod.py\n+++ b/a/mod.py\n@@ -1 +1 @@\n-A = 1\n+A = 2\n"
    result = validate_proposal(_proposal(["a\\mod.py"], diff), repo)
    assert result.ok, result.errors


def test_duplicate_declaration_and_normalization_collision(tmp_path: Path) -> None:
    repo = _repo_with_ab(tmp_path)
    diff = "--- a/a/mod.py\n+++ b/a/mod.py\n@@ -1 +1 @@\n-A = 1\n+A = 2\n"
    result = validate_proposal(_proposal(["a/mod.py", "./a/mod.py"], diff), repo)
    assert not result.ok
    assert any("Duplicate" in error for error in result.errors)
