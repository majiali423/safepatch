"""Disposable test copies must be usable by fixed non-root UID 1000."""

from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest

from code_agent.runtime.docker_pytest import (
    DockerPytestRunner,
    _prepare_test_copy_for_container_uid,
)


def _chmod(path: Path, mode: int) -> None:
    if os.name == "nt":
        return
    path.chmod(mode)


@pytest.mark.skipif(os.name == "nt", reason="POSIX permission bits are not asserted on Windows")
def test_prepare_makes_0600_and_0400_files_readable_and_writable(tmp_path: Path) -> None:
    root = tmp_path / "copy"
    root.mkdir()
    nested = root / "pkg" / "nested"
    nested.mkdir(parents=True)
    secret = root / "secret.py"
    readonly = root / "readonly.py"
    script = root / "tool.sh"
    secret.write_text("SECRET = 1\n", encoding="utf-8")
    readonly.write_text("RO = 1\n", encoding="utf-8")
    script.write_text("#!/bin/sh\n", encoding="utf-8")
    secret.chmod(0o600)
    readonly.chmod(0o400)
    script.chmod(0o700)
    nested.chmod(0o700)
    (root / "pkg").chmod(0o700)

    _prepare_test_copy_for_container_uid(root)

    for path in (secret, readonly):
        mode = stat.S_IMODE(path.stat().st_mode)
        assert mode & stat.S_IROTH
        assert mode & stat.S_IWOTH
        assert not (mode & stat.S_IXOTH)
        path.write_text(path.read_text(encoding="utf-8") + "x = 2\n", encoding="utf-8")

    dir_mode = stat.S_IMODE(nested.stat().st_mode)
    assert dir_mode & stat.S_IXOTH
    assert dir_mode & stat.S_IROTH
    assert dir_mode & stat.S_IWOTH
    assert (nested / "probe.txt").write_text("ok\n", encoding="utf-8") or True

    script_mode = stat.S_IMODE(script.stat().st_mode)
    assert script_mode & stat.S_IXUSR


@pytest.mark.docker_e2e
def test_container_can_read_0600_and_traverse_0700(tmp_path: Path) -> None:
    runner = DockerPytestRunner()
    ok, reason = runner.preflight()
    if not ok:
        pytest.skip(f"Docker daemon unavailable: {reason}")

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "pytest.ini").write_text("[pytest]\npythonpath = .\n", encoding="utf-8")
    nested = workspace / "pkg" / "deep"
    nested.mkdir(parents=True)
    (workspace / "pkg" / "__init__.py").write_text("", encoding="utf-8")
    (nested / "__init__.py").write_text("", encoding="utf-8")
    (workspace / "secret.py").write_text("VALUE = 7\n", encoding="utf-8")
    (nested / "data.py").write_text("NESTED = 3\n", encoding="utf-8")
    tests = workspace / "tests"
    tests.mkdir()
    (tests / "test_restricted.py").write_text(
        "from pathlib import Path\n"
        "import secret\n"
        "from pkg.deep import data\n"
        "\n"
        "def test_read_and_modify_restricted_files():\n"
        "    assert secret.VALUE == 7\n"
        "    assert data.NESTED == 3\n"
        "    Path('secret.py').write_text('VALUE = 8\\n', encoding='utf-8')\n"
        "    Path('pkg/deep/written.txt').write_text('ok\\n', encoding='utf-8')\n"
        "    assert Path('secret.py').read_text(encoding='utf-8') == 'VALUE = 8\\n'\n",
        encoding="utf-8",
    )
    if os.name != "nt":
        (workspace / "secret.py").chmod(0o600)
        (nested / "data.py").chmod(0o400)
        nested.chmod(0o700)
        (workspace / "pkg").chmod(0o700)

    before_secret = (workspace / "secret.py").read_bytes()
    before_mode = (
        stat.S_IMODE((workspace / "secret.py").stat().st_mode) if os.name != "nt" else None
    )
    result = runner.run_pytest(workspace)
    assert result.exit_code == 0, (result.stdout, result.stderr)
    assert (workspace / "secret.py").read_bytes() == before_secret
    if before_mode is not None:
        assert stat.S_IMODE((workspace / "secret.py").stat().st_mode) == before_mode
    assert not (workspace / "pkg" / "deep" / "written.txt").exists()
    # Disposable copy parent is removed after the run.
    leftovers = [
        path
        for path in workspace.parent.iterdir()
        if path.name.startswith("pytest-copy-")
    ]
    assert leftovers == []


def test_prepare_helper_does_not_touch_source_workspace(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    target = source / "mod.py"
    target.write_text("X = 1\n", encoding="utf-8")
    if os.name != "nt":
        target.chmod(0o600)
        before = stat.S_IMODE(target.stat().st_mode)
    copy = tmp_path / "copy"
    copy.mkdir()
    (copy / "mod.py").write_text("X = 1\n", encoding="utf-8")
    if os.name != "nt":
        (copy / "mod.py").chmod(0o600)
    _prepare_test_copy_for_container_uid(copy)
    assert target.read_text(encoding="utf-8") == "X = 1\n"
    if os.name != "nt":
        assert stat.S_IMODE(target.stat().st_mode) == before
