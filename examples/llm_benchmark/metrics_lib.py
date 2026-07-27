"""Benchmark metrics helpers (eval-only; does not modify product code)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable


def file_set(files: Iterable[str] | None) -> set[str]:
    return {f.replace("\\", "/") for f in (files or [])}


def compute_change_metrics(
    *,
    required_files: list[str],
    allowed_files: list[str],
    forbidden_files: list[str],
    changed_files: list[str],
) -> dict[str, Any]:
    changed = file_set(changed_files)
    required = file_set(required_files)
    allowed = file_set(allowed_files)
    forbidden = file_set(forbidden_files)
    return {
        "changed_files": sorted(changed),
        "missing_required_changes": sorted(required - changed),
        "unrelated_changes": sorted(changed - allowed),
        "forbidden_changes": sorted(changed & forbidden),
    }


def public_pass_from_summary(summary: dict[str, Any]) -> bool:
    """Spec: use final_tests_passed, do not infer from product_status alone."""
    return bool(summary.get("final_tests_passed"))


def artifact_is_consistent(summary: dict[str, Any]) -> bool:
    """SUCCEEDED must agree with final_tests_passed == True (and vice versa)."""
    status = str(summary.get("status", "UNKNOWN"))
    final_ok = bool(summary.get("final_tests_passed"))
    return (status == "SUCCEEDED") == final_ok


def classify_failure(
    *,
    summary: dict[str, Any],
    eval_status: str,
    policy_rejections: int = 0,
) -> str:
    """Benchmark outcome label (eval_status wins for hidden / inconsistency)."""
    if eval_status == "ARTIFACT_INCONSISTENT":
        return "ARTIFACT_INCONSISTENT"
    if eval_status == "HIDDEN_TESTS_FAILED":
        return "HIDDEN_TESTS_FAILED"
    if eval_status == "HIDDEN_TEST_ENVIRONMENT_ERROR":
        return "TEST_ENVIRONMENT_ERROR"
    if eval_status == "HIDDEN_TEST_TIMEOUT":
        return "TEST_TIMEOUT"
    if eval_status == "SUCCEEDED":
        return "SUCCEEDED"

    status = str(summary.get("status", "UNKNOWN"))
    if status == "MODEL_OUTPUT_INVALID":
        return "MODEL_OUTPUT_INVALID"
    if status == "TEST_ENVIRONMENT_ERROR":
        return "TEST_ENVIRONMENT_ERROR"
    if status == "TEST_TIMEOUT":
        return "TEST_TIMEOUT"
    if status == "FAILED_MAX_ATTEMPTS":
        return "FAILED_MAX_ATTEMPTS"
    if status == "PATCH_NOT_APPLICABLE":
        return "PATCH_NOT_APPLICABLE"
    if status == "PATCH_BASE_CHANGED":
        return "PATCH_BASE_CHANGED"
    if status == "REJECTED" and policy_rejections > 0:
        return "POLICY_BLOCKED"
    if eval_status == "PUBLIC_TESTS_FAILED":
        if status == "REJECTED":
            return "POLICY_BLOCKED"
        return "PUBLIC_TESTS_FAILED"
    return status or eval_status


def resolve_eval_status(
    *,
    summary: dict[str, Any],
    hidden_pass: bool | None,
    hidden_eval_status: str | None,
    ran_hidden: bool,
) -> str:
    """Compose final eval_status with artifact consistency gate."""
    if not artifact_is_consistent(summary):
        return "ARTIFACT_INCONSISTENT"

    public_pass = public_pass_from_summary(summary)
    if not public_pass:
        return "PUBLIC_TESTS_FAILED"

    if not ran_hidden:
        return "SUCCEEDED"

    if hidden_eval_status in {
        "HIDDEN_TEST_ENVIRONMENT_ERROR",
        "HIDDEN_TEST_TIMEOUT",
        "HIDDEN_TESTS_FAILED",
        "SUCCEEDED",
    }:
        return str(hidden_eval_status)

    if hidden_pass is True:
        return "SUCCEEDED"
    if hidden_pass is False:
        return "HIDDEN_TESTS_FAILED"
    return "SUCCEEDED"


def extract_patch_apply_metrics(trace_path: Path | str | None) -> dict[str, Any]:
    """From product trace.jsonl: apply outcomes for approved patches.

    repair_attempts stays product summary.attempts_used — not redefined here.
    """
    path = Path(trace_path) if trace_path else None
    proposals = 0
    failures = 0
    successes = 0
    first_ok: bool | None = None
    if path is None or not path.exists():
        return {
            "patch_proposals": 0,
            "patch_apply_failures": 0,
            "patch_apply_success_rate": None,
            "first_patch_applicable": None,
        }
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        ev = json.loads(line)
        if ev.get("event") != "patch_applied":
            continue
        proposals += 1
        ok = bool((ev.get("payload") or {}).get("ok"))
        if first_ok is None:
            first_ok = ok
        if ok:
            successes += 1
        else:
            failures += 1
    total = successes + failures
    rate = (successes / total) if total else None
    return {
        "patch_proposals": proposals,
        "patch_apply_failures": failures,
        "patch_apply_success_rate": rate,
        "first_patch_applicable": first_ok,
    }


def build_run_metrics(
    *,
    task: dict[str, Any],
    summary: dict[str, Any],
    hidden_pass: bool | None,
    eval_status: str,
    duration_sec: float | None = None,
    read_actions: int | None = None,
    policy_rejections: int | None = None,
    trace_path: Path | str | None = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    product_status = str(summary.get("status", "UNKNOWN"))
    attempts = int(summary.get("attempts_used") or 0)
    format_retries = int(summary.get("total_format_retries_used") or 0)
    public_pass = public_pass_from_summary(summary)
    consistent = artifact_is_consistent(summary)
    change = compute_change_metrics(
        required_files=task["required_files"],
        allowed_files=task["allowed_files"],
        forbidden_files=task["forbidden_files"],
        changed_files=list(summary.get("changed_files") or []),
    )
    patch_m = extract_patch_apply_metrics(trace_path)
    # Do not count first-attempt / eval success when artifacts disagree.
    public_first = bool(consistent and public_pass and attempts == 1)
    eval_first = bool(
        consistent
        and public_first
        and hidden_pass is True
        and eval_status == "SUCCEEDED"
    )
    row = {
        "task_id": task["task_id"],
        "difficulty": task.get("difficulty"),
        "product_status": product_status,
        "public_pass": public_pass,
        "hidden_pass": hidden_pass,
        "eval_status": eval_status,
        "artifact_consistent": consistent,
        "failure_class": classify_failure(
            summary=summary,
            eval_status=eval_status,
            policy_rejections=int(policy_rejections or 0),
        ),
        "repair_attempts": attempts,
        "format_retries": format_retries,
        "public_first_attempt_success": public_first,
        "eval_first_attempt_success": eval_first,
        "stop_reason": summary.get("stop_reason"),
        "read_actions": read_actions,
        "policy_rejections": policy_rejections,
        "duration_sec": duration_sec,
        **change,
        **patch_m,
        "has_hidden_tests": task.get("has_hidden_tests", True),
        "benchmark_success": eval_status == "SUCCEEDED",
    }
    if hidden_pass is None and not task.get("has_hidden_tests", True):
        row["note"] = "No hidden tests configured"
    if not consistent:
        row["inconsistency_note"] = (
            "product_status and final_tests_passed disagree; "
            "eval_status=ARTIFACT_INCONSISTENT (neither side counted as success)"
        )
    if extra:
        row.update(extra)
    return row
