from __future__ import annotations

from pathlib import Path
from typing import Protocol

from code_agent.state import TestResult


class TestRunner(Protocol):
    """Minimal test-execution contract used by TaskController."""

    def run_pytest(
        self,
        workspace_root: Path,
        *,
        log_path: Path | None = None,
    ) -> TestResult: ...
