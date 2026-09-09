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

from code_agent.patching.test_collection import (
    INVENTORY_FILENAME,
    load_test_inventory_file,
)
from code_agent.runtime.docker_config import DockerRunConfig
from code_agent.runtime.exit_classification import classify_runner_exit
from code_agent.state import TestResult
from code_agent.tracing.sanitize import sanitize_text

DOCKER_IMAGE = "python:3.12-slim"
LOCAL_PYTEST_IMAGE = "safepatch-pytest:local"
MAX_CAPTURE_BYTES = 1_048_576
MAX_LOG_CHARS = 1_048_576

FAILED_TEST_RE = re.compile(
    r"^(FAILED|ERROR)\s+(\S+?)(?:\s+-|$)",
    re.MULTILINE,
)
INVENTORY_PLUGIN_DIR = Path(__file__).resolve().parent / "plugins"
CONTROLLED_BOOTSTRAP = "/opt/safepatch/run_pytest.py"


def pytest_args_from_product_argv(argv: list[str]) -> list[str] | None:
    """Return pytest args after a product ``python -m pytest`` prefix, else None."""
    if (
        len(argv) >= 3
        and argv[0] in {"python", "python3"}
        and argv[1] == "-m"
        and argv[2] == "pytest"
    ):
        return list(argv[3:])
    return None


def attach_inventory_plugin(cmd: list[str], config: DockerRunConfig) -> list[str]:
    """Mount the bootstrap and load the plugin by absolute path.

    Does not set PYTHONPATH or ``-p safepatch_inventory``; those are shadowable
    from the target workdir. DockerRunConfig.to_dict() is unchanged.
    """
    argv = list(config.pytest_argv)
    image_at = len(cmd) - len(argv) - 1
    if image_at < 0 or cmd[image_at] != config.image:
        return cmd
    rest = pytest_args_from_product_argv(argv)
    if rest is None:
        return cmd
    plugin = str(INVENTORY_PLUGIN_DIR.resolve())
    return [
        *cmd[:image_at],
        "-v",
        f"{plugin}:/opt/safepatch:ro",
        config.image,
        "python",
        CONTROLLED_BOOTSTRAP,
        *rest,
    ]


_CONTAINER_NAME_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_.-]{0,62}$")
_CONTAINER_NAME_PREFIX = "safepatch-pytest-"

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
            return False, "pytest image missing; run: safepatch --build-image"
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
                cmd = attach_inventory_plugin(
                    config.build_docker_cmd(str(test_copy.resolve())),
                    config,
                )
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
                except RuntimeError as exc:
                    _force_remove_container(config.container_name)
                    message = str(exc)
                    result = TestResult(
                        exit_code=-1,
                        stdout="",
                        stderr=message,
                        duration_sec=time.time() - started,
                        environment_error=message
                        if message.startswith("TEST_ENVIRONMENT_ERROR")
                        else f"TEST_ENVIRONMENT_ERROR: {exc}",
                        error_kind="environment",
                    )
                except KeyboardInterrupt:
                    _force_remove_container(config.container_name)
                    raise
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
                    classified = classify_runner_exit(returncode, stdout, stderr)
                    inventory = load_test_inventory_file(
                        test_copy / INVENTORY_FILENAME
                    )
                    result = TestResult(
                        exit_code=returncode,
                        stdout=stdout,
                        stderr=stderr,
                        duration_sec=duration,
                        failed_tests=_extract_failed_tests(stdout + "\n" + stderr),
                        traceback_summary=_extract_traceback_summary(
                            stdout + "\n" + stderr
                        ),
                        environment_error=classified.environment_error,
                        error_kind=classified.error_kind,
                        collected_test_files=None,
                        test_inventory=inventory,
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
    cmd: list[str], *, timeout_seconds: int, max_bytes: int = MAX_CAPTURE_BYTES
) -> tuple[str, str, int]:
    """Run docker CLI with bounded stdout/stderr collection."""
    import threading

    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=False,
        env=_sanitized_subprocess_env(),
    )
    chunks = {"stdout": bytearray(), "stderr": bytearray()}
    exceeded = {"value": False}
    lock = threading.Lock()

    def _kill_proc() -> None:
        try:
            proc.kill()
        except OSError:
            pass

    def _read(stream, key: str) -> None:
        if stream is None:
            return
        try:
            while True:
                data = _read_available_bytes(stream)
                if not data:
                    break
                with lock:
                    if len(chunks["stdout"]) + len(chunks["stderr"]) + len(data) > max_bytes:
                        exceeded["value"] = True
                        _kill_proc()
                        break
                    chunks[key].extend(data)
        finally:
            stream.close()

    readers = [
        threading.Thread(target=_read, args=(proc.stdout, "stdout"), daemon=True),
        threading.Thread(target=_read, args=(proc.stderr, "stderr"), daemon=True),
    ]
    for thread in readers:
        thread.start()
    try:
        proc.wait(timeout=timeout_seconds)
    except subprocess.TimeoutExpired:
        _kill_proc()
        for thread in readers:
            thread.join(timeout=2)
        raise subprocess.TimeoutExpired(
            cmd=cmd,
            timeout=timeout_seconds,
            output=_decode_captured(chunks["stdout"]),
            stderr=_decode_captured(chunks["stderr"]),
        ) from None
    except KeyboardInterrupt:
        _kill_proc()
        for thread in readers:
            thread.join(timeout=2)
        raise
    for thread in readers:
        thread.join(timeout=5)
    if exceeded["value"]:
        _kill_proc()
        try:
            proc.wait(timeout=2)
        except Exception:  # noqa: BLE001
            pass
        raise RuntimeError(
            f"TEST_ENVIRONMENT_ERROR: docker output exceeded {max_bytes} bytes"
        )
    return (
        _decode_captured(chunks["stdout"]),
        _decode_captured(chunks["stderr"]),
        int(proc.returncode or 0),
    )


def _read_available_bytes(stream) -> bytes:
    """Return already-buffered bytes without waiting for a full 64 KiB block."""
    read1 = getattr(stream, "read1", None)
    if callable(read1):
        return read1(4096) or b""
    raw = getattr(stream, "raw", None)
    if raw is not None:
        try:
            return raw.read(4096) or b""
        except Exception:  # noqa: BLE001
            pass
    try:
        return os.read(stream.fileno(), 4096) or b""
    except Exception:  # noqa: BLE001
        data = stream.read(4096)
        return data or b""


def _decode_captured(data: bytearray) -> str:
    return bytes(data).decode("utf-8", errors="replace")


def allocate_container_name(explicit: str | None = None) -> str:
    """Allocate a Docker-safe container name for one ``run_pytest`` invocation.

    - ``None`` / empty / unsafe → ``safepatch-pytest-<uuid>`` (product default).
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


_PLACEHOLDER_PARENT = "<disposable-test-copy>"
_PLACEHOLDER_WORKING = "<disposable-working-copy>"


def _path_string_variants(path: Path) -> list[str]:
    """Absolute and slash-normalized spellings of ``path`` (longest first)."""
    variants: set[str] = set()
    try:
        resolved = path.resolve()
    except OSError:
        resolved = path
    for candidate in (path, resolved):
        raw = str(candidate)
        variants.add(raw)
        variants.add(candidate.as_posix())
        variants.add(raw.replace("/", "\\"))
        variants.add(raw.replace("\\", "/"))
        variants.add(candidate.as_posix().replace("/", "\\"))
    return sorted((item for item in variants if item), key=len, reverse=True)


def _redact_disposable_paths(text: str, *, parent: Path) -> str:
    """Replace this run's disposable absolute paths with stable placeholders."""
    working = parent / "working_copy"
    out = text
    for variant in _path_string_variants(working):
        out = out.replace(variant, _PLACEHOLDER_WORKING)
    for variant in _path_string_variants(parent):
        out = out.replace(variant, _PLACEHOLDER_PARENT)
    return out


def _format_cleanup_oserror(exc: OSError, *, stage: str) -> str:
    """Keep OSError type/errno without embedding absolute paths."""
    errno_part = (
        f" [Errno {exc.errno}]" if getattr(exc, "errno", None) is not None else ""
    )
    return f"{type(exc).__name__} during {stage}:{errno_part}"


def _remove_disposable_test_copy(parent: Path, *, image: str) -> str | None:
    """Remove one disposable parent directory. Return redacted error text on failure.

    Host ``rmtree`` first. If container UID 1000 created non-writable dirs
    (e.g. ``__pycache__``), fall back to a locked-down Docker wipe as UID 1000
    that mounts only ``parent / "working_copy"`` (never the private parent, the
    formal workspace, or the workspace parent). After wipe, the host removes the
    emptied working_copy, then the emptied parent, and confirms parent is gone.
    Returned errors never include absolute host paths.
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
        wipe_error = _redact_disposable_paths(str(exc), parent=parent)

    try:
        if test_copy.exists():
            shutil.rmtree(test_copy)
        if parent.exists():
            parent.rmdir()
    except OSError as exc:
        if parent.exists():
            detail = _format_cleanup_oserror(
                exc, stage="disposable test-copy removal"
            )
            if wipe_error:
                detail = f"{detail}; docker wipe: {wipe_error}"
            return detail

    if parent.exists():
        detail = f"path still exists after cleanup: {_PLACEHOLDER_PARENT}"
        if wipe_error:
            detail = f"{detail}; docker wipe: {wipe_error}"
        return detail
    return None


def _docker_wipe_as_uid_1000(test_copy: Path, *, image: str) -> None:
    """Clear contents of disposable ``working_copy`` as UID 1000; never delete mount root."""
    if not test_copy.exists():
        return
    name = f"safepatch-cleanup-{uuid.uuid4().hex}"
    host = str(test_copy.resolve())
    parent = test_copy.parent
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
    try:
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
    except subprocess.TimeoutExpired:
        _force_remove_container(name)
        raise RuntimeError("cleanup container timed out") from None
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "docker wipe failed").strip()
        first = detail.splitlines()[0] if detail else "docker wipe failed"
        raise RuntimeError(_redact_disposable_paths(first, parent=parent))


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
        collected_test_files=result.collected_test_files,
        test_inventory=result.test_inventory,
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
        # Persist only non-sensitive identity; never absolute host paths.
        parts.append("test_copy_created: true")
        parts.append(f"test_copy_id: {test_copy_parent.name}")
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
    text = "\n".join(parts)
    if len(text) > MAX_LOG_CHARS:
        text = text[:MAX_LOG_CHARS] + "\n# truncated: log exceeded size cap\n"
    return sanitize_text(text)
