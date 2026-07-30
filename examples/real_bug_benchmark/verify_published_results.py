"""Recalculate and verify the committed, sanitized 21-run benchmark bundle."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from publish_synthesis_results import calculate_summary

HERE = Path(__file__).resolve().parent
DEFAULT_BUNDLE = HERE / "published" / "synthesis-21-run"
EXPECTED_TASKS = {
    "pysnooper-3",
    "tornado-11",
    "tqdm-3",
    "sanic-5",
    "thefuck-19",
    "tornado-10",
    "thefuck-16",
}


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def verify(bundle: Path) -> dict[str, Any]:
    freeze = _load_json(bundle / "freeze.json")
    runs = _load_json(bundle / "runs.json")["runs"]
    recorded = _load_json(bundle / "summary.json")
    calculated = calculate_summary(runs)

    errors: list[str] = []
    if calculated != recorded:
        errors.append("summary.json does not match recalculated runs.json totals")
    if set(calculated["runs_per_task"]) != EXPECTED_TASKS:
        errors.append("published task set differs from the frozen seven-task set")
    if any(count != 3 for count in calculated["runs_per_task"].values()):
        errors.append("every task must have exactly three independent runs")
    if calculated["runs"] != 21:
        errors.append("expected exactly 21 runs")
    if calculated["public_successes"] != 19:
        errors.append("expected 19 public-test successes")
    if calculated["overall_successes"] != 17:
        errors.append("expected 17 public-plus-hidden successes")
    if calculated["modified_test_files"]:
        errors.append("published runs include modified test files")
    if calculated["apply_failures_after_preflight"] != 0:
        errors.append("post-preflight apply failures must remain zero")
    if freeze.get("reference_fix_visible_to_agent") is not False:
        errors.append("reference fixes must remain hidden from the agent")
    if freeze.get("hidden_tests_visible_to_agent") is not False:
        errors.append("hidden tests must remain hidden from the agent")
    if not freeze.get("preflight_enabled"):
        errors.append("the published experiment must have preflight enabled")

    for path in bundle.rglob("*"):
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8")
        if "C:\\Users\\" in text or "/Users/" in text:
            errors.append(f"local user path leaked into {path.relative_to(bundle)}")
        if "api_key" in text.lower():
            errors.append(f"API-key metadata leaked into {path.relative_to(bundle)}")

    if errors:
        raise ValueError("Published benchmark verification failed:\n- " + "\n- ".join(errors))
    return calculated


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("bundle", nargs="?", type=Path, default=DEFAULT_BUNDLE)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    summary = verify(args.bundle.resolve())
    print(
        "Verified published benchmark: "
        f"{summary['overall_successes']}/{summary['runs']} overall successes, "
        f"{summary['total_tokens']} tokens, ${summary['estimated_cost_usd']:.8f}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
