"""G1: conftest support is an explicit whitelist, including pytest_* bindings."""

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

G1_ASSIGNED_HOOK = (
    "import pytest\n"
    "def collect(file_path, parent):\n"
    '    if file_path.name == "checks.py":\n'
    "        return pytest.Module.from_parent(parent, path=file_path)\n"
    "pytest_collect_file = collect\n"
)

FIXTURE_ONLY = "import pytest\n@pytest.fixture\ndef value():\n    return 1\n"


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


def _repo(tmp_path: Path, conftest: str | None) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir(exist_ok=True)
    (repo / "mod.py").write_text(MOD_ORIGINAL, encoding="utf-8")
    (repo / "test_mod.py").write_text(
        "from mod import f\ndef test_f():\n    assert f() == 2\n",
        encoding="utf-8",
    )
    (repo / "checks.py").write_text(GUARD_ORIGINAL, encoding="utf-8")
    if conftest is not None:
        (repo / "conftest.py").write_text(conftest, encoding="utf-8")
    return repo


def _run_dual(tmp_path: Path, repo: Path):
    return TaskController(
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


@pytest.mark.parametrize(
    ("conftest", "must_contain"),
    [
        (
            "def pytest_collect_file(file_path, parent):\n    return None\n",
            "executable conftest",
        ),
        (
            "from helpers.hooks import collect as pytest_collect_file\n",
            "executable conftest",
        ),
        (G1_ASSIGNED_HOOK, "executable conftest"),
        ("pytest_collect_file: object = None\n", "executable conftest"),
        ("pytest_collect_file, other = None, None\n", "executable conftest"),
        ("import pytest\nexec('pass')\n", "executable conftest"),
        ("import pytest\nglobals()\n", "executable conftest"),
        ("import pytest\nsetattr(__import__('sys'), 'x', 1)\n", "executable conftest"),
        ('pytest_plugins = ["collector"]\n', "executable conftest"),
    ],
    ids=[
        "direct_hook",
        "import_as",
        "plain_assignment",
        "annotated_assignment",
        "unpacking_bind",
        "exec_call",
        "globals_call",
        "setattr_call",
        "pytest_plugins",
    ],
)
def test_g1_binding_matrix_is_unsupported(tmp_path: Path, conftest: str, must_contain: str):
    repo = _repo(tmp_path, conftest)
    helpers = repo / "helpers"
    helpers.mkdir(parents=True, exist_ok=True)
    (helpers / "__init__.py").write_text("", encoding="utf-8")
    (helpers / "hooks.py").write_text("def collect(file_path, parent):\n    return None\n", encoding="utf-8")
    inventory = build_protected_inventory(repo)
    assert inventory.unsupported_scope is True
    assert inventory.fail_closed is True
    assert "checks.py" in inventory.files
    assert any(must_contain in item for item in inventory.reasons)


@pytest.mark.parametrize(
    "conftest",
    [
        FIXTURE_ONLY,
        "import pytest\n",
        "import pytest\nfrom pytest import fixture\n@fixture\ndef value():\n    return 1\n",
    ],
    ids=["fixture_only", "import_pytest", "from_pytest_fixture"],
)
def test_g1_fixture_forms_are_unsupported(tmp_path: Path, conftest: str):
    repo = _repo(tmp_path, conftest)
    inventory = build_protected_inventory(repo)
    assert inventory.unsupported_scope is True
    assert inventory.fail_closed is True
    assert "mod.py" in inventory.files
    assert "checks.py" in inventory.files
    assert any("executable conftest" in item for item in inventory.reasons)


@pytest.mark.parametrize(
    "conftest",
    [
        "",
        "# comment only\n",
        '"""module docstring"""\n',
        "pass\n",
        '"""docs"""\npass\n',
    ],
    ids=["empty", "comment", "docstring", "pass", "docstring_and_pass"],
)
def test_g1_inert_conftest_is_supported(tmp_path: Path, conftest: str):
    repo = _repo(tmp_path, conftest)
    inventory = build_protected_inventory(repo)
    assert inventory.unsupported_scope is False
    assert "mod.py" not in inventory.files
    assert "checks.py" not in inventory.files


def test_g1_no_conftest_tests_layout_does_not_protect_mod(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "mod.py").write_text(MOD_ORIGINAL, encoding="utf-8")
    tests = repo / "tests"
    tests.mkdir()
    (tests / "test_mod.py").write_text(
        "from mod import f\ndef test_f():\n    assert f() == 2\n",
        encoding="utf-8",
    )
    inventory = build_protected_inventory(repo)
    assert inventory.unsupported_scope is False
    assert "mod.py" not in inventory.files
    assert "tests/test_mod.py" in inventory.files


def test_g1_assigned_hook_stops_before_apply(tmp_path: Path):
    repo = _repo(tmp_path, G1_ASSIGNED_HOOK)
    session = _run_dual(tmp_path, repo)
    assert session.status is SessionStatus.PATCH_NOT_APPLICABLE
    assert session.stop_reason == "unsupported_pytest_collection"
    assert session.attempts_used == 0
    assert session.status is not SessionStatus.SUCCEEDED
    assert (session.workspace_root / "checks.py").read_text(encoding="utf-8") == GUARD_ORIGINAL
    assert (session.workspace_root / "mod.py").read_text(encoding="utf-8") == MOD_ORIGINAL
    assert session.pytest_scope_unsupported is True
    assert any("executable conftest" in item for item in session.pytest_protection_reasons)


@pytest.mark.parametrize(
    "conftest",
    [
        G1_ASSIGNED_HOOK,
        "pytest_collect_file: object = None\n",
        "pytest_collect_file, other = None, None\n",
        "import pytest\nexec('pass')\n",
    ],
    ids=["plain_assignment", "annotated_assignment", "unpacking_bind", "exec_call"],
)
def test_g1_binding_class_does_not_retry_apply(tmp_path: Path, conftest: str):
    repo = _repo(tmp_path, conftest)
    session = _run_dual(tmp_path, repo)
    assert session.status is SessionStatus.PATCH_NOT_APPLICABLE
    assert session.attempts_used == 0
    assert (session.workspace_root / "checks.py").read_text(encoding="utf-8") == GUARD_ORIGINAL
    assert "return 1" in (session.workspace_root / "mod.py").read_text(encoding="utf-8")
