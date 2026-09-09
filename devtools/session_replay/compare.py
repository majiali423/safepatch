"""Compare versioned session-replay trees. Missing inputs fail closed."""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from devtools.session_replay.artifacts import (
    detect_format_version,
    fingerprint_from_dir,
    load_json,
)
from devtools.session_replay.constants import (
    FORMAT_VERSION,
    HISTORICAL_FINGERPRINT_KEYS,
    HISTORICAL_FINGERPRINT_KEYS_BY_SCENARIO,
    OBSERVABILITY_COMPARE_KEYS,
    SCENARIO_NAMES,
    SUMMARY_COMPARE_KEYS,
)
from devtools.session_replay.normalize import parse_trace

V2_FILES = (
    "summary.json",
    "trace.jsonl",
    "final.diff",
    "fingerprint.json",
    "workspace_mod.txt",
    "last_error.txt",
    "session_extras.json",
)

HISTORICAL_FILES = {
    "cancelled": ("fingerprint.json", "trace.events.json"),
    "*": ("fingerprint.json", "summary.json", "trace.events.json", "final.diff"),
}


@dataclass
class CompareReport:
    ok: bool
    errors: list[str] = field(default_factory=list)

    def exit_code(self) -> int:
        return 0 if self.ok else 1


def _err(errors: list[str], message: str) -> None:
    errors.append(message)


def _child_dirs(root: Path) -> dict[str, Path]:
    if not root.exists():
        return {}
    return {path.name: path for path in root.iterdir() if path.is_dir()}


def _validate_root(root: Path, *, side: str, errors: list[str]) -> dict[str, Path]:
    if not root.exists():
        _err(errors, f"{side} path does not exist: {root}")
        return {}
    if not root.is_dir():
        _err(errors, f"{side} is not a directory: {root}")
        return {}
    children = _child_dirs(root)
    if not children:
        _err(errors, f"{side} directory is empty: {root}")
        return {}
    names = set(children)
    expected = set(SCENARIO_NAMES)
    missing = sorted(expected - names)
    extra = sorted(names - expected)
    if missing:
        _err(errors, f"{side} missing scenarios: {missing}")
    if extra:
        _err(errors, f"{side} unexpected scenarios: {extra}")
    return children


def _historical_files(name: str) -> tuple[str, ...]:
    return HISTORICAL_FILES.get(name, HISTORICAL_FILES["*"])


def _require_files(scenario_dir: Path, files: tuple[str, ...], *, where: str, errors: list[str]) -> None:
    for name in files:
        path = scenario_dir / name
        if not path.is_file():
            _err(errors, f"{where} missing file {name}")


def _require_object_fields(data: Any, keys: tuple[str, ...], *, where: str, errors: list[str]) -> dict[str, Any]:
    if not isinstance(data, dict):
        _err(errors, f"{where} is not a JSON object")
        return {}
    missing = [key for key in keys if key not in data]
    if missing:
        _err(errors, f"{where} missing fields: {missing}")
    return data


def _load_v2_scenario(path: Path, *, where: str, errors: list[str]) -> None:
    _require_files(path, V2_FILES, where=where, errors=errors)
    summary_path = path / "summary.json"
    fingerprint_path = path / "fingerprint.json"
    if not summary_path.is_file() or not fingerprint_path.is_file():
        return
    summary = load_json(summary_path)
    _require_object_fields(
        summary,
        ("status", "stop_reason", "attempts_used"),
        where=f"{where}.summary.json",
        errors=errors,
    )
    stored = load_json(fingerprint_path)
    if not isinstance(stored, dict):
        _err(errors, f"{where}.fingerprint.json is not an object")
        return
    try:
        computed = fingerprint_from_dir(path)
    except (OSError, ValueError, TypeError) as exc:
        _err(errors, f"{where} cannot recompute fingerprint: {exc}")
        return
    if stored != computed:
        _err(
            errors,
            f"{where}.fingerprint.json does not match summary/trace/final.diff "
            "(stale fingerprint is not accepted)",
        )


def _subset(data: dict[str, Any], keys: tuple[str, ...]) -> dict[str, Any]:
    return {key: data[key] for key in keys if key in data}


def _compare_mapping(
    before: dict[str, Any],
    after: dict[str, Any],
    keys: tuple[str, ...],
    *,
    where: str,
    errors: list[str],
) -> None:
    for key in keys:
        in_before = key in before
        in_after = key in after
        if not in_before or not in_after:
            _err(
                errors,
                f"{where}.{key} missing on "
                f"{'both sides' if not in_before and not in_after else ('baseline' if not in_before else 'after')}",
            )
            continue
        if before[key] != after[key]:
            _err(errors, f"{where}.{key} differs")


def _historical_event_names(scenario_dir: Path) -> list[str]:
    events_path = scenario_dir / "trace.events.json"
    if events_path.is_file():
        data = load_json(events_path)
        if isinstance(data, list):
            return [str(item) for item in data]
    fingerprint = load_json(scenario_dir / "fingerprint.json")
    if isinstance(fingerprint, dict) and isinstance(fingerprint.get("event_order"), list):
        return [str(item) for item in fingerprint["event_order"]]
    return []


def _v2_event_names(scenario_dir: Path) -> list[str]:
    records = parse_trace((scenario_dir / "trace.jsonl").read_text(encoding="utf-8"))
    return [str(record.get("event")) for record in records]


def compare_directories(baseline: Path, after: Path) -> CompareReport:
    errors: list[str] = []
    base_children = _validate_root(baseline, side="baseline", errors=errors)
    after_children = _validate_root(after, side="after", errors=errors)
    if not base_children or not after_children:
        return CompareReport(ok=False, errors=errors)

    base_version = detect_format_version(baseline)
    after_version = detect_format_version(after)

    for name in SCENARIO_NAMES:
        base_dir = base_children.get(name)
        after_dir = after_children.get(name)
        if base_dir is None or after_dir is None:
            continue
        before_count = len(errors)
        if base_version >= FORMAT_VERSION:
            _load_v2_scenario(base_dir, where=f"baseline.{name}", errors=errors)
        else:
            _require_files(
                base_dir,
                _historical_files(name),
                where=f"baseline.{name}",
                errors=errors,
            )
        if after_version >= FORMAT_VERSION:
            _load_v2_scenario(after_dir, where=f"after.{name}", errors=errors)
        else:
            _require_files(
                after_dir,
                _historical_files(name),
                where=f"after.{name}",
                errors=errors,
            )
        if len(errors) > before_count:
            continue

        if base_version >= FORMAT_VERSION and after_version >= FORMAT_VERSION:
            _compare_v2_pair(base_dir, after_dir, name=name, errors=errors)
        else:
            _compare_historical_pair(
                base_dir,
                after_dir,
                name=name,
                after_version=after_version,
                errors=errors,
            )

    return CompareReport(ok=not errors, errors=errors)


def _compare_v2_pair(base_dir: Path, after_dir: Path, *, name: str, errors: list[str]) -> None:
    prefix = name
    base_summary = load_json(base_dir / "summary.json")
    after_summary = load_json(after_dir / "summary.json")
    if isinstance(base_summary, dict) and isinstance(after_summary, dict):
        _compare_mapping(
            base_summary,
            after_summary,
            SUMMARY_COMPARE_KEYS,
            where=f"{prefix}.summary",
            errors=errors,
        )
        base_obs = base_summary.get("observability")
        after_obs = after_summary.get("observability")
        if not isinstance(base_obs, dict) or not isinstance(after_obs, dict):
            _err(errors, f"{prefix}.summary.observability missing")
        else:
            _compare_mapping(
                base_obs,
                after_obs,
                OBSERVABILITY_COMPARE_KEYS,
                where=f"{prefix}.summary.observability",
                errors=errors,
            )
    base_trace = parse_trace((base_dir / "trace.jsonl").read_text(encoding="utf-8"))
    after_trace = parse_trace((after_dir / "trace.jsonl").read_text(encoding="utf-8"))
    if base_trace != after_trace:
        _err(errors, f"{prefix}.trace.jsonl event payload or order differs")
    base_diff = (base_dir / "final.diff").read_text(encoding="utf-8")
    after_diff = (after_dir / "final.diff").read_text(encoding="utf-8")
    if base_diff != after_diff:
        _err(errors, f"{prefix}.final.diff differs")


def _compare_historical_pair(
    base_dir: Path,
    after_dir: Path,
    *,
    name: str,
    after_version: int,
    errors: list[str],
) -> None:
    keys = HISTORICAL_FINGERPRINT_KEYS_BY_SCENARIO.get(name, HISTORICAL_FINGERPRINT_KEYS)
    base_fp = load_json(base_dir / "fingerprint.json")
    if after_version >= FORMAT_VERSION:
        after_fp = fingerprint_from_dir(after_dir)
    else:
        after_fp = load_json(after_dir / "fingerprint.json")
    if not isinstance(base_fp, dict) or not isinstance(after_fp, dict):
        _err(errors, f"{name}.fingerprint.json is not an object")
        return
    comparable = tuple(key for key in keys if key != "last_error")
    _compare_mapping(
        _subset(base_fp, comparable),
        _subset(after_fp, comparable),
        comparable,
        where=f"{name}.fingerprint",
        errors=errors,
    )
    if after_version >= FORMAT_VERSION:
        after_events = _v2_event_names(after_dir)
    else:
        after_events = _historical_event_names(after_dir)
    base_events = _historical_event_names(base_dir)
    if base_events != after_events:
        _err(errors, f"{name}.event_order differs")
    if (base_dir / "final.diff").is_file() and (after_dir / "final.diff").is_file():
        if (base_dir / "final.diff").read_text(encoding="utf-8") != (
            after_dir / "final.diff"
        ).read_text(encoding="utf-8"):
            _err(errors, f"{name}.final.diff differs")
    if (base_dir / "summary.json").is_file() and (after_dir / "summary.json").is_file():
        base_summary = load_json(base_dir / "summary.json")
        after_summary = load_json(after_dir / "summary.json")
        if isinstance(base_summary, dict) and isinstance(after_summary, dict):
            _compare_mapping(
                base_summary,
                after_summary,
                ("status", "stop_reason", "attempts_used"),
                where=f"{name}.summary",
                errors=errors,
            )


def check_invariants(results: dict[str, dict[str, Any]]) -> list[str]:
    from devtools.session_replay.constants import EXPECTED_INVARIANTS

    errors: list[str] = []
    for name in SCENARIO_NAMES:
        if name not in results:
            errors.append(f"capture missing scenario {name}")
            continue
        actual = results[name]
        expected = EXPECTED_INVARIANTS[name]
        for key, value in expected.items():
            if actual.get(key) != value:
                errors.append(
                    f"capture invariant {name}.{key}: expected {value!r}, got {actual.get(key)!r}"
                )
    extra = sorted(set(results) - set(SCENARIO_NAMES))
    if extra:
        errors.append(f"capture unexpected scenarios: {extra}")
    return errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Compare SafePatch session-replay artifacts")
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--after", type=Path, required=True)
    parser.add_argument("--report", type=Path, default=None)
    args = parser.parse_args(argv)
    report = compare_directories(args.baseline.resolve(), args.after.resolve())
    payload = {"ok": report.ok, "errors": report.errors}
    text = json.dumps(payload, indent=2, ensure_ascii=False) + "\n"
    if args.report is not None:
        args.report.write_text(text, encoding="utf-8")
    sys.stdout.write(text)
    return report.exit_code()


if __name__ == "__main__":
    raise SystemExit(main())
