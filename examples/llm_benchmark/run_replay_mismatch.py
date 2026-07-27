"""Replay v0.2 Full12 context-mismatch diffs through v0.3 preflight (eval-only).

Does NOT modify code_agent product code, TASK, tests, hidden, or references.
Proves mechanism only — not counted toward real-LLM success rates.
"""

from __future__ import annotations

import json
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BENCH = Path(__file__).resolve().parent
REPLAY_ROOT = BENCH / "replays" / "v02_full12_mismatch"
OUT_ROOT = BENCH / "results" / "replay_v03"
REPORT_PATH = BENCH / "REPLAY_V03_REPORT.md"

sys.path.insert(0, str(ROOT))
from code_agent.controller import TaskController  # noqa: E402
from code_agent.eval.hidden import discover_hidden_dir, run_hidden_tests  # noqa: E402
from code_agent.llm import LLMClient  # noqa: E402
from code_agent.runtime.docker_pytest import DockerPytestRunner  # noqa: E402
from code_agent.state import ApprovalBinding, SessionStatus  # noqa: E402

CASES = [
    {
        "task_id": "bench01_div_zero",
        "v02_run_id": "20260727T040645Z_1884b9e3",
        "bad_diff_source": "bad_attempt_1",
        "read_file": {"path": "mathutil.py", "start_line": 1, "end_line": 40},
        "good_files": ["mathutil.py"],
        "tests_to_run": ["tests/test_div.py"],
    },
    {
        "task_id": "bench05_red_herring",
        "v02_run_id": "20260727T040734Z_0e074b8c",
        "bad_diff_source": "bad_attempt_1",
        "read_file": {"path": "pricing.py", "start_line": 1, "end_line": 40},
        "good_files": ["pricing.py"],
        "tests_to_run": ["tests/test_price.py"],
    },
]


def _utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def _load_bad_tool(task_id: str, name: str) -> dict:
    path = REPLAY_ROOT / task_id / f"{name}.json"
    return json.loads(path.read_text(encoding="utf-8"))


def _good_tool(task_id: str, files: list[str], tests: list[str]) -> dict:
    diff = (BENCH / "references" / task_id / "reference.diff").read_text(
        encoding="utf-8"
    )
    return {
        "tool": "propose_patch",
        "args": {
            "diagnosis": "reference fix after preflight regeneration",
            "affected_files": files,
            "unified_diff": diff,
            "expected_behavior": "public tests pass",
            "risk_notes": "replay good patch",
            "tests_to_run": tests,
        },
    }


def _build_script(case: dict) -> list[dict]:
    bad = _load_bad_tool(case["task_id"], case["bad_diff_source"])
    good = _good_tool(case["task_id"], case["good_files"], case["tests_to_run"])
    read = {
        "tool": "read_file",
        "args": case["read_file"],
    }
    # First: historical mismatch patch; then read; then legal reference patch.
    return [bad, read, good]


def _trace_events(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _event_names(events: list[dict]) -> list[str]:
    return [e.get("event", "?") for e in events]


def run_case(case: dict, runner: DockerPytestRunner) -> dict:
    task_id = case["task_id"]
    task_dir = BENCH / task_id
    out_dir = OUT_ROOT / task_id
    if out_dir.exists():
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True)
    session_base = out_dir / "_sessions"

    approvals: list[dict] = []

    def approve(binding: ApprovalBinding, attempt: int) -> bool:
        approvals.append(
            {
                "attempt": attempt,
                "patch_hash": binding.patch_hash,
                "working_tree_hash": binding.working_tree_hash,
                "affected_files": list(binding.proposal.affected_files),
            }
        )
        return True

    description = (task_dir / "TASK.txt").read_text(encoding="utf-8").strip()
    script = _build_script(case)
    (out_dir / "dry_run_script.json").write_text(
        json.dumps(script, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    (out_dir / "source_meta.json").write_text(
        json.dumps(
            {
                "task_id": task_id,
                "v02_full12_run_id": case["v02_run_id"],
                "bad_diff": f"replays/v02_full12_mismatch/{task_id}/{case['bad_diff_source']}.diff",
                "good_diff": f"references/{task_id}/reference.diff",
                "note": "Mechanism proof only; not real-LLM score.",
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    controller = TaskController(
        llm=LLMClient(dry_run_script=script),
        runner=runner,
        approve=approve,
        say=lambda m: print(f"[{task_id}] {m}", flush=True),
        session_base=session_base,
    )
    session = controller.run(task_dir, description)

    # Copy artifacts
    art = session.artifacts_dir
    for name in ("summary.json", "trace.jsonl", "final.diff", "baseline.log"):
        src = art / name
        if src.exists():
            shutil.copy2(src, out_dir / name)
    for log in art.glob("attempt-*.log"):
        shutil.copy2(log, out_dir / log.name)

    summary = session.to_summary()
    events = _trace_events(out_dir / "trace.jsonl")
    names = _event_names(events)

    # Hidden eval only if product succeeded.
    hidden_pass = None
    eval_status = "PUBLIC_TESTS_FAILED"
    if session.status == SessionStatus.SUCCEEDED and summary.get("final_tests_passed"):
        hidden_dir = discover_hidden_dir(task_dir)
        working_copy = session.workspace_root
        if hidden_dir is not None and working_copy.is_dir():
            hidden_result = run_hidden_tests(
                working_copy=working_copy,
                hidden_tests_dir=hidden_dir,
                results_dir=out_dir,
                runner=runner,
            )
            hidden_pass = hidden_result.get("hidden_pass")
            eval_status = hidden_result.get("eval_status") or (
                "SUCCEEDED" if hidden_pass else "HIDDEN_TESTS_FAILED"
            )
        else:
            eval_status = "SUCCEEDED"
    elif session.status.value == "PATCH_NOT_APPLICABLE":
        eval_status = "PATCH_NOT_APPLICABLE"

    preflight_fails = sum(1 for e in events if e.get("event") == "patch_preflight_failed")
    preflight_oks = sum(1 for e in events if e.get("event") == "patch_preflight_succeeded")
    regens = sum(1 for e in events if e.get("event") == "patch_regeneration_requested")
    approvals_evt = sum(1 for e in events if e.get("event") == "approval_decision")
    pytest_after = sum(1 for e in events if e.get("event") == "pytest_finished")
    apply_after = sum(
        1 for e in events if e.get("event") == "patch_apply_failed_after_preflight"
    )
    # Prefer product summary counters when present.
    metrics = {
        "task_id": task_id,
        "product_status": session.status.value,
        "public_pass": bool(summary.get("final_tests_passed")),
        "hidden_pass": hidden_pass,
        "eval_status": eval_status,
        "patch_preflight_failures": int(
            summary.get("patch_preflight_failures") or preflight_fails
        ),
        "patch_preflight_successes": int(
            summary.get("patch_preflight_successes") or preflight_oks
        ),
        "patch_regeneration_retries": int(
            summary.get("total_patch_regeneration_retries") or regens
        ),
        "approvals": len(approvals),
        "approval_events": approvals_evt,
        "repair_attempts": int(summary.get("attempts_used") or 0),
        "pytest_after_apply": pytest_after,
        "patch_apply_failures_after_preflight": int(
            summary.get("patch_apply_failures_after_preflight") or apply_after
        ),
        "approval_records": approvals,
        "trace_event_order": names,
        "checks": {},
        "session_path": str(session.session_dir),
        "result_path": str(out_dir),
    }

    # Chronological: first approval must follow a preflight_succeeded; no approval
    # may follow preflight_failed without an intervening success.
    last_ok = False
    bad_approval = 0
    for e in events:
        ev = e.get("event")
        if ev == "patch_preflight_succeeded":
            last_ok = True
        elif ev == "patch_preflight_failed":
            last_ok = False
        elif ev == "approval_decision":
            if not last_ok:
                bad_approval += 1

    metrics["checks"] = {
        "preflight_rejected_bad_diff": metrics["patch_preflight_failures"] >= 1,
        "no_approval_on_preflight_fail": bad_approval == 0 and metrics["approvals"] == 1,
        "repair_zero_until_good_apply": metrics["repair_attempts"] == 1,
        "regen_requested": metrics["patch_regeneration_retries"] >= 1,
        "public_and_hidden": metrics["public_pass"] is True and hidden_pass is True,
        "eval_succeeded": eval_status == "SUCCEEDED",
        "after_preflight_apply_fail_zero": metrics[
            "patch_apply_failures_after_preflight"
        ]
        == 0,
    }
    metrics["mechanism_pass"] = all(metrics["checks"].values())

    (out_dir / "replay_metrics.json").write_text(
        json.dumps(metrics, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    return metrics


def write_report(rows: list[dict], *, started_at: str) -> None:
    lines = [
        "# v0.3 Mismatch Diff Replay Report",
        "",
        "**Mode:** scripted / fake LLM using v0.2 Full12 real context-mismatch diffs.",
        "**Not counted** toward real-LLM success rates.",
        "",
        f"started_at: `{started_at}`",
        f"finished_at: `{_utc()}`",
        "",
        "## Results",
        "",
        "| task | product | eval | public | hidden | preflight_fail | regen | approvals | repair | pytest_after | after_pf | mechanism_pass |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for r in rows:
        lines.append(
            "| {tid} | {ps} | {es} | {pp} | {hp} | {pf} | {rg} | {ap} | {ra} | {py} | {af} | {mp} |".format(
                tid=r["task_id"],
                ps=r["product_status"],
                es=r["eval_status"],
                pp=r["public_pass"],
                hp=r["hidden_pass"],
                pf=r["patch_preflight_failures"],
                rg=r["patch_regeneration_retries"],
                ap=r["approvals"],
                ra=r["repair_attempts"],
                py=r["pytest_after_apply"],
                af=r["patch_apply_failures_after_preflight"],
                mp=r["mechanism_pass"],
            )
        )
    lines.append("")
    for r in rows:
        lines.append(f"### `{r['task_id']}`")
        lines.append("")
        lines.append("Checks:")
        for k, v in (r.get("checks") or {}).items():
            lines.append(f"- {k}: **{v}**")
        lines.append("")
        lines.append("Trace event order:")
        lines.append("")
        lines.append("```")
        lines.append(" -> ".join(r.get("trace_event_order") or []))
        lines.append("```")
        lines.append("")
        lines.append(f"result: `{r.get('result_path')}`")
        lines.append("")
    lines.append(
        f"Overall mechanism: **{'PASS' if all(r['mechanism_pass'] for r in rows) else 'FAIL'}**"
    )
    lines.append("")
    REPORT_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"Wrote {REPORT_PATH}", flush=True)


def main() -> int:
    OUT_ROOT.mkdir(parents=True, exist_ok=True)
    started = _utc()
    runner = DockerPytestRunner()
    ok, reason = runner.preflight()
    if not ok:
        print(f"ERROR: Docker unavailable: {reason}", file=sys.stderr)
        return 4

    rows = []
    for case in CASES:
        print(f"\n===== REPLAY {case['task_id']} =====", flush=True)
        row = run_case(case, runner)
        rows.append(row)
        print(json.dumps({k: row[k] for k in row if k != "trace_event_order"}, indent=2), flush=True)

    write_report(rows, started_at=started)
    (OUT_ROOT / "index.json").write_text(
        json.dumps({"started_at": started, "rows": rows}, indent=2, ensure_ascii=False)
        + "\n",
        encoding="utf-8",
    )
    return 0 if all(r["mechanism_pass"] for r in rows) else 1


if __name__ == "__main__":
    raise SystemExit(main())
