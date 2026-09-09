"""Load, fingerprint, and write versioned session-replay artifacts."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from devtools.session_replay.constants import (
    FORMAT_NAME,
    FORMAT_VERSION,
    HISTORICAL_FORMAT_VERSION,
    SCENARIO_NAMES,
)
from devtools.session_replay.normalize import dump_trace, normalize_value, parse_trace


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def event_names(records: list[dict[str, Any]]) -> list[str]:
    names: list[str] = []
    for record in records:
        event = record.get("event")
        if not isinstance(event, str):
            raise ValueError("trace record missing event name")
        names.append(event)
    return names


def compute_fingerprint(
    *,
    summary: dict[str, Any],
    records: list[dict[str, Any]],
    final_diff: str,
    workspace_mod: str,
    last_error: str = "",
    extras: dict[str, Any] | None = None,
) -> dict[str, Any]:
    extra = extras or {}
    obs = summary.get("observability") or {}
    model = obs.get("model") or {}
    retries = obs.get("retries") or {}
    analysis = summary.get("analysis") or {}
    return {
        "fingerprint_schema_version": FORMAT_VERSION,
        "status": summary.get("status"),
        "stop_reason": summary.get("stop_reason"),
        "attempts_used": summary.get("attempts_used"),
        "last_error": last_error,
        "total_format_retries_used": summary.get("total_format_retries_used"),
        "consecutive_format_retries": summary.get("consecutive_format_retries"),
        "total_patch_regeneration_retries": summary.get("total_patch_regeneration_retries"),
        "read_actions_used": (summary.get("read_budget") or {}).get("total_read_actions"),
        "exploration_read_actions_used": (summary.get("read_budget") or {}).get("used"),
        "evidence_requests_used": analysis.get("evidence_requests_used"),
        "evidence_parameter_corrections_used": analysis.get("parameter_corrections_used"),
        "hard_policy_violations": analysis.get("hard_policy_violations"),
        "total_no_progress_actions": analysis.get("total_no_progress_actions"),
        "patch_preflight_failures": summary.get("patch_preflight_failures"),
        "patch_preflight_successes": summary.get("patch_preflight_successes"),
        "pytest_scope_unsupported": extra.get(
            "pytest_scope_unsupported", summary.get("pytest_scope_unsupported")
        ),
        "summary_status": summary.get("status"),
        "summary_stop_reason": summary.get("stop_reason"),
        "summary_attempts_used": summary.get("attempts_used"),
        "model_calls": model.get("calls"),
        "logical_calls": model.get("logical_calls"),
        "transport_attempts": model.get("transport_attempts"),
        "format_retries": retries.get("format"),
        "patch_regeneration": retries.get("patch_regeneration"),
        "read_budget": summary.get("read_budget"),
        "event_order": event_names(records),
        "final_diff": final_diff,
        "workspace_mod": workspace_mod,
    }


def fingerprint_from_dir(scenario_dir: Path, *, last_error: str = "") -> dict[str, Any]:
    summary = load_json(scenario_dir / "summary.json")
    if not isinstance(summary, dict):
        raise ValueError(f"{scenario_dir}/summary.json is not an object")
    records = parse_trace((scenario_dir / "trace.jsonl").read_text(encoding="utf-8"))
    diff_path = scenario_dir / "final.diff"
    final_diff = diff_path.read_text(encoding="utf-8") if diff_path.exists() else ""
    mod_path = scenario_dir / "workspace_mod.txt"
    workspace_mod = mod_path.read_text(encoding="utf-8") if mod_path.exists() else ""
    stored_error = last_error
    error_path = scenario_dir / "last_error.txt"
    if not stored_error and error_path.exists():
        stored_error = error_path.read_text(encoding="utf-8")
    extras_path = scenario_dir / "session_extras.json"
    extras = load_json(extras_path) if extras_path.is_file() else {}
    if extras is not None and not isinstance(extras, dict):
        raise ValueError(f"{scenario_dir}/session_extras.json is not an object")
    return compute_fingerprint(
        summary=summary,
        records=records,
        final_diff=final_diff,
        workspace_mod=workspace_mod,
        last_error=stored_error,
        extras=extras if isinstance(extras, dict) else {},
    )


def write_v2_scenario(
    dest: Path,
    *,
    summary: dict[str, Any],
    records: list[dict[str, Any]],
    final_diff: str,
    workspace_mod: str,
    last_error: str,
    replacements: list[tuple[str, str]],
    extras: dict[str, Any] | None = None,
) -> dict[str, Any]:
    dest.mkdir(parents=True, exist_ok=True)
    summary_n = normalize_value(summary, replacements)
    records_n = [normalize_value(record, replacements) for record in records]
    diff_n = str(normalize_value(final_diff, replacements))
    mod_n = str(normalize_value(workspace_mod, replacements))
    error_n = str(normalize_value(last_error, replacements))
    extras_n = normalize_value(extras or {}, replacements)
    if not isinstance(summary_n, dict):
        raise TypeError("normalized summary is not an object")
    if not isinstance(extras_n, dict):
        raise TypeError("normalized extras is not an object")
    write_json(dest / "summary.json", summary_n)
    write_json(dest / "session_extras.json", extras_n)
    (dest / "trace.jsonl").write_text(dump_trace(records_n), encoding="utf-8")
    (dest / "final.diff").write_text(diff_n, encoding="utf-8")
    (dest / "workspace_mod.txt").write_text(mod_n, encoding="utf-8")
    (dest / "last_error.txt").write_text(error_n, encoding="utf-8")
    fingerprint = compute_fingerprint(
        summary=summary_n,
        records=records_n,
        final_diff=diff_n,
        workspace_mod=mod_n,
        last_error=error_n,
        extras=extras_n,
    )
    write_json(dest / "fingerprint.json", fingerprint)
    return fingerprint


def write_manifest(out_dir: Path, *, label: str) -> None:
    write_json(
        out_dir / "manifest.json",
        {
            "format": FORMAT_NAME,
            "version": FORMAT_VERSION,
            "label": label,
            "scenarios": list(SCENARIO_NAMES),
            "evidence_scope": "full_summary_trace_diff",
            "notes": (
                "Current-version baseline. It does not reconstruct missing "
                "historical trace payloads and does not prove pre-refactor "
                "payload equality. Historical v1 stored event names only."
            ),
        },
    )


def detect_format_version(root: Path) -> int:
    manifest = root / "manifest.json"
    if manifest.is_file():
        data = load_json(manifest)
        if isinstance(data, dict) and isinstance(data.get("version"), int):
            return int(data["version"])
    return HISTORICAL_FORMAT_VERSION
