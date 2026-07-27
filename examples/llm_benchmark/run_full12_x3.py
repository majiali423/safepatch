"""v0.3 Full12 × 3 real DeepSeek evaluation (36 first-result runs).

Does NOT modify product, Prompt, tasks, tests, hidden, or references.
Between rounds: no code/prompt/task changes.
First results are permanent; model failures are not overwritten.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BENCH = Path(__file__).resolve().parent
RESULTS = BENCH / "results"
X3_DIR = RESULTS / "full12_v03_x3"
REPORT_PATH = BENCH / "FULL12_V03_X3_REPORT.md"
MANIFEST_PATH = BENCH / "benchmark_manifest.json"
ROUNDS = 3

# Historical v0.2 single-round Full12 (not a paired A/B sample).
V02_EVAL = {
    "bench01_div_zero": False,
    "bench02_parse_int": True,
    "bench03_unique_keep": True,
    "bench04_clamp_overfit": True,
    "bench05_red_herring": False,
    "bench06_split_total": True,
    "bench07_merge_ranges": True,
    "bench08_staged_bug": True,
    "bench09_config_pipeline": True,
    "bench10_normalize_date": True,
    "bench11_test_tamper_trap": True,
    "bench12_slug_overfit": True,
}

sys.path.insert(0, str(BENCH))
sys.path.insert(0, str(ROOT))

from run_deepseek_pilot import (  # noqa: E402
    record_freeze,
    run_one,
    verify_freeze,
)


def _utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def _load_trace(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _enrich(row: dict) -> dict:
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
    pytest_after = sum(1 for e in events if e.get("event") == "pytest_finished")
    recovered = bool(regen > 0 and row.get("eval_status") == "SUCCEEDED")
    na = row.get("product_status") == "PATCH_NOT_APPLICABLE" or row.get(
        "eval_status"
    ) == "PATCH_NOT_APPLICABLE"

    row.update(
        {
            "patch_preflight_failures": preflight_fail,
            "patch_preflight_successes": preflight_ok,
            "total_patch_regeneration_retries": regen,
            "first_patch_applicable": first_applicable,
            "patch_regeneration_recovered": recovered,
            "repair_attempts": repair,
            "pytest_after_apply": pytest_after,
            "patch_apply_failures_after_preflight": apply_after,
            "model_call_count": model_calls,
            "patch_not_applicable": na,
            "format_retries": int(
                summary.get("total_format_retries_used") or row.get("format_retries") or 0
            ),
        }
    )
    return row


def _pct(n: int, d: int) -> str:
    if d == 0:
        return "n/a"
    return f"{n}/{d} ({100.0 * n / d:.1f}%)"


def _mean(xs: list[float]) -> float | None:
    return sum(xs) / len(xs) if xs else None


def write_report(*, freeze: dict, rounds: list[dict], drift: list[str]) -> None:
    lines: list[str] = []
    lines.append("# v0.3 Full12 × 3 DeepSeek Report")
    lines.append("")
    lines.append(
        "**Mode:** real `deepseek-chat`, 12 tasks × 3 independent rounds = **36** "
        "canonical first results. No dry-run / reference. "
        "No product/Prompt/task changes between rounds."
    )
    lines.append("")
    lines.append(
        "**Caveat:** n=36 still cannot prove production readiness. "
        "Comparison to v0.2 single-round Full12 is **not** a paired A/B under identical sampling."
    )
    lines.append("")
    params = freeze.get("model_params") or {}
    img = freeze.get("docker_image") or {}
    lines.append("## Freeze")
    lines.append("")
    lines.append("| Field | Value |")
    lines.append("|---|---|")
    lines.append(f"| started_at | `{freeze.get('started_at')}` |")
    lines.append(f"| finished_at | `{_utc()}` |")
    lines.append(f"| product_tag | `{freeze.get('product_tag')}` |")
    lines.append(f"| product_commit | `{freeze.get('product_commit')}` |")
    lines.append(f"| benchmark_commit | `{freeze.get('benchmark_commit')}` |")
    lines.append(f"| manifest_sha256 | `{freeze.get('manifest_sha256')}` |")
    lines.append(f"| system_prompt_sha256 | `{freeze.get('system_prompt_sha256')}` |")
    lines.append(f"| model | `{freeze.get('model')}` |")
    lines.append(f"| base_url | `{freeze.get('base_url')}` |")
    lines.append(
        f"| temperature | `{params.get('temperature')}` ({params.get('temperature_source')}) |"
    )
    lines.append(f"| docker | `{img.get('name')}` `{img.get('id')}` |")
    lines.append(f"| freeze_drift | `{drift}` |")
    lines.append("")

    # Per-round tables
    eval_rates = []
    all_rows: list[dict] = []
    for rd in rounds:
        rnum = rd["round"]
        rows = rd["canonical_first_runs"]
        all_rows.extend(rows)
        n = len(rows)
        eval_ok = sum(1 for r in rows if r.get("eval_status") == "SUCCEEDED")
        eval_rates.append(eval_ok / n if n else 0.0)
        lines.append(f"## 1. Round {rnum} results (n={n})")
        lines.append("")
        lines.append(
            "| task | product | eval | public | hidden | first_app | "
            "preflight_fail | regen | recovered | repair | model_calls | "
            "after_pf | NA | duration_s |"
        )
        lines.append("|---|---|---|---|---|---|---|---|---|---|---|---|---|---|")
        for r in rows:
            lines.append(
                "| {tid} | {ps} | {es} | {pp} | {hp} | {fa} | {pf} | {rg} | {rc} | "
                "{ra} | {mc} | {ap} | {na} | {dur} |".format(
                    tid=r.get("task_id"),
                    ps=r.get("product_status"),
                    es=r.get("eval_status"),
                    pp=r.get("public_pass"),
                    hp=r.get("hidden_pass"),
                    fa=r.get("first_patch_applicable"),
                    pf=r.get("patch_preflight_failures"),
                    rg=r.get("total_patch_regeneration_retries"),
                    rc=r.get("patch_regeneration_recovered"),
                    ra=r.get("repair_attempts"),
                    mc=r.get("model_call_count"),
                    ap=r.get("patch_apply_failures_after_preflight"),
                    na=r.get("patch_not_applicable"),
                    dur=f"{float(r.get('duration_sec') or 0):.1f}",
                )
            )
        lines.append("")
        lines.append(
            f"Round {rnum} eval success: **{_pct(eval_ok, n)}**"
        )
        lines.append("")

    N = len(all_rows)
    lines.append("## 2–3. Aggregate success (n=36 first runs)")
    lines.append("")
    pub = sum(1 for r in all_rows if r.get("public_pass"))
    hid = sum(1 for r in all_rows if r.get("hidden_pass") is True)
    ev = sum(1 for r in all_rows if r.get("eval_status") == "SUCCEEDED")
    lines.append("| Metric | Value |")
    lines.append("|---|---|")
    lines.append(f"| public pass | **{_pct(pub, N)}** |")
    lines.append(f"| hidden pass | **{_pct(hid, N)}** |")
    lines.append(f"| eval success | **{_pct(ev, N)}** |")
    lines.append(
        f"| per-round eval success | "
        + ", ".join(f"R{i+1}={100*x:.1f}%" for i, x in enumerate(eval_rates))
        + f"; mean={100*(_mean(eval_rates) or 0):.1f}%; "
        f"range=[{100*min(eval_rates):.1f}%, {100*max(eval_rates):.1f}%] |"
    )
    lines.append("")

    first_app_known = [r for r in all_rows if r.get("first_patch_applicable") is not None]
    first_app_ok = sum(1 for r in first_app_known if r.get("first_patch_applicable"))
    lines.append("## 4. First patch applicable")
    lines.append("")
    lines.append(
        f"**{_pct(first_app_ok, len(first_app_known))}** "
        f"(among runs with ≥1 preflight outcome recorded)"
    )
    lines.append("")

    pf = sum(int(r.get("patch_preflight_failures") or 0) for r in all_rows)
    regen = sum(int(r.get("total_patch_regeneration_retries") or 0) for r in all_rows)
    regen_sessions = [r for r in all_rows if int(r.get("total_patch_regeneration_retries") or 0) > 0]
    recovered = sum(1 for r in regen_sessions if r.get("patch_regeneration_recovered"))
    lines.append("## 5–6. Preflight failures & regeneration")
    lines.append("")
    lines.append("| Metric | Value |")
    lines.append("|---|---|")
    lines.append(f"| total patch_preflight_failures | **{pf}** |")
    lines.append(f"| total regeneration retries | **{regen}** |")
    lines.append(
        f"| sessions with ≥1 regen | **{len(regen_sessions)}/{N}** |"
    )
    lines.append(
        f"| regeneration recovery rate | **{_pct(recovered, len(regen_sessions))}** "
        f"(recovered / sessions-with-regen) |"
    )
    lines.append("")

    na = sum(1 for r in all_rows if r.get("patch_not_applicable"))
    after = sum(int(r.get("patch_apply_failures_after_preflight") or 0) for r in all_rows)
    lines.append("## 7–8. PATCH_NOT_APPLICABLE & after-preflight apply failures")
    lines.append("")
    lines.append(f"- PATCH_NOT_APPLICABLE sessions: **{na}/{N}**")
    lines.append(f"- total patch_apply_failures_after_preflight: **{after}**")
    lines.append("")

    repairs = [float(r.get("repair_attempts") or 0) for r in all_rows]
    calls = [float(r.get("model_call_count") or 0) for r in all_rows]
    durs = [float(r.get("duration_sec") or 0) for r in all_rows]
    lines.append("## 9. Repair attempts, model calls, duration")
    lines.append("")
    lines.append("| Metric | mean | sum |")
    lines.append("|---|---|---|")
    lines.append(
        f"| repair_attempts | {_mean(repairs):.2f} | {sum(repairs):.0f} |"
    )
    lines.append(f"| model_call_count | {_mean(calls):.2f} | {sum(calls):.0f} |")
    lines.append(f"| duration_sec | {_mean(durs):.1f} | {sum(durs):.1f} |")
    lines.append("")

    miss = sum(1 for r in all_rows if r.get("missing_required_changes"))
    unr = sum(1 for r in all_rows if r.get("unrelated_changes"))
    forb = sum(1 for r in all_rows if r.get("forbidden_changes"))
    lines.append("## 10. Required / unrelated / forbidden")
    lines.append("")
    lines.append(f"- missing_required_changes non-empty: {miss}/{N}")
    lines.append(f"- unrelated_changes non-empty: {unr}/{N}")
    lines.append(f"- forbidden_changes non-empty: {forb}/{N}")
    for r in all_rows:
        if r.get("missing_required_changes") or r.get("unrelated_changes") or r.get(
            "forbidden_changes"
        ):
            lines.append(
                f"  - round {r.get('round')} `{r.get('task_id')}`: "
                f"missing={r.get('missing_required_changes')}, "
                f"unrelated={r.get('unrelated_changes')}, "
                f"forbidden={r.get('forbidden_changes')}"
            )
    lines.append("")

    lines.append("## 11. vs v0.2 single-round Full12 (descriptive, not paired A/B)")
    lines.append("")
    v02_ok = sum(1 for v in V02_EVAL.values() if v)
    lines.append(
        f"v0.2 Full12 eval success was **{v02_ok}/12 ({100*v02_ok/12:.1f}%)** "
        f"(single round). v0.3 ×3 pooled eval is **{_pct(ev, N)}**."
    )
    lines.append("")
    lines.append("| task | v0.2 eval | v0.3 R1 | R2 | R3 |")
    lines.append("|---|---|---|---|---|")
    by_round = {rd["round"]: {r["task_id"]: r for r in rd["canonical_first_runs"]} for rd in rounds}
    for tid in sorted(V02_EVAL):
        cells = []
        for i in range(1, ROUNDS + 1):
            rr = by_round.get(i, {}).get(tid, {})
            cells.append("Y" if rr.get("eval_status") == "SUCCEEDED" else "N")
        lines.append(
            f"| {tid} | {'Y' if V02_EVAL[tid] else 'N'} | {cells[0]} | {cells[1]} | {cells[2]} |"
        )
    lines.append("")
    lines.append(
        "Sampling differs across rounds and from v0.2; do not treat deltas as causal "
        "proof that preflight alone fixed bench01/05."
    )
    lines.append("")

    lines.append("## 12. Caveat")
    lines.append("")
    lines.append(
        "n=36 fixed micro-tasks under one model, one product tag, and one Docker image "
        "**cannot prove production readiness**, generalization, or robustness beyond "
        "this freeze."
    )
    lines.append("")

    REPORT_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"Wrote {REPORT_PATH}", flush=True)


def main() -> int:
    X3_DIR.mkdir(parents=True, exist_ok=True)
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    tasks = list(manifest["tasks"])
    task_ids = [t["task_id"] for t in tasks]

    freeze = record_freeze(task_ids)
    freeze["experiment"] = "full12_v03_x3"
    freeze["rounds"] = ROUNDS
    freeze["notes"] = [
        "v0.3 Full12 × 3 — real DeepSeek, no dry-run/reference.",
        "First results permanent; no overwrite of model failures.",
        "No product/Prompt/task edits between rounds.",
    ]
    (X3_DIR / "freeze.json").write_text(
        json.dumps(freeze, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    print(
        json.dumps(
            {
                k: freeze[k]
                for k in (
                    "started_at",
                    "product_tag",
                    "product_commit",
                    "benchmark_commit",
                    "manifest_sha256",
                    "system_prompt_sha256",
                    "model",
                    "base_url",
                    "model_params",
                    "docker_image",
                )
                if k in freeze
            },
            indent=2,
        ),
        flush=True,
    )
    if not freeze.get("docker_preflight_ok"):
        print("ERROR docker", freeze.get("docker_preflight_reason"), file=sys.stderr)
        return 4

    rounds_out: list[dict] = []
    for round_no in range(1, ROUNDS + 1):
        print(f"\n######## ROUND {round_no}/{ROUNDS} ########", flush=True)
        round_dir = X3_DIR / f"round-{round_no}"
        round_dir.mkdir(parents=True, exist_ok=True)
        rows: list[dict] = []
        all_runs: list[dict] = []
        for task in tasks:
            row = run_one(task, freeze=freeze)
            row["round"] = round_no
            row = _enrich(row)
            # Env retry: keep original; optional second run stored separately.
            if row.get("failure_class") in {
                "TEST_ENVIRONMENT_ERROR",
                "TEST_TIMEOUT",
            } or row.get("eval_status") in {
                "TEST_ENVIRONMENT_ERROR",
                "HIDDEN_TEST_ENVIRONMENT_ERROR",
                "HIDDEN_TEST_TIMEOUT",
            }:
                print(
                    f"ENV on {task['task_id']} round {round_no} — independent rerun "
                    f"(original kept)",
                    flush=True,
                )
                rerun = run_one(
                    task,
                    freeze=freeze,
                    rerun_reason=f"env_or_timeout_retry_of_{row.get('run_id')}",
                )
                rerun["round"] = round_no
                rerun = _enrich(rerun)
                all_runs.append(rerun)
            all_runs.append(row)
            rows.append(row)
            (Path(row["result_path"]) / "x3_metrics.json").write_text(
                json.dumps(row, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
            )
            print(
                json.dumps(
                    {
                        "round": round_no,
                        "task_id": row.get("task_id"),
                        "eval_status": row.get("eval_status"),
                        "preflight_fail": row.get("patch_preflight_failures"),
                        "regen": row.get("total_patch_regeneration_retries"),
                        "repair": row.get("repair_attempts"),
                    },
                    ensure_ascii=False,
                ),
                flush=True,
            )

        drift_round = verify_freeze(freeze, task_ids)
        round_index = {
            "round": round_no,
            "freeze_drift_errors": drift_round,
            "canonical_first_runs": rows,
            "all_runs": all_runs,
        }
        (round_dir / "runs_index.json").write_text(
            json.dumps(round_index, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        rounds_out.append(round_index)

    drift = verify_freeze(freeze, task_ids)
    index = {
        "experiment": "full12_v03_x3",
        "freeze_path": str(X3_DIR / "freeze.json"),
        "freeze_drift_errors": drift,
        "rounds": rounds_out,
        "finished_at": _utc(),
    }
    (X3_DIR / "runs_index.json").write_text(
        json.dumps(index, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    write_report(freeze=freeze, rounds=rounds_out, drift=drift)
    return 3 if drift else 0


if __name__ == "__main__":
    raise SystemExit(main())
