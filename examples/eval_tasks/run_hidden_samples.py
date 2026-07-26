"""Run the two P2-C1 hidden-eval sample tasks (dry-run scripts, no live LLM).

Sample 01: correct fix → public + hidden pass.
Sample 02: hard-coded public-only fix → product SUCCEEDED, eval HIDDEN_TESTS_FAILED.

Note: sample02 uses a fixed dry-run hard-coded patch to validate the hidden-test
infrastructure only. It does NOT represent real LLM capability. See
sample02_normalize/NOTE.md.
"""

from __future__ import annotations

import json
import shutil
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TASKS_ROOT = Path(__file__).resolve().parent
RESULTS = TASKS_ROOT / "results"

sys.path.insert(0, str(ROOT))
from code_agent.controller import TaskController  # noqa: E402
from code_agent.envfile import load_dotenv  # noqa: E402
from code_agent.eval.hidden import evaluate_after_product  # noqa: E402
from code_agent.llm import LLMClient  # noqa: E402
from code_agent.runtime.docker_pytest import DockerPytestRunner  # noqa: E402

load_dotenv(ROOT / ".env")

SAMPLES = [
    {
        "id": "sample01_zero_div",
        "script": TASKS_ROOT / "dry_run_sample01_correct.json",
    },
    {
        "id": "sample02_normalize",
        "script": TASKS_ROOT / "dry_run_sample02_hardcode.json",
    },
]


def _run_id() -> str:
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"{ts}_{uuid.uuid4().hex[:8]}"


def run_sample(sample: dict) -> dict:
    task_dir = TASKS_ROOT / sample["id"]
    description = (task_dir / "TASK.txt").read_text(encoding="utf-8").strip()
    script = json.loads(sample["script"].read_text(encoding="utf-8"))
    run_id = _run_id()
    out_dir = RESULTS / sample["id"] / run_id
    out_dir.mkdir(parents=True, exist_ok=False)
    session_base = RESULTS / "_sessions" / sample["id"] / run_id
    session_base.mkdir(parents=True, exist_ok=False)

    print(f"\n===== {sample['id']} (run_id={run_id}) =====", flush=True)

    runner = DockerPytestRunner()
    ok, reason = runner.preflight()
    if not ok:
        print(f"TEST_ENVIRONMENT_ERROR: {reason}", flush=True)
        return {
            "task": sample["id"],
            "run_id": run_id,
            "error": reason,
            "eval_status": "HIDDEN_TEST_ENVIRONMENT_ERROR",
        }

    controller = TaskController(
        llm=LLMClient(dry_run_script=script),
        runner=runner,
        approve=lambda *_: True,
        say=lambda m: print(m, flush=True),
        session_base=session_base,
    )
    session = controller.run(task_dir, description)
    summary = session.to_summary()
    (out_dir / "summary.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    # Copy product artifacts (never rewrite product summary after hidden eval).
    for name in ("trace.jsonl", "baseline.log", "repo_map.json", "final.diff"):
        src = session.artifacts_dir / name
        if src.exists():
            shutil.copy2(src, out_dir / name)
    for log in session.artifacts_dir.glob("attempt-*.log"):
        shutil.copy2(log, out_dir / log.name)

    metrics = evaluate_after_product(
        product_summary=summary,
        working_copy=session.workspace_root,
        task_dir=task_dir,
        results_dir=out_dir,
        runner=runner,
    )
    # Ensure product summary on disk is unchanged by eval.
    disk_summary = json.loads((out_dir / "summary.json").read_text(encoding="utf-8"))
    assert disk_summary["status"] == summary["status"]

    row = {
        "task": sample["id"],
        "run_id": run_id,
        "results_path": str(out_dir),
        "session_path": str(session.session_dir),
        **metrics,
    }
    print(json.dumps(row, indent=2, ensure_ascii=False), flush=True)
    return row


def main() -> int:
    RESULTS.mkdir(parents=True, exist_ok=True)
    rows = [run_sample(s) for s in SAMPLES]
    report = {"samples": rows}
    (RESULTS / "hidden_samples_report.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print("\n===== HIDDEN SAMPLES REPORT =====", flush=True)
    print(json.dumps(report, indent=2, ensure_ascii=False), flush=True)
    ok = (
        rows[0].get("eval_status") == "SUCCEEDED"
        and rows[0].get("hidden_pass") is True
        and rows[1].get("eval_status") == "HIDDEN_TESTS_FAILED"
        and rows[1].get("product_status") == "SUCCEEDED"
        and rows[1].get("hidden_pass") is False
    )
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
