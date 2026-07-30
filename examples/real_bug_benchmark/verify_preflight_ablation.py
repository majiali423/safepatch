"""Recalculate and verify the committed 28-slot preflight comparison."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

from publish_preflight_ablation import EXPECTED_TASKS, calculate_summary

HERE = Path(__file__).resolve().parent
DEFAULT_BUNDLE = HERE / "published" / "preflight-ablation-28-run"
EXPECTED_SCHEDULE_SHA256 = "69c1cb4d76b5101b3b4954e93155cb2dd32ac28e148a88e43aede4c395412e4a"


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def verify(bundle: Path) -> dict[str, Any]:
    freeze = load_json(bundle / "freeze.json")
    runs = load_json(bundle / "runs.json")["runs"]
    recorded = load_json(bundle / "summary.json")
    calculated = calculate_summary(runs)
    errors: list[str] = []

    if calculated != recorded:
        errors.append("summary.json does not match recalculated runs.json totals")
    if calculated["completed_slots"] != 28 or calculated["unique_tasks"] != 7:
        errors.append("expected 28 completed slots over seven tasks")
    identities = {
        (run["task_id"], run["repeat"], run["preflight_enabled"]) for run in runs
    }
    expected_identities = {
        (task_id, repeat, enabled)
        for task_id in EXPECTED_TASKS
        for repeat in (1, 2)
        for enabled in (True, False)
    }
    if identities != expected_identities:
        errors.append("task/repeat/condition identities are incomplete or duplicated")
    if [run["slot"] for run in runs] != list(range(1, 29)):
        errors.append("run slots are not the frozen 1..28 sequence")

    schedule_payload = json.dumps(
        freeze["schedule"], sort_keys=True, separators=(",", ":")
    ).encode()
    schedule_hash = hashlib.sha256(schedule_payload).hexdigest()
    if schedule_hash != EXPECTED_SCHEDULE_SHA256:
        errors.append("frozen schedule content has changed")
    if freeze.get("schedule_sha256") != schedule_hash:
        errors.append("recorded schedule hash does not match schedule content")
    if freeze.get("reference_fix_visible_to_agent") is not False:
        errors.append("reference fixes must remain hidden")
    if freeze.get("hidden_tests_visible_to_agent") is not False:
        errors.append("hidden tests must remain hidden")

    on = calculated["preflight_enabled"]
    off = calculated["preflight_disabled"]
    if (on["public_successes"], on["overall_successes"]) != (13, 11):
        errors.append("enabled outcome differs from frozen 13 public / 11 overall")
    if (off["public_successes"], off["overall_successes"]) != (14, 9):
        errors.append("disabled outcome differs from frozen 14 public / 9 overall")
    if on["preflight_rejections"] != 0:
        errors.append("frozen enabled runs had no preflight rejections")
    if on["apply_failures_after_preflight"] != 0:
        errors.append("frozen enabled runs had no post-preflight apply failures")
    if off["apply_failures_without_preflight"] != 0:
        errors.append("frozen disabled runs had no apply failures")
    if off["first_patch_applicable"] is not None:
        errors.append("first-patch applicability is unobserved when preflight is disabled")
    if calculated["environment_failures"] != 0:
        errors.append("frozen experiment had no environment failures")
    if calculated["modified_test_files"]:
        errors.append("published runs include modified test files")

    failure_diffs = list((bundle / "failures").glob("*.diff"))
    failure_logs = list((bundle / "failures").glob("*.txt"))
    failures = sum(not run["overall_pass"] for run in runs)
    if len(failure_diffs) != failures or len(failure_logs) != failures:
        errors.append("every failed slot must retain one diff and one hidden-test log")

    for path in bundle.rglob("*"):
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8")
        if "C:\\Users\\" in text or "/Users/" in text:
            errors.append(f"local user path leaked into {path.relative_to(bundle)}")
        if "api_key" in text.lower():
            errors.append(f"API-key metadata leaked into {path.relative_to(bundle)}")

    if errors:
        raise ValueError("Published ablation verification failed:\n- " + "\n- ".join(errors))
    return calculated


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("bundle", nargs="?", type=Path, default=DEFAULT_BUNDLE)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    summary = verify(args.bundle.resolve())
    print(
        "Verified preflight comparison: enabled "
        f"{summary['preflight_enabled']['overall_successes']}/14, disabled "
        f"{summary['preflight_disabled']['overall_successes']}/14; "
        "zero preflight rejections and zero apply failures"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
