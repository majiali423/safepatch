"""H1: fixture definition-time expressions are outside the inert conftest subset."""

from __future__ import annotations

from pathlib import Path

import pytest

from code_agent.controller import TaskController
from code_agent.llm import LLMClient
from code_agent.patching.test_collection import build_protected_inventory
from code_agent.state import SessionStatus, TestResult

DUAL_DIFF = (
    "--- a/mod.py\n+++ b/mod.py\n@@ -1,2 +1,2 @@\n def f():\n-    return 1\n+    return 2\n"
    "--- a/checks.py\n+++ b/checks.py\n@@ -1,2 +1,2 @@\n def test_guard():\n"
    "-    assert 1 == 1\n+    assert True\n"
)
GUARD_ORIGINAL = "def test_guard():\n    assert 1 == 1\n"
MOD_ORIGINAL = "def f():\n    return 1\n"

REGISTER = (
    "globals().__setitem__('pytest_collect_file', lambda file_path, parent: "
    "pytest.Module.from_parent(parent, path=file_path) "
    "if file_path.name == 'checks.py' else None)"
)
H1_VARIANTS = {
    "default_argument": (
        "import pytest\n@pytest.fixture\ndef value(_=" + REGISTER + "):\n    return 1\n"
    ),
    "decorator_argument": (
        "import pytest\n@pytest.fixture(scope=(" + REGISTER + ', "function")[1])\n'
        "def value():\n    return 1\n"
    ),
    "return_annotation": (
        "import pytest\n@pytest.fixture\ndef value() -> " + REGISTER + ":\n    return 1\n"
    ),
}


class _FailThenPass:
    def __init__(self):
        self.calls = 0

    def run_pytest(self, workspace_root, *, log_path=None):
        self.calls += 1
        code = 1 if self.calls == 1 else 0
        result = TestResult(
            exit_code=code,
            stdout="FAILED test_mod.py::test_f" if code else "1 passed",
            stderr="",
            duration_sec=0.01,
            failed_tests=["test_mod.py::test_f"] if code else [],
        )
        if log_path:
            log_path.parent.mkdir(parents=True, exist_ok=True)
            log_path.write_text(result.stdout, encoding="utf-8")
        return result


def _repo(tmp_path: Path, conftest: str) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "mod.py").write_text(MOD_ORIGINAL, encoding="utf-8")
    (repo / "test_mod.py").write_text(
        "from mod import f\ndef test_f():\n    assert f() == 2\n",
        encoding="utf-8",
    )
    (repo / "checks.py").write_text(GUARD_ORIGINAL, encoding="utf-8")
    (repo / "conftest.py").write_text(conftest, encoding="utf-8")
    return repo


@pytest.mark.parametrize("name", list(H1_VARIANTS))
def test_h1_definition_expressions_are_unsupported(tmp_path: Path, name: str):
    repo = _repo(tmp_path, H1_VARIANTS[name])
    inventory = build_protected_inventory(repo)
    assert inventory.unsupported_scope is True
    assert inventory.fail_closed is True
    assert "checks.py" in inventory.files
    assert "mod.py" in inventory.files
    assert any("executable conftest" in item for item in inventory.reasons)


@pytest.mark.parametrize("name", list(H1_VARIANTS))
def test_h1_definition_expressions_stop_before_apply(tmp_path: Path, name: str):
    repo = _repo(tmp_path, H1_VARIANTS[name])
    session = TaskController(
        llm=LLMClient(
            dry_run_script=[
                {
                    "tool": "propose_patch",
                    "args": {
                        "diagnosis": "fix return and weaken guard",
                        "affected_files": ["mod.py", "checks.py"],
                        "unified_diff": DUAL_DIFF,
                        "expected_behavior": "tests pass",
                        "risk_notes": "synthetic",
                        "tests_to_run": ["test_mod.py"],
                    },
                }
            ]
        ),
        runner=_FailThenPass(),
        approve=lambda *_: True,
        session_base=tmp_path / "sessions",
    ).run(repo, "fix f")
    assert session.status is SessionStatus.PATCH_NOT_APPLICABLE
    assert session.stop_reason == "unsupported_pytest_collection"
    assert session.attempts_used == 0
    assert session.status is not SessionStatus.SUCCEEDED
    assert (session.workspace_root / "checks.py").read_text(encoding="utf-8") == GUARD_ORIGINAL
    assert (session.workspace_root / "mod.py").read_text(encoding="utf-8") == MOD_ORIGINAL
    assert "fixtures are not auto-repaired" in (session.last_error or "")
