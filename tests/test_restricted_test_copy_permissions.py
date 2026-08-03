"""Disposable test copies must be usable by fixed non-root UID 1000."""

from __future__ import annotations

import os
import stat
import subprocess
import threading
from pathlib import Path

import pytest

from code_agent.runtime.docker_pytest import (
    DockerPytestRunner,
    _prepare_test_copy_for_container_uid,
    _remove_disposable_test_copy,
    _with_cleanup_failure,
)
from code_agent.state import TestResult


def _chmod(path: Path, mode: int) -> None:
    if os.name == "nt":
        return
    path.chmod(mode)


def _workspace(tmp_path: Path, *, test_source: str = "def test_ok():\n    assert True\n") -> Path:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "pytest.ini").write_text("[pytest]\npythonpath = .\n", encoding="utf-8")
    (workspace / "mod.py").write_text("VALUE = 1\n", encoding="utf-8")
    tests = workspace / "tests"
    tests.mkdir()
    (tests / "test_mod.py").write_text(test_source, encoding="utf-8")
    return workspace


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
    assert result.environment_error is None
    assert (workspace / "secret.py").read_bytes() == before_secret
    if before_mode is not None:
        assert stat.S_IMODE((workspace / "secret.py").stat().st_mode) == before_mode
    assert not (workspace / "pkg" / "deep" / "written.txt").exists()
    parent = runner.last_test_copy_parent
    assert parent is not None
    assert not parent.exists()
    assert runner.last_cleanup_error is None


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


def test_success_removes_exact_test_copy_parent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = _workspace(tmp_path)
    runner = DockerPytestRunner()
    monkeypatch.setattr(runner, "preflight", lambda: (True, "ok"))
    monkeypatch.setattr(runner, "_resolve_image", lambda: "code-agent-pytest:local")

    def fake_run(cmd: list[str], *, timeout_seconds: int):
        return "ok", "", 0

    monkeypatch.setattr("code_agent.runtime.docker_pytest._run_docker_cmd", fake_run)
    result = runner.run_pytest(workspace)
    assert result.passed
    assert runner.last_test_copy_parent is not None
    assert not runner.last_test_copy_parent.exists()
    assert runner.last_cleanup_error is None


def test_pytest_failure_removes_exact_test_copy_parent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = _workspace(tmp_path)
    runner = DockerPytestRunner()
    monkeypatch.setattr(runner, "preflight", lambda: (True, "ok"))
    monkeypatch.setattr(runner, "_resolve_image", lambda: "code-agent-pytest:local")

    def fake_run(cmd: list[str], *, timeout_seconds: int):
        return "FAILED tests/test_mod.py::test_ok", "", 1

    monkeypatch.setattr("code_agent.runtime.docker_pytest._run_docker_cmd", fake_run)
    result = runner.run_pytest(workspace)
    assert result.exit_code == 1
    assert runner.last_test_copy_parent is not None
    assert not runner.last_test_copy_parent.exists()
    assert runner.last_cleanup_error is None


def test_timeout_removes_exact_test_copy_parent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = _workspace(tmp_path)
    runner = DockerPytestRunner()
    monkeypatch.setattr(runner, "preflight", lambda: (True, "ok"))
    monkeypatch.setattr(runner, "_resolve_image", lambda: "code-agent-pytest:local")

    def timeout(cmd: list[str], *, timeout_seconds: int):
        raise subprocess.TimeoutExpired(cmd, timeout_seconds)

    monkeypatch.setattr("code_agent.runtime.docker_pytest._run_docker_cmd", timeout)
    monkeypatch.setattr("code_agent.runtime.docker_pytest._force_remove_container", lambda _n: None)
    result = runner.run_pytest(workspace)
    assert result.error_kind == "timeout"
    assert runner.last_test_copy_parent is not None
    assert not runner.last_test_copy_parent.exists()
    assert runner.last_cleanup_error is None


def test_docker_cli_exception_removes_exact_test_copy_parent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = _workspace(tmp_path)
    runner = DockerPytestRunner()
    monkeypatch.setattr(runner, "preflight", lambda: (True, "ok"))
    monkeypatch.setattr(runner, "_resolve_image", lambda: "code-agent-pytest:local")

    def boom(cmd: list[str], *, timeout_seconds: int):
        raise RuntimeError("docker cli exploded")

    monkeypatch.setattr("code_agent.runtime.docker_pytest._run_docker_cmd", boom)
    monkeypatch.setattr("code_agent.runtime.docker_pytest._force_remove_container", lambda _n: None)
    result = runner.run_pytest(workspace)
    assert result.error_kind == "environment"
    assert "docker cli exploded" in (result.environment_error or "")
    assert runner.last_test_copy_parent is not None
    assert not runner.last_test_copy_parent.exists()
    assert runner.last_cleanup_error is None


@pytest.mark.skipif(os.name == "nt", reason="POSIX mode 0555 host-rmtree trap")
def test_unwritable_nested_dir_still_cleaned(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Host-owned 0555 nested dir blocks first rmtree; docker wipe fallback recovers."""
    parent = tmp_path / "pytest-copy-owned"
    working = parent / "working_copy"
    nested = working / "pkg"
    nested.mkdir(parents=True)
    (nested / "cache.pyc").write_text("x", encoding="utf-8")
    nested.chmod(0o555)

    wiped: list[Path] = []

    def fake_wipe(path: Path, *, image: str) -> None:
        wiped.append(path)
        assert path == working
        # Simulate UID 1000 clearing mount contents only (not the mount root).
        nested.chmod(0o755)
        for child in nested.iterdir():
            child.unlink()
        nested.rmdir()

    monkeypatch.setattr("code_agent.runtime.docker_pytest._docker_wipe_as_uid_1000", fake_wipe)
    error = _remove_disposable_test_copy(parent, image="code-agent-pytest:local")
    assert error is None
    assert wiped == [working]
    assert not working.exists()
    assert not parent.exists()


def test_cleanup_wipe_mounts_only_working_copy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Cleanup docker -v source must be working_copy, never parent/workspace/workspace.parent."""
    workspace = tmp_path / "formal_workspace"
    workspace.mkdir()
    parent = tmp_path / "pytest-copy-owned"
    working = parent / "working_copy"
    working.mkdir(parents=True)
    (working / "leftover.txt").write_text("x", encoding="utf-8")

    captured: list[list[str]] = []

    def fake_run(cmd, **kwargs):  # noqa: ANN001
        captured.append(list(cmd))
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr("code_agent.runtime.docker_pytest.subprocess.run", fake_run)
    from code_agent.runtime.docker_pytest import _docker_wipe_as_uid_1000

    _docker_wipe_as_uid_1000(working, image="code-agent-pytest:local")

    assert len(captured) == 1
    cmd = captured[0]
    assert cmd[0:2] == ["docker", "run"]
    assert "--user" in cmd and cmd[cmd.index("--user") + 1] == "1000:1000"
    assert "--network" in cmd and cmd[cmd.index("--network") + 1] == "none"
    assert "--cap-drop" in cmd and cmd[cmd.index("--cap-drop") + 1] == "ALL"
    mount = cmd[cmd.index("-v") + 1]
    assert mount.endswith(":/cleanup:rw")
    source = Path(mount[: -len(":/cleanup:rw")])
    assert source == working.resolve()
    assert source != parent.resolve()
    assert source != workspace.resolve()
    assert source != workspace.parent.resolve()
    assert str(parent.resolve()) != str(source)
    assert str(workspace.resolve()) not in {str(source), mount}
    # Exact mount destination token.
    assert ":/cleanup:rw" in mount
    assert cmd[cmd.index("-w") + 1] == "/cleanup"


@pytest.mark.skipif(os.name == "nt", reason="POSIX mode 0555 host-rmtree trap")
def test_remove_disposable_fallback_wipes_working_copy_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = tmp_path / "formal_workspace"
    workspace.mkdir()
    parent = tmp_path / "pytest-copy-owned"
    working = parent / "working_copy"
    nested = working / "pkg"
    nested.mkdir(parents=True)
    (nested / "cache.pyc").write_text("x", encoding="utf-8")
    nested.chmod(0o555)

    mounts: list[str] = []

    def fake_run(cmd, **kwargs):  # noqa: ANN001
        if isinstance(cmd, list) and "-v" in cmd:
            mounts.append(cmd[cmd.index("-v") + 1])
        # Perform the real wipe effect as UID 1000 would.
        nested.chmod(0o755)
        for child in list(nested.iterdir()):
            child.unlink()
        nested.rmdir()
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr("code_agent.runtime.docker_pytest.subprocess.run", fake_run)
    error = _remove_disposable_test_copy(parent, image="code-agent-pytest:local")
    assert error is None
    assert len(mounts) == 1
    mount = mounts[0]
    assert mount.endswith(":/cleanup:rw")
    source = Path(mount[: -len(":/cleanup:rw")])
    assert source == working.resolve()
    assert source != parent.resolve()
    assert source != workspace.resolve()
    assert source != workspace.parent.resolve()
    assert not parent.exists()


def test_cleanup_failure_is_not_swallowed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = _workspace(tmp_path)
    runner = DockerPytestRunner()
    monkeypatch.setattr(runner, "preflight", lambda: (True, "ok"))
    monkeypatch.setattr(runner, "_resolve_image", lambda: "code-agent-pytest:local")
    monkeypatch.setattr(
        "code_agent.runtime.docker_pytest._run_docker_cmd",
        lambda cmd, timeout_seconds: ("passed stdout", "", 0),
    )
    monkeypatch.setattr(
        "code_agent.runtime.docker_pytest._remove_disposable_test_copy",
        lambda parent, *, image: f"blocked:{parent.name}",
    )
    log_path = tmp_path / "run.log"
    result = runner.run_pytest(workspace, log_path=log_path)
    assert result.passed is False
    assert result.error_kind == "environment"
    assert result.stdout == "passed stdout"
    assert runner.last_cleanup_error == f"blocked:{runner.last_test_copy_parent.name}"
    assert "disposable test copy cleanup failed" in (result.environment_error or "")
    assert "disposable test copy cleanup failed" in result.stderr
    log_text = log_path.read_text(encoding="utf-8")
    assert "cleanup_error:" in log_text
    assert "blocked:" in log_text


def test_with_cleanup_failure_preserves_original_streams() -> None:
    original = TestResult(
        exit_code=0,
        stdout="keep-me",
        stderr="warn",
        duration_sec=1.0,
    )
    updated = _with_cleanup_failure(original, "still there")
    assert updated.stdout == "keep-me"
    assert "warn" in updated.stderr
    assert "still there" in (updated.environment_error or "")
    assert updated.error_kind == "environment"
    assert updated.exit_code == -1


def test_runners_only_clean_their_own_parents(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = _workspace(tmp_path)
    runner_a = DockerPytestRunner()
    runner_b = DockerPytestRunner()
    for runner in (runner_a, runner_b):
        monkeypatch.setattr(runner, "preflight", lambda: (True, "ok"))
        monkeypatch.setattr(runner, "_resolve_image", lambda: "code-agent-pytest:local")

    seen: list[Path] = []
    lock = threading.Lock()

    def fake_run(cmd: list[str], *, timeout_seconds: int):
        mount = cmd[cmd.index("-v") + 1]
        parent = Path(mount.removesuffix(":/work:rw")).parent
        with lock:
            seen.append(parent)
        return "ok", "", 0

    monkeypatch.setattr("code_agent.runtime.docker_pytest._run_docker_cmd", fake_run)
    result_a = runner_a.run_pytest(workspace)
    result_b = runner_b.run_pytest(workspace)
    assert result_a.passed and result_b.passed
    assert runner_a.last_test_copy_parent != runner_b.last_test_copy_parent
    assert runner_a.last_test_copy_parent in seen
    assert runner_b.last_test_copy_parent in seen
    assert not runner_a.last_test_copy_parent.exists()
    assert not runner_b.last_test_copy_parent.exists()
    assert runner_a.last_cleanup_error is None
    assert runner_b.last_cleanup_error is None


def test_formal_working_copy_unchanged_after_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = _workspace(tmp_path)
    before = {
        path.relative_to(workspace).as_posix(): path.read_bytes()
        for path in workspace.rglob("*")
        if path.is_file()
    }
    before_mode = None
    if os.name != "nt":
        target = workspace / "mod.py"
        target.chmod(0o600)
        before_mode = stat.S_IMODE(target.stat().st_mode)
        before[target.relative_to(workspace).as_posix()] = target.read_bytes()

    runner = DockerPytestRunner()
    monkeypatch.setattr(runner, "preflight", lambda: (True, "ok"))
    monkeypatch.setattr(runner, "_resolve_image", lambda: "code-agent-pytest:local")

    def mutate_copy(cmd: list[str], *, timeout_seconds: int):
        mount = cmd[cmd.index("-v") + 1]
        test_copy = Path(mount.removesuffix(":/work:rw"))
        (test_copy / "mod.py").write_text("mutated\n", encoding="utf-8")
        (test_copy / "extra.tmp").write_text("x\n", encoding="utf-8")
        return "ok", "", 0

    monkeypatch.setattr("code_agent.runtime.docker_pytest._run_docker_cmd", mutate_copy)
    result = runner.run_pytest(workspace)
    assert result.passed
    after = {
        path.relative_to(workspace).as_posix(): path.read_bytes()
        for path in workspace.rglob("*")
        if path.is_file()
    }
    assert after == before
    if before_mode is not None:
        assert stat.S_IMODE((workspace / "mod.py").stat().st_mode) == before_mode
    assert not (workspace / "extra.tmp").exists()


@pytest.mark.docker_e2e
def test_container_created_artifacts_are_removed(tmp_path: Path) -> None:
    """Real UID-1000 imports create __pycache__; cleanup must still delete the parent."""
    runner = DockerPytestRunner()
    ok, reason = runner.preflight()
    if not ok:
        pytest.skip(f"Docker daemon unavailable: {reason}")
    workspace = _workspace(
        tmp_path,
        test_source=(
            "import mod\n"
            "from pathlib import Path\n"
            "\n"
            "def test_writes_cache_and_file():\n"
            "    assert mod.VALUE == 1\n"
            "    Path('artifact.txt').write_text('uid1000\\n', encoding='utf-8')\n"
        ),
    )
    result = runner.run_pytest(workspace)
    assert result.exit_code == 0, (result.stdout, result.stderr)
    assert runner.last_cleanup_error is None
    assert runner.last_test_copy_parent is not None
    assert not runner.last_test_copy_parent.exists()
    assert not (workspace / "artifact.txt").exists()
