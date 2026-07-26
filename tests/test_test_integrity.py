"""Stage B / P1: test-integrity policy (PolicyValidator + TestIntegrityPolicy)."""

from __future__ import annotations

import json
from pathlib import Path

from code_agent.controller import TaskController
from code_agent.llm import LLMClient
from code_agent.patching.applier import apply_proposal
from code_agent.patching.validator import PolicyValidator, validate_proposal
from code_agent.state import PatchProposal, SessionStatus, TestResult


def _proposal(diff: str, files: list[str] | None = None) -> PatchProposal:
    return PatchProposal(
        diagnosis="d",
        affected_files=files or [],
        unified_diff=diff,
        expected_behavior="e",
        risk_notes="r",
        tests_to_run=["tests/test_x.py"],
    )


def _workspace(tmp_path: Path) -> Path:
    root = tmp_path / "work"
    root.mkdir()
    (root / "mod.py").write_text("def f():\n    return 1\n", encoding="utf-8")
    tests = root / "tests"
    tests.mkdir()
    (tests / "test_x.py").write_text(
        "from mod import f\n"
        "def test_f():\n"
        "    assert f() == 2\n",
        encoding="utf-8",
    )
    (root / "test_root.py").write_text(
        "def test_root():\n    assert True is False\n",
        encoding="utf-8",
    )
    (root / "conftest.py").write_text("import pytest\n", encoding="utf-8")
    (root / "pytest.ini").write_text("[pytest]\npythonpath = .\n", encoding="utf-8")
    (root / "pyproject.toml").write_text("[project]\nname='x'\n", encoding="utf-8")
    return root


def test_default_modify_existing_tests_rejected(tmp_path: Path):
    root = _workspace(tmp_path)
    diff = (
        "--- a/tests/test_x.py\n"
        "+++ b/tests/test_x.py\n"
        "@@ -1,3 +1,3 @@\n"
        " from mod import f\n"
        " def test_f():\n"
        "-    assert f() == 2\n"
        "+    assert f() == 1\n"
    )
    v = validate_proposal(_proposal(diff, ["tests/test_x.py"]), root)
    assert not v.ok
    assert any("existing test" in e.lower() for e in v.errors)


def test_default_delete_existing_test_rejected(tmp_path: Path):
    root = _workspace(tmp_path)
    diff = (
        "--- a/tests/test_x.py\n"
        "+++ /dev/null\n"
        "@@ -1,3 +0,0 @@\n"
        "-from mod import f\n"
        "-def test_f():\n"
        "-    assert f() == 2\n"
    )
    v = validate_proposal(_proposal(diff, ["tests/test_x.py"]), root)
    assert not v.ok
    assert any("Deleting" in e for e in v.errors)


def test_default_modify_root_test_rejected(tmp_path: Path):
    root = _workspace(tmp_path)
    diff = (
        "--- a/test_root.py\n"
        "+++ b/test_root.py\n"
        "@@ -1,2 +1,2 @@\n"
        " def test_root():\n"
        "-    assert True is False\n"
        "+    assert 1 == 1\n"
    )
    v = validate_proposal(_proposal(diff, ["test_root.py"]), root)
    assert not v.ok
    assert any("existing test" in e.lower() for e in v.errors)


def test_modify_conftest_rejected(tmp_path: Path):
    root = _workspace(tmp_path)
    diff = (
        "--- a/conftest.py\n"
        "+++ b/conftest.py\n"
        "@@ -1 +1,2 @@\n"
        " import pytest\n"
        "+collect_ignore = ['tests']\n"
    )
    v = validate_proposal(_proposal(diff, ["conftest.py"]), root)
    assert not v.ok
    assert any("conftest" in e.lower() or "forbidden" in e.lower() for e in v.errors)


def test_modify_pytest_ini_rejected(tmp_path: Path):
    root = _workspace(tmp_path)
    diff = (
        "--- a/pytest.ini\n"
        "+++ b/pytest.ini\n"
        "@@ -1,2 +1,3 @@\n"
        " [pytest]\n"
        " pythonpath = .\n"
        "+norecursedirs = tests\n"
    )
    v = validate_proposal(_proposal(diff, ["pytest.ini"]), root)
    assert not v.ok


def test_modify_pyproject_rejected(tmp_path: Path):
    root = _workspace(tmp_path)
    diff = (
        "--- a/pyproject.toml\n"
        "+++ b/pyproject.toml\n"
        "@@ -1,2 +1,3 @@\n"
        " [project]\n"
        " name='x'\n"
        "+[tool.pytest.ini_options]\n"
    )
    v = validate_proposal(_proposal(diff, ["pyproject.toml"]), root)
    assert not v.ok


def test_new_test_file_allowed_high_risk(tmp_path: Path):
    root = _workspace(tmp_path)
    diff = (
        "--- /dev/null\n"
        "+++ b/tests/test_extra.py\n"
        "@@ -0,0 +1,3 @@\n"
        "+def test_extra():\n"
        "+    assert 1 + 1 == 2\n"
        "+\n"
    )
    v = validate_proposal(_proposal(diff, ["tests/test_extra.py"]), root)
    assert v.ok, v.errors
    assert v.high_risk
    assert v.new_test_files == ["tests/test_extra.py"]
    assert any("NEW TEST FILE" in w for w in v.test_integrity_warnings)


def test_new_helpers_under_tests_rejected(tmp_path: Path):
    root = _workspace(tmp_path)
    diff = (
        "--- /dev/null\n"
        "+++ b/tests/helpers.py\n"
        "@@ -0,0 +1,2 @@\n"
        "+def helper():\n"
        "+    return 1\n"
    )
    v = validate_proposal(_proposal(diff, ["tests/helpers.py"]), root)
    assert not v.ok
    assert any("test_*.py" in e for e in v.errors)


def test_new_test_with_pytest_skip_rejected(tmp_path: Path):
    root = _workspace(tmp_path)
    diff = (
        "--- /dev/null\n"
        "+++ b/tests/test_skip.py\n"
        "@@ -0,0 +1,4 @@\n"
        "+import pytest\n"
        "+def test_skip():\n"
        "+    pytest.skip('nope')\n"
        "+    assert 1 == 1\n"
    )
    v = validate_proposal(_proposal(diff, ["tests/test_skip.py"]), root)
    assert not v.ok
    assert any("skip" in e.lower() for e in v.errors)


def test_new_test_with_mark_skip_rejected(tmp_path: Path):
    root = _workspace(tmp_path)
    diff = (
        "--- /dev/null\n"
        "+++ b/tests/test_mark.py\n"
        "@@ -0,0 +1,5 @@\n"
        "+import pytest\n"
        "+@pytest.mark.skip\n"
        "+def test_mark():\n"
        "+    assert 1 == 1\n"
        "+\n"
    )
    v = validate_proposal(_proposal(diff, ["tests/test_mark.py"]), root)
    assert not v.ok
    assert any("skip" in e.lower() for e in v.errors)


def test_new_test_assert_true_rejected(tmp_path: Path):
    root = _workspace(tmp_path)
    diff = (
        "--- /dev/null\n"
        "+++ b/tests/test_true.py\n"
        "@@ -0,0 +1,3 @@\n"
        "+def test_true():\n"
        "+    assert True\n"
        "+\n"
    )
    v = validate_proposal(_proposal(diff, ["tests/test_true.py"]), root)
    assert not v.ok
    assert any("assert True" in e for e in v.errors)


def test_new_test_without_assert_rejected(tmp_path: Path):
    root = _workspace(tmp_path)
    diff = (
        "--- /dev/null\n"
        "+++ b/tests/test_empty.py\n"
        "@@ -0,0 +1,3 @@\n"
        "+def test_empty():\n"
        "+    x = 1\n"
        "+\n"
    )
    v = validate_proposal(_proposal(diff, ["tests/test_empty.py"]), root)
    assert not v.ok
    assert any("assert or pytest.raises" in e for e in v.errors)


def test_allow_test_changes_modifies_existing_high_risk(tmp_path: Path):
    root = _workspace(tmp_path)
    diff = (
        "--- a/tests/test_x.py\n"
        "+++ b/tests/test_x.py\n"
        "@@ -1,3 +1,4 @@\n"
        " from mod import f\n"
        " def test_f():\n"
        "     assert f() == 2\n"
        "+    assert isinstance(f(), int)\n"
    )
    v = validate_proposal(
        _proposal(diff, ["tests/test_x.py"]),
        root,
        allow_test_changes=True,
    )
    assert v.ok, v.errors
    assert v.high_risk
    assert v.modified_test_files == ["tests/test_x.py"]
    assert any("EXISTING TESTS MODIFIED" in w for w in v.test_integrity_warnings)


def test_allow_test_changes_still_forbids_delete(tmp_path: Path):
    root = _workspace(tmp_path)
    diff = (
        "--- a/tests/test_x.py\n"
        "+++ /dev/null\n"
        "@@ -1,3 +0,0 @@\n"
        "-from mod import f\n"
        "-def test_f():\n"
        "-    assert f() == 2\n"
    )
    v = validate_proposal(
        _proposal(diff, ["tests/test_x.py"]),
        root,
        allow_test_changes=True,
    )
    assert not v.ok
    assert any("Deleting" in e for e in v.errors)


def test_allow_test_changes_still_forbids_skip_and_strip_asserts(tmp_path: Path):
    root = _workspace(tmp_path)
    skip_diff = (
        "--- a/tests/test_x.py\n"
        "+++ b/tests/test_x.py\n"
        "@@ -1,3 +1,5 @@\n"
        "+import pytest\n"
        " from mod import f\n"
        " def test_f():\n"
        "+    pytest.skip('cheat')\n"
        "     assert f() == 2\n"
    )
    v = validate_proposal(
        _proposal(skip_diff, ["tests/test_x.py"]),
        root,
        allow_test_changes=True,
    )
    assert not v.ok
    assert any("skip" in e.lower() for e in v.errors)

    strip_diff = (
        "--- a/tests/test_x.py\n"
        "+++ b/tests/test_x.py\n"
        "@@ -1,3 +1,3 @@\n"
        " from mod import f\n"
        " def test_f():\n"
        "-    assert f() == 2\n"
        "+    pass\n"
    )
    v2 = validate_proposal(
        _proposal(strip_diff, ["tests/test_x.py"]),
        root,
        allow_test_changes=True,
    )
    assert not v2.ok
    assert any("Removing all assertions" in e for e in v2.errors)


def test_rejected_patch_does_not_modify_working_copy(tmp_path: Path):
    root = _workspace(tmp_path)
    before = (root / "tests" / "test_x.py").read_text(encoding="utf-8")
    diff = (
        "--- a/tests/test_x.py\n"
        "+++ b/tests/test_x.py\n"
        "@@ -1,3 +1,3 @@\n"
        " from mod import f\n"
        " def test_f():\n"
        "-    assert f() == 2\n"
        "+    assert True\n"
    )
    proposal = _proposal(diff, ["tests/test_x.py"])
    result = apply_proposal(proposal, root)
    assert not result.ok
    assert (root / "tests" / "test_x.py").read_text(encoding="utf-8") == before


def test_policy_violation_does_not_increment_format_or_attempts(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "mod.py").write_text("def f():\n    return 1\n", encoding="utf-8")
    tests = repo / "tests"
    tests.mkdir()
    (tests / "test_mod.py").write_text(
        "from mod import f\n"
        "def test_f():\n"
        "    assert f() == 2\n",
        encoding="utf-8",
    )
    (repo / "pytest.ini").write_text("[pytest]\npythonpath = .\n", encoding="utf-8")

    cheat = {
        "tool": "propose_patch",
        "args": {
            "diagnosis": "weaken test",
            "affected_files": ["tests/test_mod.py"],
            "unified_diff": (
                "--- a/tests/test_mod.py\n"
                "+++ b/tests/test_mod.py\n"
                "@@ -1,3 +1,3 @@\n"
                " from mod import f\n"
                " def test_f():\n"
                "-    assert f() == 2\n"
                "+    assert True\n"
            ),
            "expected_behavior": "green",
            "risk_notes": "cheat",
            "tests_to_run": ["tests/test_mod.py"],
        },
    }
    fix = {
        "tool": "propose_patch",
        "args": {
            "diagnosis": "fix return",
            "affected_files": ["mod.py"],
            "unified_diff": (
                "--- a/mod.py\n"
                "+++ b/mod.py\n"
                "@@ -1,2 +1,2 @@\n"
                " def f():\n"
                "-    return 1\n"
                "+    return 2\n"
            ),
            "expected_behavior": "f returns 2",
            "risk_notes": "low",
            "tests_to_run": ["tests/test_mod.py"],
        },
    }

    class Runner:
        def __init__(self):
            self.calls = 0

        def run_pytest(self, workspace_root, *, log_path=None):
            self.calls += 1
            if self.calls == 1:
                result = TestResult(
                    exit_code=1,
                    stdout="FAILED",
                    stderr="",
                    duration_sec=0.01,
                    failed_tests=["tests/test_mod.py::test_f"],
                )
            else:
                result = TestResult(
                    exit_code=0, stdout="1 passed", stderr="", duration_sec=0.01
                )
            if log_path:
                log_path.write_text(result.stdout, encoding="utf-8")
            return result

    controller = TaskController(
        llm=LLMClient(dry_run_script=[cheat, fix]),
        runner=Runner(),  # type: ignore[arg-type]
        approve=lambda *_: True,
        say=lambda _m: None,
        session_base=tmp_path / "sessions",
    )
    session = controller.run(repo, "make f return 2")
    assert session.status == SessionStatus.SUCCEEDED
    assert session.total_format_retries_used == 0
    assert session.attempts_used == 1

    records = [
        json.loads(line)
        for line in (session.artifacts_dir / "trace.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
        if line.strip()
    ]
    # Cheat attempt: policy feedback via tool_result, not format_retry.
    policy_hits = [
        r
        for r in records
        if r["event"] == "tool_result"
        and r["payload"].get("tool") == "propose_patch"
        and r["payload"].get("ok") is False
    ]
    assert policy_hits
    assert any(
        "existing test" in (r["payload"].get("error") or "").lower()
        or "assert True" in (r["payload"].get("error") or "")
        for r in policy_hits
    )
    assert "format_retry" not in {r["event"] for r in records}


def test_policy_validator_instance_flags(tmp_path: Path):
    root = _workspace(tmp_path)
    diff = (
        "--- a/tests/test_x.py\n"
        "+++ b/tests/test_x.py\n"
        "@@ -1,3 +1,4 @@\n"
        " from mod import f\n"
        " def test_f():\n"
        "     assert f() == 2\n"
        "+    assert f() != 0\n"
    )
    denied = PolicyValidator(allow_test_changes=False).validate(
        _proposal(diff, ["tests/test_x.py"]), root
    )
    allowed = PolicyValidator(allow_test_changes=True).validate(
        _proposal(diff, ["tests/test_x.py"]), root
    )
    assert not denied.ok
    assert allowed.ok
    assert allowed.high_risk
