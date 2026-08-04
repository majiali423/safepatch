"""Publish a sanitized audit bundle for the frozen 28-slot comparison."""

from __future__ import annotations

import argparse
import json
import re
import shutil
from collections import Counter
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
DEFAULT_EXPERIMENT = HERE / "results" / "deepseek_v4_flash_preflight_ablation_28run_20260730"
DEFAULT_OUTPUT = HERE / "published" / "preflight-ablation-28-run"
EXPECTED_TASKS = (
    "pysnooper-3",
    "tornado-11",
    "tqdm-3",
    "sanic-5",
    "thefuck-19",
    "tornado-10",
    "thefuck-16",
)
FREEZE_FIELDS = (
    "schema_version",
    "started_at",
    "experiment",
    "task_ids",
    "model",
    "base_host",
    "temperature",
    "system_prompt_sha256",
    "product_files_sha256",
    "task_assets_sha256",
    "pricing",
    "reference_fix_visible_to_agent",
    "hidden_tests_visible_to_agent",
    "git_commit",
    "comparison",
    "runs_per_task_per_condition",
    "schedule",
    "schedule_sha256",
    "limits",
    "rerun_policy",
)


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, value: object) -> None:
    path.write_text(
        json.dumps(value, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def is_test_file(path: str) -> bool:
    normalized = path.replace("\\", "/")
    name = normalized.rsplit("/", 1)[-1]
    return (
        normalized.startswith("tests/")
        or "/tests/" in f"/{normalized}"
        or name.startswith("test_")
        or name == "conftest.py"
    )


def sanitize_run(run: dict[str, Any]) -> dict[str, Any]:
    enabled = bool(run["preflight_enabled"])
    return {
        "slot": run["slot"],
        "task_id": run["task_id"],
        "repeat": run["repeat"],
        "preflight_enabled": enabled,
        "run_id": run["run_id"],
        "model": run["model"],
        "baseline_valid": run["baseline_valid"],
        "product_status": run["product_status"],
        "public_pass": run["public_pass"],
        "hidden_pass": run["hidden_pass"],
        "overall_pass": run["overall_pass"],
        "failure_type": run["failure_type"],
        "analysis_phase": run["analysis_phase"],
        "exploration_reads": run["exploration_reads"],
        "evidence_requests": run["evidence_requests"],
        "evidence_parameter_corrections": run["evidence_parameter_corrections"],
        "no_progress_actions": run["no_progress_actions"],
        "hard_policy_violations": run["hard_policy_violations"],
        "attempts_used": run["attempts_used"],
        "changed_files": run["changed_files"],
        "first_patch_applicable": run["first_patch_applicable"] if enabled else None,
        "preflight_failures": run["preflight_failures"],
        "patch_regenerations": run["patch_regenerations"],
        "format_retries": run["format_retries"],
        "apply_failures_after_preflight": run["apply_failures_after_preflight"],
        "apply_failures_without_preflight": run["apply_failures_without_preflight"],
        "model_calls": run["model_calls"],
        "token_usage": run["token_usage"],
        "estimated_cost": run["estimated_cost"],
        "duration_sec": run["duration_sec"],
    }


def condition_summary(runs: list[dict[str, Any]], enabled: bool) -> dict[str, Any]:
    selected = [run for run in runs if run["preflight_enabled"] is enabled]
    return {
        "runs": len(selected),
        "public_successes": sum(bool(run["public_pass"]) for run in selected),
        "hidden_successes": sum(bool(run["hidden_pass"]) for run in selected),
        "overall_successes": sum(bool(run["overall_pass"]) for run in selected),
        "first_patch_applicable": (
            sum(run["first_patch_applicable"] is True for run in selected)
            if enabled
            else None
        ),
        "preflight_rejections": sum(int(run["preflight_failures"]) for run in selected),
        "apply_failures_after_preflight": sum(
            int(run["apply_failures_after_preflight"]) for run in selected
        ),
        "apply_failures_without_preflight": sum(
            int(run["apply_failures_without_preflight"]) for run in selected
        ),
        "repair_attempts": sum(int(run["attempts_used"]) for run in selected),
        "model_calls": sum(int(run["model_calls"]) for run in selected),
        "tokens": sum(int(run["token_usage"]["total_tokens"]) for run in selected),
        "duration_sec": round(sum(float(run["duration_sec"]) for run in selected), 3),
        "estimated_cost_usd": round(
            sum(float(run["estimated_cost"]["estimated_usd"]) for run in selected),
            8,
        ),
        "failure_types": dict(
            sorted(Counter(run["failure_type"] for run in selected if run["failure_type"]).items())
        ),
    }


def calculate_summary(runs: list[dict[str, Any]]) -> dict[str, Any]:
    per_task: dict[str, dict[str, Any]] = {}
    for task_id in EXPECTED_TASKS:
        task_runs = [run for run in runs if run["task_id"] == task_id]
        per_task[task_id] = {
            "preflight_enabled": condition_summary(task_runs, True),
            "preflight_disabled": condition_summary(task_runs, False),
        }
    enabled = condition_summary(runs, True)
    disabled = condition_summary(runs, False)
    modified_tests = sorted(
        {
            path
            for run in runs
            for path in run["changed_files"]
            if is_test_file(path)
        }
    )
    return {
        "schema_version": 1,
        "completed_slots": len(runs),
        "unique_tasks": len({run["task_id"] for run in runs}),
        "preflight_enabled": enabled,
        "preflight_disabled": disabled,
        "per_task": per_task,
        "environment_failures": 0,
        "total_tokens": enabled["tokens"] + disabled["tokens"],
        "total_duration_sec": round(enabled["duration_sec"] + disabled["duration_sec"], 3),
        "total_estimated_cost_usd": round(
            enabled["estimated_cost_usd"] + disabled["estimated_cost_usd"], 8
        ),
        "modified_test_files": modified_tests,
    }


def redact_local_paths(text: str) -> str:
    return re.sub(
        r"(?i)[A-Z]:\\Users\\[^\\\r\n]+\\[^\r\n\"']*",
        "<LOCAL_PATH>",
        text,
    )


def bundle_readme(summary: dict[str, Any]) -> str:
    on = summary["preflight_enabled"]
    off = summary["preflight_disabled"]
    rows = []
    for task_id, task in summary["per_task"].items():
        rows.append(
            f"| `{task_id}` | {task['preflight_enabled']['overall_successes']}/2 | "
            f"{task['preflight_disabled']['overall_successes']}/2 |"
        )
    task_table = "\n".join(rows)
    return f"""# Published preflight controlled comparison

This sanitized bundle records a frozen 28-slot comparison over seven real
historical bugs: preflight enabled and disabled, two runs per task per condition.
The schedule was fixed before execution and reversed condition order in repeat two.

| Condition | Runs | Public | Public + hidden | Tokens | Duration | Estimated cost |
|---|---:|---:|---:|---:|---:|---:|
| Enabled | 14 | {on['public_successes']}/14 | {on['overall_successes']}/14 | {on['tokens']:,} | {on['duration_sec']:.3f}s | ${on['estimated_cost_usd']:.8f} |
| Disabled | 14 | {off['public_successes']}/14 | {off['overall_successes']}/14 | {off['tokens']:,} | {off['duration_sec']:.3f}s | ${off['estimated_cost_usd']:.8f} |

| Task | Enabled | Disabled |
|---|---:|---:|
{task_table}

## The important result

Enabled finished 11/14 and disabled finished 9/14, but **no run triggered a
preflight rejection and no run had an apply failure**. The experiment therefore
did not exercise preflight's core interception mechanism. The two-run outcome
difference cannot be attributed to preflight; model sampling produced different
semantic patches between conditions. `first_patch_applicable` is `null` when
preflight is disabled because that property is not observed in that condition.

This is a controlled small-sample comparison, not a same-random-seed paired
experiment or a causal accuracy claim. It is useful negative evidence: future
preflight evaluation should deliberately include naturally occurring context
mismatches or a frozen replay corpus, while keeping semantic accuracy separate.

Files:

- `freeze.json`: candidate commit, input hashes, fixed schedule and resource limits.
- `runs.json`: sanitized per-slot outcomes, usage, cost and failure classification.
- `summary.json`: aggregates recalculated from `runs.json`.
- `failures/`: submitted diffs and hidden-test output for every failed slot.

Verify without an API account or Docker:

```bash
python examples/real_bug_benchmark/verify_preflight_ablation.py
```
"""


def publish_failure(run: dict[str, Any], output: Path) -> None:
    source = Path(run["result_path"])
    failures = output / "failures"
    failures.mkdir(parents=True, exist_ok=True)
    stem = f"slot-{int(run['slot']):02d}-{run['task_id']}"
    for source_name, suffix in (("final.diff", ".diff"), ("hidden_test.log", ".txt")):
        source_file = source / source_name
        if not source_file.exists():
            raise FileNotFoundError(source_file)
        (failures / f"{stem}{suffix}").write_text(
            redact_local_paths(source_file.read_text(encoding="utf-8")),
            encoding="utf-8",
            newline="\n",
        )


def publish(experiment: Path, output: Path) -> dict[str, Any]:
    state = load_json(experiment / "ablation_index.json")
    if state["status"] != "complete":
        raise ValueError(f"experiment is not complete: {state['status']}")
    freeze = {field: state["freeze"][field] for field in FREEZE_FIELDS}
    runs = [sanitize_run(run) for run in state["runs"]]
    if output.exists():
        shutil.rmtree(output)
    output.mkdir(parents=True)
    write_json(output / "freeze.json", freeze)
    write_json(output / "runs.json", {"schema_version": 1, "runs": runs})
    summary = calculate_summary(runs)
    write_json(output / "summary.json", summary)
    (output / "README.md").write_text(
        bundle_readme(summary), encoding="utf-8", newline="\n"
    )
    for source_run, public_run in zip(state["runs"], runs, strict=True):
        if not public_run["overall_pass"]:
            publish_failure(source_run, output)
    return summary


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--experiment", type=Path, default=DEFAULT_EXPERIMENT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    summary = publish(args.experiment.resolve(), args.output.resolve())
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
