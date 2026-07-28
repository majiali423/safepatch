from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

from code_agent.controller import TaskController
from code_agent.llm import LLMClient
from code_agent.state import TestResult


class LocalPytestRunner:
    """TEST DOUBLE ONLY: host pytest. Not the product Docker runtime."""

    def available(self):
        return True, "local"

    def run_pytest(self, workspace_root: Path, *, log_path: Path | None = None) -> TestResult:
        started = time.time()
        proc = subprocess.run(
            [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider"],
            cwd=workspace_root,
            capture_output=True,
            text=True,
            check=False,
        )
        result = TestResult(
            exit_code=proc.returncode,
            stdout=proc.stdout or "",
            stderr=proc.stderr or "",
            duration_sec=time.time() - started,
            failed_tests=[],
            traceback_summary=(proc.stdout or "")[-1000:],
        )
        if "FAILED" in result.stdout:
            for line in result.stdout.splitlines():
                if line.startswith("FAILED "):
                    result.failed_tests.append(line.split()[1])
        if log_path is not None:
            log_path.parent.mkdir(parents=True, exist_ok=True)
            log_path.write_text(result.stdout + "\n" + result.stderr, encoding="utf-8")
        return result


def test_fix_divide_closed_loop(tmp_path: Path):
    root = Path(__file__).resolve().parents[1]
    repo = root / "examples" / "buggy_calculator"
    script = json.loads(
        (root / "examples" / "dry_run_fix_divide.json").read_text(encoding="utf-8")
    )

    controller = TaskController(
        llm=LLMClient(dry_run_script=script),
        runner=LocalPytestRunner(),  # type: ignore[arg-type]
        approve=lambda *_: True,
        say=lambda _m: None,
        session_base=tmp_path / "sessions",
    )
    session = controller.run(
        repo,
        "divide 函数在除数为 0 时应该抛出 ValueError，但现在抛出了 ZeroDivisionError，请修复。",
    )

    assert session.status.value == "SUCCEEDED"
    summary = json.loads((session.artifacts_dir / "summary.json").read_text(encoding="utf-8"))
    assert summary["final_tests_passed"] is True
    assert summary["baseline_tests_passed"] is False
    assert session.attempts_used == 1
    final_diff = (session.artifacts_dir / "final.diff").read_text(encoding="utf-8")
    assert "ValueError" in final_diff
    assert (session.artifacts_dir / "trace.jsonl").exists()
    assert (session.artifacts_dir / "baseline.log").exists()
    assert (session.artifacts_dir / "attempt-1.log").exists()
