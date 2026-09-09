"""F1/F2: unique pytest inifile and no speculative conftest import walking."""

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


class _FailThenPass:
    def run_pytest(self, workspace_root, *, log_path=None):
        result = TestResult(
            exit_code=1,
            stdout="FAILED test_mod.py::test_f",
            stderr="",
            duration_sec=0.01,
            failed_tests=["test_mod.py::test_f"],
        )
        if log_path:
            log_path.parent.mkdir(parents=True, exist_ok=True)
            log_path.write_text(result.stdout, encoding="utf-8")
        return result


def _base_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "mod.py").write_text("def f():\n    return 1\n", encoding="utf-8")
    (repo / "test_mod.py").write_text(
        "from mod import f\ndef test_f():\n    assert f() == 2\n",
        encoding="utf-8",
    )
    (repo / "checks.py").write_text(GUARD_ORIGINAL, encoding="utf-8")
    return repo


def test_f1_setup_cfg_and_pyproject_are_rejected(tmp_path: Path):
    repo = _base_repo(tmp_path)
    (repo / "setup.cfg").write_text("[tool:pytest]\naddopts = -q\n", encoding="utf-8")
    (repo / "pyproject.toml").write_text(
        '[tool.pytest.ini_options]\npython_files = ["test_*.py", "checks.py"]\n',
        encoding="utf-8",
    )
    inventory = build_protected_inventory(repo)
    assert inventory.unsupported_scope is True
    assert inventory.fail_closed is True
    assert "checks.py" in inventory.files
    assert any("multiple pytest configuration" in item for item in inventory.reasons)


def test_f1_single_pyproject_python_files_protects_checks(tmp_path: Path):
    repo = _base_repo(tmp_path)
    (repo / "pyproject.toml").write_text(
        '[tool.pytest.ini_options]\npython_files = ["test_*.py", "checks.py"]\n',
        encoding="utf-8",
    )
    inventory = build_protected_inventory(repo)
    assert inventory.unsupported_scope is False
    assert "checks.py" in inventory.files
    assert "mod.py" not in inventory.files


@pytest.mark.parametrize(
    "writer",
    [
        lambda repo: (repo / "pytest.ini").write_text(
            "[pytest]\naddopts = -q\n", encoding="utf-8"
        ),
        lambda repo: (repo / "tox.ini").write_text(
            "[pytest]\naddopts = -q\n", encoding="utf-8"
        ),
        lambda repo: (repo / "setup.cfg").write_text(
            "[tool:pytest]\naddopts = -q\n", encoding="utf-8"
        ),
        lambda repo: (repo / "pyproject.toml").write_text(
            '[tool.pytest.ini_options]\naddopts = "-q"\n', encoding="utf-8"
        ),
    ],
    ids=["pytest.ini", "tox.ini", "setup.cfg", "pyproject.toml"],
)
def test_f1_single_supported_inifile_does_not_fail_closed(tmp_path: Path, writer):
    repo = _base_repo(tmp_path)
    writer(repo)
    inventory = build_protected_inventory(repo)
    assert inventory.unsupported_scope is False
    assert "test_mod.py" in inventory.files
    assert "mod.py" not in inventory.files
    assert "checks.py" not in inventory.files


def test_f1_plain_build_files_without_pytest_section_are_not_candidates(tmp_path: Path):
    repo = _base_repo(tmp_path)
    (repo / "setup.cfg").write_text("[metadata]\nname = demo\n", encoding="utf-8")
    (repo / "pyproject.toml").write_text(
        '[project]\nname = "demo"\nversion = "0"\n', encoding="utf-8"
    )
    (repo / "tox.ini").write_text("[tox]\nenvlist = py\n", encoding="utf-8")
    inventory = build_protected_inventory(repo)
    assert inventory.unsupported_scope is False
    assert "checks.py" not in inventory.files


def test_f1_pytest_ini_plus_setup_cfg_rejected(tmp_path: Path):
    repo = _base_repo(tmp_path)
    (repo / "pytest.ini").write_text("[pytest]\naddopts = -q\n", encoding="utf-8")
    (repo / "setup.cfg").write_text("[tool:pytest]\naddopts = -q\n", encoding="utf-8")
    inventory = build_protected_inventory(repo)
    assert inventory.unsupported_scope is True


def test_f2_aliased_submodule_hook_is_unsupported(tmp_path: Path):
    repo = _base_repo(tmp_path)
    helpers = repo / "helpers"
    helpers.mkdir()
    (helpers / "__init__.py").write_text("", encoding="utf-8")
    (helpers / "hooks.py").write_text(
        "import pytest\n"
        "def collect(file_path, parent):\n"
        '    if file_path.name == "checks.py":\n'
        "        return pytest.Module.from_parent(parent, path=file_path)\n",
        encoding="utf-8",
    )
    (repo / "conftest.py").write_text(
        "from helpers.hooks import collect as pytest_collect_file\n",
        encoding="utf-8",
    )
    inventory = build_protected_inventory(repo)
    assert inventory.unsupported_scope is True
    assert "checks.py" in inventory.files
    assert any("pytest_collect_file" in item or "executable conftest" in item for item in inventory.reasons)


@pytest.mark.parametrize(
    "conftest",
    [
        "def pytest_collect_file(file_path, parent):\n    return None\n",
        'pytest_plugins = ["collector"]\n',
        "from helpers.hooks import pytest_collect_file\n",
        "from .hooks import collect as pytest_collect_file\n",
    ],
    ids=["direct_hook", "pytest_plugins", "submodule_hook", "relative_import"],
)
def test_f2_import_matrix_unsupported(tmp_path: Path, conftest: str):
    repo = _base_repo(tmp_path)
    helpers = repo / "helpers"
    helpers.mkdir()
    (helpers / "__init__.py").write_text("", encoding="utf-8")
    (helpers / "hooks.py").write_text("def pytest_collect_file(file_path, parent):\n    return None\n", encoding="utf-8")
    (repo / "collector.py").write_text("def pytest_collect_file(file_path, parent):\n    return None\n", encoding="utf-8")
    (repo / "conftest.py").write_text(conftest, encoding="utf-8")
    inventory = build_protected_inventory(repo)
    assert inventory.unsupported_scope is True


def test_f2_controller_stops_before_apply_for_aliased_hook(tmp_path: Path):
    repo = _base_repo(tmp_path)
    helpers = repo / "helpers"
    helpers.mkdir()
    (helpers / "__init__.py").write_text("", encoding="utf-8")
    (helpers / "hooks.py").write_text("def collect(file_path, parent):\n    return None\n", encoding="utf-8")
    (repo / "conftest.py").write_text(
        "from helpers.hooks import collect as pytest_collect_file\n",
        encoding="utf-8",
    )
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
    assert (session.workspace_root / "checks.py").read_text(encoding="utf-8") == GUARD_ORIGINAL
    assert "return 1" in (session.workspace_root / "mod.py").read_text(encoding="utf-8")
    assert session.status is not SessionStatus.SUCCEEDED
