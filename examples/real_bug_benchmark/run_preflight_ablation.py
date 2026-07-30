"""Run the frozen 28-slot preflight on/off controlled comparison."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

BENCHMARK = Path(__file__).resolve().parent
ROOT = BENCHMARK.parents[1]
RESULTS = BENCHMARK / "results"

sys.path.insert(0, str(BENCHMARK))

from run_model_eval import freeze_record, run_one, utc_now  # noqa: E402

TASK_IDS = (
    "pysnooper-3",
    "tornado-11",
    "tqdm-3",
    "sanic-5",
    "thefuck-19",
    "tornado-10",
    "thefuck-16",
)
DEFAULT_EXPERIMENT = "deepseek_v4_flash_preflight_ablation_28run_20260730"
DEFAULT_MODEL = "deepseek-v4-flash"


def build_schedule() -> list[dict[str, Any]]:
    """Return a fixed interleaved schedule with reversed order in repeat two."""
    slots: list[dict[str, Any]] = []
    for repeat in (1, 2):
        for task_index, task_id in enumerate(TASK_IDS):
            enabled_first = (task_index + repeat) % 2 == 1
            for preflight_enabled in (enabled_first, not enabled_first):
                slots.append(
                    {
                        "slot": len(slots) + 1,
                        "task_id": task_id,
                        "repeat": repeat,
                        "preflight_enabled": preflight_enabled,
                    }
                )
    return slots


def schedule_sha256(schedule: list[dict[str, Any]]) -> str:
    payload = json.dumps(schedule, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(payload).hexdigest()


def current_git_commit() -> str:
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


def tracked_worktree_clean() -> bool:
    result = subprocess.run(
        ["git", "status", "--porcelain", "--untracked-files=no"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    return not result.stdout.strip()


def assert_frozen_inputs(state: dict[str, Any], args: argparse.Namespace) -> None:
    freeze = state["freeze"]
    if freeze["git_commit"] != current_git_commit():
        raise SystemExit("refusing to run: git commit changed")
    if not tracked_worktree_clean():
        raise SystemExit("refusing to run: tracked worktree is dirty")
    current = freeze_record(list(TASK_IDS), args.experiment, freeze["model"])
    for field in (
        "system_prompt_sha256",
        "product_files_sha256",
        "task_assets_sha256",
    ):
        if freeze[field] != current[field]:
            raise SystemExit(f"refusing to run: frozen {field} changed")
    limits = freeze["limits"]
    if float(limits["max_active_minutes"]) != args.max_minutes:
        raise SystemExit("refusing to run: frozen time limit changed")
    if float(limits["max_estimated_cost_usd"]) != args.max_cost_usd:
        raise SystemExit("refusing to run: frozen cost limit changed")


def atomic_json(path: Path, payload: dict[str, Any]) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def is_environment_failure(row: dict[str, Any]) -> bool:
    return bool(
        row.get("environment_error")
        or row.get("product_status") == "TEST_ENVIRONMENT_ERROR"
        or row.get("failure_type") in {"test_environment_error", "llm_error"}
    )


def condition_summary(rows: list[dict[str, Any]], enabled: bool) -> dict[str, Any]:
    selected = [row for row in rows if row["preflight_enabled"] is enabled]
    hidden_rows = [row for row in selected if row.get("hidden_pass") is not None]
    return {
        "runs": len(selected),
        "public_successes": sum(bool(row.get("public_pass")) for row in selected),
        "hidden_successes": sum(bool(row.get("hidden_pass")) for row in hidden_rows),
        "overall_successes": sum(bool(row.get("overall_pass")) for row in selected),
        "first_patch_applicable": (
            sum(row.get("first_patch_applicable") is True for row in selected)
            if enabled
            else None
        ),
        "preflight_rejections": sum(int(row.get("preflight_failures") or 0) for row in selected),
        "apply_failures_after_preflight": sum(
            int(row.get("apply_failures_after_preflight") or 0) for row in selected
        ),
        "apply_failures_without_preflight": sum(
            int(row.get("apply_failures_without_preflight") or 0) for row in selected
        ),
        "repair_attempts": sum(int(row.get("attempts_used") or 0) for row in selected),
        "model_calls": sum(int(row.get("model_calls") or 0) for row in selected),
        "tokens": sum(
            int((row.get("token_usage") or {}).get("total_tokens") or 0) for row in selected
        ),
        "duration_sec": round(sum(float(row.get("duration_sec") or 0) for row in selected), 3),
        "estimated_cost_usd": round(
            sum(
                float((row.get("estimated_cost") or {}).get("estimated_usd") or 0)
                for row in selected
            ),
            8,
        ),
    }


def totals(rows: list[dict[str, Any]]) -> dict[str, Any]:
    enabled = condition_summary(rows, True)
    disabled = condition_summary(rows, False)
    return {
        "completed_slots": len(rows),
        "preflight_enabled": enabled,
        "preflight_disabled": disabled,
        "total_duration_sec": round(enabled["duration_sec"] + disabled["duration_sec"], 3),
        "total_estimated_cost_usd": round(
            enabled["estimated_cost_usd"] + disabled["estimated_cost_usd"], 8
        ),
    }


def write_report(experiment_dir: Path, state: dict[str, Any]) -> None:
    summary = totals(state["runs"])
    on = summary["preflight_enabled"]
    off = summary["preflight_disabled"]
    lines = [
        "# Controlled preflight comparison",
        "",
        "This is a frozen small-sample controlled comparison, not a same-random-seed",
        "paired experiment or a causal accuracy claim.",
        "",
        f"- Candidate commit: `{state['freeze']['git_commit']}`",
        f"- Schedule SHA-256: `{state['freeze']['schedule_sha256']}`",
        f"- Completed slots: {summary['completed_slots']}/28",
        f"- Environment failures retained: {len(state['environment_failures'])}",
        f"- Status: `{state['status']}`",
        "",
        "| Condition | Runs | Public | Hidden | Overall | First patch applicable | Preflight rejects | Apply failures | Attempts | Tokens | Duration s | Cost USD |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
        _condition_row("enabled", on),
        _condition_row("disabled", off),
        "",
        "A model API without a fixed sampling seed may produce different initial patches",
        "between conditions. Interpret observed differences as engineering evidence only.",
    ]
    (experiment_dir / "REPORT.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def _condition_row(name: str, row: dict[str, Any]) -> str:
    apply_failures = (
        row["apply_failures_after_preflight"] + row["apply_failures_without_preflight"]
    )
    first_applicable = row["first_patch_applicable"]
    first_applicable_text = "N/A" if first_applicable is None else str(first_applicable)
    return (
        f"| {name} | {row['runs']} | {row['public_successes']} | "
        f"{row['hidden_successes']} | {row['overall_successes']} | "
        f"{first_applicable_text} | {row['preflight_rejections']} | "
        f"{apply_failures} | {row['repair_attempts']} | {row['tokens']} | "
        f"{row['duration_sec']} | {row['estimated_cost_usd']} |"
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--experiment", default=DEFAULT_EXPERIMENT)
    parser.add_argument("--model", default=None)
    parser.add_argument("--max-minutes", type=float, default=60.0)
    parser.add_argument("--max-cost-usd", type=float, default=0.25)
    parser.add_argument("--print-schedule", action="store_true")
    return parser.parse_args()


def initial_state(args: argparse.Namespace, schedule: list[dict[str, Any]]) -> dict[str, Any]:
    model = args.model or os.getenv("CODE_AGENT_MODEL") or os.getenv("OPENAI_MODEL")
    if not model:
        model = DEFAULT_MODEL
    freeze = freeze_record(list(TASK_IDS), args.experiment, model)
    freeze.update(
        {
            "git_commit": current_git_commit(),
            "comparison": "preflight_enabled_vs_disabled",
            "runs_per_task_per_condition": 2,
            "schedule": schedule,
            "schedule_sha256": schedule_sha256(schedule),
            "limits": {
                "max_active_minutes": args.max_minutes,
                "max_estimated_cost_usd": args.max_cost_usd,
                "enforcement": "checked before and after every completed slot",
            },
            "rerun_policy": {
                "model_or_semantic_failures": "counted; never rerun",
                "environment_or_api_failures": "retained; resume same slot only",
            },
        }
    )
    return {
        "schema_version": 1,
        "freeze": freeze,
        "status": "running",
        "runs": [],
        "environment_failures": [],
        "started_at": utc_now(),
        "updated_at": utc_now(),
    }


def main() -> int:
    args = parse_args()
    if args.max_minutes <= 0 or args.max_cost_usd <= 0:
        raise SystemExit("resource limits must be positive")
    schedule = build_schedule()
    if args.print_schedule:
        print(json.dumps({"sha256": schedule_sha256(schedule), "slots": schedule}, indent=2))
        return 0

    experiment_dir = RESULTS / args.experiment
    state_path = experiment_dir / "ablation_index.json"
    if state_path.exists():
        state = json.loads(state_path.read_text(encoding="utf-8"))
        if state["freeze"]["schedule_sha256"] != schedule_sha256(schedule):
            raise SystemExit("refusing to resume: frozen schedule changed")
        state["status"] = "running"
    else:
        if not tracked_worktree_clean():
            raise SystemExit("refusing to start: tracked worktree is dirty")
        experiment_dir.mkdir(parents=True, exist_ok=False)
        state = initial_state(args, schedule)
        atomic_json(experiment_dir / "freeze.json", state["freeze"])
        atomic_json(state_path, state)

    assert_frozen_inputs(state, args)
    frozen_limits = state["freeze"]["limits"]
    max_active_seconds = float(frozen_limits["max_active_minutes"]) * 60
    max_cost_usd = float(frozen_limits["max_estimated_cost_usd"])

    model = state["freeze"]["model"]
    completed_slots = {int(row["slot"]) for row in state["runs"]}
    for slot in schedule:
        if slot["slot"] in completed_slots:
            continue
        summary = totals(state["runs"])
        if summary["total_duration_sec"] >= max_active_seconds:
            state["status"] = "stopped_time_limit"
            break
        if summary["total_estimated_cost_usd"] >= max_cost_usd:
            state["status"] = "stopped_cost_limit"
            break

        print(
            f"SLOT {slot['slot']:02d}/28 task={slot['task_id']} "
            f"repeat={slot['repeat']} preflight={slot['preflight_enabled']}",
            flush=True,
        )
        started = time.perf_counter()
        try:
            row = run_one(
                task_id=slot["task_id"],
                experiment=args.experiment,
                model=model,
                prepare_only=False,
                preflight_enabled=slot["preflight_enabled"],
            )
        except Exception as exc:  # preserve checkpoint; classify manually before resume
            state["status"] = "stopped_orchestrator_error"
            state["orchestrator_error"] = {
                **slot,
                "type": type(exc).__name__,
                "message": str(exc),
                "recorded_at": utc_now(),
            }
            state["updated_at"] = utc_now()
            atomic_json(state_path, state)
            write_report(experiment_dir, state)
            raise

        row.update(slot)
        if is_environment_failure(row):
            state["environment_failures"].append(
                {**row, "recorded_at": utc_now(), "slot_duration_sec": time.perf_counter() - started}
            )
            state["status"] = "stopped_environment_failure"
            state["updated_at"] = utc_now()
            atomic_json(state_path, state)
            write_report(experiment_dir, state)
            print("Environment/API failure retained; fix it and resume the same slot.")
            return 3

        state["runs"].append(row)
        state["updated_at"] = utc_now()
        atomic_json(state_path, state)
        write_report(experiment_dir, state)

        summary = totals(state["runs"])
        if summary["total_duration_sec"] >= max_active_seconds:
            state["status"] = "stopped_time_limit"
            break
        if summary["total_estimated_cost_usd"] >= max_cost_usd:
            state["status"] = "stopped_cost_limit"
            break
    else:
        state["status"] = "complete"
        state["finished_at"] = utc_now()

    state["updated_at"] = utc_now()
    atomic_json(state_path, state)
    atomic_json(experiment_dir / "summary.json", totals(state["runs"]))
    write_report(experiment_dir, state)
    print(json.dumps(totals(state["runs"]), indent=2))
    return 0 if state["status"] == "complete" else 4


if __name__ == "__main__":
    sys.exit(main())
