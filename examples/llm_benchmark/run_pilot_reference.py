"""Orchestrate pilot public+hidden checks and emit metrics samples.

Uses reference patches only — does NOT call a real LLM.
public_pass is derived from final_tests_passed-equivalent (pytest exit==0).
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
REPO_ROOT = ROOT.parents[1]
sys.path.insert(0, str(REPO_ROOT))

from code_agent.eval.hidden import discover_hidden_dir, run_hidden_tests  # noqa: E402
from code_agent.eval.status import EvalStatus  # noqa: E402
from code_agent.patching.applier import apply_proposal  # noqa: E402
from code_agent.state import PatchProposal, TestResult  # noqa: E402

from metrics_lib import build_run_metrics  # noqa: E402
from self_check import LocalRunner  # noqa: E402

MANIFEST = json.loads((ROOT / "benchmark_manifest.json").read_text(encoding="utf-8"))


def _apply_reference(task_id: str, wc: Path) -> list[str]:
    meta = json.loads(
        (ROOT / "references" / task_id / "meta.json").read_text(encoding="utf-8")
    )
    diff = (ROOT / "references" / task_id / "reference.diff").read_text(encoding="utf-8")
    files = list(meta["files"])
    proposal = PatchProposal(
        diagnosis="reference",
        affected_files=files,
        unified_diff=diff,
        expected_behavior="ok",
        risk_notes="ref",
        tests_to_run=["tests"],
    )
    result = apply_proposal(proposal, wc, allow_test_changes=False, allow_new_tests=False)
    if not result.ok:
        raise RuntimeError(result.error)
    return files


def run_task_reference(task: dict) -> dict:
    task_id = task["task_id"]
    task_dir = ROOT / task_id
    started = time.time()
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        wc = tmp_path / "wc"
        shutil.copytree(task_dir, wc)
        results_dir = ROOT / "results" / "pilot_reference" / task_id
        if results_dir.exists():
            shutil.rmtree(results_dir)
        results_dir.mkdir(parents=True)

        base = subprocess.run(
            ["python", "-m", "pytest", "-q", "-p", "no:cacheprovider"],
            cwd=wc,
            capture_output=True,
            text=True,
            check=False,
        )
        (results_dir / "baseline_public.log").write_text(
            (base.stdout or "") + (base.stderr or ""), encoding="utf-8"
        )

        changed = _apply_reference(task_id, wc)
        pub = subprocess.run(
            ["python", "-m", "pytest", "-q", "-p", "no:cacheprovider"],
            cwd=wc,
            capture_output=True,
            text=True,
            check=False,
        )
        (results_dir / "reference_public.log").write_text(
            (pub.stdout or "") + (pub.stderr or ""), encoding="utf-8"
        )
        final_tests_passed = pub.returncode == 0

        # Synthetic product summary (reference path — not an Agent run).
        summary = {
            "status": "SUCCEEDED" if final_tests_passed else "FAILED_MAX_ATTEMPTS",
            "final_tests_passed": final_tests_passed,
            "attempts_used": 1,
            "total_format_retries_used": 0,
            "changed_files": changed,
            "stop_reason": "reference_patch"
            if final_tests_passed
            else "reference_public_failed",
        }
        (results_dir / "summary.json").write_text(
            json.dumps(summary, indent=2) + "\n", encoding="utf-8"
        )

        hidden_dir = discover_hidden_dir(task_dir)
        hidden_metrics = run_hidden_tests(
            working_copy=wc,
            hidden_tests_dir=hidden_dir,
            results_dir=results_dir,
            runner=LocalRunner(),
        )
        # Prefer final_tests_passed for public_pass (spec).
        metrics = build_run_metrics(
            task=task,
            summary=summary,
            hidden_pass=hidden_metrics.get("hidden_pass"),
            eval_status=str(hidden_metrics.get("eval_status")),
            duration_sec=time.time() - started,
            read_actions=0,
            policy_rejections=0,
            extra={
                "mode": "reference_patch_only",
                "baseline_public_exit": base.returncode,
                "product_status": summary["status"],
                "note_real_llm": False,
            },
        )
        # Demonstrate public_pass != product_status inference requirement:
        # if somehow status and final_tests_passed diverge, metrics follow final_tests_passed.
        assert metrics["public_pass"] is final_tests_passed
        (results_dir / "metrics.json").write_text(
            json.dumps(metrics, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        return metrics


def main() -> int:
    rows = [run_task_reference(t) for t in MANIFEST["tasks"]]
    report = {
        "real_llm_runs": False,
        "mode": "reference_patch_pilot",
        "tasks": rows,
        "public_pass_count": sum(1 for r in rows if r["public_pass"]),
        "hidden_pass_count": sum(1 for r in rows if r["hidden_pass"] is True),
        "eval_success_count": sum(1 for r in rows if r["eval_status"] == "SUCCEEDED"),
    }
    out = ROOT / "results" / "pilot_reference" / "summary_report.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2, ensure_ascii=False), flush=True)
    ok = (
        report["public_pass_count"] == len(rows)
        and report["hidden_pass_count"] == len(rows)
        and report["eval_success_count"] == len(rows)
    )
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
