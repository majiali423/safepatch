from __future__ import annotations

from dataclasses import asdict, dataclass, field, replace
from typing import Any

# Public product pytest (default).
DEFAULT_PYTEST_ARGV: tuple[str, ...] = (
    "python",
    "-m",
    "pytest",
    "-q",
    "-p",
    "no:cacheprovider",
)

# Eval-only: run injected hidden tests under tests_hidden/.
HIDDEN_PYTEST_ARGV: tuple[str, ...] = (
    "python",
    "-m",
    "pytest",
    "-q",
    "-p",
    "no:cacheprovider",
    "tests_hidden",
)


@dataclass(frozen=True)
class DockerRunConfig:
    """Single source of truth for Docker pytest invocation and trace payload."""

    image: str
    network_mode: str = "none"
    memory: str = "512m"
    cpus: int = 1
    pids_limit: int = 128
    user: str = "1000:1000"
    timeout_seconds: int = 120
    remove_container: bool = True
    # Optional unique name for tests/audit (e.g. code-agent-test-<uuid>).
    # Empty/None = Docker assigns an ephemeral name (product default).
    container_name: str | None = None
    cap_drop: str = "ALL"
    no_new_privileges: bool = True
    read_only: bool = True
    tmpfs: str = "/tmp:rw,nosuid,nodev,noexec,size=64m"
    pytest_argv: tuple[str, ...] = field(default_factory=lambda: DEFAULT_PYTEST_ARGV)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["pytest_argv"] = list(self.pytest_argv)
        return data

    def with_pytest_argv(self, pytest_argv: tuple[str, ...]) -> DockerRunConfig:
        """Return a copy with different pytest argv; security fields unchanged."""
        return replace(self, pytest_argv=pytest_argv)

    def for_hidden_tests(self) -> DockerRunConfig:
        """Eval-only: same sandbox, pytest target = tests_hidden."""
        return self.with_pytest_argv(HIDDEN_PYTEST_ARGV)

    def build_docker_cmd(self, host_workspace: str) -> list[str]:
        """Build the exact docker CLI argv used for this run."""
        cmd: list[str] = ["docker", "run"]
        if self.remove_container:
            cmd.append("--rm")
        if self.container_name:
            cmd.extend(["--name", self.container_name])
        cmd.extend(
            [
                "--network",
                self.network_mode,
                "--cap-drop",
                self.cap_drop,
                "--security-opt",
                "no-new-privileges",
                "--read-only",
                "--tmpfs",
                self.tmpfs,
                "--memory",
                self.memory,
                "--cpus",
                str(self.cpus),
                "--pids-limit",
                str(self.pids_limit),
                "--user",
                self.user,
                # Minimal non-secret container env only (not host env dump).
                "--env",
                "HOME=/tmp",
                "-v",
                f"{host_workspace}:/work:rw",
                "-w",
                "/work",
                self.image,
                *self.pytest_argv,
            ]
        )
        return cmd
