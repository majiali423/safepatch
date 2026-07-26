"""Hidden-test evaluation — runs only after the product Agent finishes.

Never feeds results back into the Agent loop, product SessionStatus,
product trace.jsonl, Repo Map, or model prompts.
"""

from __future__ import annotations

import ast
import hashlib
import json
import re
import shutil
from pathlib import Path
from typing import Any, Callable

from code_agent.eval.status import EvalStatus
from code_agent.runtime.docker_pytest import DockerPytestRunner
from code_agent.state import TestResult
from code_agent.tracing.recorder import TraceRecorder

PytestRunner = Callable[..., TestResult]


def discover_hidden_dir(task_dir: Path) -> Path | None:
    """Sibling directory: <task_id>.hidden/ outside the public task repo."""
    candidate = task_dir.parent / f"{task_dir.name}.hidden"
    if candidate.is_dir() and any(candidate.glob("test_*.py")):
        return candidate
    return None


def evaluate_after_product(
    *,
    product_summary: dict[str, Any],
    working_copy: Path,
    task_dir: Path,
    results_dir: Path,
    runner: DockerPytestRunner | PytestRunner | None = None,
) -> dict[str, Any]:
    """Build metrics + eval_trace after a product run. Does not mutate product status."""
    results_dir.mkdir(parents=True, exist_ok=True)
    eval_trace = TraceRecorder(results_dir / "eval_trace.jsonl")
    product_status = str(product_summary.get("status", "UNKNOWN"))
    public_pass = product_status == "SUCCEEDED"
    hidden_dir = discover_hidden_dir(task_dir)

    base = {
        "product_status": product_status,
        "public_pass": public_pass,
        "attempts_used": product_summary.get("attempts_used"),
        "format_retries_used": product_summary.get("total_format_retries_used"),
        "changed_files": list(product_summary.get("changed_files") or []),
        "no_hidden_tests_configured": hidden_dir is None,
    }

    if not public_pass:
        metrics = {
            **base,
            "hidden_pass": None,  # hidden suite not executed
            "eval_status": EvalStatus.PUBLIC_TESTS_FAILED.value,
            "hidden_test_count": 0,
            "hidden_exit_code": None,
        }
        if hidden_dir is None:
            metrics["note"] = "No hidden tests configured"
        eval_trace.emit(
            "eval_finished",
            eval_status=metrics["eval_status"],
            reason="product_not_succeeded",
            skipped_hidden=True,
        )
        _write_metrics(results_dir, metrics)
        return metrics

    if hidden_dir is None:
        metrics = {
            **base,
            "hidden_pass": None,
            "eval_status": EvalStatus.SUCCEEDED.value,
            "hidden_test_count": 0,
            "hidden_exit_code": None,
            "note": "No hidden tests configured",
        }
        eval_trace.emit(
            "eval_finished",
            eval_status=metrics["eval_status"],
            reason="no_hidden_tests",
            note="No hidden tests configured",
        )
        _write_metrics(results_dir, metrics)
        return metrics

    hidden_result = run_hidden_tests(
        working_copy=working_copy,
        hidden_tests_dir=hidden_dir,
        results_dir=results_dir,
        runner=runner or DockerPytestRunner(),
        eval_trace=eval_trace,
    )
    metrics = {**base, **hidden_result}
    _write_metrics(results_dir, metrics)
    return metrics


def run_hidden_tests(
    *,
    working_copy: Path,
    hidden_tests_dir: Path,
    results_dir: Path,
    runner: DockerPytestRunner | PytestRunner,
    eval_trace: TraceRecorder | None = None,
) -> dict[str, Any]:
    """Copy working_copy → eval_temp_copy, inject hidden tests, Docker pytest, cleanup."""
    results_dir.mkdir(parents=True, exist_ok=True)
    if eval_trace is None:
        eval_trace = TraceRecorder(results_dir / "eval_trace.jsonl")

    eval_trace.emit(
        "hidden_eval_started",
        working_copy=str(working_copy),
        hidden_tests_dir=str(hidden_tests_dir),
    )

    eval_temp = results_dir / "eval_temp_copy"
    if eval_temp.exists():
        shutil.rmtree(eval_temp)

    shutil.copytree(
        working_copy,
        eval_temp,
        ignore=shutil.ignore_patterns(
            ".git",
            ".code_agent_sessions",
            "__pycache__",
            ".pytest_cache",
            "*.pyc",
        ),
    )
    eval_trace.emit(
        "eval_temp_copy_created",
        path=str(eval_temp),
        # Confirm product working_copy was not the injection target.
        product_working_copy=str(working_copy.resolve()),
    )

    injected = _inject_hidden_tests(eval_temp, hidden_tests_dir)
    eval_trace.emit(
        "hidden_tests_injected",
        target="tests_hidden",
        file_count=len(injected),
        files=[{"name": f["name"], "sha256": f["sha256"]} for f in injected],
    )

    log_path = results_dir / "hidden.log"
    image = None
    # Duck-typed: product DockerPytestRunner or test doubles with the same surface.
    if hasattr(runner, "run_pytest") and hasattr(runner, "make_run_config"):
        resolve = getattr(runner, "_resolve_image", None)
        resolved = resolve() if callable(resolve) else None
        image_name = resolved or getattr(runner, "image", "code-agent-pytest:local")
        base_cfg = runner.make_run_config(image_name)
        hidden_cfg = base_cfg.for_hidden_tests()
        image = hidden_cfg.image
        eval_trace.emit(
            "hidden_pytest_started",
            docker_config=hidden_cfg.to_dict(),
            pytest_target="tests_hidden",
        )
        result = runner.run_pytest(eval_temp, log_path=log_path, config=hidden_cfg)
    elif callable(runner):
        eval_trace.emit(
            "hidden_pytest_started",
            docker_config=None,
            pytest_target="tests_hidden",
            note="custom_runner",
        )
        result = runner(eval_temp, log_path=log_path)
    else:
        raise TypeError("runner must provide run_pytest/make_run_config or be callable")

    eval_trace.emit(
        "hidden_pytest_finished",
        exit_code=result.exit_code,
        error_kind=result.error_kind,
        failed_tests=result.failed_tests,
        traceback_summary=(result.traceback_summary or "")[:2000],
        log_path=str(log_path),
        duration_sec=result.duration_sec,
        image=image,
    )

    if eval_temp.exists():
        shutil.rmtree(eval_temp, ignore_errors=True)
    eval_trace.emit(
        "eval_temp_copy_removed",
        path=str(eval_temp),
        still_exists=eval_temp.exists(),
    )

    test_count = sum(item["test_count"] for item in injected)
    metrics = _status_from_hidden_result(result, hidden_test_count=test_count)
    eval_trace.emit(
        "eval_finished",
        eval_status=metrics["eval_status"],
        hidden_pass=metrics["hidden_pass"],
        hidden_exit_code=metrics["hidden_exit_code"],
    )
    return metrics


def _inject_hidden_tests(eval_temp: Path, hidden_tests_dir: Path) -> list[dict[str, Any]]:
    dest = eval_temp / "tests_hidden"
    dest.mkdir(parents=True, exist_ok=True)
    # Keep public tests/ untouched; hidden suite is isolated under tests_hidden/.
    injected: list[dict[str, Any]] = []
    for src in sorted(hidden_tests_dir.glob("test_*.py")):
        text = src.read_text(encoding="utf-8")
        digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
        target = dest / src.name
        target.write_text(text, encoding="utf-8", newline="\n")
        injected.append(
            {
                "name": src.name,
                "sha256": digest,
                "test_count": _count_tests_in_source(text),
            }
        )
    return injected


def _count_tests_in_source(text: str) -> int:
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return len(re.findall(r"^\s*def\s+test_", text, flags=re.M))
    n = 0
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name.startswith("test_"):
            n += 1
        elif isinstance(node, ast.ClassDef):
            for item in node.body:
                if isinstance(item, ast.FunctionDef) and item.name.startswith("test_"):
                    n += 1
    return n


def _status_from_hidden_result(
    result: TestResult, *, hidden_test_count: int
) -> dict[str, Any]:
    if result.error_kind == "timeout":
        return {
            "hidden_pass": False,
            "eval_status": EvalStatus.HIDDEN_TEST_TIMEOUT.value,
            "hidden_test_count": hidden_test_count,
            "hidden_exit_code": result.exit_code,
            "hidden_failed_tests": result.failed_tests,
        }
    if result.error_kind == "environment" or result.environment_error:
        return {
            "hidden_pass": False,
            "eval_status": EvalStatus.HIDDEN_TEST_ENVIRONMENT_ERROR.value,
            "hidden_test_count": hidden_test_count,
            "hidden_exit_code": result.exit_code,
            "hidden_failed_tests": result.failed_tests,
        }
    if result.exit_code == 0:
        return {
            "hidden_pass": True,
            "eval_status": EvalStatus.SUCCEEDED.value,
            "hidden_test_count": hidden_test_count,
            "hidden_exit_code": 0,
            "hidden_failed_tests": [],
        }
    return {
        "hidden_pass": False,
        "eval_status": EvalStatus.HIDDEN_TESTS_FAILED.value,
        "hidden_test_count": hidden_test_count,
        "hidden_exit_code": result.exit_code,
        "hidden_failed_tests": result.failed_tests,
    }


def _write_metrics(results_dir: Path, metrics: dict[str, Any]) -> None:
    (results_dir / "metrics.json").write_text(
        json.dumps(metrics, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
