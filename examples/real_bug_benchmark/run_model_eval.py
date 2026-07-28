"""Run model repairs against accepted real-bug benchmark environments."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[2]
BENCHMARK = Path(__file__).resolve().parent
TASKS = BENCHMARK / "tasks"
RESULTS = BENCHMARK / "results"
PRICING_SOURCE = "https://api-docs.deepseek.com/quick_start/pricing/"
MODEL_PRICES = {
    "deepseek-v4-flash": {
        "input_cache_hit_usd_per_million": 0.0028,
        "input_cache_miss_usd_per_million": 0.14,
        "output_usd_per_million": 0.28,
    },
    "deepseek-v4-pro": {
        "input_cache_hit_usd_per_million": 0.003625,
        "input_cache_miss_usd_per_million": 0.435,
        "output_usd_per_million": 0.87,
    },
}

sys.path.insert(0, str(ROOT))

from code_agent.controller import TaskController  # noqa: E402
from code_agent.envfile import load_dotenv  # noqa: E402
from code_agent.llm import SYSTEM_PROMPT, LLMClient  # noqa: E402
from code_agent.runtime.docker_config import DockerRunConfig  # noqa: E402
from code_agent.runtime.docker_pytest import (  # noqa: E402
    DockerPytestRunner,
    _sanitized_subprocess_env,
)

load_dotenv(ROOT / ".env")


class TaskImageRunner(DockerPytestRunner):
    """Use exactly one accepted task image and its frozen test command."""

    def __init__(self, *, image: str, test_command: list[str]) -> None:
        super().__init__(image=image)
        self.test_command = tuple(test_command)

    def _resolve_image(self) -> str | None:
        result = subprocess.run(
            ["docker", "image", "inspect", self.image],
            capture_output=True,
            text=True,
            check=False,
            env=_sanitized_subprocess_env(),
        )
        return self.image if result.returncode == 0 else None

    def make_run_config(self, image: str) -> DockerRunConfig:
        return DockerRunConfig(image=image, pytest_argv=self.test_command)


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def run_id() -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"{stamp}_{uuid.uuid4().hex[:8]}"


def sha256_files(paths: list[Path]) -> str:
    digest = hashlib.sha256()
    for path in sorted(paths):
        digest.update(path.relative_to(ROOT).as_posix().encode())
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def pricing_for_model(model: str) -> dict[str, float]:
    aliases = {
        "deepseek-chat": "deepseek-v4-flash",
        "deepseek-reasoner": "deepseek-v4-flash",
    }
    canonical = aliases.get(model, model)
    try:
        return MODEL_PRICES[canonical]
    except KeyError as error:
        raise ValueError(f"no pricing snapshot configured for model: {model}") from error


def freeze_record(task_ids: list[str], experiment: str, model: str) -> dict[str, Any]:
    product_files = list((ROOT / "code_agent").rglob("*.py"))
    task_files = [
        path for task_id in task_ids for path in (TASKS / task_id).rglob("*") if path.is_file()
    ]
    base_url = os.getenv("OPENAI_BASE_URL") or os.getenv("CODE_AGENT_BASE_URL") or ""
    return {
        "schema_version": 1,
        "started_at": utc_now(),
        "experiment": experiment,
        "task_ids": task_ids,
        "model": model,
        "base_host": urlparse(base_url).hostname,
        "api_key_present": bool(os.getenv("OPENAI_API_KEY") or os.getenv("CODE_AGENT_API_KEY")),
        "temperature": 0.1,
        "system_prompt_sha256": hashlib.sha256(SYSTEM_PROMPT.encode()).hexdigest(),
        "product_files_sha256": sha256_files(product_files),
        "task_assets_sha256": sha256_files(task_files),
        "pricing": {
            "effective_date": "2026-07-28",
            "model": model,
            "source": PRICING_SOURCE,
            **(pricing_for_model(model) if model != "prepare-only" else {}),
        },
        "reference_fix_visible_to_agent": False,
    }


def ensure_mirror(task: dict[str, Any]) -> Path:
    cache = RESULTS / "_cache" / f"{task['id']}.git"
    if cache.exists():
        return cache
    cache.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["git", "clone", "--mirror", "--quiet", task["repository"], str(cache)],
        check=True,
    )
    return cache


def prepare_buggy_source(task_dir: Path, destination: Path) -> None:
    task = load_json(task_dir / "task.json")
    mirror = ensure_mirror(task)
    subprocess.run(["git", "clone", "--quiet", str(mirror), str(destination)], check=True)
    subprocess.run(
        ["git", "checkout", "--quiet", task["buggy_commit"]],
        cwd=destination,
        check=True,
    )
    subprocess.run(
        [
            "git",
            "apply",
            "--whitespace=nowarn",
            str((task_dir / task["public_test_patch"]).resolve()),
        ],
        cwd=destination,
        check=True,
    )


def estimate_cost(usage: dict[str, Any], *, model: str = "deepseek-v4-flash") -> dict[str, Any]:
    prompt = usage.get("prompt_tokens")
    completion = usage.get("completion_tokens")
    cached = usage.get("cached_tokens")
    if prompt is None or completion is None:
        return {"available": False, "estimated_usd": None}
    cache_hit = min(int(cached or 0), int(prompt))
    cache_miss = int(prompt) - cache_hit
    prices = pricing_for_model(model)
    cost = (
        cache_hit * prices["input_cache_hit_usd_per_million"]
        + cache_miss * prices["input_cache_miss_usd_per_million"]
        + int(completion) * prices["output_usd_per_million"]
    ) / 1_000_000
    return {
        "available": True,
        "estimated_usd": round(cost, 8),
        "input_cache_hit_tokens": cache_hit,
        "input_cache_miss_tokens": cache_miss,
        "output_tokens": int(completion),
    }


def run_one(
    *,
    task_id: str,
    experiment: str,
    model: str,
    prepare_only: bool,
    preflight_enabled: bool,
) -> dict[str, Any]:
    task_dir = TASKS / task_id
    task = load_json(task_dir / "task.json")
    acceptance = load_json(task_dir / "acceptance.json")
    current_run = run_id()
    output = RESULTS / experiment / task_id / current_run
    output.mkdir(parents=True, exist_ok=False)
    started = time.perf_counter()

    with tempfile.TemporaryDirectory(prefix=f"safepatch-eval-{task_id}-") as raw:
        source = Path(raw) / "source"
        prepare_buggy_source(task_dir, source)
        runner = TaskImageRunner(
            image=acceptance["image"]["tag"],
            test_command=task["test_command"],
        )
        ok, reason = runner.preflight()
        if not ok:
            raise RuntimeError(f"task image preflight failed: {reason}")

        baseline = runner.run_pytest(source, log_path=output / "baseline_precheck.log")
        baseline_text = baseline.stdout + baseline.stderr
        baseline_valid = (
            baseline.exit_code != 0
            and baseline.environment_error is None
            and task["failure_signature"] in baseline_text
        )
        if not baseline_valid or prepare_only:
            result = {
                "task_id": task_id,
                "run_id": current_run,
                "prepare_only": prepare_only,
                "baseline_valid": baseline_valid,
                "baseline_exit_code": baseline.exit_code,
                "failure_signature_found": task["failure_signature"] in baseline_text,
                "environment_error": baseline.environment_error,
                "duration_sec": round(time.perf_counter() - started, 3),
                "result_path": str(output),
            }
            (output / "metrics.json").write_text(
                json.dumps(result, indent=2) + "\n", encoding="utf-8"
            )
            return result

        session_base = RESULTS / "_sessions" / experiment / task_id / current_run
        llm = LLMClient(model=model)
        controller = TaskController(
            llm=llm,
            runner=runner,
            approve=lambda _binding, _attempt: True,
            say=lambda message: print(f"[{task_id}] {message}", flush=True),
            session_base=session_base,
            preflight_enabled=preflight_enabled,
        )
        session = controller.run(source, task["prompt"])

    summary = session.to_summary()
    usage = summary["observability"]["model"]["usage"]
    cost = estimate_cost(usage, model=model)
    result = {
        "task_id": task_id,
        "run_id": current_run,
        "experiment": experiment,
        "model": model,
        "prepare_only": False,
        "baseline_valid": True,
        "product_status": summary["status"],
        "public_pass": summary["final_tests_passed"],
        "failure_type": None if summary["final_tests_passed"] else summary["stop_reason"],
        "attempts_used": summary["attempts_used"],
        "changed_files": summary["changed_files"],
        "first_patch_applicable": summary["first_patch_applicable"],
        "preflight_failures": summary["patch_preflight_failures"],
        "patch_regenerations": summary["total_patch_regeneration_retries"],
        "format_retries": summary["total_format_retries_used"],
        "apply_failures_after_preflight": summary["patch_apply_failures_after_preflight"],
        "apply_failures_without_preflight": summary["patch_apply_failures_without_preflight"],
        "model_calls": summary["observability"]["model"]["calls"],
        "token_usage": usage,
        "estimated_cost": cost,
        "duration_sec": round(time.perf_counter() - started, 3),
        "session_path": str(session.session_dir),
        "result_path": str(output),
    }
    (output / "summary.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    (output / "metrics.json").write_text(
        json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    for name in ("final.diff", "trace.jsonl", "baseline.log"):
        source_artifact = session.artifacts_dir / name
        if source_artifact.exists():
            shutil.copy2(source_artifact, output / name)
    return result


def write_report(experiment: str, freeze: dict[str, Any], rows: list[dict[str, Any]]) -> Path:
    report = RESULTS / experiment / "REPORT.md"
    completed = [row for row in rows if not row.get("prepare_only")]
    total_cost = sum(
        float((row.get("estimated_cost") or {}).get("estimated_usd") or 0) for row in completed
    )
    total_tokens = sum(
        int((row.get("token_usage") or {}).get("total_tokens") or 0) for row in completed
    )
    total_calls = sum(int(row.get("model_calls") or 0) for row in completed)
    total_duration = sum(float(row.get("duration_sec") or 0) for row in completed)
    total_preflight_failures = sum(int(row.get("preflight_failures") or 0) for row in completed)
    total_apply_failures = sum(
        int(row.get("apply_failures_without_preflight") or 0) for row in completed
    )
    total_format_retries = sum(int(row.get("format_retries") or 0) for row in completed)
    lines = [
        f"# Real-bug model evaluation: {experiment}",
        "",
        f"Started: `{freeze['started_at']}`",
        f"Model: `{freeze['model']}`",
        f"Pricing source: {freeze['pricing']['source']}",
        "",
        "| Task | Status | Public | Attempts | Calls | Tokens | Cost USD | Duration s |",
        "|---|---|---:|---:|---:|---:|---:|---:|",
    ]
    for row in rows:
        usage = row.get("token_usage") or {}
        cost = row.get("estimated_cost") or {}
        lines.append(
            "| {task} | {status} | {public} | {attempts} | {calls} | {tokens} | {cost} | {duration} |".format(
                task=row["task_id"],
                status=row.get("product_status", "prepare-only"),
                public=row.get("public_pass", "-"),
                attempts=row.get("attempts_used", "-"),
                calls=row.get("model_calls", "-"),
                tokens=usage.get("total_tokens", "-"),
                cost=cost.get("estimated_usd", "-"),
                duration=row.get("duration_sec", "-"),
            )
        )
    lines += [
        "",
        f"Completed model runs: **{len(completed)}**",
        f"Public successes: **{sum(1 for row in completed if row.get('public_pass'))}/{len(completed)}**",
        f"Provider-reported tokens: **{total_tokens}** across **{total_calls}** model calls",
        f"Wall-clock duration: **{total_duration:.3f} s**",
        f"Format retries: **{total_format_retries}**",
        f"Preflight rejections: **{total_preflight_failures}**",
        f"Apply/rollback failures with preflight disabled: **{total_apply_failures}**",
        f"Estimated API cost: **${total_cost:.6f}**",
        "",
        "Cost is estimated from provider-reported tokens and the pricing snapshot in `freeze.json`; it is not a billing invoice.",
    ]
    report.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return report


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--task", action="append", dest="tasks")
    parser.add_argument("--runs", type=int, default=1)
    parser.add_argument("--experiment", default="canary")
    parser.add_argument("--model")
    parser.add_argument("--prepare-only", action="store_true")
    parser.add_argument("--disable-preflight", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    task_ids = args.tasks or ["pysnooper-3"]
    if args.runs < 1:
        raise SystemExit("--runs must be positive")
    missing = [task_id for task_id in task_ids if not (TASKS / task_id / "task.json").exists()]
    if missing:
        raise SystemExit(f"unknown task ids: {missing}")
    model = args.model or os.getenv("CODE_AGENT_MODEL") or os.getenv("OPENAI_MODEL") or ""
    if not model and not args.prepare_only:
        raise SystemExit("model is not configured")

    experiment_dir = RESULTS / args.experiment
    experiment_dir.mkdir(parents=True, exist_ok=True)
    freeze = freeze_record(task_ids, args.experiment, model or "prepare-only")
    freeze["preflight_enabled"] = not args.disable_preflight
    (experiment_dir / "freeze.json").write_text(
        json.dumps(freeze, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    rows = [
        run_one(
            task_id=task_id,
            experiment=args.experiment,
            model=model,
            prepare_only=args.prepare_only,
            preflight_enabled=not args.disable_preflight,
        )
        for _ in range(args.runs)
        for task_id in task_ids
    ]
    index = {"freeze": freeze, "runs": rows, "finished_at": utc_now()}
    (experiment_dir / "runs_index.json").write_text(
        json.dumps(index, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    report = write_report(args.experiment, freeze, rows)
    print(json.dumps(rows, indent=2, ensure_ascii=False))
    print(f"Report: {report}")
    return 0 if all(row.get("baseline_valid") for row in rows) else 2


if __name__ == "__main__":
    sys.exit(main())
