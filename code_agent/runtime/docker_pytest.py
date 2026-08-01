from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
import time
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


class DockerPytestRunner:
    """Controller-owned Docker pytest runner. Model cannot change these params."""

    def __init__(self, image: str = LOCAL_PYTEST_IMAGE) -> None:
        self.image = image
        self.last_run_config: DockerRunConfig | None = None

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
        self.last_run_config = config
        test_copy_parent = Path(
            tempfile.mkdtemp(prefix="pytest-copy-", dir=str(workspace_root.resolve().parent))
        )
        test_copy = test_copy_parent / "working_copy"
        try:
            shutil.copytree(
                workspace_root,
                test_copy,
                ignore=shutil.ignore_patterns("__pycache__", ".pytest_cache", "*.pyc"),
            )
        except Exception as exc:  # noqa: BLE001
            shutil.rmtree(test_copy_parent, ignore_errors=True)
            return TestResult(
                exit_code=-1,
                stdout="",
                stderr=str(exc),
                duration_sec=0.0,
                environment_error=f"TEST_ENVIRONMENT_ERROR: could not create test copy: {exc}",
                error_kind="environment",
            )

        cmd = config.build_docker_cmd(str(test_copy.resolve()))
        started = time.time()
        try:
            stdout, stderr, returncode = _run_docker_cmd(
                cmd, timeout_seconds=config.timeout_seconds
            )
        except subprocess.TimeoutExpired as exc:
            # Kill client process (done inside helper) + force-remove named container.
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
            if log_path is not None:
                _write_log(log_path, result, config=config)
            return result
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
            if log_path is not None:
                _write_log(log_path, result, config=config)
            return result
        finally:
            shutil.rmtree(test_copy_parent, ignore_errors=True)

        duration = time.time() - started
        env_err = None
        error_kind = None
        combined = stdout + "\n" + stderr
        if "No module named pytest" in combined or "No module named 'pytest'" in combined:
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
        if log_path is not None:
            _write_log(log_path, result, config=config)
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
) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text(_format_log(result, config=config), encoding="utf-8", newline="\n")


def _format_log(
    result: TestResult, *, config: DockerRunConfig | None = None
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
