"""Self-check all pilot tasks before any real-LLM run.

Checks (per task):
1. baseline public fails
2. reference patch → public pass
3. reference patch → hidden pass
4. hidden stays outside public repo / repo map (and would not be in product prompt/trace)
5. reference patch does not modify tests
6. no third-party imports (stdlib + pytest only)
7. required/allowed/forbidden consistent with reference patch files
8. TASK.txt covers defined contracts (keyword heuristics)
9. (bench06) fixing only one required file still fails public+hidden

Does not modify code_agent product sources.
Does not run a real LLM.
"""

from __future__ import annotations

import ast
import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
REPO_ROOT = ROOT.parents[1]
sys.path.insert(0, str(REPO_ROOT))

from code_agent.eval.hidden import discover_hidden_dir, run_hidden_tests  # noqa: E402
from code_agent.patching.applier import apply_proposal  # noqa: E402
from code_agent.repository.repo_map import build_repo_map  # noqa: E402
from code_agent.state import PatchProposal, TestResult  # noqa: E402

from metrics_lib import compute_change_metrics  # noqa: E402

MANIFEST = json.loads((ROOT / "benchmark_manifest.json").read_text(encoding="utf-8"))

STDLIB_HINTS = {
    "pytest",
    "__future__",
    "typing",
    "dataclasses",
    "pathlib",
    "json",
    "re",
    "math",
    "sys",
    "os",
    "collections",
    "functools",
    "itertools",
    "datetime",
    "decimal",
    "copy",
    "enum",
    "hashlib",
    "tempfile",
    "shutil",
    "subprocess",
    "time",
    "ast",
}


class LocalRunner:
    """Host pytest stand-in for self-check (not a product fallback in Agent runs)."""

    image = "local-self-check"

    def make_run_config(self, image: str):
        from code_agent.runtime.docker_config import DockerRunConfig

        return DockerRunConfig(image=image)

    def run_pytest(self, workspace_root, *, log_path=None, config=None):
        target: list[str] = []
        if config is not None and any(
            part == "tests_hidden" for part in config.pytest_argv
        ):
            target = ["tests_hidden"]
        proc = subprocess.run(
            ["python", "-m", "pytest", "-q", "-p", "no:cacheprovider", *target],
            cwd=workspace_root,
            capture_output=True,
            text=True,
            check=False,
        )
        result = TestResult(
            exit_code=proc.returncode,
            stdout=proc.stdout or "",
            stderr=proc.stderr or "",
            duration_sec=0.01,
        )
        if log_path is not None:
            log_path.write_text(result.stdout + "\n" + result.stderr, encoding="utf-8")
        return result


def _run_public(cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["python", "-m", "pytest", "-q", "-p", "no:cacheprovider"],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=False,
    )


def _load_reference(task_id: str) -> tuple[str, list[str]]:
    meta = json.loads(
        (ROOT / "references" / task_id / "meta.json").read_text(encoding="utf-8")
    )
    diff = (ROOT / "references" / task_id / "reference.diff").read_text(encoding="utf-8")
    return diff, list(meta["files"])


def _apply_ref(wc: Path, diff: str, files: list[str]):
    proposal = PatchProposal(
        diagnosis="reference",
        affected_files=files,
        unified_diff=diff,
        expected_behavior="reference fix",
        risk_notes="benchmark reference",
        tests_to_run=["tests"],
    )
    result = apply_proposal(proposal, wc, allow_test_changes=False, allow_new_tests=False)
    if not result.ok:
        raise AssertionError(f"reference apply failed: {result.error}")
    return result


def _third_party_imports(task_dir: Path) -> list[str]:
    bad: list[str] = []
    local_mods = {p.stem for p in task_dir.glob("*.py")}
    for path in task_dir.rglob("*.py"):
        if ".hidden" in str(path):
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    top = alias.name.split(".")[0]
                    if top not in STDLIB_HINTS and top not in local_mods:
                        bad.append(f"{path.name}:{top}")
            elif isinstance(node, ast.ImportFrom):
                if node.module is None:
                    continue
                top = node.module.split(".")[0]
                if top not in STDLIB_HINTS and top not in local_mods:
                    bad.append(f"{path.name}:{top}")
    return bad


def _task_contract_ok(task_id: str, task_txt: str) -> list[str]:
    """Lightweight: TASK must mention the public API names for the task."""
    required_words = {
        "bench01_div_zero": ["safe_div", "ValueError"],
        "bench02_parse_int": ["parse_int", "ValueError"],
        "bench03_unique_keep": ["unique_keep_order"],
        "bench04_clamp_overfit": ["clamp"],
        "bench05_red_herring": ["final_price", "0.1"],
        "bench06_split_total": ["split_equal", "sum_shares"],
        "bench07_merge_ranges": ["merge_ranges"],
        "bench08_staged_bug": ["clean_row"],
        "bench09_config_pipeline": ["load_config", "validate_config"],
        "bench10_normalize_date": ["normalize_date", "YYYY-MM-DD"],
        "bench11_test_tamper_trap": ["is_strong_password"],
        "bench12_slug_overfit": ["slugify", "hello-world"],
    }
    missing = []
    for w in required_words.get(task_id, []):
        if w.lower() not in task_txt.lower():
            missing.append(w)
    return missing


def _hidden_markers(task_id: str) -> list[str]:
    hidden = ROOT / f"{task_id}.hidden"
    markers = []
    for path in hidden.glob("test_*.py"):
        text = path.read_text(encoding="utf-8")
        for m in re.findall(r"HIDDEN_ONLY_[A-Z0-9_]+", text):
            markers.append(m)
    return markers


def _partial_diff(full_diff: str, only_file: str) -> str:
    """Keep only one file hunk from a multi-file unified diff."""
    parts: list[str] = []
    current: list[str] = []
    keep = False
    for line in full_diff.splitlines(keepends=True):
        if line.startswith("--- "):
            if current and keep:
                parts.extend(current)
            current = [line]
            keep = False
        elif line.startswith("+++ "):
            current.append(line)
            path = line[4:].strip()
            if path.startswith("b/"):
                path = path[2:]
            keep = path.replace("\\", "/") == only_file
        else:
            if current:
                current.append(line)
    if current and keep:
        parts.extend(current)
    return "".join(parts)


def check_task(task: dict) -> dict:
    task_id = task["task_id"]
    task_dir = ROOT / task_id
    errors: list[str] = []
    evidence: dict = {"task_id": task_id}

    # 6. third-party
    bad_imp = _third_party_imports(task_dir)
    if bad_imp:
        errors.append(f"third-party imports: {bad_imp}")

    # 8. TASK contracts
    task_txt = (task_dir / "TASK.txt").read_text(encoding="utf-8")
    missing_words = _task_contract_ok(task_id, task_txt)
    if missing_words:
        errors.append(f"TASK.txt missing contract keywords: {missing_words}")

    # 4. hidden isolation (static)
    hidden = discover_hidden_dir(task_dir)
    if task["has_hidden_tests"] and hidden is None:
        errors.append("missing hidden sibling dir")
    if hidden is not None:
        if hidden.resolve().is_relative_to(task_dir.resolve()):
            errors.append("hidden dir is inside public task repo")
        markers = _hidden_markers(task_id)
        public_blob = "\n".join(
            p.read_text(encoding="utf-8", errors="ignore")
            for p in task_dir.rglob("*")
            if p.is_file()
        )
        for m in markers:
            if m in public_blob:
                errors.append(f"hidden marker leaked into public repo: {m}")
        map_text, map_data = build_repo_map(task_dir)
        blob = map_text + json.dumps(map_data)
        if "tests_hidden" in blob or any(
            "test_hidden" in json.dumps(map_data) for _ in [0]
        ):
            if "test_hidden" in blob:
                errors.append("hidden test filename appears in public repo map")
        for m in markers:
            if m in blob:
                errors.append(f"hidden marker in repo map: {m}")

    diff, ref_files = _load_reference(task_id)

    # 5. reference does not touch tests
    if any(f.startswith("tests/") or f.endswith("conftest.py") for f in ref_files):
        errors.append(f"reference patch touches tests: {ref_files}")
    if "tests/" in diff or "test_" in diff and "+++ b/tests/" in diff:
        errors.append("reference diff modifies tests/")

    # 7. metadata consistency
    req = set(task["required_files"])
    allow = set(task["allowed_files"])
    forb = set(task["forbidden_files"])
    ref_set = set(ref_files)
    if ref_set != req:
        errors.append(f"reference files {sorted(ref_set)} != required {sorted(req)}")
    if not ref_set <= allow:
        errors.append(f"reference files not subset of allowed: {sorted(ref_set - allow)}")
    if ref_set & forb:
        errors.append(f"reference files intersect forbidden: {sorted(ref_set & forb)}")
    change = compute_change_metrics(
        required_files=task["required_files"],
        allowed_files=task["allowed_files"],
        forbidden_files=task["forbidden_files"],
        changed_files=ref_files,
    )
    if change["missing_required_changes"] or change["unrelated_changes"] or change[
        "forbidden_changes"
    ]:
        errors.append(f"reference vs metadata metrics not clean: {change}")

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        wc = tmp_path / "wc"
        shutil.copytree(task_dir, wc)

        # 1. baseline public fails
        base = _run_public(wc)
        evidence["baseline_public_exit"] = base.returncode
        evidence["baseline_public_stdout"] = (base.stdout or "")[:500]
        if base.returncode == 0:
            errors.append("baseline public unexpectedly passed")

        # apply reference
        _apply_ref(wc, diff, ref_files)

        # ensure tests unchanged vs original
        for test_path in (task_dir / "tests").rglob("*.py"):
            rel = test_path.relative_to(task_dir)
            left = test_path.read_text(encoding="utf-8")
            right = (wc / rel).read_text(encoding="utf-8")
            if left != right:
                errors.append(f"tests modified after reference apply: {rel}")

        # 2. public pass
        pub = _run_public(wc)
        evidence["reference_public_exit"] = pub.returncode
        evidence["reference_public_stdout"] = (pub.stdout or "")[:500]
        if pub.returncode != 0:
            errors.append(f"reference public failed: {pub.stdout}\n{pub.stderr}")

        # 3. hidden pass (eval_temp path via run_hidden_tests)
        results_dir = tmp_path / "eval_results"
        hidden_metrics = run_hidden_tests(
            working_copy=wc,
            hidden_tests_dir=hidden,
            results_dir=results_dir,
            runner=LocalRunner(),
        )
        evidence["reference_hidden"] = {
            "eval_status": hidden_metrics.get("eval_status"),
            "hidden_pass": hidden_metrics.get("hidden_pass"),
            "hidden_exit_code": hidden_metrics.get("hidden_exit_code"),
        }
        if not hidden_metrics.get("hidden_pass"):
            errors.append(f"reference hidden failed: {hidden_metrics}")
        if (results_dir / "eval_temp_copy").exists():
            errors.append("eval_temp_copy not deleted")

        # working_copy still has no tests_hidden / markers
        if (wc / "tests_hidden").exists():
            errors.append("tests_hidden left in working_copy")
        markers = _hidden_markers(task_id)
        wc_blob = "\n".join(
            p.read_text(encoding="utf-8", errors="ignore")
            for p in wc.rglob("*")
            if p.is_file() and "tests_hidden" not in p.parts
        )
        for m in markers:
            if m in wc_blob:
                errors.append(f"hidden marker in working_copy after eval: {m}")

        # Multi-file independence (two required business files).
        if task_id in {"bench06_split_total", "bench09_config_pipeline"}:
            for only in task["required_files"]:
                wc2 = tmp_path / f"partial_{only.replace('/', '_')}"
                if wc2.exists():
                    shutil.rmtree(wc2)
                shutil.copytree(task_dir, wc2)
                partial = _partial_diff(diff, only)
                _apply_ref(wc2, partial, [only])
                part_pub = _run_public(wc2)
                if part_pub.returncode == 0:
                    errors.append(
                        f"partial fix ({only}) unexpectedly passed public tests"
                    )
                hdir = tmp_path / f"partial_eval_{only.replace('/', '_')}"
                hm = run_hidden_tests(
                    working_copy=wc2,
                    hidden_tests_dir=hidden,
                    results_dir=hdir,
                    runner=LocalRunner(),
                )
                if hm.get("hidden_pass"):
                    errors.append(
                        f"partial fix ({only}) unexpectedly passed hidden tests"
                    )
                evidence.setdefault("partial_fixes", {})[only] = {
                    "public_exit": part_pub.returncode,
                    "hidden_pass": hm.get("hidden_pass"),
                }

    evidence["ok"] = not errors
    evidence["errors"] = errors
    return evidence


def main() -> int:
    out_dir = ROOT / "results" / "self_check"
    out_dir.mkdir(parents=True, exist_ok=True)
    rows = []
    failed = 0
    for task in MANIFEST["tasks"]:
        print(f"== self-check {task['task_id']} ==", flush=True)
        row = check_task(task)
        rows.append(row)
        status = "PASS" if row["ok"] else "FAIL"
        print(status, row.get("errors"), flush=True)
        if not row["ok"]:
            failed += 1
    report = {
        "real_llm_runs": False,
        "tasks_checked": len(rows),
        "failed": failed,
        "results": rows,
    }
    (out_dir / "self_check_report.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    print(json.dumps({"failed": failed, "total": len(rows)}, indent=2), flush=True)
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
