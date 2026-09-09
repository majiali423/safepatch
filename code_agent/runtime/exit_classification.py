"""Classify Docker and pytest exit codes without treating infra as assertion failures."""

from __future__ import annotations

from dataclasses import dataclass

# pytest: https://docs.pytest.org/en/stable/reference/exit-codes.html
PYTEST_OK = 0
PYTEST_TESTS_FAILED = 1
PYTEST_INTERRUPTED = 2
PYTEST_INTERNAL_ERROR = 3
PYTEST_USAGE_ERROR = 4
PYTEST_NO_TESTS = 5

# docker run: https://docs.docker.com/engine/containers/run/#exit-status
DOCKER_CLI_ERROR = 125
DOCKER_COMMAND_NOT_INVOKABLE = 126
DOCKER_COMMAND_NOT_FOUND = 127
SIGKILL_EXIT = 137


@dataclass(frozen=True)
class RunnerClassification:
    environment_error: str | None
    error_kind: str | None
    valid_failure_oracle: bool


def classify_runner_exit(
    returncode: int,
    stdout: str = "",
    stderr: str = "",
) -> RunnerClassification:
    combined = f"{stdout}\n{stderr}"
    if "No module named pytest" in combined or "No module named 'pytest'" in combined:
        return RunnerClassification(
            "TEST_ENVIRONMENT_ERROR: pytest missing in container",
            "environment",
            False,
        )
    if returncode == PYTEST_OK:
        return RunnerClassification(None, None, False)
    if returncode == PYTEST_TESTS_FAILED:
        has_failure = bool(
            "FAILED" in combined or "ERROR" in combined or "AssertionError" in combined
        )
        return RunnerClassification(None, None, has_failure)
    if returncode == DOCKER_CLI_ERROR:
        return RunnerClassification(
            "TEST_ENVIRONMENT_ERROR: docker failed to run the container (exit 125)",
            "environment",
            False,
        )
    if returncode == DOCKER_COMMAND_NOT_INVOKABLE:
        return RunnerClassification(
            "TEST_ENVIRONMENT_ERROR: container command not invocable (exit 126)",
            "environment",
            False,
        )
    if returncode == DOCKER_COMMAND_NOT_FOUND:
        return RunnerClassification(
            "TEST_ENVIRONMENT_ERROR: container command not found (exit 127)",
            "environment",
            False,
        )
    if returncode == SIGKILL_EXIT:
        return RunnerClassification(
            "TEST_ENVIRONMENT_ERROR: process killed by signal (exit 137); "
            "OOM is not inferred without Docker memory events",
            "environment",
            False,
        )
    if returncode == PYTEST_INTERNAL_ERROR:
        return RunnerClassification(
            "TEST_ENVIRONMENT_ERROR: pytest internal error (exit 3)",
            "runner",
            False,
        )
    if returncode == PYTEST_USAGE_ERROR:
        return RunnerClassification(
            "TEST_ENVIRONMENT_ERROR: pytest usage error (exit 4)",
            "runner",
            False,
        )
    if returncode == PYTEST_NO_TESTS:
        return RunnerClassification(
            "TEST_ENVIRONMENT_ERROR: pytest collected no tests (exit 5)",
            "runner",
            False,
        )
    if returncode == PYTEST_INTERRUPTED:
        if "KeyboardInterrupt" in combined or "Interrupted" in combined:
            detail = "pytest interrupted (exit 2)"
        elif "ERROR collecting" in combined or "collection error" in combined.lower():
            detail = "pytest collection error (exit 2)"
        else:
            detail = "pytest exit 2 (interrupt or collection error)"
        return RunnerClassification(
            f"TEST_ENVIRONMENT_ERROR: {detail}",
            "runner",
            False,
        )
    if returncode > 128:
        return RunnerClassification(
            f"TEST_ENVIRONMENT_ERROR: process signaled (exit {returncode})",
            "environment",
            False,
        )
    return RunnerClassification(
        f"TEST_ENVIRONMENT_ERROR: unexpected runner exit {returncode}",
        "environment",
        False,
    )


def has_valid_failure_oracle(exit_code: int, stdout: str, stderr: str) -> bool:
    return classify_runner_exit(exit_code, stdout, stderr).valid_failure_oracle
