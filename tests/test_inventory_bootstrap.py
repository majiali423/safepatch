"""D1: workspace-writable inventory JSON is not a trusted plugin origin."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

from code_agent.patching.test_collection import (
    INVENTORY_VERSION,
    PLUGIN_INVENTORY_SOURCE,
    InventoryCompleteness,
    TestInventoryReport,
    build_protected_inventory,
    load_test_inventory_file,
)
from code_agent.runtime.docker_config import DockerRunConfig
from code_agent.runtime.docker_pytest import (
    CONTROLLED_BOOTSTRAP,
    attach_inventory_plugin,
    pytest_args_from_product_argv,
)

ROOT = Path(__file__).resolve().parents[1]


def _utf8_subprocess_env() -> dict[str, str]:
    env = os.environ.copy()
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    return env


def _looks_like_network_failure(*texts: str) -> bool:
    blob = "\n".join(texts).lower()
    needles = (
        "could not fetch",
        "connection",
        "timed out",
        "timeout",
        "name resolution",
        "max retries exceeded",
        "network is unreachable",
        "temporary failure",
        "no matching distribution",
        "httpx",
        "proxy",
    )
    return any(item in blob for item in needles)


def test_d1_forged_source_string_is_not_trusted(tmp_path: Path):
    payload = {
        "version": INVENTORY_VERSION,
        "source": PLUGIN_INVENTORY_SOURCE,
        "complete": True,
        "truncated": False,
        "files": ["/work/test_mod.py"],
    }
    path = tmp_path / ".safepatch_pytest_inventory.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    report = load_test_inventory_file(path)
    assert report.completeness is InventoryCompleteness.UNVERIFIED
    assert report.origin_trusted is False
    assert report.source == PLUGIN_INVENTORY_SOURCE


def test_d1_unverified_report_does_not_lift_custom_collector(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "mod.py").write_text("def f():\n    return 1\n", encoding="utf-8")
    (repo / "test_mod.py").write_text("def test_f():\n    assert False\n", encoding="utf-8")
    (repo / "checks.py").write_text("def test_guard():\n    assert 1 == 1\n", encoding="utf-8")
    (repo / "conftest.py").write_text(
        "import pytest\n"
        "def pytest_collect_file(file_path, parent):\n"
        '    if file_path.name == "checks.py":\n'
        "        return pytest.Module.from_parent(parent, path=file_path)\n",
        encoding="utf-8",
    )
    (repo / "pytest.ini").write_text("[pytest]\npythonpath = .\n", encoding="utf-8")
    report = TestInventoryReport(
        completeness=InventoryCompleteness.UNVERIFIED,
        files=("/work/test_mod.py",),
        source=PLUGIN_INVENTORY_SOURCE,
        version=INVENTORY_VERSION,
        origin_trusted=False,
        error="workspace inventory cannot prove origin",
    )
    inventory = build_protected_inventory(repo, report=report)
    assert inventory.fail_closed is True
    assert "checks.py" in inventory.files
    assert "mod.py" in inventory.files


def test_d1_bootstrap_replaces_python_m_pytest_without_name_plugin():
    cfg = DockerRunConfig(image="safepatch-pytest:local")
    cmd = attach_inventory_plugin(cfg.build_docker_cmd("/work/copy"), cfg)
    assert pytest_args_from_product_argv(list(cfg.pytest_argv)) == ["-q", "-p", "no:cacheprovider"]
    assert cmd[cmd.index(cfg.image) + 1 : cmd.index(cfg.image) + 3] == [
        "python",
        CONTROLLED_BOOTSTRAP,
    ]
    assert "-m" not in cmd[cmd.index(cfg.image) :]
    assert "safepatch_inventory" not in cmd


def test_d1_plugin_assets_present_offline():
    plugins = ROOT / "code_agent" / "runtime" / "plugins"
    assert (plugins / "run_pytest.py").is_file()
    assert (plugins / "safepatch_inventory.py").is_file()


@pytest.mark.packaging_network
def test_d1_wheel_and_install_include_bootstrap(tmp_path: Path):
    dist = tmp_path / "dist"
    dist.mkdir()
    try:
        built = subprocess.run(
            [sys.executable, "-m", "build", "--wheel", "--outdir", str(dist)],
            cwd=ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
            timeout=180,
            env=_utf8_subprocess_env(),
        )
    except subprocess.TimeoutExpired as exc:
        pytest.fail(f"PACKAGING_TIMEOUT: wheel build exceeded 180s: {exc}")
    if built.returncode != 0:
        detail = (built.stdout or "") + (built.stderr or "")
        prefix = "PACKAGING_NETWORK" if _looks_like_network_failure(detail) else "PACKAGING"
        pytest.fail(f"{prefix}: wheel build failed: {detail[-4000:]}")
    wheels = list(dist.glob("*.whl"))
    assert len(wheels) == 1
    with zipfile.ZipFile(wheels[0]) as archive:
        names = set(archive.namelist())
    assert "code_agent/runtime/plugins/run_pytest.py" in names
    assert "code_agent/runtime/plugins/safepatch_inventory.py" in names

    site = tmp_path / "site"
    try:
        installed = subprocess.run(
            [
                sys.executable,
                "-m",
                "pip",
                "install",
                str(wheels[0]),
                "-t",
                str(site),
                "--no-deps",
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
            timeout=120,
            env=_utf8_subprocess_env(),
        )
    except subprocess.TimeoutExpired as exc:
        pytest.fail(f"PACKAGING_TIMEOUT: pip install exceeded 120s: {exc}")
    if installed.returncode != 0:
        detail = (installed.stdout or "") + (installed.stderr or "")
        prefix = "PACKAGING_NETWORK" if _looks_like_network_failure(detail) else "PACKAGING"
        pytest.fail(f"{prefix}: pip install failed: {detail[-4000:]}")
    assert (site / "code_agent" / "runtime" / "plugins" / "run_pytest.py").is_file()
    assert (site / "code_agent" / "runtime" / "plugins" / "safepatch_inventory.py").is_file()
