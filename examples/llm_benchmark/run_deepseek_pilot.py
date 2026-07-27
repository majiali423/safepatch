"""Real DeepSeek formal runner for the 12 frozen LLM benchmark tasks.

- Does NOT modify code_agent/ or task assets.
- Does NOT use dry-run or reference patches.
- First results are permanent; env-failure reruns keep the original run.
- Writes freeze + runs index under results/full12_deepseek/ and FULL12_REPORT.md.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BENCH = Path(__file__).resolve().parent
RESULTS = BENCH / "results"
FULL12_DIR = RESULTS / "full12_deepseek"
MANIFEST_PATH = BENCH / "benchmark_manifest.json"
REPORT_PATH = BENCH / "FULL12_REPORT.md"
PILOT_TASKS = {
    "bench01_div_zero",
    "bench05_red_herring",
    "bench06_split_total",
    "bench12_slug_overfit",
}

sys.path.insert(0, str(ROOT))
from code_agent.envfile import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env")

from code_agent.eval.hidden import (  # noqa: E402
    discover_hidden_dir,
    evaluate_after_product,
    run_hidden_tests,
)
from code_agent.llm import SYSTEM_PROMPT  # noqa: E402
from code_agent.runtime.docker_pytest import DockerPytestRunner  # noqa: E402

from metrics_lib import (  # noqa: E402
    artifact_is_consistent,
    build_run_metrics,
    resolve_eval_status,
)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _run_id() -> str:
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"{ts}_{uuid.uuid4().hex[:8]}"


def _git_head() -> str:
    return subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
    ).strip()


def _git_tag() -> str:
    try:
        return subprocess.check_output(
            ["git", "describe", "--tags", "--always"], cwd=ROOT, text=True
        ).strip()
    except subprocess.CalledProcessError:
        return "unknown"


def _docker_image_info(image: str = "code-agent-pytest:local") -> dict:
    proc = subprocess.run(
        ["docker", "image", "inspect", image, "--format", "{{.Id}}"],
        capture_output=True,
        text=True,
        check=False,
    )
    digest = (proc.stdout or "").strip() if proc.returncode == 0 else None
    return {"name": image, "id": digest, "inspect_ok": proc.returncode == 0}


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    h.update(path.read_bytes())
    return h.hexdigest()


def _asset_fingerprints(task_ids: list[str]) -> dict:
    """Hash public/hidden/reference assets to prove freeze during the run."""
    out: dict[str, str] = {}
    for tid in task_ids:
        for base in (BENCH / tid, BENCH / f"{tid}.hidden", BENCH / "references" / tid):
            if not base.exists():
                continue
            for path in sorted(base.rglob("*")):
                if path.is_file():
                    rel = path.relative_to(BENCH).as_posix()
                    out[rel] = _sha256_file(path)
    # product package fingerprint (shallow: all .py under code_agent)
    for path in sorted((ROOT / "code_agent").rglob("*.py")):
        rel = path.relative_to(ROOT).as_posix()
        out[rel] = _sha256_file(path)
    return out


def _count_reads(trace_path: Path) -> int:
    n = 0
    if not trace_path.exists():
        return 0
    for line in trace_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        ev = json.loads(line)
        if ev.get("event") != "tool_call":
            continue
        tool = ev.get("payload", {}).get("tool")
        if tool in {
            "read_file",
            "list_tree",
            "search_text",
            "search_symbol",
            "get_repo_map",
        }:
            n += 1
    return n


def _count_policy_rejections(trace_path: Path) -> int:
    n = 0
    if not trace_path.exists():
        return 0
    for line in trace_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        ev = json.loads(line)
        if ev.get("event") != "tool_result":
            continue
        payload = ev.get("payload") or {}
        if payload.get("tool") != "propose_patch":
            continue
        if payload.get("ok") is False:
            err = str(payload.get("error") or "")
            if "validation" in err.lower() or "forbidden" in err.lower() or "Patch validation":
                n += 1
            elif payload.get("validation", {}).get("ok") is False:
                n += 1
            else:
                n += 1
    return n


def _model_params_freeze() -> dict:
    """Record configured model params; unset knobs → provider_default.

    Product LLMClient hardcodes temperature=0.1; that is recorded as used.
    Env overrides (if any) win for documentation of this run.
    """
    env_temp = os.environ.get("OPENAI_TEMPERATURE") or os.environ.get(
        "CODE_AGENT_TEMPERATURE"
    )
    return {
        "temperature": env_temp if env_temp is not None else 0.1,
        "temperature_source": "env" if env_temp is not None else "product_hardcode",
        "max_tokens": os.environ.get("OPENAI_MAX_TOKENS")
        or os.environ.get("CODE_AGENT_MAX_TOKENS")
        or "provider_default",
        "top_p": os.environ.get("OPENAI_TOP_P")
        or os.environ.get("CODE_AGENT_TOP_P")
        or "provider_default",
        "frequency_penalty": "provider_default",
        "presence_penalty": "provider_default",
    }


def record_freeze(task_ids: list[str]) -> dict:
    runner = DockerPytestRunner()
    ok, reason = runner.preflight()
    freeze = {
        "started_at": _utc_now(),
        "product_tag": _git_tag(),
        "product_commit": _git_head(),
        "benchmark_tag": _git_tag(),
        "benchmark_commit": _git_head(),
        "manifest_path": str(MANIFEST_PATH.relative_to(ROOT)).replace("\\", "/"),
        "manifest_sha256": _sha256_file(MANIFEST_PATH),
        "system_prompt_sha256": hashlib.sha256(
            SYSTEM_PROMPT.encode("utf-8")
        ).hexdigest(),
        "model": os.environ.get("CODE_AGENT_MODEL")
        or os.environ.get("OPENAI_MODEL")
        or "unknown",
        "base_url": os.environ.get("OPENAI_BASE_URL")
        or os.environ.get("CODE_AGENT_BASE_URL")
        or "",
        "model_params": _model_params_freeze(),
        # Never record API keys.
        "api_key_present": bool(
            os.environ.get("OPENAI_API_KEY") or os.environ.get("CODE_AGENT_API_KEY")
        ),
        "docker_preflight_ok": ok,
        "docker_preflight_reason": reason,
        "docker_image": _docker_image_info(),
        "python_version": sys.version.replace("\n", " "),
        "asset_fingerprints": _asset_fingerprints(task_ids),
        "notes": [
            "Real DeepSeek full-12 — no dry-run, no reference patches.",
            "First results (run-001 semantics) are permanent; model failures are not overwritten.",
            "infra01_format_retry excluded from LLM rates.",
            "API key present but never recorded.",
        ],
    }
    FULL12_DIR.mkdir(parents=True, exist_ok=True)
    path = FULL12_DIR / "freeze.json"
    path.write_text(json.dumps(freeze, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return freeze


def _count_context_mismatches(trace_path: Path) -> int:
    n = 0
    if not trace_path.exists():
        return 0
    for line in trace_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        ev = json.loads(line)
        if ev.get("event") != "patch_applied":
            continue
        payload = ev.get("payload") or {}
        if payload.get("ok") is False:
            err = str(payload.get("error") or "")
            if "Context mismatch" in err or "Deletion mismatch" in err:
                n += 1
    return n


def verify_freeze(freeze: dict, task_ids: list[str]) -> list[str]:
    errors: list[str] = []
    now = _asset_fingerprints(task_ids)
    before = freeze.get("asset_fingerprints") or {}
    for rel, digest in before.items():
        if now.get(rel) != digest:
            errors.append(f"asset changed during run: {rel}")
    for rel in now:
        if rel not in before and rel.startswith("code_agent/"):
            errors.append(f"new product file appeared: {rel}")
    return errors


def run_one(
    task: dict,
    *,
    freeze: dict,
    rerun_reason: str | None = None,
    replaces_run_id: str | None = None,
) -> dict:
    from code_agent.cli import main as cli_main

    task_id = task["task_id"]
    task_dir = BENCH / task_id
    description = (task_dir / "TASK.txt").read_text(encoding="utf-8").strip()
    run_id = _run_id()
    out_dir = RESULTS / task_id / run_id
    out_dir.mkdir(parents=True, exist_ok=False)
    session_base = RESULTS / "_sessions" / task_id / run_id
    session_base.mkdir(parents=True, exist_ok=False)

    started = time.time()
    meta = {
        "task_id": task_id,
        "run_id": run_id,
        "rerun_reason": rerun_reason,
        "replaces_run_id": replaces_run_id,
        "is_first_result": replaces_run_id is None and rerun_reason is None,
        "model": freeze["model"],
        "base_url": freeze["base_url"],
        "product_commit": freeze["product_commit"],
        "started_at": _utc_now(),
    }
    (out_dir / "run_meta.json").write_text(
        json.dumps(meta, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )

    print(f"\n===== REAL LLM {task_id} run_id={run_id} =====", flush=True)

    # 1) Baseline must fail (product also runs baseline; we record an explicit check).
    runner = DockerPytestRunner()
    ok, reason = runner.preflight()
    if not ok:
        metrics = {
            "task_id": task_id,
            "run_id": run_id,
            "product_status": "TEST_ENVIRONMENT_ERROR",
            "public_pass": False,
            "hidden_pass": None,
            "eval_status": "TEST_ENVIRONMENT_ERROR",
            "failure_class": "TEST_ENVIRONMENT_ERROR",
            "benchmark_success": False,
            "rerun_reason": rerun_reason or "docker_preflight_failed",
            "stop_reason": reason,
            "session_path": str(session_base),
            "result_path": str(out_dir),
            "duration_sec": time.time() - started,
        }
        (out_dir / "metrics.json").write_text(
            json.dumps(metrics, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        return metrics

    baseline = runner.run_pytest(task_dir, log_path=out_dir / "baseline_precheck.log")
    if baseline.exit_code == 0:
        metrics = {
            "task_id": task_id,
            "run_id": run_id,
            "product_status": "ARTIFACT_INCONSISTENT",
            "public_pass": False,
            "hidden_pass": None,
            "eval_status": "ARTIFACT_INCONSISTENT",
            "failure_class": "ARTIFACT_INCONSISTENT",
            "benchmark_success": False,
            "stop_reason": "baseline_unexpectedly_passed_before_agent",
            "rerun_reason": rerun_reason,
            "session_path": str(session_base),
            "result_path": str(out_dir),
            "duration_sec": time.time() - started,
        }
        (out_dir / "metrics.json").write_text(
            json.dumps(metrics, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        return metrics

    argv = [
        str(task_dir),
        description,
        "--yes",
        "--session-base",
        str(session_base),
    ]
    exit_code = cli_main(argv)

    sessions = sorted(
        (p for p in session_base.iterdir() if p.is_dir()),
        key=lambda p: p.stat().st_mtime,
    )
    if not sessions:
        metrics = {
            "task_id": task_id,
            "run_id": run_id,
            "product_status": "ERROR",
            "public_pass": False,
            "hidden_pass": None,
            "eval_status": "PUBLIC_TESTS_FAILED",
            "failure_class": "PUBLIC_TESTS_FAILED",
            "benchmark_success": False,
            "stop_reason": "no_session_created",
            "cli_exit_code": exit_code,
            "rerun_reason": rerun_reason,
            "session_path": str(session_base),
            "result_path": str(out_dir),
            "duration_sec": time.time() - started,
        }
        (out_dir / "metrics.json").write_text(
            json.dumps(metrics, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        return metrics

    session = sessions[-1]
    artifacts = session / "artifacts"
    for name in (
        "final.diff",
        "summary.json",
        "trace.jsonl",
        "baseline.log",
        "repo_map.json",
    ):
        src = artifacts / name
        if src.exists():
            shutil.copy2(src, out_dir / name)
    for log in artifacts.glob("attempt-*.log"):
        shutil.copy2(log, out_dir / log.name)

    summary = {}
    if (out_dir / "summary.json").exists():
        summary = json.loads((out_dir / "summary.json").read_text(encoding="utf-8"))

    # Consistency gate before hidden.
    ran_hidden = False
    hidden_pass = None
    hidden_eval_status = None
    if artifact_is_consistent(summary) and summary.get("final_tests_passed"):
        # Product SUCCEEDED + final_tests_passed — run hidden on working_copy.
        hidden_dir = discover_hidden_dir(task_dir)
        working_copy = session / "working_copy"
        if hidden_dir is not None and working_copy.is_dir():
            ran_hidden = True
            hidden_result = run_hidden_tests(
                working_copy=working_copy,
                hidden_tests_dir=hidden_dir,
                results_dir=out_dir,
                runner=runner,
            )
            hidden_pass = hidden_result.get("hidden_pass")
            hidden_eval_status = hidden_result.get("eval_status")
        else:
            # Fall back to evaluate_after_product helper for bookkeeping.
            hidden_result = evaluate_after_product(
                product_summary=summary,
                working_copy=working_copy,
                task_dir=task_dir,
                results_dir=out_dir,
                runner=runner,
            )
            # evaluate_after_product may have its own public_pass from status;
            # we still override eval_status via resolve_eval_status below.
            hidden_pass = hidden_result.get("hidden_pass")
            hidden_eval_status = hidden_result.get("eval_status")
            ran_hidden = hidden_dir is not None
    elif not artifact_is_consistent(summary):
        (out_dir / "eval_trace.jsonl").write_text(
            json.dumps(
                {
                    "ts": _utc_now(),
                    "event": "artifact_inconsistent",
                    "payload": {
                        "product_status": summary.get("status"),
                        "final_tests_passed": summary.get("final_tests_passed"),
                    },
                },
                ensure_ascii=False,
            )
            + "\n",
            encoding="utf-8",
        )

    eval_status = resolve_eval_status(
        summary=summary,
        hidden_pass=hidden_pass,
        hidden_eval_status=hidden_eval_status,
        ran_hidden=ran_hidden and task.get("has_hidden_tests", True),
    )

    reads = _count_reads(out_dir / "trace.jsonl")
    policy_rej = _count_policy_rejections(out_dir / "trace.jsonl")
    ctx_mismatches = _count_context_mismatches(out_dir / "trace.jsonl")
    metrics = build_run_metrics(
        task=task,
        summary=summary,
        hidden_pass=hidden_pass,
        eval_status=eval_status,
        duration_sec=time.time() - started,
        read_actions=reads,
        policy_rejections=policy_rej,
        trace_path=out_dir / "trace.jsonl",
        extra={
            "run_id": run_id,
            "rerun_reason": rerun_reason,
            "replaces_run_id": replaces_run_id,
            "is_first_result": replaces_run_id is None and rerun_reason is None,
            "cli_exit_code": exit_code,
            "session_path": str(session),
            "result_path": str(out_dir),
            "model": freeze["model"],
            "base_url": freeze["base_url"],
            "product_commit": freeze["product_commit"],
            "mode": "real_llm_deepseek_full12",
            "real_llm": True,
            "patch_context_mismatches": ctx_mismatches,
        },
    )
    (out_dir / "metrics.json").write_text(
        json.dumps(metrics, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    print(json.dumps(metrics, indent=2, ensure_ascii=False), flush=True)
    return metrics


def _pct(num: int, den: int) -> str:
    if den == 0:
        return "n/a"
    return f"{num}/{den} ({100.0 * num / den:.1f}%)"


def _avg(vals: list[float | int]) -> float | None:
    if not vals:
        return None
    return sum(vals) / len(vals)


def _load_pilot_first_rows() -> list[dict]:
    idx = RESULTS / "pilot_deepseek" / "runs_index.json"
    if not idx.exists():
        return []
    data = json.loads(idx.read_text(encoding="utf-8"))
    return list(data.get("canonical_first_runs") or [])


def write_full12_report(*, freeze: dict, first: list[dict], all_runs: list[dict], drift: list[str]) -> None:
    n = len(first)
    public_ok = sum(1 for r in first if r.get("public_pass"))
    hidden_ok = sum(1 for r in first if r.get("hidden_pass") is True)
    eval_ok = sum(1 for r in first if r.get("eval_status") == "SUCCEEDED")
    pub_1st = sum(1 for r in first if r.get("public_first_attempt_success"))
    eval_1st = sum(1 for r in first if r.get("eval_first_attempt_success"))
    first_applicable = [r for r in first if r.get("first_patch_applicable") is not None]
    first_app_ok = sum(1 for r in first_applicable if r.get("first_patch_applicable"))
    repairs = [int(r.get("repair_attempts") or 0) for r in first]
    reads = [int(r.get("read_actions") or 0) for r in first]
    fmt = sum(int(r.get("format_retries") or 0) for r in first)
    pol = sum(int(r.get("policy_rejections") or 0) for r in first)
    ctx = sum(int(r.get("patch_context_mismatches") or 0) for r in first)
    apply_fail = sum(int(r.get("patch_apply_failures") or 0) for r in first)
    apply_tot = sum(int(r.get("patch_proposals") or 0) for r in first)
    miss = sum(1 for r in first if r.get("missing_required_changes"))
    unr = sum(1 for r in first if r.get("unrelated_changes"))
    forb = sum(1 for r in first if r.get("forbidden_changes"))

    env_reruns = [r for r in all_runs if r.get("rerun_reason")]

    by_class: dict[str, list[str]] = {}
    for r in first:
        if r.get("eval_status") == "SUCCEEDED":
            continue
        cls = str(r.get("failure_class") or r.get("eval_status") or "UNKNOWN")
        by_class.setdefault(cls, []).append(str(r.get("task_id")))

    groups = ("basic", "medium", "reliability")
    group_rows: list[str] = []
    for g in groups:
        subset = [r for r in first if r.get("difficulty") == g]
        gn = len(subset)
        group_rows.append(
            "| {g} | {n} | {pub} | {hid} | {ev} | {p1} | {e1} | {rep} | {rd} |".format(
                g=g,
                n=gn,
                pub=_pct(sum(1 for r in subset if r.get("public_pass")), gn),
                hid=_pct(sum(1 for r in subset if r.get("hidden_pass") is True), gn),
                ev=_pct(sum(1 for r in subset if r.get("eval_status") == "SUCCEEDED"), gn),
                p1=_pct(sum(1 for r in subset if r.get("public_first_attempt_success")), gn),
                e1=_pct(sum(1 for r in subset if r.get("eval_first_attempt_success")), gn),
                rep=f"{_avg([int(r.get('repair_attempts') or 0) for r in subset]) or 0:.2f}",
                rd=f"{_avg([int(r.get('read_actions') or 0) for r in subset]) or 0:.2f}",
            )
        )

    pilot = _load_pilot_first_rows()
    pilot_by_id = {r.get("task_id"): r for r in pilot}
    full_pilot_subset = [r for r in first if r.get("task_id") in PILOT_TASKS]
    pilot_cmp_lines = []
    for tid in sorted(PILOT_TASKS):
        p = pilot_by_id.get(tid)
        f = next((r for r in first if r.get("task_id") == tid), None)
        if not p or not f:
            pilot_cmp_lines.append(f"| {tid} | (missing pilot or full12) | | |")
            continue
        pilot_cmp_lines.append(
            "| {tid} | {pe} / {pp1} | {fe} / {fp1} | {pa}→{fa} attempts |".format(
                tid=tid,
                pe=p.get("eval_status"),
                pp1=p.get("eval_first_attempt_success"),
                fe=f.get("eval_status"),
                fp1=f.get("eval_first_attempt_success"),
                pa=p.get("repair_attempts"),
                fa=f.get("repair_attempts"),
            )
        )

    img = freeze.get("docker_image") or {}
    params = freeze.get("model_params") or {}
    finished = _utc_now()

    lines: list[str] = []
    lines.append("# Full-12 DeepSeek Formal Evaluation Report")
    lines.append("")
    lines.append(
        "**Mode:** real LLM (`deepseek-chat`) — **not** dry-run, **not** reference patch."
    )
    lines.append(
        "**Sample size:** n=12. **This does not prove production readiness.**"
    )
    lines.append(
        "`infra01_format_retry` is excluded from all rates below."
    )
    lines.append("")
    lines.append("## Freeze record")
    lines.append("")
    lines.append("| Field | Value |")
    lines.append("|---|---|")
    lines.append(f"| started_at (UTC) | `{freeze.get('started_at')}` |")
    lines.append(f"| finished_at (UTC) | `{finished}` |")
    lines.append(f"| product_tag | `{freeze.get('product_tag')}` |")
    lines.append(f"| product_commit | `{freeze.get('product_commit')}` |")
    lines.append(f"| benchmark_tag | `{freeze.get('benchmark_tag')}` |")
    lines.append(f"| benchmark_commit | `{freeze.get('benchmark_commit')}` |")
    lines.append(f"| manifest_sha256 | `{freeze.get('manifest_sha256')}` |")
    lines.append(f"| system_prompt_sha256 | `{freeze.get('system_prompt_sha256')}` |")
    lines.append(f"| model | `{freeze.get('model')}` |")
    lines.append(f"| base_url | `{freeze.get('base_url')}` |")
    lines.append(
        f"| model_params.temperature | `{params.get('temperature')}` "
        f"({params.get('temperature_source')}) |"
    )
    lines.append(f"| model_params.max_tokens | `{params.get('max_tokens')}` |")
    lines.append(f"| model_params.top_p | `{params.get('top_p')}` |")
    lines.append("| API key | present (value **not** recorded) |")
    lines.append(f"| Docker image | `{img.get('name')}` |")
    lines.append(f"| Docker image id/digest | `{img.get('id')}` |")
    lines.append(f"| Python | `{freeze.get('python_version')}` |")
    lines.append(
        f"| Freeze drift after run | "
        f"{'**none**' if not drift else '**DRIFT**: ' + '; '.join(drift)} |"
    )
    lines.append("")
    lines.append(f"Source: `{FULL12_DIR.as_posix()}/freeze.json`")
    lines.append("")
    lines.append("## 1. Per-task results (canonical run-001 / first result)")
    lines.append("")
    lines.append(
        "| task_id | product_status | public_pass | hidden_pass | eval_status | "
        "public_1st | eval_1st | repair_attempts | format_retries | policy_rej | "
        "reads | patch_proposals | patch_apply_fail | patch_apply_rate | "
        "first_patch_ok | changed | missing_req | unrelated | forbidden | "
        "duration_s | stop_reason | rerun_reason | session_path | result_path |"
    )
    lines.append("|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|")
    for r in first:
        rate = r.get("patch_apply_success_rate")
        rate_s = "n/a" if rate is None else f"{rate:.2f}"
        lines.append(
            "| {tid} | {ps} | {pp} | {hp} | {es} | {p1} | {e1} | {ra} | {fr} | {pr} | "
            "{rd} | {prop} | {paf} | {rate} | {fpa} | {ch} | {miss} | {unr} | {forb} | "
            "{dur} | {sr} | {rr} | `{sp}` | `{rp}` |".format(
                tid=r.get("task_id"),
                ps=r.get("product_status"),
                pp=r.get("public_pass"),
                hp=r.get("hidden_pass"),
                es=r.get("eval_status"),
                p1=r.get("public_first_attempt_success"),
                e1=r.get("eval_first_attempt_success"),
                ra=r.get("repair_attempts"),
                fr=r.get("format_retries"),
                pr=r.get("policy_rejections"),
                rd=r.get("read_actions"),
                prop=r.get("patch_proposals"),
                paf=r.get("patch_apply_failures"),
                rate=rate_s,
                fpa=r.get("first_patch_applicable"),
                ch=", ".join(r.get("changed_files") or []) or "—",
                miss=r.get("missing_required_changes") or [],
                unr=r.get("unrelated_changes") or [],
                forb=r.get("forbidden_changes") or [],
                dur=f"{float(r.get('duration_sec') or 0):.1f}",
                sr=r.get("stop_reason"),
                rr=r.get("rerun_reason") or "—",
                sp=r.get("session_path"),
                rp=r.get("result_path"),
            )
        )
    lines.append("")
    lines.append(
        "Artifacts: `examples/llm_benchmark/results/<task_id>/<run_id>/` "
        "(`metrics.json`, `summary.json`, `final.diff`, `trace.jsonl`, …)."
    )
    lines.append("")
    lines.append("## 2–3. Aggregate success rates (n=12, first runs only)")
    lines.append("")
    lines.append("| Metric | Value |")
    lines.append("|---|---|")
    lines.append(f"| public pass rate | **{_pct(public_ok, n)}** |")
    lines.append(f"| hidden pass rate | **{_pct(hidden_ok, n)}** |")
    lines.append(f"| eval success (`eval_status==SUCCEEDED`) | **{_pct(eval_ok, n)}** |")
    lines.append(f"| public first-attempt success | **{_pct(pub_1st, n)}** |")
    lines.append(f"| eval first-attempt success | **{_pct(eval_1st, n)}** |")
    lines.append("")
    lines.append("## 4. First patch applicability")
    lines.append("")
    lines.append("| Metric | Value |")
    lines.append("|---|---|")
    lines.append(
        f"| first_patch_applicable among tasks with ≥1 apply | "
        f"**{_pct(first_app_ok, len(first_applicable))}** |"
    )
    lines.append(
        f"| patch apply success (all approved applies) | "
        f"**{_pct(apply_tot - apply_fail, apply_tot) if apply_tot else 'n/a'}** "
        f"(failures={apply_fail}, proposals_reaching_applier={apply_tot}) |"
    )
    lines.append(
        "Denominator for `patch_apply_success_rate` = approved proposals that "
        "invoked PatchApplier (`patch_applied` events); policy-layer rejections excluded."
    )
    lines.append("")
    lines.append("## 5. Average repair attempts & read actions")
    lines.append("")
    lines.append("| Metric | Value |")
    lines.append("|---|---|")
    lines.append(f"| mean repair_attempts | **{_avg(repairs):.2f}** |" if repairs else "| mean repair_attempts | n/a |")
    lines.append(f"| mean read_actions | **{_avg(reads):.2f}** |" if reads else "| mean read_actions | n/a |")
    lines.append("")
    lines.append("## 6. Format retry / policy rejection")
    lines.append("")
    lines.append("| Metric | Value |")
    lines.append("|---|---|")
    lines.append(f"| total format_retries | **{fmt}** |")
    lines.append(f"| total policy_rejections | **{pol}** |")
    lines.append("")
    lines.append("## 7. Patch context mismatch statistics")
    lines.append("")
    lines.append("| Metric | Value |")
    lines.append("|---|---|")
    lines.append(
        f"| apply failures with Context/Deletion mismatch | **{ctx}** "
        f"(across {n} first runs) |"
    )
    lines.append(
        f"| tasks with ≥1 context mismatch | "
        f"**{sum(1 for r in first if int(r.get('patch_context_mismatches') or 0) > 0)}/{n}** |"
    )
    lines.append("")
    lines.append(
        "Per Pilot audit (`PATCH_MISMATCH_AUDIT.md`), such mismatches are classified "
        "as model `hallucinated_context`; PatchApplier rejection is expected unified-diff behavior."
    )
    lines.append("")
    lines.append("## 8. Required / unrelated / forbidden changes")
    lines.append("")
    lines.append("| Metric | Tasks affected |")
    lines.append("|---|---|")
    lines.append(f"| missing_required_changes non-empty | {miss}/{n} |")
    lines.append(f"| unrelated_changes non-empty | {unr}/{n} |")
    lines.append(f"| forbidden_changes non-empty | {forb}/{n} |")
    lines.append("")
    for r in first:
        if r.get("missing_required_changes") or r.get("unrelated_changes") or r.get("forbidden_changes"):
            lines.append(
                f"- `{r.get('task_id')}`: missing={r.get('missing_required_changes')}, "
                f"unrelated={r.get('unrelated_changes')}, forbidden={r.get('forbidden_changes')}"
            )
    if miss + unr + forb == 0:
        lines.append("No missing/unrelated/forbidden file-set violations on first runs.")
    lines.append("")
    lines.append("## 9. Failure root-cause classification (first runs)")
    lines.append("")
    if not by_class:
        lines.append("No benchmark failures (`eval_status==SUCCEEDED` for all 12).")
    else:
        lines.append("| failure_class | tasks |")
        lines.append("|---|---|")
        for cls, tids in sorted(by_class.items()):
            lines.append(f"| {cls} | {', '.join(tids)} |")
    lines.append("")
    if env_reruns:
        lines.append("### Environment-fault reruns (excluded from model-ability rates)")
        lines.append("")
        for r in env_reruns:
            lines.append(
                f"- `{r.get('task_id')}` run `{r.get('run_id')}` "
                f"rerun_reason=`{r.get('rerun_reason')}` "
                f"eval_status=`{r.get('eval_status')}`"
            )
        lines.append("")
    else:
        lines.append("No API/Docker environment-fault reruns.")
        lines.append("")
    lines.append("## 10. Difficulty group comparison")
    lines.append("")
    lines.append(
        "| group | n | public | hidden | eval | public_1st | eval_1st | "
        "mean_attempts | mean_reads |"
    )
    lines.append("|---|---|---|---|---|---|---|---|---|")
    lines.extend(group_rows)
    lines.append("")
    lines.append("## 11. Comparison with 4-task Pilot")
    lines.append("")
    lines.append(
        f"Pilot subset in this full-12: "
        f"eval {_pct(sum(1 for r in full_pilot_subset if r.get('eval_status')=='SUCCEEDED'), len(full_pilot_subset))} "
        f"(Pilot historical: see `PILOT_REPORT.md`)."
    )
    lines.append("")
    lines.append("| task_id | Pilot eval / eval_1st | Full12 eval / eval_1st | attempts Pilot→Full12 |")
    lines.append("|---|---|---|---|")
    lines.extend(pilot_cmp_lines)
    lines.append("")
    lines.append(
        "Pilot and Full12 are independent real-LLM runs; scores are not averaged across runs. "
        "Main report rates use only this Full12 run-001 set."
    )
    lines.append("")
    lines.append("## 12. Caveat — n=12 is not production proof")
    lines.append("")
    lines.append(
        "This formal evaluation covers **12** fixed micro-tasks under one model "
        "(`deepseek-chat`), one product commit, and one Docker image. "
        "**n=12 cannot prove production readiness**, generalization to arbitrary "
        "repositories, or robustness under adversarial prompts, large diffs, or "
        "non-Python stacks. Treat rates as descriptive evidence for this freeze only."
    )
    lines.append("")

    REPORT_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"Wrote report: {REPORT_PATH}", flush=True)


def main() -> int:
    RESULTS.mkdir(parents=True, exist_ok=True)
    FULL12_DIR.mkdir(parents=True, exist_ok=True)
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    tasks = list(manifest["tasks"])
    task_ids = [t["task_id"] for t in tasks]
    excluded = set(manifest.get("infra_tasks_excluded_from_llm_rates") or [])
    tasks = [t for t in tasks if t["task_id"] not in excluded]
    task_ids = [t["task_id"] for t in tasks]

    if not (
        os.environ.get("OPENAI_API_KEY") or os.environ.get("CODE_AGENT_API_KEY")
    ):
        print("ERROR: missing API key in .env", file=sys.stderr)
        return 2

    freeze = record_freeze(task_ids)
    print(
        "FREEZE:\n"
        + json.dumps(
            {
                k: freeze[k]
                for k in (
                    "started_at",
                    "product_tag",
                    "product_commit",
                    "benchmark_tag",
                    "benchmark_commit",
                    "manifest_sha256",
                    "system_prompt_sha256",
                    "model",
                    "base_url",
                    "model_params",
                    "docker_image",
                    "python_version",
                    "api_key_present",
                )
                if k in freeze
            },
            indent=2,
            ensure_ascii=False,
        ),
        flush=True,
    )
    if not freeze["docker_preflight_ok"]:
        print(
            f"ERROR: Docker unavailable: {freeze['docker_preflight_reason']}",
            file=sys.stderr,
        )
        return 4

    rows: list[dict] = []
    for task in tasks:
        row = run_one(task, freeze=freeze)
        rows.append(row)
        # Env-failure may be retried once; keep original + annotate.
        if row.get("failure_class") in {
            "TEST_ENVIRONMENT_ERROR",
            "TEST_TIMEOUT",
        } or row.get("eval_status") in {
            "TEST_ENVIRONMENT_ERROR",
            "HIDDEN_TEST_ENVIRONMENT_ERROR",
            "HIDDEN_TEST_TIMEOUT",
        }:
            print(
                f"ENV/TIMEOUT on {task['task_id']} — scheduling independent rerun "
                f"(original run_id={row.get('run_id')} kept)",
                flush=True,
            )
            rerun = run_one(
                task,
                freeze=freeze,
                rerun_reason=f"env_or_timeout_retry_of_{row.get('run_id')}",
                replaces_run_id=None,  # must NOT replace first result
            )
            rerun["original_run_id"] = row.get("run_id")
            # First result stays canonical for rates; rerun stored separately.
            row["env_rerun"] = {
                "run_id": rerun.get("run_id"),
                "eval_status": rerun.get("eval_status"),
                "failure_class": rerun.get("failure_class"),
            }
            rows.append(rerun)

    drift = verify_freeze(freeze, task_ids)
    # Prefer first results for aggregate rates.
    first = []
    seen = set()
    for r in rows:
        tid = r.get("task_id")
        if tid in seen:
            continue
        if r.get("rerun_reason"):
            continue
        seen.add(tid)
        first.append(r)
    report = {
        "real_llm_runs": True,
        "freeze_path": str(FULL12_DIR / "freeze.json"),
        "freeze_drift_errors": drift,
        "tasks_first": first,
        "canonical_first_runs": first,
        "all_runs": rows,
        "report_path": str(REPORT_PATH),
    }
    (FULL12_DIR / "runs_index.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    write_full12_report(freeze=freeze, first=first, all_runs=rows, drift=drift)
    if drift:
        print("WARNING: freeze drift detected:", drift, flush=True)
        return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
