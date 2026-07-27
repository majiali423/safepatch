"""infra01_format_retry — dry-run / fake-model only.

Verifies: illegal model output → format retry → legal tool call → success.
Excluded from real-LLM 12-task success rates and natural format-retry stats.
Does not modify product code.
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from code_agent.controller import TaskController
from code_agent.llm import LLMClient
from code_agent.state import SessionStatus, TestResult

INFRA = Path(__file__).resolve().parent
MINI = INFRA / "mini_repo"


class FailThenPassRunner:
    def __init__(self) -> None:
        self.calls = 0

    def run_pytest(self, workspace_root, *, log_path=None, config=None):
        self.calls += 1
        if self.calls == 1:
            result = TestResult(
                exit_code=1,
                stdout="FAILED",
                stderr="",
                duration_sec=0.01,
                failed_tests=["tests/test_mod.py::test_f"],
            )
        else:
            result = TestResult(
                exit_code=0, stdout="1 passed", stderr="", duration_sec=0.01
            )
        if log_path:
            log_path.write_text(result.stdout, encoding="utf-8")
        return result


def main() -> int:
    script = [
        "THIS IS NOT JSON AT ALL",
        {
            "tool": "propose_patch",
            "args": {
                "diagnosis": "wrong return",
                "affected_files": ["mod.py"],
                "unified_diff": (
                    "--- a/mod.py\n"
                    "+++ b/mod.py\n"
                    "@@ -1,2 +1,2 @@\n"
                    " def f():\n"
                    "-    return 1\n"
                    "+    return 2\n"
                ),
                "expected_behavior": "f returns 2",
                "risk_notes": "low",
                "tests_to_run": ["tests/test_mod.py"],
            },
        },
    ]
    with tempfile.TemporaryDirectory() as tmp:
        session_base = Path(tmp) / "sessions"
        controller = TaskController(
            llm=LLMClient(dry_run_script=script),
            runner=FailThenPassRunner(),  # type: ignore[arg-type]
            approve=lambda *_: True,
            say=lambda _m: None,
            session_base=session_base,
        )
        session = controller.run(MINI, "make f return 2")
        out = {
            "infra_id": "infra01_format_retry",
            "excluded_from_real_llm_rates": True,
            "status": session.status.value,
            "attempts_used": session.attempts_used,
            "total_format_retries_used": session.total_format_retries_used,
            "final_tests_passed": session.to_summary().get("final_tests_passed"),
            "ok": (
                session.status == SessionStatus.SUCCEEDED
                and session.total_format_retries_used >= 1
                and session.attempts_used == 1
            ),
        }
        report_dir = INFRA.parent / "results" / "infra01_format_retry"
        report_dir.mkdir(parents=True, exist_ok=True)
        (report_dir / "infra01_report.json").write_text(
            json.dumps(out, indent=2) + "\n", encoding="utf-8"
        )
        print(json.dumps(out, indent=2), flush=True)
        return 0 if out["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
