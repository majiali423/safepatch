"""Fail dedicated Docker gates when docker_e2e tests never actually pass."""

from __future__ import annotations

import os

_docker_selected = 0
_docker_outcomes: dict[str, str] = {}


def pytest_sessionstart(session) -> None:  # noqa: ARG001
    global _docker_selected, _docker_outcomes
    _docker_selected = 0
    _docker_outcomes = {}


def pytest_collection_modifyitems(config, items) -> None:  # noqa: ARG001
    global _docker_selected
    _docker_selected = sum(1 for item in items if item.get_closest_marker("docker_e2e"))


def pytest_runtest_logreport(report) -> None:
    if "docker_e2e" not in getattr(report, "keywords", {}):
        return
    nodeid = report.nodeid
    if report.when == "setup" and report.skipped:
        _docker_outcomes[nodeid] = "skipped"
        return
    if report.when == "setup" and report.failed:
        _docker_outcomes[nodeid] = "failed"
        return
    if report.when == "call":
        if report.passed:
            _docker_outcomes[nodeid] = "passed"
        elif report.failed:
            _docker_outcomes[nodeid] = "failed"
        elif report.skipped:
            _docker_outcomes[nodeid] = "skipped"
    if report.when == "teardown" and report.failed:
        _docker_outcomes[nodeid] = "failed"


def pytest_sessionfinish(session, exitstatus) -> None:  # noqa: ARG001
    if os.environ.get("SAFEPATCH_REQUIRE_DOCKER_E2E") != "1":
        return
    passed = sum(1 for outcome in _docker_outcomes.values() if outcome == "passed")
    if _docker_selected == 0 or passed == 0:
        session.exitstatus = 1
        reporter = session.config.pluginmanager.get_plugin("terminalreporter")
        if reporter is not None:
            reporter.write_line(
                "SAFEPATCH_REQUIRE_DOCKER_E2E: Docker E2E tests must run and pass; "
                "missing infrastructure is a failure",
                red=True,
            )
