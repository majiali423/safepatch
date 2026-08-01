"""v0.3 DeepSeek canary — 4 Pilot tasks only.

Does NOT modify code_agent/, TASK, tests, hidden, or references.
Writes results under results/canary_v03/ and CANARY_V03_REPORT.md.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BENCH = Path(__file__).resolve().parent
RESULTS = BENCH / "results"
CANARY_DIR = RESULTS / "canary_v03"
REPORT_PATH = BENCH / "CANARY_V03_REPORT.md"
MANIFEST_PATH = BENCH / "benchmark_manifest.json"

CANARY_IDS = [
    "bench01_div_zero",
    "bench05_red_herring",
    "bench06_split_total",
    "bench12_slug_overfit",
]

# v0.2 Full12 first-run outcomes for the same four tasks (historical, not rewritten).
V02_FULL12 = {
    "bench01_div_zero": {
        "product_status": "FAILED_MAX_ATTEMPTS",
        "eval_status": "PUBLIC_TESTS_FAILED",
    },
    "bench05_red_herring": {
        "product_status": "FAILED_MAX_ATTEMPTS",
        "eval_status": "PUBLIC_TESTS_FAILED",
    },
    "bench06_split_total": {
        "product_status": "SUCCEEDED",
        "eval_status": "SUCCEEDED",
    },
    "bench12_slug_overfit": {
        "product_status": "SUCCEEDED",
        "eval_status": "SUCCEEDED",
    },
}


def report_path(value: object) -> str:
    """Render local artifact paths relative to the repository in public reports."""
    if not value:
        return "—"
    try:
        return Path(str(value)).resolve().relative_to(ROOT.resolve()).as_posix()
    except ValueError:
        return "<EXTERNAL_PATH>"

sys.path.insert(0, str(BENCH))
sys.path.insert(0, str(ROOT))

from run_deepseek_pilot import (  # noqa: E402
    record_freeze,
    run_one,
    verify_freeze,
)


def _load_trace(path: Path) -> list[dict]:
    if not path.exists():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            out.append(json.loads(line))
    return out


def _enrich_canary_metrics(row: dict) -> dict:
    """Derive v0.3 canary fields from summary.json + trace.jsonl."""
    result_path = Path(row.get("result_path") or "")
    summary = {}
    if (result_path / "summary.json").exists():
        summary = json.loads((result_path / "summary.json").read_text(encoding="utf-8"))
    events = _load_trace(result_path / "trace.jsonl")

    preflight_fail = int(summary.get("patch_preflight_failures") or 0)
    preflight_ok = int(summary.get("patch_preflight_successes") or 0)
    regen = int(summary.get("total_patch_regeneration_retries") or 0)
    apply_after = int(summary.get("patch_apply_failures_after_preflight") or 0)
    first_applicable = summary.get("first_patch_applicable")
    repair = int(summary.get("attempts_used") or row.get("repair_attempts") or 0)

    model_calls = sum(1 for e in events if e.get("event") == "model_request")
    pytest_runs = sum(1 for e in events if e.get("event") == "pytest_finished")
    # baseline is separate
    baseline_runs = sum(1 for e in events if e.get("event") == "baseline_test_finished")
    pytest_total = pytest_runs + baseline_runs

    approvals = [e for e in events if e.get("event") == "approval_decision"]
    preflight_fails = [e for e in events if e.get("event") == "patch_preflight_failed"]
    preflight_oks = [e for e in events if e.get("event") == "patch_preflight_succeeded"]
    regenerations = [e for e in events if e.get("event") == "patch_regeneration_requested"]
    proposed = [e for e in events if e.get("event") == "patch_proposed"]

    # Chronological check: no approval_decision may appear after a preflight_failed
    # unless a preflight_succeeded occurred in between (for that proposal cycle).
    preflight_fail_without_prior_success_before_approval = 0
    last_preflight_ok = False
    for e in events:
        ev = e.get("event")
        if ev == "patch_preflight_succeeded":
            last_preflight_ok = True
        elif ev == "patch_preflight_failed":
            last_preflight_ok = False
        elif ev == "approval_decision":
            if not last_preflight_ok:
                preflight_fail_without_prior_success_before_approval += 1

    recovered = bool(regen > 0 and row.get("eval_status") == "SUCCEEDED")

    row.update(
        {
            "patch_preflight_failures": preflight_fail,
            "patch_preflight_successes": preflight_ok,
            "total_patch_regeneration_retries": regen,
            "first_patch_applicable": first_applicable,
            "patch_regeneration_recovered": recovered,
            "repair_attempts": repair,
            "pytest_run_count": pytest_total,
            "pytest_after_apply_count": pytest_runs,
            "baseline_pytest_count": baseline_runs,
            "patch_apply_failures_after_preflight": apply_after,
            "model_call_count": model_calls,
            "approval_count": len(approvals),
            "preflight_failed_event_count": len(preflight_fails),
            "preflight_succeeded_event_count": len(preflight_oks),
            "regeneration_event_count": len(regenerations),
            "patch_proposed_count": len(proposed),
            "approval_without_prior_preflight_success": (
                preflight_fail_without_prior_success_before_approval
            ),
            "checks": {
                "preflight_fail_never_approved": (
                    preflight_fail_without_prior_success_before_approval == 0
                ),
                "preflight_fail_did_not_increment_repair": (
                    # If only preflight failures and no success apply, repair must be 0
                    not (preflight_fail > 0 and preflight_ok == 0 and repair > 0)
                ),
                "repair_only_after_apply": repair == pytest_runs,
                "apply_after_preflight_zero_or_noted": True,  # always record value
            },
            "v02_full12": V02_FULL12.get(row.get("task_id") or "", {}),
            "recovered_from_v02_failed_max": (
                V02_FULL12.get(row.get("task_id") or {}, {}).get("product_status")
                == "FAILED_MAX_ATTEMPTS"
                and row.get("eval_status") == "SUCCEEDED"
            ),
        }
    )
    return row


def write_report(*, freeze: dict, rows: list[dict], drift: list[str]) -> None:
    lines: list[str] = []
    lines.append("# v0.3 DeepSeek Canary Report (4 tasks)")
    lines.append("")
    lines.append(
        "**Mode:** real `deepseek-chat`. Not dry-run / reference. "
        "Product/Prompt/tasks/hidden/reference untouched during this run."
    )
    lines.append("")
    lines.append("## Freeze")
    lines.append("")
    lines.append("| Field | Value |")
    lines.append("|---|---|")
    lines.append(f"| started_at | `{freeze.get('started_at')}` |")
    lines.append(f"| product_commit | `{freeze.get('product_commit')}` |")
    lines.append(f"| product_tag | `{freeze.get('product_tag')}` |")
    lines.append(f"| model | `{freeze.get('model')}` |")
    lines.append(f"| base_url | `{freeze.get('base_url')}` |")
    img = freeze.get("docker_image") or {}
    lines.append(f"| docker | `{img.get('name')}` `{img.get('id')}` |")
    lines.append(f"| freeze_drift | `{drift}` |")
    lines.append("")
    lines.append("## Per-task results")
    lines.append("")
    lines.append(
        "| task | product_status | eval_status | public | hidden | "
        "preflight_fail | regen | first_applicable | regen_recovered | "
        "repair | pytest_runs | apply_after_pf | model_calls | "
        "approvals | duration_s | v02_status → now |"
    )
    lines.append("|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|")
    for r in rows:
        v02 = (r.get("v02_full12") or {}).get("product_status", "?")
        lines.append(
            "| {tid} | {ps} | {es} | {pp} | {hp} | {pf} | {rg} | {fa} | {rr} | "
            "{ra} | {py} | {ap} | {mc} | {ac} | {dur} | {v02} → {ps2} |".format(
                tid=r.get("task_id"),
                ps=r.get("product_status"),
                es=r.get("eval_status"),
                pp=r.get("public_pass"),
                hp=r.get("hidden_pass"),
                pf=r.get("patch_preflight_failures"),
                rg=r.get("total_patch_regeneration_retries"),
                fa=r.get("first_patch_applicable"),
                rr=r.get("patch_regeneration_recovered"),
                ra=r.get("repair_attempts"),
                py=r.get("pytest_run_count"),
                ap=r.get("patch_apply_failures_after_preflight"),
                mc=r.get("model_call_count"),
                ac=r.get("approval_count"),
                dur=f"{float(r.get('duration_sec') or 0):.1f}",
                v02=v02,
                ps2=r.get("product_status"),
            )
        )
    lines.append("")
    lines.append("## Special checks")
    lines.append("")
    for r in rows:
        c = r.get("checks") or {}
        lines.append(f"### `{r.get('task_id')}`")
        lines.append(
            f"- preflight fail never approved: **{c.get('preflight_fail_never_approved')}** "
            f"(approval_without_prior_preflight_success="
            f"{r.get('approval_without_prior_preflight_success')})"
        )
        lines.append(
            f"- preflight fail did not alone inflate repair: "
            f"**{c.get('preflight_fail_did_not_increment_repair')}** "
            f"(repair={r.get('repair_attempts')}, preflight_fail="
            f"{r.get('patch_preflight_failures')}, preflight_ok="
            f"{r.get('patch_preflight_successes')})"
        )
        lines.append(
            f"- repair_attempts == pytest_after_apply: "
            f"**{c.get('repair_only_after_apply')}** "
            f"({r.get('repair_attempts')} == {r.get('pytest_after_apply_count')})"
        )
        lines.append(
            f"- patch_apply_failures_after_preflight: "
            f"**{r.get('patch_apply_failures_after_preflight')}**"
        )
        lines.append(
            f"- recovered_from_v02_FAILED_MAX_ATTEMPTS: "
            f"**{r.get('recovered_from_v02_failed_max')}**"
        )
        lines.append(
            f"- session: `{report_path(r.get('session_path'))}`"
        )
        lines.append(
            f"- result: `{report_path(r.get('result_path'))}`"
        )
        lines.append("")
    n = len(rows)
    eval_ok = sum(1 for r in rows if r.get("eval_status") == "SUCCEEDED")
    lines.append("## Aggregate")
    lines.append("")
    lines.append(f"- eval success: **{eval_ok}/{n}**")
    lines.append(
        f"- total apply_after_preflight failures: "
        f"**{sum(int(r.get('patch_apply_failures_after_preflight') or 0) for r in rows)}**"
    )
    lines.append(
        f"- total preflight failures: "
        f"**{sum(int(r.get('patch_preflight_failures') or 0) for r in rows)}**"
    )
    lines.append(
        f"- total regenerations: "
        f"**{sum(int(r.get('total_patch_regeneration_retries') or 0) for r in rows)}**"
    )
    lines.append("")
    lines.append(
        "n=4 canary only — not Full12, not production proof. "
        "v0.2 Full12 numbers remain historical."
    )
    lines.append("")
    REPORT_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"Wrote {REPORT_PATH}", flush=True)


def main() -> int:
    # Patch record_freeze output path by running then copying — reuse freeze but
    # store under canary_v03.
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    tasks = [t for t in manifest["tasks"] if t["task_id"] in CANARY_IDS]
    # Preserve canary order.
    by_id = {t["task_id"]: t for t in tasks}
    tasks = [by_id[i] for i in CANARY_IDS if i in by_id]
    if len(tasks) != 4:
        print(f"ERROR: expected 4 canary tasks, got {[t['task_id'] for t in tasks]}", file=sys.stderr)
        return 2

    task_ids = [t["task_id"] for t in tasks]
    freeze = record_freeze(task_ids)
    # Move/copy freeze into canary dir (record_freeze writes full12_deepseek).
    CANARY_DIR.mkdir(parents=True, exist_ok=True)
    freeze_path = CANARY_DIR / "freeze.json"
    freeze["canary_tasks"] = task_ids
    freeze["notes"] = [
        "v0.3 DeepSeek canary — 4 Pilot tasks only.",
        "No product/prompt/task/hidden/reference modifications.",
    ]
    freeze_path.write_text(
        json.dumps(freeze, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    print(json.dumps({k: freeze[k] for k in (
        "started_at", "product_commit", "product_tag", "model", "base_url",
        "manifest_sha256", "system_prompt_sha256", "docker_image", "api_key_present",
    ) if k in freeze}, indent=2), flush=True)

    if not freeze.get("docker_preflight_ok"):
        print("ERROR: docker preflight failed", freeze.get("docker_preflight_reason"), file=sys.stderr)
        return 4

    rows = []
    for task in tasks:
        row = run_one(task, freeze=freeze)
        row = _enrich_canary_metrics(row)
        (Path(row["result_path"]) / "canary_metrics.json").write_text(
            json.dumps(row, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        rows.append(row)
        print(json.dumps({
            "task_id": row.get("task_id"),
            "product_status": row.get("product_status"),
            "eval_status": row.get("eval_status"),
            "patch_preflight_failures": row.get("patch_preflight_failures"),
            "total_patch_regeneration_retries": row.get("total_patch_regeneration_retries"),
            "repair_attempts": row.get("repair_attempts"),
            "first_patch_applicable": row.get("first_patch_applicable"),
            "patch_regeneration_recovered": row.get("patch_regeneration_recovered"),
            "patch_apply_failures_after_preflight": row.get(
                "patch_apply_failures_after_preflight"
            ),
            "model_call_count": row.get("model_call_count"),
            "approval_count": row.get("approval_count"),
        }, indent=2), flush=True)

    drift = verify_freeze(freeze, task_ids)
    index = {
        "canary": True,
        "freeze_path": str(freeze_path),
        "freeze_drift_errors": drift,
        "canonical_first_runs": rows,
        "finished_at": datetime.now(timezone.utc).isoformat(),
    }
    (CANARY_DIR / "runs_index.json").write_text(
        json.dumps(index, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    write_report(freeze=freeze, rows=rows, drift=drift)
    return 3 if drift else 0


if __name__ == "__main__":
    raise SystemExit(main())
