"""C1: partial pytest logs must not drop custom-collected test protection."""

from __future__ import annotations

import json
from pathlib import Path

from code_agent.controller import TaskController
from code_agent.llm import LLMClient
from code_agent.patching.test_collection import (
    INVENTORY_VERSION,
    PLUGIN_INVENTORY_SOURCE,
    InventoryCompleteness,
    TestInventoryReport,
    build_protected_inventory,
    load_test_inventory_file,
)
from code_agent.runtime.docker_config import DockerRunConfig
from code_agent.runtime.docker_pytest import CONTROLLED_BOOTSTRAP, attach_inventory_plugin
from code_agent.state import SessionStatus, TestResult

DUAL_DIFF = (
    "--- a/mod.py\n+++ b/mod.py\n@@ -1,2 +1,2 @@\n def f():\n-    return 1\n+    return 2\n"
    "--- a/checks.py\n+++ b/checks.py\n@@ -1,2 +1,2 @@\n def test_guard():\n"
    "-    assert 1 == 1\n+    assert True\n"
)
MOD_ONLY_DIFF = (
    "--- a/mod.py\n+++ b/mod.py\n@@ -1,2 +1,2 @@\n def f():\n-    return 1\n+    return 2\n"
)
GUARD_ORIGINAL = "def test_guard():\n    assert 1 == 1\n"


def _custom_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "pytest.ini").write_text("[pytest]\npythonpath = .\n", encoding="utf-8")
    (repo / "mod.py").write_text("def f():\n    return 1\n", encoding="utf-8")
    (repo / "test_mod.py").write_text(
        "from mod import f\ndef test_f():\n    assert f() == 2\n",
        encoding="utf-8",
    )
    (repo / "checks.py").write_text(GUARD_ORIGINAL, encoding="utf-8")
    (repo / "conftest.py").write_text(
        "import pytest\n"
        "def pytest_collect_file(file_path, parent):\n"
        '    if file_path.name == "checks.py":\n'
        "        return pytest.Module.from_parent(parent, path=file_path)\n",
        encoding="utf-8",
    )
    return repo


def _complete_report(*files: str) -> TestInventoryReport:
    return TestInventoryReport(
        completeness=InventoryCompleteness.COMPLETE,
        files=files,
        source=PLUGIN_INVENTORY_SOURCE,
        version=INVENTORY_VERSION,
    )


class _PartialLogRunner:
    def __init__(self, codes=(1, 0)):
        self.codes = codes
        self.calls = 0

    def run_pytest(self, workspace_root, *, log_path=None):
        code = self.codes[min(self.calls, len(self.codes) - 1)]
        self.calls += 1
        result = TestResult(
            exit_code=code,
            stdout="FAILED test_mod.py::test_f" if code else "2 passed",
            stderr="",
            duration_sec=0.01,
            failed_tests=["test_mod.py::test_f"] if code else [],
            collected_test_files=["test_mod.py::test_f"] if code else [],
        )
        if log_path:
            log_path.parent.mkdir(parents=True, exist_ok=True)
            log_path.write_text(result.stdout, encoding="utf-8")
        return result


class _CompleteInventoryRunner:
    def __init__(self, codes=(1, 0)):
        self.codes = codes
        self.calls = 0

    def run_pytest(self, workspace_root, *, log_path=None):
        code = self.codes[min(self.calls, len(self.codes) - 1)]
        self.calls += 1
        result = TestResult(
            exit_code=code,
            stdout="1 failed, 1 passed" if code else "2 passed",
            stderr="",
            duration_sec=0.01,
            failed_tests=["test_mod.py::test_f"] if code else [],
            test_inventory=_complete_report("test_mod.py", "checks.py"),
        )
        if log_path:
            log_path.parent.mkdir(parents=True, exist_ok=True)
            log_path.write_text(result.stdout, encoding="utf-8")
        return result


def test_c1_partial_isolated_nodeids_do_not_lift_fail_closed(tmp_path: Path):
    repo = _custom_repo(tmp_path)
    initial = build_protected_inventory(repo)
    partial = build_protected_inventory(repo, isolated_nodeids=["test_mod.py::test_f"])
    assert initial.fail_closed is True
    assert "checks.py" in initial.files
    assert "mod.py" in initial.files
    assert partial.fail_closed is True
    assert "checks.py" in partial.files
    assert partial.completeness is InventoryCompleteness.PARTIAL


def test_c1_missing_failed_empty_and_mixed_paths(tmp_path: Path):
    repo = _custom_repo(tmp_path)
    missing = build_protected_inventory(repo, report=TestInventoryReport())
    assert missing.fail_closed is True
    assert "checks.py" in missing.files

    failed = build_protected_inventory(
        repo,
        report=TestInventoryReport(
            completeness=InventoryCompleteness.FAILED,
            error="corrupt",
            source=PLUGIN_INVENTORY_SOURCE,
            version=INVENTORY_VERSION,
        ),
    )
    assert failed.fail_closed is True

    empty = build_protected_inventory(
        repo,
        report=TestInventoryReport(
            completeness=InventoryCompleteness.COMPLETE_EMPTY,
            source=PLUGIN_INVENTORY_SOURCE,
            version=INVENTORY_VERSION,
        ),
    )
    assert empty.fail_closed is True
    assert "checks.py" in empty.files

    mixed = build_protected_inventory(
        repo,
        isolated_nodeids=["test_mod.py::test_f", "../secret.py::t"],
    )
    assert mixed.fail_closed is True
    assert "checks.py" in mixed.files
    assert "mod.py" in mixed.files


def test_c1_complete_mixed_valid_invalid_does_not_lift(tmp_path: Path):
    repo = _custom_repo(tmp_path)
    inventory = build_protected_inventory(
        repo,
        report=TestInventoryReport(
            completeness=InventoryCompleteness.COMPLETE,
            files=("test_mod.py", "/etc/nope.py"),
            source=PLUGIN_INVENTORY_SOURCE,
            version=INVENTORY_VERSION,
        ),
    )
    assert inventory.fail_closed is True
    assert "checks.py" in inventory.files
    assert "mod.py" in inventory.files


def test_c1_unsafe_config_not_lifted_by_complete_inventory(tmp_path: Path):
    repo = tmp_path / "unsafe"
    repo.mkdir()
    (repo / "mod.py").write_text("VALUE = 1\n", encoding="utf-8")
    (repo / "test_mod.py").write_text("def test_f():\n    assert True\n", encoding="utf-8")
    (repo / "pytest.ini").write_text(
        "[pytest]\npython_files = **/*.py\n", encoding="utf-8"
    )
    inventory = build_protected_inventory(
        repo, report=_complete_report("test_mod.py")
    )
    assert inventory.fail_closed is True
    assert "mod.py" in inventory.files


def test_c1_complete_shaped_report_does_not_lift_without_trusted_origin(tmp_path: Path):
    repo = _custom_repo(tmp_path)
    inventory = build_protected_inventory(
        repo, report=_complete_report("/work/checks.py", "test_mod.py")
    )
    assert inventory.fail_closed is True
    assert "checks.py" in inventory.files
    assert "mod.py" in inventory.files


def test_c1_load_inventory_rejects_bad_payloads(tmp_path: Path):
    missing = load_test_inventory_file(tmp_path / "nope.json")
    assert missing.completeness is InventoryCompleteness.MISSING

    bad = tmp_path / "inv.json"
    bad.write_text("{not json", encoding="utf-8")
    assert load_test_inventory_file(bad).completeness is InventoryCompleteness.FAILED

    unsupported = tmp_path / "old.json"
    unsupported.write_text(
        json.dumps(
            {
                "version": 99,
                "source": PLUGIN_INVENTORY_SOURCE,
                "complete": True,
                "files": ["test_mod.py"],
            }
        ),
        encoding="utf-8",
    )
    assert load_test_inventory_file(unsupported).completeness is InventoryCompleteness.FAILED

    incomplete = tmp_path / "partial.json"
    incomplete.write_text(
        json.dumps(
            {
                "version": INVENTORY_VERSION,
                "source": PLUGIN_INVENTORY_SOURCE,
                "complete": False,
                "truncated": False,
                "files": ["test_mod.py"],
            }
        ),
        encoding="utf-8",
    )
    loaded = load_test_inventory_file(incomplete)
    assert loaded.completeness is InventoryCompleteness.PARTIAL

    complete_looking = tmp_path / "complete.json"
    complete_looking.write_text(
        json.dumps(
            {
                "version": INVENTORY_VERSION,
                "source": PLUGIN_INVENTORY_SOURCE,
                "complete": True,
                "truncated": False,
                "files": ["test_mod.py"],
            }
        ),
        encoding="utf-8",
    )
    trusted_looking = load_test_inventory_file(complete_looking)
    assert trusted_looking.completeness is InventoryCompleteness.UNVERIFIED
    assert trusted_looking.origin_trusted is False

    mixed_entry = tmp_path / "bad_entry.json"
    mixed_entry.write_text(
        json.dumps(
            {
                "version": INVENTORY_VERSION,
                "source": PLUGIN_INVENTORY_SOURCE,
                "complete": True,
                "files": ["test_mod.py", 12],
            }
        ),
        encoding="utf-8",
    )
    assert load_test_inventory_file(mixed_entry).completeness is InventoryCompleteness.FAILED


def test_c1_attach_plugin_does_not_change_config_payload():
    cfg = DockerRunConfig(image="safepatch-pytest:local")
    payload = cfg.to_dict()
    cmd = attach_inventory_plugin(cfg.build_docker_cmd("/tmp/work"), cfg)
    assert payload["pytest_argv"][-1] == "no:cacheprovider"
    assert cmd[-2:] == ["-p", "no:cacheprovider"]
    joined = " ".join(cmd)
    assert "/opt/safepatch:ro" in joined
    assert CONTROLLED_BOOTSTRAP in cmd
    assert "PYTHONPATH=/opt/safepatch" not in cmd
    assert "safepatch_inventory" not in cmd


def test_c1_partial_logs_block_weakening_custom_guard(tmp_path: Path):
    repo = _custom_repo(tmp_path)
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
        runner=_PartialLogRunner(),
        approve=lambda *_: True,
        session_base=tmp_path / "sessions",
    ).run(repo, "fix f")
    assert session.status != SessionStatus.SUCCEEDED
    assert session.attempts_used == 0
    assert (session.workspace_root / "checks.py").read_text(encoding="utf-8") == GUARD_ORIGINAL
    assert "return 1" in (session.workspace_root / "mod.py").read_text(encoding="utf-8")


def test_c1_complete_inventory_still_blocks_weakening_guard(tmp_path: Path):
    repo = _custom_repo(tmp_path)
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
        runner=_CompleteInventoryRunner(),
        approve=lambda *_: True,
        session_base=tmp_path / "sessions",
    ).run(repo, "fix f")
    assert session.status != SessionStatus.SUCCEEDED
    assert (session.workspace_root / "checks.py").read_text(encoding="utf-8") == GUARD_ORIGINAL


def test_c1_untrusted_complete_report_does_not_allow_custom_collector_business_fix(
    tmp_path: Path,
):
    repo = _custom_repo(tmp_path)
    proposal = {
        "tool": "propose_patch",
        "args": {
            "diagnosis": "return 2",
            "affected_files": ["mod.py"],
            "unified_diff": MOD_ONLY_DIFF,
            "expected_behavior": "tests pass",
            "risk_notes": "low",
            "tests_to_run": ["test_mod.py"],
        },
    }
    session = TaskController(
        llm=LLMClient(
            dry_run_script=[proposal, {"tool": "finish", "args": {"reason": "blocked"}}]
        ),
        runner=_CompleteInventoryRunner(),
        approve=lambda *_: True,
        session_base=tmp_path / "sessions",
    ).run(repo, "fix f")
    assert session.status != SessionStatus.SUCCEEDED
    assert session.attempts_used == 0
    assert "return 1" in (session.workspace_root / "mod.py").read_text(encoding="utf-8")
    assert (session.workspace_root / "checks.py").read_text(encoding="utf-8") == GUARD_ORIGINAL


def test_c1_standard_repo_allows_business_only_fix(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "mod.py").write_text("def f():\n    return 1\n", encoding="utf-8")
    tests = repo / "tests"
    tests.mkdir()
    (tests / "test_mod.py").write_text(
        "from mod import f\ndef test_f():\n    assert f() == 2\n",
        encoding="utf-8",
    )
    (repo / "pytest.ini").write_text("[pytest]\npythonpath = .\n", encoding="utf-8")
    proposal = {
        "tool": "propose_patch",
        "args": {
            "diagnosis": "return 2",
            "affected_files": ["mod.py"],
            "unified_diff": MOD_ONLY_DIFF,
            "expected_behavior": "return 2",
            "risk_notes": "low",
            "tests_to_run": ["tests/test_mod.py"],
        },
    }

    class _StdRunner:
        def __init__(self):
            self.calls = 0

        def run_pytest(self, workspace_root, *, log_path=None):
            self.calls += 1
            code = 1 if self.calls == 1 else 0
            result = TestResult(
                exit_code=code,
                stdout="FAILED tests/test_mod.py::test_f" if code else "1 passed",
                stderr="",
                duration_sec=0.01,
                failed_tests=["tests/test_mod.py::test_f"] if code else [],
            )
            if log_path:
                log_path.parent.mkdir(parents=True, exist_ok=True)
                log_path.write_text(result.stdout, encoding="utf-8")
            return result

    session = TaskController(
        llm=LLMClient(dry_run_script=[proposal]),
        runner=_StdRunner(),
        approve=lambda *_: True,
        session_base=tmp_path / "sessions",
    ).run(repo, "fix f")
    assert session.status is SessionStatus.SUCCEEDED
    assert "return 2" in (session.workspace_root / "mod.py").read_text(encoding="utf-8")
