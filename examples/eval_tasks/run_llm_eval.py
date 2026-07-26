"""Run 5 real-LLM eval tasks and collect metrics.

Requires OPENAI_API_KEY or CODE_AGENT_API_KEY.
Uses product Docker Runtime (not LocalPytestRunner).
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TASKS_ROOT = Path(__file__).resolve().parent
RESULTS = TASKS_ROOT / "results"

# Make package importable and load project-root .env if present.
sys.path.insert(0, str(ROOT))
from code_agent.envfile import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env")


# Expected minimal fix targets (for unrelated-change detection).
TASKS = [
    {
        "id": "task01_zero_div",
        "expected_files": {"calculator.py"},
        "allow_test_edits": False,
    },
    {
        "id": "task02_boundary",
        "expected_files": {"age.py"},
        "allow_test_edits": False,
    },
    {
        "id": "task03_signature",
        "expected_files": {"user_service.py"},
        "allow_test_edits": False,
    },
    {
        "id": "task04_validation",
        "expected_files": {"scores.py"},
        "allow_test_edits": False,
    },
    {
        "id": "task05_vague",
        "expected_files": {"inventory.py"},
        "allow_test_edits": False,
        # pricing.py is a red herring — touching it counts as unrelated.
        "unrelated_if_touched": {"pricing.py"},
    },
]


def _has_api_key() -> bool:
    return bool(os.environ.get("OPENAI_API_KEY") or os.environ.get("CODE_AGENT_API_KEY"))


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
        if tool in {"read_file", "list_tree", "search_text", "search_symbol", "get_repo_map"}:
            n += 1
    return n


def _changed_from_diff(diff_text: str) -> list[str]:
    files: list[str] = []
    for line in diff_text.splitlines():
        if line.startswith("+++ b/"):
            path = line[6:]
            if path != "/dev/null":
                files.append(path)
    return files


def _would_reject(
    task: dict,
    changed: list[str],
    status: str,
    diff: str,
) -> tuple[bool, str]:
    """Post-hoc human-rejection judgment (runs used --yes)."""
    changed_set = set(changed)
    unrelated = set(task.get("unrelated_if_touched") or ())
    hit_unrelated = sorted(changed_set & unrelated)
    if hit_unrelated:
        return True, f"touched red-herring files: {hit_unrelated}"
    extras = sorted(changed_set - set(task["expected_files"]))
    if extras and not task.get("allow_test_edits"):
        # Allow test-only extras only if explicitly permitted.
        non_test = [f for f in extras if not f.startswith("tests/")]
        if non_test:
            return True, f"unexpected files changed: {non_test}"
    if status != "SUCCEEDED":
        return False, "failed/incomplete — rejection N/A (auto-approved during run)"
    if not diff.strip():
        return True, "empty diff after claimed success"
    return False, "patch looks on-scope"


def _new_run_id() -> str:
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"{ts}_{uuid.uuid4().hex[:8]}"


def run_one(task: dict) -> dict:
    from code_agent.cli import main as cli_main

    task_dir = TASKS_ROOT / task["id"]
    description = (task_dir / "TASK.txt").read_text(encoding="utf-8").strip()
    run_id = _new_run_id()

    # Never delete prior runs/sessions — each run gets an isolated directory.
    out_dir = RESULTS / task["id"] / run_id
    out_dir.mkdir(parents=True, exist_ok=False)

    session_base = RESULTS / "_sessions" / task["id"] / run_id
    session_base.mkdir(parents=True, exist_ok=False)

    print(f"\n===== {task['id']} (run_id={run_id}) =====", flush=True)
    print(description, flush=True)

    argv = [
        str(task_dir),
        description,
        "--yes",
        "--session-base",
        str(session_base),
    ]
    code = cli_main(argv)

    sessions = sorted(
        (p for p in session_base.iterdir() if p.is_dir()),
        key=lambda p: p.stat().st_mtime,
    )
    if not sessions:
        return {
            "task": task["id"],
            "run_id": run_id,
            "success": False,
            "error": "no session created",
            "exit_code": code,
            "results_path": str(out_dir),
        }
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
    summary_path = out_dir / "summary.json"
    if summary_path.exists():
        summary = json.loads(summary_path.read_text(encoding="utf-8"))

    diff = ""
    diff_path = out_dir / "final.diff"
    if diff_path.exists():
        diff = diff_path.read_text(encoding="utf-8")

    changed = summary.get("changed_files") or _changed_from_diff(diff)
    reads = _count_reads(out_dir / "trace.jsonl")
    status = summary.get("status", "UNKNOWN")
    success = status == "SUCCEEDED"
    reject, reject_reason = _would_reject(task, list(changed), status, diff)
    expected = set(task["expected_files"])
    unrelated = sorted(set(changed) - expected)

    row = {
        "task": task["id"],
        "run_id": run_id,
        "success": success,
        "status": status,
        "attempts_used": summary.get("attempts_used"),
        "total_format_retries_used": summary.get("total_format_retries_used"),
        "files_read_actions": reads,
        "files_changed": changed,
        "changed_count": len(changed),
        "unrelated_modifications": unrelated,
        "has_unrelated_modifications": bool(unrelated),
        "human_reject_recommended": reject,
        "human_reject_reason": reject_reason,
        "stop_reason": summary.get("stop_reason"),
        "session_path": str(session),
        "results_path": str(out_dir),
        "exit_code": code,
    }
    (out_dir / "metrics.json").write_text(
        json.dumps(row, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    # Also refresh a "latest" pointer copy for convenience (does not delete history).
    latest = RESULTS / task["id"] / "latest"
    if latest.exists() or latest.is_symlink():
        if latest.is_dir() and not latest.is_symlink():
            shutil.rmtree(latest)
        else:
            latest.unlink(missing_ok=True)
    try:
        latest.symlink_to(out_dir, target_is_directory=True)
    except OSError:
        # Windows without symlink privilege: shallow copy of metrics only.
        latest.mkdir(parents=True, exist_ok=True)
        shutil.copy2(out_dir / "metrics.json", latest / "metrics.json")

    print(json.dumps(row, indent=2, ensure_ascii=False), flush=True)
    return row


def main() -> int:
    if not _has_api_key():
        print(
            "ERROR: Set OPENAI_API_KEY or CODE_AGENT_API_KEY before running real LLM eval.",
            file=sys.stderr,
        )
        return 2

    RESULTS.mkdir(parents=True, exist_ok=True)

    rows = []
    for task in TASKS:
        try:
            rows.append(run_one(task))
        except Exception as exc:  # noqa: BLE001
            rows.append({"task": task["id"], "success": False, "error": str(exc)})
            print(f"FAILED {task['id']}: {exc}", flush=True)

    report = {
        "model": os.environ.get("CODE_AGENT_MODEL")
        or os.environ.get("OPENAI_MODEL")
        or "gpt-4o-mini",
        "tasks": rows,
        "success_count": sum(1 for r in rows if r.get("success")),
        "total": len(rows),
    }
    (RESULTS / "report.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    _write_markdown(report)
    print("\n===== REPORT =====", flush=True)
    print(json.dumps(report, indent=2, ensure_ascii=False), flush=True)
    return 0 if report["success_count"] == report["total"] else 1


def _write_markdown(report: dict) -> None:
    lines = [
        "# Real LLM Eval Report (5 tasks)",
        "",
        f"- model: `{report['model']}`",
        f"- success: **{report['success_count']}/{report['total']}**",
        "",
        "| 任务 | 成功 | 轮数 | 读操作次数 | 改文件数 | 无关修改 | 建议人工拒绝 |",
        "|---|---|---|---|---|---|---|",
    ]
    for r in report["tasks"]:
        lines.append(
            "| {task} | {ok} | {att} | {reads} | {chg} | {unrel} | {rej} |".format(
                task=r.get("task"),
                ok="Y" if r.get("success") else "N",
                att=r.get("attempts_used", "-"),
                reads=r.get("files_read_actions", "-"),
                chg=r.get("changed_count", "-"),
                unrel=", ".join(r.get("unrelated_modifications") or []) or "-",
                rej="Y" if r.get("human_reject_recommended") else "N",
            )
        )
        if r.get("human_reject_reason"):
            lines.append(f"|  |  |  |  |  |  | _{r['human_reject_reason']}_ |")
    lines.append("")
    lines.append("Artifacts per task under `examples/eval_tasks/results/<task_id>/`.")
    lines.append("")
    (RESULTS / "REPORT.md").write_text("\n".join(lines), encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
