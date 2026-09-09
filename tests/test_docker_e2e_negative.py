"""Stage D / P3: real Docker negative E2E (serial, marker=docker_e2e).

Requires Docker daemon + safepatch-pytest:local. No host-pytest fallback.
"""

from __future__ import annotations

import json
import subprocess
import threading
import time
import uuid
from dataclasses import replace
from pathlib import Path

import pytest

from code_agent.runtime.docker_config import DockerRunConfig
from code_agent.runtime.docker_pytest import DockerPytestRunner, _sanitized_subprocess_env


def _unique_name() -> str:
    return f"safepatch-test-{uuid.uuid4().hex[:12]}"


def _require_docker() -> DockerPytestRunner:
    runner = DockerPytestRunner()
    ok, reason = runner.preflight()
    if not ok:
        pytest.skip(f"Docker daemon unavailable: {reason}")
    return runner


def _write_repo(root: Path, test_source: str) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    (root / "pytest.ini").write_text("[pytest]\npythonpath = .\n", encoding="utf-8")
    tests = root / "tests"
    tests.mkdir(exist_ok=True)
    (tests / "test_case.py").write_text(test_source, encoding="utf-8", newline="\n")
    return root


def _container_exists(name: str) -> bool:
    proc = subprocess.run(
        ["docker", "ps", "-a", "--format", "{{.Names}}"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
        env=_sanitized_subprocess_env(),
    )
    names = {line.strip() for line in (proc.stdout or "").splitlines() if line.strip()}
    return name in names


def _inspect_network_mode(name: str) -> str | None:
    proc = subprocess.run(
        ["docker", "inspect", "-f", "{{.HostConfig.NetworkMode}}", name],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
        env=_sanitized_subprocess_env(),
    )
    if proc.returncode != 0:
        return None
    return (proc.stdout or "").strip() or None


def _evidence(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def test_product_default_timeout_still_120():
    cfg = DockerRunConfig(image="safepatch-pytest:local")
    assert cfg.timeout_seconds == 120
    runner = DockerPytestRunner()
    assert runner.make_run_config("safepatch-pytest:local").timeout_seconds == 120


def test_docker_run_config_is_single_source_for_e2e_cmd():
    name = _unique_name()
    cfg = DockerRunConfig(
        image="safepatch-pytest:local",
        timeout_seconds=2,
        container_name=name,
    )
    payload = cfg.to_dict()
    cmd = cfg.build_docker_cmd("/tmp/work")
    assert payload["network_mode"] == "none"
    assert payload["user"] == "1000:1000"
    assert payload["timeout_seconds"] == 2
    assert payload["container_name"] == name
    assert payload["remove_container"] is True
    assert "--network" in cmd and cmd[cmd.index("--network") + 1] == "none"
    assert "--name" in cmd and cmd[cmd.index("--name") + 1] == name
    assert "--user" in cmd and cmd[cmd.index("--user") + 1] == "1000:1000"
    assert "OPENAI" not in " ".join(cmd)
    assert "API_KEY" not in " ".join(cmd)


@pytest.mark.docker_e2e
def test_network_none_config_and_socket_blocked(tmp_path: Path):
    runner = _require_docker()
    name = _unique_name()
    workspace = _write_repo(
        tmp_path / "network_blocked",
        """
import pathlib
import socket
import time

def test_external_network_is_blocked():
    pathlib.Path("/work/.e2e_ready").write_text("1", encoding="utf-8")
    # Give the host a short window to docker-inspect NetworkMode.
    time.sleep(3)
    sock = socket.socket()
    sock.settimeout(2)
    try:
        result = sock.connect_ex(("1.1.1.1", 53))
        assert result != 0, f"unexpected connect success: {result}"
    finally:
        sock.close()
""".lstrip(),
    )
    image = runner._resolve_image()  # noqa: SLF001
    assert image
    cfg = replace(
        runner.make_run_config(image),
        container_name=name,
    )
    assert cfg.network_mode == "none"
    assert cfg.to_dict()["network_mode"] == "none"

    inspected: dict[str, str | None] = {"network_mode": None}

    def _inspect_while_running() -> None:
        for _ in range(80):
            inspected["network_mode"] = _inspect_network_mode(name)
            if inspected["network_mode"] is not None:
                break
            time.sleep(0.1)

    thread = threading.Thread(target=_inspect_while_running, daemon=True)
    thread.start()
    log_path = tmp_path / "network.log"
    result = runner.run_pytest(workspace, log_path=log_path, config=cfg)
    thread.join(timeout=15)

    assert runner.last_run_config is not None
    assert runner.last_run_config.to_dict() == cfg.to_dict()
    assert inspected["network_mode"] == "none", inspected
    assert result.error_kind is None
    assert result.exit_code == 0, (result.stdout, result.stderr)
    assert result.passed
    assert not _container_exists(name)

    _evidence(
        tmp_path / "network_evidence.json",
        {
            "docker_config": cfg.to_dict(),
            "inspect_network_mode": inspected["network_mode"],
            "result": {
                "exit_code": result.exit_code,
                "passed": result.passed,
                "timed_out": False,
                "environment_error": result.environment_error,
                "duration_seconds": result.duration_sec,
            },
        },
    )


@pytest.mark.docker_e2e
def test_process_runs_as_uid_1000(tmp_path: Path):
    runner = _require_docker()
    name = _unique_name()
    workspace = _write_repo(
        tmp_path / "non_root",
        """
import os
from pathlib import Path

def test_process_is_not_root():
    uid = os.geteuid()
    print(f"E2E_UID={uid}")
    Path("/work/uid.txt").write_text(str(uid), encoding="utf-8")
    assert uid != 0
    assert uid == 1000
""".lstrip(),
    )
    image = runner._resolve_image()  # noqa: SLF001
    cfg = replace(runner.make_run_config(image), container_name=name)
    assert cfg.user == "1000:1000"
    log_path = tmp_path / "non_root.log"
    result = runner.run_pytest(workspace, log_path=log_path, config=cfg)
    assert result.exit_code == 0, (result.stdout, result.stderr)
    # The in-container test's uid == 1000 assertion is the evidence. Pytest's
    # default fd capture intentionally omits the print from successful -q output.
    assert not (workspace / "uid.txt").exists()
    assert not _container_exists(name)
    _evidence(
        tmp_path / "non_root_evidence.json",
        {
            "docker_config": cfg.to_dict(),
            "geteuid": 1000,
            "result": {
                "exit_code": result.exit_code,
                "passed": result.passed,
                "timed_out": False,
                "environment_error": result.environment_error,
                "duration_seconds": result.duration_sec,
            },
        },
    )


@pytest.mark.docker_e2e
def test_timeout_returns_independent_timeout_kind(tmp_path: Path):
    """Product-default config (container_name=None) must still allocate and clean up."""
    runner = _require_docker()
    workspace = _write_repo(
        tmp_path / "timeout",
        """
import time

def test_never_finishes():
    time.sleep(30)
""".lstrip(),
    )
    image = runner._resolve_image()  # noqa: SLF001
    # Intentionally do not inject container_name — exercise product default path.
    cfg = replace(runner.make_run_config(image), timeout_seconds=2)
    assert cfg.container_name is None
    assert DockerRunConfig(image=image).timeout_seconds == 120  # product default
    assert cfg.timeout_seconds == 2

    log_path = tmp_path / "timeout.log"
    started = time.time()
    result = runner.run_pytest(workspace, log_path=log_path, config=cfg)
    elapsed = time.time() - started

    assert result.error_kind == "timeout"
    assert result.environment_error is not None
    assert result.environment_error.startswith("TEST_TIMEOUT:")
    assert "2s" in result.environment_error
    assert result.passed is False
    # Not an assertion failure classification.
    assert not result.failed_tests
    assert result.error_kind != "environment"
    assert elapsed < 20
    assert result.duration_sec >= 1.5
    assert runner.last_run_config is not None
    name = runner.last_run_config.container_name
    assert name and name.startswith("safepatch-pytest-")
    log_text = log_path.read_text(encoding="utf-8")
    assert "timeout_seconds: 2" in log_text
    assert "TEST_TIMEOUT" in log_text
    assert f"container_name: {name}" in log_text
    # docker inspect should report the name no longer exists.
    inspect = subprocess.run(
        ["docker", "inspect", name],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
        env=_sanitized_subprocess_env(),
    )
    assert inspect.returncode != 0
    assert not _container_exists(name)
    running = subprocess.run(
        ["docker", "ps", "--filter", f"name=^{name}$", "--format", "{{.Names}}"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
        env=_sanitized_subprocess_env(),
    )
    assert name not in (running.stdout or "")

    _evidence(
        tmp_path / "timeout_evidence.json",
        {
            "docker_config": runner.last_run_config.to_dict(),
            "allocated_container_name": name,
            "docker_inspect_returncode": inspect.returncode,
            "result": {
                "exit_code": result.exit_code if result.exit_code >= 0 else None,
                "passed": result.passed,
                "timed_out": True,
                "environment_error": result.environment_error,
                "error_kind": result.error_kind,
                "duration_seconds": result.duration_sec,
                "failed_tests": result.failed_tests,
            },
        },
    )


@pytest.mark.docker_e2e
def test_container_removed_after_pytest_success(tmp_path: Path):
    runner = _require_docker()
    name = _unique_name()
    workspace = _write_repo(
        tmp_path / "ok",
        "def test_ok():\n    assert 1 + 1 == 2\n",
    )
    image = runner._resolve_image()  # noqa: SLF001
    cfg = replace(runner.make_run_config(image), container_name=name)
    result = runner.run_pytest(workspace, config=cfg)
    assert result.exit_code == 0
    assert not _container_exists(name)


@pytest.mark.docker_e2e
def test_container_removed_after_pytest_failure(tmp_path: Path):
    runner = _require_docker()
    name = _unique_name()
    workspace = _write_repo(
        tmp_path / "fail",
        "def test_fail():\n    assert False\n",
    )
    image = runner._resolve_image()  # noqa: SLF001
    cfg = replace(runner.make_run_config(image), container_name=name)
    result = runner.run_pytest(workspace, config=cfg)
    assert result.exit_code != 0
    assert result.error_kind is None  # assertion failure, not infra
    assert not _container_exists(name)


@pytest.mark.docker_e2e
def test_container_removed_after_timeout(tmp_path: Path):
    runner = _require_docker()
    workspace = _write_repo(
        tmp_path / "timeout_cleanup",
        "import time\n\ndef test_never_finishes():\n    time.sleep(30)\n",
    )
    image = runner._resolve_image()  # noqa: SLF001
    cfg = replace(runner.make_run_config(image), timeout_seconds=2)
    assert cfg.container_name is None
    result = runner.run_pytest(workspace, config=cfg)
    assert result.error_kind == "timeout"
    assert runner.last_run_config is not None
    name = runner.last_run_config.container_name
    assert name and name.startswith("safepatch-pytest-")
    # Allow brief daemon settle, then confirm absence.
    time.sleep(0.5)
    inspect = subprocess.run(
        ["docker", "inspect", name],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
        env=_sanitized_subprocess_env(),
    )
    assert inspect.returncode != 0
    assert not _container_exists(name)


@pytest.mark.docker_e2e
def test_pytest_side_effects_cannot_mutate_formal_working_copy(tmp_path: Path):
    runner = _require_docker()
    workspace = _write_repo(
        tmp_path / "copy_isolation",
        """
from pathlib import Path

def test_writes_are_confined_to_test_copy():
    Path("/work/mod.py").write_text("mutated\\n", encoding="utf-8")
    Path("/work/ordinary.tmp").write_text("temporary\\n", encoding="utf-8")
    assert Path("/work/ordinary.tmp").read_text(encoding="utf-8") == "temporary\\n"
""".lstrip(),
    )
    (workspace / "mod.py").write_text("ORIGINAL = True\n", encoding="utf-8")
    before = (workspace / "mod.py").read_bytes()
    result = runner.run_pytest(workspace)
    assert result.exit_code == 0, (result.stdout, result.stderr)
    assert (workspace / "mod.py").read_bytes() == before
    assert not (workspace / "ordinary.tmp").exists()


def test_no_host_pytest_fallback_when_docker_missing(monkeypatch, tmp_path: Path):
    """If preflight fails, runner returns environment error — never host pytest."""
    runner = DockerPytestRunner()
    monkeypatch.setattr(
        runner, "preflight", lambda: (False, "Docker daemon unavailable")
    )
    workspace = _write_repo(tmp_path / "x", "def test_ok():\n    assert True\n")
    # Poison host execution: if someone shells out to host pytest it would see this.
    result = runner.run_pytest(workspace)
    assert result.error_kind == "environment"
    assert result.environment_error is not None
    assert result.environment_error.startswith("TEST_ENVIRONMENT_ERROR:")
    assert "Docker daemon unavailable" in result.environment_error


def _custom_collector_repo(root: Path) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    (root / "pytest.ini").write_text("[pytest]\npythonpath = .\n", encoding="utf-8")
    (root / "mod.py").write_text("def f():\n    return 1\n", encoding="utf-8")
    (root / "test_mod.py").write_text(
        "from mod import f\ndef test_f():\n    assert f() == 2\n",
        encoding="utf-8",
    )
    (root / "checks.py").write_text(
        "def test_guard():\n    assert 1 == 1\n", encoding="utf-8"
    )
    (root / "conftest.py").write_text(
        "import pytest\n"
        "def pytest_collect_file(file_path, parent):\n"
        '    if file_path.name == "checks.py":\n'
        "        return pytest.Module.from_parent(parent, path=file_path)\n",
        encoding="utf-8",
    )
    return root


_SHADOW_PLUGIN = (
    "import json\nfrom pathlib import Path\n"
    "def pytest_sessionfinish(session, exitstatus):\n"
    '    print("SYNTHETIC_PLUGIN_ORIGIN=" + __file__)\n'
    "    payload = {"
    '"version":1,"source":"safepatch_inventory_plugin",'
    '"complete":True,"truncated":False,"files":["/work/test_mod.py"]}\n'
    '    Path("/work/.safepatch_pytest_inventory.json").write_text(json.dumps(payload))\n'
)


@pytest.mark.docker_e2e
def test_d1_same_name_py_does_not_replace_controlled_plugin(tmp_path: Path):
    from code_agent.patching.test_collection import InventoryCompleteness

    runner = _require_docker()
    workspace = _custom_collector_repo(tmp_path / "shadow_py")
    (workspace / "safepatch_inventory.py").write_text(_SHADOW_PLUGIN, encoding="utf-8")
    result = runner.run_pytest(workspace)
    combined = result.stdout + "\n" + result.stderr
    assert result.environment_error is None, result.environment_error
    assert "SAFEPATCH_CONTROLLED_PLUGIN_ORIGIN=/opt/safepatch/safepatch_inventory.py" in combined
    assert "SYNTHETIC_PLUGIN_ORIGIN=/work/safepatch_inventory.py" not in combined
    inventory = result.test_inventory
    assert inventory is not None
    assert inventory.origin_trusted is False
    assert inventory.completeness is not InventoryCompleteness.COMPLETE


@pytest.mark.docker_e2e
def test_d1_same_name_package_does_not_replace_controlled_plugin(tmp_path: Path):
    runner = _require_docker()
    workspace = _custom_collector_repo(tmp_path / "shadow_pkg")
    pkg = workspace / "safepatch_inventory"
    pkg.mkdir()
    (pkg / "__init__.py").write_text(_SHADOW_PLUGIN, encoding="utf-8")
    result = runner.run_pytest(workspace)
    combined = result.stdout + "\n" + result.stderr
    assert result.environment_error is None, result.environment_error
    assert "SAFEPATCH_CONTROLLED_PLUGIN_ORIGIN=/opt/safepatch/safepatch_inventory.py" in combined
    assert "SYNTHETIC_PLUGIN_ORIGIN=/work/safepatch_inventory" not in combined


@pytest.mark.docker_e2e
def test_d1_preseeded_inventory_is_not_complete(tmp_path: Path):
    from code_agent.patching.test_collection import InventoryCompleteness

    runner = _require_docker()
    workspace = _custom_collector_repo(tmp_path / "preseed")
    (workspace / ".safepatch_pytest_inventory.json").write_text(
        json.dumps(
            {
                "version": 1,
                "source": "safepatch_inventory_plugin",
                "complete": True,
                "truncated": False,
                "files": ["/work/test_mod.py"],
            }
        ),
        encoding="utf-8",
    )
    result = runner.run_pytest(workspace)
    inventory = result.test_inventory
    assert inventory is not None
    assert inventory.origin_trusted is False
    assert inventory.completeness in {
        InventoryCompleteness.UNVERIFIED,
        InventoryCompleteness.PARTIAL,
        InventoryCompleteness.MISSING,
        InventoryCompleteness.FAILED,
    }
    assert inventory.completeness is not InventoryCompleteness.COMPLETE


@pytest.mark.docker_e2e
def test_d1_shadowed_plugin_blocks_weakening_custom_guard(tmp_path: Path):
    from code_agent.controller import TaskController
    from code_agent.llm import LLMClient
    from code_agent.state import SessionStatus

    _require_docker()
    workspace = _custom_collector_repo(tmp_path / "probe")
    (workspace / "safepatch_inventory.py").write_text(_SHADOW_PLUGIN, encoding="utf-8")
    original_guard = (workspace / "checks.py").read_text(encoding="utf-8")
    proposal = {
        "tool": "propose_patch",
        "args": {
            "diagnosis": "fix return and weaken guard",
            "affected_files": ["mod.py", "checks.py"],
            "unified_diff": (
                "--- a/mod.py\n+++ b/mod.py\n@@ -1,2 +1,2 @@\n def f():\n"
                "-    return 1\n+    return 2\n"
                "--- a/checks.py\n+++ b/checks.py\n@@ -1,2 +1,2 @@\n"
                " def test_guard():\n-    assert 1 == 1\n+    assert True\n"
            ),
            "expected_behavior": "tests pass",
            "risk_notes": "synthetic",
            "tests_to_run": ["test_mod.py"],
        },
    }
    session = TaskController(
        llm=LLMClient(
            dry_run_script=[
                proposal,
                {"tool": "finish", "args": {"reason": "blocked"}},
            ]
        ),
        runner=DockerPytestRunner(),
        approve=lambda *_: True,
        session_base=tmp_path / "sessions",
    ).run(workspace, "fix f")
    assert session.status is not SessionStatus.SUCCEEDED
    assert session.attempts_used == 0
    assert (session.workspace_root / "checks.py").read_text(encoding="utf-8") == original_guard
    assert "return 1" in (session.workspace_root / "mod.py").read_text(encoding="utf-8")


@pytest.mark.docker_e2e
def test_c1_docker_plugin_records_custom_collected_file(tmp_path: Path):
    from code_agent.patching.test_collection import InventoryCompleteness

    runner = _require_docker()
    workspace = tmp_path / "custom_collect"
    workspace.mkdir()
    (workspace / "pytest.ini").write_text("[pytest]\npythonpath = .\n", encoding="utf-8")
    (workspace / "mod.py").write_text("def f():\n    return 1\n", encoding="utf-8")
    (workspace / "test_mod.py").write_text(
        "from mod import f\ndef test_f():\n    assert f() == 1\n",
        encoding="utf-8",
    )
    (workspace / "checks.py").write_text(
        "def test_guard():\n    assert 1 == 1\n", encoding="utf-8"
    )
    (workspace / "conftest.py").write_text(
        "import pytest\n"
        "def pytest_collect_file(file_path, parent):\n"
        '    if file_path.name == "checks.py":\n'
        "        return pytest.Module.from_parent(parent, path=file_path)\n",
        encoding="utf-8",
    )
    result = runner.run_pytest(workspace)
    assert result.environment_error is None, result.environment_error
    assert result.exit_code == 0, (result.stdout, result.stderr)
    combined = result.stdout + "\n" + result.stderr
    assert "SAFEPATCH_CONTROLLED_PLUGIN_ORIGIN=/opt/safepatch/safepatch_inventory.py" in combined
    inventory = result.test_inventory
    assert inventory is not None
    assert inventory.origin_trusted is False
    assert inventory.completeness is not InventoryCompleteness.COMPLETE


def _standard_tests_repo(root: Path, *, return_value: int, with_init: bool) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    (root / "mod.py").write_text(f"def f():\n    return {return_value}\n", encoding="utf-8")
    tests = root / "tests"
    tests.mkdir()
    if with_init:
        (tests / "__init__.py").write_text("", encoding="utf-8")
    (tests / "test_mod.py").write_text(
        "from mod import f\ndef test_f():\n    assert f() == 2\n",
        encoding="utf-8",
    )
    return root


@pytest.mark.docker_e2e
@pytest.mark.parametrize("with_init", [False, True], ids=["no_init", "pkg_init"])
def test_e1_standard_repo_without_pythonpath_passes(tmp_path: Path, with_init: bool):
    runner = _require_docker()
    workspace = _standard_tests_repo(
        tmp_path / f"simple_{with_init}", return_value=2, with_init=with_init
    )
    result = runner.run_pytest(workspace)
    assert result.environment_error is None, result.environment_error
    assert result.exit_code == 0, (result.stdout, result.stderr)
    combined = result.stdout + "\n" + result.stderr
    assert "No module named 'mod'" not in combined
    assert "SAFEPATCH_CONTROLLED_PLUGIN_ORIGIN=/opt/safepatch/safepatch_inventory.py" in combined


@pytest.mark.docker_e2e
def test_e1_standard_repo_wrong_return_is_assertion_failure(tmp_path: Path):
    runner = _require_docker()
    workspace = _standard_tests_repo(tmp_path / "failing", return_value=1, with_init=False)
    result = runner.run_pytest(workspace)
    assert result.environment_error is None, result.environment_error
    assert result.exit_code == 1, (result.stdout, result.stderr)
    combined = result.stdout + "\n" + result.stderr
    assert "assert 1 == 2" in combined or "AssertionError" in combined
    assert result.error_kind not in {"environment", "timeout"}


@pytest.mark.docker_e2e
def test_e2_pytest_plugins_blocks_weakening_custom_guard(tmp_path: Path):
    from code_agent.controller import TaskController
    from code_agent.llm import LLMClient
    from code_agent.state import SessionStatus

    _require_docker()
    workspace = tmp_path / "indirect"
    workspace.mkdir()
    (workspace / "pytest.ini").write_text("[pytest]\npythonpath = .\n", encoding="utf-8")
    (workspace / "mod.py").write_text("def f():\n    return 1\n", encoding="utf-8")
    (workspace / "test_mod.py").write_text(
        "from mod import f\ndef test_f():\n    assert f() == 2\n",
        encoding="utf-8",
    )
    original_guard = "def test_guard():\n    assert 1 == 1\n"
    (workspace / "checks.py").write_text(original_guard, encoding="utf-8")
    (workspace / "conftest.py").write_text('pytest_plugins = ["collector"]\n', encoding="utf-8")
    (workspace / "collector.py").write_text(
        "import pytest\n"
        "def pytest_collect_file(file_path, parent):\n"
        '    if file_path.name == "checks.py":\n'
        "        return pytest.Module.from_parent(parent, path=file_path)\n",
        encoding="utf-8",
    )
    proposal = {
        "tool": "propose_patch",
        "args": {
            "diagnosis": "fix return and weaken guard",
            "affected_files": ["mod.py", "checks.py"],
            "unified_diff": (
                "--- a/mod.py\n+++ b/mod.py\n@@ -1,2 +1,2 @@\n def f():\n"
                "-    return 1\n+    return 2\n"
                "--- a/checks.py\n+++ b/checks.py\n@@ -1,2 +1,2 @@\n"
                " def test_guard():\n-    assert 1 == 1\n+    assert True\n"
            ),
            "expected_behavior": "tests pass",
            "risk_notes": "synthetic",
            "tests_to_run": ["test_mod.py"],
        },
    }
    session = TaskController(
        llm=LLMClient(dry_run_script=[proposal]),
        runner=DockerPytestRunner(),
        approve=lambda *_: True,
        session_base=tmp_path / "sessions",
    ).run(workspace, "fix f")
    assert session.status is SessionStatus.PATCH_NOT_APPLICABLE
    assert session.stop_reason == "unsupported_pytest_collection"
    assert session.attempts_used == 0
    assert (session.workspace_root / "checks.py").read_text(encoding="utf-8") == original_guard
    assert "return 1" in (session.workspace_root / "mod.py").read_text(encoding="utf-8")
    assert session.status is not SessionStatus.SUCCEEDED


@pytest.mark.docker_e2e
def test_e2_standard_repo_business_only_fix_still_succeeds(tmp_path: Path):
    from code_agent.controller import TaskController
    from code_agent.llm import LLMClient
    from code_agent.state import SessionStatus

    _require_docker()
    workspace = _standard_tests_repo(tmp_path / "biz", return_value=1, with_init=False)
    proposal = {
        "tool": "propose_patch",
        "args": {
            "diagnosis": "return 2",
            "affected_files": ["mod.py"],
            "unified_diff": (
                "--- a/mod.py\n+++ b/mod.py\n@@ -1,2 +1,2 @@\n def f():\n"
                "-    return 1\n+    return 2\n"
            ),
            "expected_behavior": "return 2",
            "risk_notes": "low",
            "tests_to_run": ["tests/test_mod.py"],
        },
    }
    session = TaskController(
        llm=LLMClient(dry_run_script=[proposal]),
        runner=DockerPytestRunner(),
        approve=lambda *_: True,
        session_base=tmp_path / "sessions",
    ).run(workspace, "fix f")
    assert session.status is SessionStatus.SUCCEEDED
    assert session.baseline is not None
    assert session.baseline.exit_code == 1
    assert session.attempts
    post = session.attempts[-1].test_result
    assert post is not None
    assert post.exit_code == 0
    assert "1 passed" in (post.stdout or "")
    assert "return 2" in (session.workspace_root / "mod.py").read_text(encoding="utf-8")


_F12_DUAL = (
    "--- a/mod.py\n+++ b/mod.py\n@@ -1,2 +1,2 @@\n def f():\n-    return 1\n+    return 2\n"
    "--- a/checks.py\n+++ b/checks.py\n@@ -1,2 +1,2 @@\n def test_guard():\n"
    "-    assert 1 == 1\n+    assert True\n"
)


def _f12_weakening_repo(root: Path) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    (root / "mod.py").write_text("def f():\n    return 1\n", encoding="utf-8")
    (root / "test_mod.py").write_text(
        "from mod import f\ndef test_f():\n    assert f() == 2\n",
        encoding="utf-8",
    )
    (root / "checks.py").write_text("def test_guard():\n    assert 1 == 1\n", encoding="utf-8")
    return root


def _f12_run_dual_patch(tmp_path: Path, workspace: Path):
    from code_agent.controller import TaskController
    from code_agent.llm import LLMClient
    from code_agent.state import SessionStatus

    original_guard = (workspace / "checks.py").read_text(encoding="utf-8")
    session = TaskController(
        llm=LLMClient(
            dry_run_script=[
                {
                    "tool": "propose_patch",
                    "args": {
                        "diagnosis": "fix return and weaken guard",
                        "affected_files": ["mod.py", "checks.py"],
                        "unified_diff": _F12_DUAL,
                        "expected_behavior": "tests pass",
                        "risk_notes": "synthetic",
                        "tests_to_run": ["test_mod.py"],
                    },
                }
            ]
        ),
        runner=DockerPytestRunner(),
        approve=lambda *_: True,
        session_base=tmp_path / "sessions",
    ).run(workspace, "fix f")
    return session, original_guard, SessionStatus


@pytest.mark.docker_e2e
def test_f1_config_precedence_blocks_weakening_checks(tmp_path: Path):
    _require_docker()
    workspace = _f12_weakening_repo(tmp_path / "config_precedence")
    (workspace / "setup.cfg").write_text("[tool:pytest]\naddopts = -q\n", encoding="utf-8")
    (workspace / "pyproject.toml").write_text(
        '[tool.pytest.ini_options]\npython_files = ["test_*.py", "checks.py"]\n',
        encoding="utf-8",
    )
    session, original_guard, SessionStatus = _f12_run_dual_patch(tmp_path, workspace)
    assert session.baseline is not None
    assert session.baseline.exit_code == 1
    combined = (session.baseline.stdout or "") + (session.baseline.stderr or "")
    assert "1 failed, 1 passed" in combined or "1 passed" in combined
    assert session.status is SessionStatus.PATCH_NOT_APPLICABLE
    assert session.stop_reason == "unsupported_pytest_collection"
    assert session.attempts_used == 0
    assert (session.workspace_root / "checks.py").read_text(encoding="utf-8") == original_guard
    assert "assert True" not in (session.workspace_root / "checks.py").read_text(encoding="utf-8")
    assert session.status is not SessionStatus.SUCCEEDED


@pytest.mark.docker_e2e
def test_f2_aliased_imported_hook_blocks_weakening_checks(tmp_path: Path):
    _require_docker()
    workspace = _f12_weakening_repo(tmp_path / "imported_hook_alias")
    helpers = workspace / "helpers"
    helpers.mkdir()
    (helpers / "__init__.py").write_text("", encoding="utf-8")
    (helpers / "hooks.py").write_text(
        "import pytest\n"
        "def collect(file_path, parent):\n"
        '    if file_path.name == "checks.py":\n'
        "        return pytest.Module.from_parent(parent, path=file_path)\n",
        encoding="utf-8",
    )
    (workspace / "conftest.py").write_text(
        "from helpers.hooks import collect as pytest_collect_file\n",
        encoding="utf-8",
    )
    session, original_guard, SessionStatus = _f12_run_dual_patch(tmp_path, workspace)
    assert session.baseline is not None
    assert session.baseline.exit_code == 1
    combined = (session.baseline.stdout or "") + (session.baseline.stderr or "")
    assert "1 failed, 1 passed" in combined or "1 passed" in combined
    assert session.status is SessionStatus.PATCH_NOT_APPLICABLE
    assert session.attempts_used == 0
    assert (session.workspace_root / "checks.py").read_text(encoding="utf-8") == original_guard
    assert session.status is not SessionStatus.SUCCEEDED


@pytest.mark.docker_e2e
def test_g1_assigned_hook_blocks_weakening_checks(tmp_path: Path):
    _require_docker()
    workspace = _f12_weakening_repo(tmp_path / "hook_binding")
    (workspace / "conftest.py").write_text(
        "import pytest\n"
        "def collect(file_path, parent):\n"
        '    if file_path.name == "checks.py":\n'
        "        return pytest.Module.from_parent(parent, path=file_path)\n"
        "pytest_collect_file = collect\n",
        encoding="utf-8",
    )
    session, original_guard, SessionStatus = _f12_run_dual_patch(tmp_path, workspace)
    assert session.baseline is not None
    assert session.baseline.exit_code == 1
    combined = (session.baseline.stdout or "") + (session.baseline.stderr or "")
    assert "1 failed, 1 passed" in combined or "1 passed" in combined
    assert session.status is SessionStatus.PATCH_NOT_APPLICABLE
    assert session.stop_reason == "unsupported_pytest_collection"
    assert session.attempts_used == 0
    assert (session.workspace_root / "checks.py").read_text(encoding="utf-8") == original_guard
    assert "assert True" not in (session.workspace_root / "checks.py").read_text(encoding="utf-8")
    assert "return 1" in (session.workspace_root / "mod.py").read_text(encoding="utf-8")
    assert session.status is not SessionStatus.SUCCEEDED


@pytest.mark.docker_e2e
def test_g1_fixture_only_conftest_is_unsupported(tmp_path: Path):
    from code_agent.controller import TaskController
    from code_agent.llm import LLMClient
    from code_agent.state import SessionStatus

    _require_docker()
    workspace = _standard_tests_repo(tmp_path / "fixture_only", return_value=1, with_init=False)
    (workspace / "tests" / "conftest.py").write_text(
        "import pytest\n@pytest.fixture\ndef value():\n    return 1\n",
        encoding="utf-8",
    )
    proposal = {
        "tool": "propose_patch",
        "args": {
            "diagnosis": "return 2",
            "affected_files": ["mod.py"],
            "unified_diff": (
                "--- a/mod.py\n+++ b/mod.py\n@@ -1,2 +1,2 @@\n def f():\n"
                "-    return 1\n+    return 2\n"
            ),
            "expected_behavior": "return 2",
            "risk_notes": "low",
            "tests_to_run": ["tests/test_mod.py"],
        },
    }
    original_mod = (workspace / "mod.py").read_text(encoding="utf-8")
    session = TaskController(
        llm=LLMClient(dry_run_script=[proposal]),
        runner=DockerPytestRunner(),
        approve=lambda *_: True,
        session_base=tmp_path / "sessions",
    ).run(workspace, "fix f")
    assert session.status is SessionStatus.PATCH_NOT_APPLICABLE
    assert session.stop_reason == "unsupported_pytest_collection"
    assert session.attempts_used == 0
    assert session.status is not SessionStatus.SUCCEEDED
    assert (session.workspace_root / "mod.py").read_text(encoding="utf-8") == original_mod
    assert any("executable conftest" in item for item in session.pytest_protection_reasons)


_H1_REGISTER = (
    "globals().__setitem__('pytest_collect_file', lambda file_path, parent: "
    "pytest.Module.from_parent(parent, path=file_path) "
    "if file_path.name == 'checks.py' else None)"
)
_H1_VARIANTS = {
    "default_argument": (
        "import pytest\n@pytest.fixture\ndef value(_=" + _H1_REGISTER + "):\n    return 1\n"
    ),
    "decorator_argument": (
        "import pytest\n@pytest.fixture(scope=(" + _H1_REGISTER + ', "function")[1])\n'
        "def value():\n    return 1\n"
    ),
    "return_annotation": (
        "import pytest\n@pytest.fixture\ndef value() -> " + _H1_REGISTER + ":\n    return 1\n"
    ),
}


@pytest.mark.docker_e2e
@pytest.mark.parametrize("name", list(_H1_VARIANTS))
def test_h1_definition_expressions_block_weakening_checks(tmp_path: Path, name: str):
    _require_docker()
    workspace = _f12_weakening_repo(tmp_path / name)
    (workspace / "conftest.py").write_text(_H1_VARIANTS[name], encoding="utf-8")
    session, original_guard, SessionStatus = _f12_run_dual_patch(tmp_path, workspace)
    assert session.baseline is not None
    assert session.baseline.exit_code == 1
    combined = (session.baseline.stdout or "") + (session.baseline.stderr or "")
    assert "1 failed, 1 passed" in combined or "1 passed" in combined
    assert session.status is SessionStatus.PATCH_NOT_APPLICABLE
    assert session.stop_reason == "unsupported_pytest_collection"
    assert session.attempts_used == 0
    assert (session.workspace_root / "checks.py").read_text(encoding="utf-8") == original_guard
    assert "assert True" not in (session.workspace_root / "checks.py").read_text(encoding="utf-8")
    assert "return 1" in (session.workspace_root / "mod.py").read_text(encoding="utf-8")
    assert session.status is not SessionStatus.SUCCEEDED
    assert any("executable conftest" in item for item in session.pytest_protection_reasons)


@pytest.mark.docker_e2e
@pytest.mark.parametrize(
    "conftest",
    ["", "# comment only\n", '"""docs"""\npass\n'],
    ids=["empty", "comment", "docstring_pass"],
)
def test_h1_inert_conftest_business_fix_succeeds(tmp_path: Path, conftest: str):
    from code_agent.controller import TaskController
    from code_agent.llm import LLMClient
    from code_agent.state import SessionStatus

    _require_docker()
    workspace = _standard_tests_repo(tmp_path / "inert", return_value=1, with_init=False)
    (workspace / "tests" / "conftest.py").write_text(conftest, encoding="utf-8")
    proposal = {
        "tool": "propose_patch",
        "args": {
            "diagnosis": "return 2",
            "affected_files": ["mod.py"],
            "unified_diff": (
                "--- a/mod.py\n+++ b/mod.py\n@@ -1,2 +1,2 @@\n def f():\n"
                "-    return 1\n+    return 2\n"
            ),
            "expected_behavior": "return 2",
            "risk_notes": "low",
            "tests_to_run": ["tests/test_mod.py"],
        },
    }
    session = TaskController(
        llm=LLMClient(dry_run_script=[proposal]),
        runner=DockerPytestRunner(),
        approve=lambda *_: True,
        session_base=tmp_path / "sessions",
    ).run(workspace, "fix f")
    assert session.status is SessionStatus.SUCCEEDED
    assert session.baseline is not None
    assert session.baseline.exit_code == 1
    assert session.attempts
    post = session.attempts[-1].test_result
    assert post is not None
    assert post.exit_code == 0
    assert "1 passed" in (post.stdout or "")
    assert "return 2" in (session.workspace_root / "mod.py").read_text(encoding="utf-8")
