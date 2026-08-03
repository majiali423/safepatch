from __future__ import annotations

import os
import re
import shutil
import stat
import subprocess
import tempfile
import time
import uuid
from dataclasses import replace
from pathlib import Path

from code_agent.runtime.docker_config import DockerRunConfig
from code_agent.state import TestResult

DOCKER_IMAGE = "python:3.12-slim"
LOCAL_PYTEST_IMAGE = "code-agent-pytest:local"

FAILED_TEST_RE = re.compile(
    r"^(FAILED|ERROR)\s+(\S+?)(?:\s+-|$)",
    re.MULTILINE,
)
# Docker allows [a-zA-Z0-9][a-zA-Z0-9_.-]*; keep well under the usual 63-char cap.
_CONTAINER_NAME_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_.-]{0,62}$")
_CONTAINER_NAME_PREFIX = "code-agent-pytest-"

# Fixed wipe program for UID-1000-owned leftovers. Not user/model-controlled shell.
_DOCKER_WIPE_PY = (
    "import pathlib, shutil\n"
    "root = pathlib.Path('/cleanup')\n"
    "for child in list(root.iterdir()):\n"
    "    if child.is_dir() and not child.is_symlink():\n"
    "        shutil.rmtree(child)\n"
    "    else:\n"
    "        child.unlink(missing_ok=True)\n"
)


class DockerPytestRunner:
    """Controller-owned Docker pytest runner. Model cannot change these params."""

    def __init__(self, image: str = LOCAL_PYTEST_IMAGE) -> None:
        self.image = image
        self.last_run_config: DockerRunConfig | None = None
        self.last_test_copy_parent: Path | None = None
        self.last_cleanup_error: str | None = None

    def make_run_config(self, image: str) -> DockerRunConfig:
        """Create the config object shared by docker argv and trace."""
        return DockerRunConfig(image=image)

    def preflight(self) -> tuple[bool, str]:
        """Run docker info / image checks. Safe to call from CLI before a session."""
        if shutil.which("docker") is None:
            return False, "docker executable not found on PATH"

        try:
            info = subprocess.run(
                ["docker", "info"],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=20,
                check=False,
                env=_sanitized_subprocess_env(),
            )
        except subprocess.TimeoutExpired:
            return False, "docker info timed out; is Docker Desktop starting?"
        except Exception as exc:  # noqa: BLE001
            return False, f"docker info failed: {exc}"

        if info.returncode != 0:
            detail = (info.stderr or info.stdout or "docker daemon unavailable").strip()
            first = detail.splitlines()[0] if detail else "docker daemon unavailable"
            return False, first

        image = self._resolve_image()
        if image is None:
            return False, "pytest image missing; run: code-agent --build-image"
        return True, f"docker ok; image={image}"

    def available(self) -> tuple[bool, str]:
        return self.preflight()

    def run_pytest(
        self,
        workspace_root: Path,
        *,
        log_path: Path | None = None,
        config: DockerRunConfig | None = None,
    ) -> TestResult:
        """Run pytest in Docker. Optional config from make_run_config / replace /
        for_hidden_tests — security params always come from DockerRunConfig.
        """
        self.last_run_config = None
        self.last_test_copy_parent = None
        self.last_cleanup_error = None
        ok, reason = self.preflight()
        if not ok:
            return TestResult(
                exit_code=-1,
                stdout="",
                stderr=reason,
                duration_sec=0.0,
                environment_error=f"TEST_ENVIRONMENT_ERROR: {reason}",
                error_kind="environment",
            )

        image = self._resolve_image()
        assert image is not None

        if config is None:
            config = self.make_run_config(image)
        elif config.image != image:
            config = replace(config, image=image)
        # Every real docker run gets a unique, non-user-controlled name so
        # timeout / CLI-error cleanup can target exactly this container.
        config = replace(
            config, container_name=allocate_container_name(config.container_name)
        )
        self.last_run_config = config
        test_copy_parent = Path(
            tempfile.mkdtemp(prefix="pytest-copy-", dir=str(workspace_root.resolve().parent))
        )
        self.last_test_copy_parent = test_copy_parent
        test_copy = test_copy_parent / "working_copy"
        started = time.time()
        result: TestResult | None = None
        try:
            try:
                shutil.copytree(
                    workspace_root,
                    test_copy,
                    ignore=shutil.ignore_patterns("__pycache__", ".pytest_cache", "*.pyc"),
                )
                _prepare_test_copy_for_container_uid(test_copy)
            except Exception as exc:  # noqa: BLE001
                result = TestResult(
                    exit_code=-1,
                    stdout="",
                    stderr=str(exc),
                    duration_sec=time.time() - started,
                    environment_error=(
                        f"TEST_ENVIRONMENT_ERROR: could not create test copy: {exc}"
                    ),
                    error_kind="environment",
                )
            else:
                cmd = config.build_docker_cmd(str(test_copy.resolve()))
                try:
                    stdout, stderr, returncode = _run_docker_cmd(
                        cmd, timeout_seconds=config.timeout_seconds
                    )
                except subprocess.TimeoutExpired as exc:
                    _force_remove_container(config.container_name)
                    result = TestResult(
                        exit_code=-1,
                        stdout=_as_text(exc.stdout),
                        stderr="pytest timed out in Docker",
                        duration_sec=time.time() - started,
                        environment_error=(
                            f"TEST_TIMEOUT: pytest exceeded {config.timeout_seconds}s"
                        ),
                        error_kind="timeout",
                    )
                except Exception as exc:  # noqa: BLE001
                    _force_remove_container(config.container_name)
                    result = TestResult(
                        exit_code=-1,
                        stdout="",
                        stderr=str(exc),
                        duration_sec=time.time() - started,
                        environment_error=f"TEST_ENVIRONMENT_ERROR: {exc}",
                        error_kind="environment",
                    )
                else:
                    duration = time.time() - started
                    env_err = None
                    error_kind = None
                    combined = stdout + "\n" + stderr
                    if (
                        "No module named pytest" in combined
                        or "No module named 'pytest'" in combined
                    ):
                        env_err = "TEST_ENVIRONMENT_ERROR: pytest missing in container"
                        error_kind = "environment"
                    result = TestResult(
                        exit_code=returncode,
                        stdout=stdout,
                        stderr=stderr,
                        duration_sec=duration,
                        failed_tests=_extract_failed_tests(combined),
                        traceback_summary=_extract_traceback_summary(combined),
                        environment_error=env_err,
                        error_kind=error_kind,
                    )
        finally:
            cleanup_error = _remove_disposable_test_copy(test_copy_parent, image=image)
            if cleanup_error is not None:
                self.last_cleanup_error = cleanup_error
                if result is None:
                    result = TestResult(
                        exit_code=-1,
                        stdout="",
                        stderr=cleanup_error,
                        duration_sec=time.time() - started,
                        environment_error=(
                            "TEST_ENVIRONMENT_ERROR: disposable test copy "
                            f"cleanup failed: {cleanup_error}"
                        ),
                        error_kind="environment",
                    )
                else:
                    result = _with_cleanup_failure(result, cleanup_error)

        assert result is not None
        if log_path is not None:
            _write_log(
                log_path,
                result,
                config=config,
                test_copy_parent=self.last_test_copy_parent,
                cleanup_error=self.last_cleanup_error,
            )
        return result

    def _resolve_image(self) -> str | None:
        candidates = [LOCAL_PYTEST_IMAGE]
        if self.image not in {DOCKER_IMAGE, LOCAL_PYTEST_IMAGE}:
            candidates.append(self.image)
        for candidate in candidates:
            proc = subprocess.run(
                ["docker", "image", "inspect", candidate],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                check=False,
                env=_sanitized_subprocess_env(),
            )
            if proc.returncode == 0:
                return candidate
        return None


def _run_docker_cmd(
    cmd: list[str], *, timeout_seconds: int
) -> tuple[str, str, int]:
    """Run docker CLI; on timeout kill the client process and re-raise TimeoutExpired."""
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=_sanitized_subprocess_env(),
    )
    try:
        stdout, stderr = proc.communicate(timeout=timeout_seconds)
    except subprocess.TimeoutExpired as exc:
        proc.kill()
        try:
            out, err = proc.communicate(timeout=10)
        except Exception:  # noqa: BLE001
            out, err = "", ""
        raise subprocess.TimeoutExpired(
            cmd=cmd,
            timeout=timeout_seconds,
            output=out or (exc.output if isinstance(exc.output, str) else ""),
            stderr=err or (exc.stderr if isinstance(exc.stderr, str) else ""),
        ) from None
    return stdout or "", stderr or "", int(proc.returncode or 0)


def allocate_container_name(explicit: str | None = None) -> str:
    """Allocate a Docker-safe container name for one ``run_pytest`` invocation.

    - ``None`` / empty / unsafe → ``code-agent-pytest-<uuid>`` (product default).
    - Explicit names that already match Docker naming rules are preserved so
      audits and tests can pin a known name; they are never taken from model input.
    """
    if explicit and _CONTAINER_NAME_RE.fullmatch(explicit):
        return explicit
    return f"{_CONTAINER_NAME_PREFIX}{uuid.uuid4().hex}"


def _prepare_test_copy_for_container_uid(test_copy: Path) -> None:
    """Make a one-shot test copy usable by fixed non-root container UID 1000.

    Only mutates the disposable test copy — never the formal working copy.
    Adds read+write for usr/grp/oth so UID 1000 can read ``0600``/``0400`` files
    and rewrite pytest artifacts; directories also get execute (traverse). Existing
    file executable bits are preserved; ordinary source is not made executable.
    The private temp parent directory is not widened. This is not a full sandbox.
    """
    read_write = (
        stat.S_IRUSR
        | stat.S_IWUSR
        | stat.S_IRGRP
        | stat.S_IWGRP
        | stat.S_IROTH
        | stat.S_IWOTH
    )
    dir_traverse = stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH
    for path in (test_copy, *test_copy.rglob("*")):
        current = stat.S_IMODE(path.stat().st_mode)
        mode = current | read_write
        if path.is_dir():
            mode |= dir_traverse
        else:
            # Preserve prior execute bits only; do not add execute to normal files.
            mode = (mode & ~dir_traverse) | (current & dir_traverse)
        path.chmod(mode)


# Backward-compatible alias for older imports/tests.
_make_test_copy_writable = _prepare_test_copy_for_container_uid


def _remove_disposable_test_copy(parent: Path, *, image: str) -> str | None:
    """Remove one disposable parent directory. Return error text on failure.

    Host ``rmtree`` first. If container UID 1000 created non-writable dirs
    (e.g. ``__pycache__``), fall back to a locked-down Docker wipe as UID 1000
    that mounts only ``parent / "working_copy"`` (never the private parent, the
    formal workspace, or the workspace parent). After wipe, the host removes the
    emptied working_copy, then the emptied parent, and confirms parent is gone.
    """
    if not parent.exists():
        return None

    try:
        shutil.rmtree(parent)
    except OSError:
        pass
    else:
        if not parent.exists():
            return None

    test_copy = parent / "working_copy"
    wipe_error: str | None = None
    try:
        if test_copy.exists():
            _docker_wipe_as_uid_1000(test_copy, image=image)
    except Exception as exc:  # noqa: BLE001
        wipe_error = str(exc)

    try:
        if test_copy.exists():
            shutil.rmtree(test_copy)
        if parent.exists():
            parent.rmdir()
    except OSError as exc:
        if parent.exists():
            detail = f"{exc}"
            if wipe_error:
                detail = f"{detail}; docker wipe: {wipe_error}"
            return detail

    if parent.exists():
        detail = f"path still exists after cleanup: {parent}"
        if wipe_error:
            detail = f"{detail}; docker wipe: {wipe_error}"
        return detail
    return None


def _docker_wipe_as_uid_1000(test_copy: Path, *, image: str) -> None:
    """Clear contents of disposable ``working_copy`` as UID 1000; never delete mount root."""
    if not test_copy.exists():
        return
    name = f"code-agent-cleanup-{uuid.uuid4().hex}"
    host = str(test_copy.resolve())
    cmd = [
        "docker",
        "run",
        "--rm",
        "--name",
        name,
        "--network",
        "none",
        "--cap-drop",
        "ALL",
        "--security-opt",
        "no-new-privileges",
        "--read-only",
        "--tmpfs",
        "/tmp:rw,nosuid,nodev,noexec,size=16m",
        "--user",
        "1000:1000",
        "-v",
        f"{host}:/cleanup:rw",
        "-w",
        "/cleanup",
        image,
        "python",
        "-c",
        _DOCKER_WIPE_PY,
    ]
    proc = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
        timeout=60,
        env=_sanitized_subprocess_env(),
    )
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "docker wipe failed").strip()
        raise RuntimeError(detail.splitlines()[0] if detail else "docker wipe failed")


def _with_cleanup_failure(result: TestResult, cleanup_error: str) -> TestResult:
    """Attach cleanup failure without erasing original pytest stdout/stderr."""
    note = (
        "TEST_ENVIRONMENT_ERROR: disposable test copy cleanup failed: "
        f"{cleanup_error}"
    )
    env = result.environment_error
    environment_error = f"{env}; {note}" if env else note
    stderr = result.stderr
    if note not in stderr:
        stderr = f"{stderr}\n{note}" if stderr else note
    error_kind = result.error_kind if result.error_kind == "timeout" else "environment"
    exit_code = result.exit_code if result.exit_code != 0 else -1
    return TestResult(
        exit_code=exit_code,
        stdout=result.stdout,
        stderr=stderr,
        duration_sec=result.duration_sec,
        failed_tests=list(result.failed_tests),
        traceback_summary=result.traceback_summary,
        environment_error=environment_error,
        error_kind=error_kind,
    )


def _force_remove_container(container_name: str | None) -> None:
    """Best-effort cleanup when --rm may not run after a host-side kill."""
    if not container_name:
        return
    subprocess.run(
        ["docker", "rm", "-f", container_name],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
        timeout=30,
        env=_sanitized_subprocess_env(),
    )


def _as_text(value: object) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return ""


def _sanitized_subprocess_env() -> dict[str, str]:
    """Pass only what Docker CLI needs; never forward API keys."""
    allow = {
        "PATH",
        "SystemRoot",
        "SYSTEMROOT",
        "WINDIR",
        "TMP",
        "TEMP",
        "TMPDIR",
        "HOME",
        "USERPROFILE",
        "APPDATA",
        "LOCALAPPDATA",
        "PROGRAMDATA",
        "PROGRAMFILES",
        "PROGRAMFILES(X86)",
        "COMMONPROGRAMFILES",
        "COMSPEC",
        "PATHEXT",
        "NUMBER_OF_PROCESSORS",
        "PROCESSOR_ARCHITECTURE",
        "DOCKER_HOST",
        "DOCKER_CERT_PATH",
        "DOCKER_TLS_VERIFY",
    }
    out: dict[str, str] = {}
    for key in allow:
        val = os.environ.get(key)
        if val:
            out[key] = val
    return out


def _extract_failed_tests(text: str) -> list[str]:
    names = [m.group(2) for m in FAILED_TEST_RE.finditer(text)]
    if not names:
        for line in text.splitlines():
            if line.startswith("FAILED "):
                names.append(line.split()[1])
    seen: set[str] = set()
    out: list[str] = []
    for n in names:
        if n not in seen:
            seen.add(n)
            out.append(n)
    return out


def _extract_traceback_summary(text: str) -> str:
    lines = text.splitlines()
    start = None
    for i, line in enumerate(lines):
        if "FAILURES" in line:
            start = i
            break
    if start is None:
        return "\n".join(lines[-40:])
    return "\n".join(lines[start : start + 80])


def _write_log(
    log_path: Path,
    result: TestResult,
    *,
    config: DockerRunConfig | None = None,
    test_copy_parent: Path | None = None,
    cleanup_error: str | None = None,
) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text(
        _format_log(
            result,
            config=config,
            test_copy_parent=test_copy_parent,
            cleanup_error=cleanup_error,
        ),
        encoding="utf-8",
        newline="\n",
    )


def _format_log(
    result: TestResult,
    *,
    config: DockerRunConfig | None = None,
    test_copy_parent: Path | None = None,
    cleanup_error: str | None = None,
) -> str:
    parts = [
        f"exit_code: {result.exit_code}",
        f"duration_sec: {result.duration_sec:.3f}",
        f"environment_error: {result.environment_error}",
        f"error_kind: {result.error_kind}",
        f"failed_tests: {result.failed_tests}",
    ]
    if config is not None:
        parts += [
            f"timeout_seconds: {config.timeout_seconds}",
            f"container_name: {config.container_name}",
            f"network_mode: {config.network_mode}",
            f"user: {config.user}",
        ]
    if test_copy_parent is not None:
        parts.append(f"test_copy_parent: {test_copy_parent}")
    if cleanup_error is not None:
        parts.append(f"cleanup_error: {cleanup_error}")
    parts += [
        "",
        "--- stdout ---",
        result.stdout,
        "",
        "--- stderr ---",
        result.stderr,
        "",
        "--- traceback_summary ---",
        result.traceback_summary,
    ]
    return "\n".join(parts)
