"""Export a sanitized, compact audit bundle from frozen local model results."""

from __future__ import annotations

import argparse
import json
import re
import shutil
from collections import Counter
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
DEFAULT_EXPERIMENTS = (
    "deepseek_v4_flash_7task_synthesis_20260730",
    "deepseek_v4_flash_7task_synthesis_repeats_20260730",
)
DEFAULT_OUTPUT = HERE / "published" / "synthesis-21-run"
FROZEN_FIELDS = (
    "schema_version",
    "model",
    "base_host",
    "temperature",
    "system_prompt_sha256",
    "product_files_sha256",
    "task_assets_sha256",
    "pricing",
    "reference_fix_visible_to_agent",
    "hidden_tests_visible_to_agent",
    "preflight_enabled",
)


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, value: object) -> None:
    path.write_text(
        json.dumps(value, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def _public_freeze(freeze: dict[str, Any]) -> dict[str, Any]:
    return {field: freeze[field] for field in FROZEN_FIELDS}


def _sanitize_run(run: dict[str, Any], *, repeat: int) -> dict[str, Any]:
    return {
        "task_id": run["task_id"],
        "repeat": repeat,
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
        "evidence_parameter_corrections": run[
            "evidence_parameter_corrections"
        ],
        "no_progress_actions": run["no_progress_actions"],
        "hard_policy_violations": run["hard_policy_violations"],
        "attempts_used": run["attempts_used"],
        "changed_files": run["changed_files"],
        "first_patch_applicable": run["first_patch_applicable"],
        "preflight_failures": run["preflight_failures"],
        "patch_regenerations": run["patch_regenerations"],
        "format_retries": run["format_retries"],
        "apply_failures_after_preflight": run[
            "apply_failures_after_preflight"
        ],
        "apply_failures_without_preflight": run[
            "apply_failures_without_preflight"
        ],
        "model_calls": run["model_calls"],
        "token_usage": run["token_usage"],
        "estimated_cost": run["estimated_cost"],
        "duration_sec": run["duration_sec"],
    }


def _is_test_file(path: str) -> bool:
    normalized = path.replace("\\", "/")
    name = normalized.rsplit("/", 1)[-1]
    return (
        normalized.startswith("tests/")
        or "/tests/" in f"/{normalized}"
        or name.startswith("test_")
        or name == "conftest.py"
    )


def calculate_summary(runs: list[dict[str, Any]]) -> dict[str, Any]:
    tasks = Counter(run["task_id"] for run in runs)
    costs = [float(run["estimated_cost"]["estimated_usd"]) for run in runs]
    test_files = sorted(
        {
            path
            for run in runs
            for path in run["changed_files"]
            if _is_test_file(path)
        }
    )
    return {
        "schema_version": 1,
        "unique_tasks": len(tasks),
        "runs": len(runs),
        "runs_per_task": dict(sorted(tasks.items())),
        "public_successes": sum(bool(run["public_pass"]) for run in runs),
        "hidden_successes": sum(bool(run["hidden_pass"]) for run in runs),
        "overall_successes": sum(bool(run["overall_pass"]) for run in runs),
        "overall_success_rate": round(
            sum(bool(run["overall_pass"]) for run in runs) / len(runs),
            6,
        ),
        "evidence_requests": sum(int(run["evidence_requests"]) for run in runs),
        "runs_using_evidence": sum(
            int(run["evidence_requests"] > 0) for run in runs
        ),
        "evidence_parameter_corrections": sum(
            int(run["evidence_parameter_corrections"]) for run in runs
        ),
        "no_progress_actions": sum(
            int(run["no_progress_actions"]) for run in runs
        ),
        "hard_policy_violations": sum(
            int(run["hard_policy_violations"]) for run in runs
        ),
        "patch_not_applicable": sum(
            run["failure_type"] == "patch_not_applicable" for run in runs
        ),
        "preflight_failures": sum(int(run["preflight_failures"]) for run in runs),
        "apply_failures_after_preflight": sum(
            int(run["apply_failures_after_preflight"]) for run in runs
        ),
        "format_retries": sum(int(run["format_retries"]) for run in runs),
        "model_calls": sum(int(run["model_calls"]) for run in runs),
        "total_tokens": sum(int(run["token_usage"]["total_tokens"]) for run in runs),
        "duration_sec": round(sum(float(run["duration_sec"]) for run in runs), 3),
        "estimated_cost_usd": round(sum(costs), 8),
        "modified_test_files": test_files,
    }


def _redact_local_paths(text: str) -> str:
    text = re.sub(
        r"(?i)[A-Z]:\\Users\\[^\\\r\n]+\\[^\r\n\"']*",
        "<LOCAL_PATH>",
        text,
    )
    return text.replace(str(HERE.parent.parent), "<REPOSITORY_ROOT>")


def _bundle_readme(summary: dict[str, Any]) -> str:
    return f"""# Published synthesis benchmark evidence

This directory is the sanitized, committed audit bundle for the frozen
seven-task, three-repeat SafePatch experiment. It contains
{summary['runs']} independent model runs over {summary['unique_tasks']} real
historical bugs. Public tests passed {summary['public_successes']}/{summary['runs']};
public-plus-hidden scoring passed {summary['overall_successes']}/{summary['runs']}
({summary['overall_success_rate'] * 100:.1f}%).

The bundle intentionally excludes local session paths, provider credentials,
complete model conversations, working copies, and large container logs.

Files:

- `freeze.json`: model settings and hashes binding the two source experiments.
- `runs.json`: sanitized per-run outcomes, usage, cost, phase and failure metrics.
- `summary.json`: totals recomputed from `runs.json`.
- `failures/`: one Tornado lifecycle false positive and one tqdm precedence false
  positive, each with the submitted diff and hidden-test output.

Verify the committed evidence without a model account or Docker:

```bash
python examples/real_bug_benchmark/verify_published_results.py
```

The verifier checks task/repeat counts, recomputes every aggregate, rejects
modified test files and post-preflight apply failures, checks frozen visibility
settings, and scans the bundle for local user paths or credential metadata.

These are small-sample engineering results from one model and are not a claim of
general production accuracy. Re-running the model may produce different patches.
See `../../SYNTHESIS_MODEL_EVAL_REPORT.md` for interpretation and limitations.
"""


def _publish_failure(
    run: dict[str, Any],
    *,
    stem: str,
    output: Path,
) -> None:
    source = Path(run["result_path"])
    failures = output / "failures"
    failures.mkdir(parents=True, exist_ok=True)
    for source_name, suffix in (("final.diff", ".diff"), ("hidden_test.log", ".txt")):
        source_file = source / source_name
        if not source_file.exists():
            raise FileNotFoundError(source_file)
        text = _redact_local_paths(source_file.read_text(encoding="utf-8"))
        (failures / f"{stem}{suffix}").write_text(
            text,
            encoding="utf-8",
            newline="\n",
        )


def publish(experiments: list[Path], output: Path) -> dict[str, Any]:
    source_freezes: list[dict[str, Any]] = []
    source_runs: list[dict[str, Any]] = []
    experiment_meta: list[dict[str, Any]] = []
    for experiment in experiments:
        index = _load_json(experiment / "runs_index.json")
        freeze = _public_freeze(index["freeze"])
        source_freezes.append(freeze)
        source_runs.extend(index["runs"])
        experiment_meta.append(
            {"name": experiment.name, "runs": len(index["runs"])}
        )

    first_freeze = source_freezes[0]
    if any(freeze != first_freeze for freeze in source_freezes[1:]):
        raise ValueError("Frozen experiment configuration hashes or settings differ")

    repeat_counter: Counter[str] = Counter()
    public_runs: list[dict[str, Any]] = []
    for run in source_runs:
        repeat_counter[run["task_id"]] += 1
        public_runs.append(
            _sanitize_run(run, repeat=repeat_counter[run["task_id"]])
        )

    if output.exists():
        shutil.rmtree(output)
    output.mkdir(parents=True)
    _write_json(
        output / "freeze.json",
        {
            **first_freeze,
            "experiments": experiment_meta,
        },
    )
    _write_json(output / "runs.json", {"schema_version": 1, "runs": public_runs})
    summary = calculate_summary(public_runs)
    _write_json(output / "summary.json", summary)
    (output / "README.md").write_text(
        _bundle_readme(summary),
        encoding="utf-8",
        newline="\n",
    )

    tornado_failure = next(
        run
        for run in source_runs
        if run["task_id"] == "tornado-10"
        and run["public_pass"]
        and run["hidden_pass"] is False
    )
    tqdm_failure = next(
        run
        for run in source_runs
        if run["task_id"] == "tqdm-3"
        and run["public_pass"]
        and run["hidden_pass"] is False
    )
    _publish_failure(
        tornado_failure,
        stem="tornado-10-hidden-lifecycle",
        output=output,
    )
    _publish_failure(
        tqdm_failure,
        stem="tqdm-3-hidden-total-precedence",
        output=output,
    )
    return summary


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--experiment",
        action="append",
        dest="experiments",
        help="Local result directory; repeat for each frozen experiment",
    )
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    experiments = (
        [Path(path).resolve() for path in args.experiments]
        if args.experiments
        else [HERE / "results" / name for name in DEFAULT_EXPERIMENTS]
    )
    summary = publish(experiments, args.output.resolve())
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
