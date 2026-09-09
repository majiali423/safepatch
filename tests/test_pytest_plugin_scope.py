"""E1/E2: workspace imports and unknown pytest collection extensions."""

from __future__ import annotations

from pathlib import Path

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


def _indirect_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "pytest.ini").write_text("[pytest]\npythonpath = .\n", encoding="utf-8")
    (repo / "mod.py").write_text("def f():\n    return 1\n", encoding="utf-8")
    (repo / "test_mod.py").write_text(
        "from mod import f\ndef test_f():\n    assert f() == 2\n",
        encoding="utf-8",
    )
    (repo / "checks.py").write_text(GUARD_ORIGINAL, encoding="utf-8")
    (repo / "conftest.py").write_text('pytest_plugins = ["collector"]\n', encoding="utf-8")
    (repo / "collector.py").write_text(
        "import pytest\n"
        "def pytest_collect_file(file_path, parent):\n"
        '    if file_path.name == "checks.py":\n'
        "        return pytest.Module.from_parent(parent, path=file_path)\n",
        encoding="utf-8",
    )
    return repo


def test_e2_pytest_plugins_is_fail_closed(tmp_path: Path):
    repo = _indirect_repo(tmp_path)
    inventory = build_protected_inventory(repo)
    assert inventory.fail_closed is True
    assert inventory.unsupported_scope is True
    assert "checks.py" in inventory.files
    assert "mod.py" in inventory.files
    assert any("pytest_plugins" in item or "executable conftest" in item for item in inventory.reasons)


def test_e2_addopts_plugin_flag_is_fail_closed(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "mod.py").write_text("VALUE = 1\n", encoding="utf-8")
    (repo / "test_mod.py").write_text("def test_f():\n    assert True\n", encoding="utf-8")
    (repo / "pytest.ini").write_text("[pytest]\naddopts = -p collector\n", encoding="utf-8")
    inventory = build_protected_inventory(repo)
    assert inventory.fail_closed is True
    assert inventory.unsupported_scope is True


def test_e2_imported_collect_hook_is_fail_closed(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "mod.py").write_text("VALUE = 1\n", encoding="utf-8")
    (repo / "test_mod.py").write_text("def test_f():\n    assert True\n", encoding="utf-8")
    (repo / "collector.py").write_text(
        "def pytest_collect_file(file_path, parent):\n    return None\n",
        encoding="utf-8",
    )
    (repo / "conftest.py").write_text(
        "from collector import pytest_collect_file\n",
        encoding="utf-8",
    )
    inventory = build_protected_inventory(repo)
    assert inventory.fail_closed is True
    assert inventory.unsupported_scope is True


def test_e2_fixture_only_conftest_is_unsupported(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "mod.py").write_text("def f():\n    return 1\n", encoding="utf-8")
    tests = repo / "tests"
    tests.mkdir()
    (tests / "test_mod.py").write_text(
        "from mod import f\ndef test_f():\n    assert f() == 2\n",
        encoding="utf-8",
    )
    (tests / "conftest.py").write_text(
        "import pytest\n@pytest.fixture\ndef value():\n    return 1\n",
        encoding="utf-8",
    )
    inventory = build_protected_inventory(repo)
    assert inventory.fail_closed is True
    assert inventory.unsupported_scope is True
    assert "mod.py" in inventory.files
    assert any("executable conftest" in item for item in inventory.reasons)


def test_e2_controller_stops_before_apply_for_pytest_plugins(tmp_path: Path):
    repo = _indirect_repo(tmp_path)
    proposal = {
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
    session = TaskController(
        llm=LLMClient(dry_run_script=[proposal, {"tool": "finish", "args": {"reason": "blocked"}}]),
        runner=_FailThenPass(),
        approve=lambda *_: True,
        session_base=tmp_path / "sessions",
    ).run(repo, "fix f")
    assert session.status is SessionStatus.PATCH_NOT_APPLICABLE
    assert session.stop_reason == "unsupported_pytest_collection"
    assert session.attempts_used == 0
    assert (session.workspace_root / "checks.py").read_text(encoding="utf-8") == GUARD_ORIGINAL
    assert "return 1" in (session.workspace_root / "mod.py").read_text(encoding="utf-8")
    assert "Unsupported pytest configuration" in session.last_error
