"""Offline mechanism experiments. No paid model calls."""

from __future__ import annotations

import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
REPLAY_ROOT = ROOT / "examples" / "llm_benchmark" / "replays" / "v02_full12_mismatch"
OUT_DIR = ROOT / "examples" / "agent_experiments" / "results"


def _hash_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _preflight_case(task_id: str, workspace: Path, tool_payload: dict) -> dict:
    from code_agent.patching.preflight import PatchPreflight, PreflightFailure
    from code_agent.patching.proposal import parse_proposal

    proposal = parse_proposal(tool_payload["args"])
    before = sorted(p.read_bytes() for p in workspace.rglob("*.py") if p.is_file())
    enabled = PatchPreflight.run(proposal, workspace)
    after_enabled = sorted(p.read_bytes() for p in workspace.rglob("*.py") if p.is_file())
    intercepted = isinstance(enabled, PreflightFailure) or not enabled.ok
    return {
        "task_id": task_id,
        "preflight_enabled_intercepted": intercepted,
        "preflight_error_kind": getattr(enabled, "error_kind", None),
        "working_tree_unchanged": before == after_enabled,
        "side_effect": before != after_enabled,
    }


def _copy_bench(task_id: str, dest: Path) -> Path:
    import shutil

    src = ROOT / "examples" / "llm_benchmark" / task_id
    shutil.copytree(src, dest, dirs_exist_ok=True)
    return dest


def run_preflight_mechanism(out_dir: Path) -> dict:
    cases = []
    for task_dir in sorted(REPLAY_ROOT.iterdir()):
        if not task_dir.is_dir():
            continue
        json_path = task_dir / "bad_attempt_1.json"
        if not json_path.exists():
            continue
        tool_payload = json.loads(json_path.read_text(encoding="utf-8"))
        work = out_dir / "workspaces" / task_dir.name
        work.mkdir(parents=True, exist_ok=True)
        workspace = _copy_bench(task_dir.name, work)
        cases.append(_preflight_case(task_dir.name, workspace, tool_payload))
    synthetic = out_dir / "workspaces" / "synthetic_stale_context"
    synthetic.mkdir(parents=True, exist_ok=True)
    (synthetic / "mod.py").write_text("def f():\n    return 1\n", encoding="utf-8")
    cases.append(
        _preflight_case(
            "synthetic_stale_context",
            synthetic,
            {
                "args": {
                    "diagnosis": "stale",
                    "affected_files": ["mod.py"],
                    "unified_diff": (
                        "--- a/mod.py\n+++ b/mod.py\n@@ -1,2 +1,2 @@\n"
                        " def f():\n-    return 999\n+    return 2\n"
                    ),
                    "expected_behavior": "return 2",
                    "risk_notes": "offline",
                    "tests_to_run": ["tests/test_mod.py"],
                }
            },
        )
    )
    return {
        "kind": "preflight_mechanism",
        "cases": cases,
        "intercepted": sum(1 for case in cases if case["preflight_enabled_intercepted"]),
        "side_effects": sum(1 for case in cases if case["side_effect"]),
        "note": (
            "Mechanism replay of frozen mismatch diffs. Not a semantic repair-rate "
            "experiment and not a causal claim about the published 11/14 vs 9/14 ablation."
        ),
    }


def run_context_selector(out_dir: Path) -> dict:
    from code_agent.repository.context_selector import select_context_files
    from code_agent.repository.repo_map import build_repo_map

    repo = out_dir / "context_repo"
    repo.mkdir(parents=True, exist_ok=True)
    (repo / "mod.py").write_text("def f():\n    return 1\n", encoding="utf-8")
    (repo / "helpers.py").write_text("MARKER = 1\n", encoding="utf-8")
    (repo / "unrelated.py").write_text("x = 0\n", encoding="utf-8")
    _text, data = build_repo_map(repo)
    traceback = 'File "mod.py", line 1, in f\nAssertionError'
    ranked = select_context_files(
        workspace_root=repo,
        repo_map_data=data,
        traceback_summary=traceback,
        bug_description="fix f in mod.py",
        limit=2,
    )
    full_paths = [item["path"] for item in data.get("files", [])]
    return {
        "kind": "context_selector",
        "full_map_files": full_paths,
        "ranked_files": ranked,
        "hit_mod": "mod.py" in ranked,
        "note": "Offline ranking only; no model sampling.",
    }


def main() -> int:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_dir = OUT_DIR / stamp
    out_dir.mkdir(parents=True, exist_ok=True)
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "code_commit_note": "local working tree; not a published score",
        "preflight": run_preflight_mechanism(out_dir),
        "context": run_context_selector(out_dir),
        "paid_model_runs": "not executed; requires an explicit budget",
    }
    encoded = json.dumps(report, indent=2, ensure_ascii=False).encode("utf-8")
    report["bundle_sha256"] = _hash_bytes(encoded)
    payload = json.dumps(report, indent=2, ensure_ascii=False)
    (out_dir / "report.json").write_text(payload + "\n", encoding="utf-8")
    (out_dir / "REPORT.md").write_text(
        "# Offline SafePatch mechanism report\n\n"
        f"- Generated: {report['generated_at']}\n"
        f"- Preflight intercepted: {report['preflight']['intercepted']}/"
        f"{len(report['preflight']['cases'])}\n"
        f"- Side effects: {report['preflight']['side_effects']}\n"
        f"- Context hit mod.py: {report['context']['hit_mod']}\n"
        f"- Paid model runs: {report['paid_model_runs']}\n",
        encoding="utf-8",
    )
    print(out_dir / "REPORT.md")
    failures: list[str] = []
    pre = report["preflight"]
    if pre["side_effects"]:
        failures.append("side_effects")
    synthetic = next(
        (case for case in pre["cases"] if case["task_id"] == "synthetic_stale_context"),
        None,
    )
    if synthetic is None or not synthetic["preflight_enabled_intercepted"]:
        failures.append("synthetic_stale_not_intercepted")
    if not report["context"]["hit_mod"]:
        failures.append("context_miss")
    report["gate_failures"] = failures
    payload = json.dumps(report, indent=2, ensure_ascii=False)
    (out_dir / "report.json").write_text(payload + "\n", encoding="utf-8")
    if failures:
        print("offline experiment gate failed:", ", ".join(failures))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
