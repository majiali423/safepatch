"""Unit tests for llm_benchmark metrics helpers (no product changes)."""

from __future__ import annotations

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MOD = ROOT / "examples" / "llm_benchmark" / "metrics_lib.py"


def _load():
    spec = importlib.util.spec_from_file_location("metrics_lib", MOD)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_change_metrics_sets():
    m = _load()
    row = m.compute_change_metrics(
        required_files=["split.py", "ledger.py"],
        allowed_files=["split.py", "ledger.py"],
        forbidden_files=["money.py", "tests/test_split.py"],
        changed_files=["split.py", "money.py"],
    )
    assert row["missing_required_changes"] == ["ledger.py"]
    assert row["unrelated_changes"] == ["money.py"]
    assert row["forbidden_changes"] == ["money.py"]


def test_public_pass_uses_final_tests_passed_not_status():
    m = _load()
    task = {
        "task_id": "x",
        "required_files": ["a.py"],
        "allowed_files": ["a.py"],
        "forbidden_files": [],
        "has_hidden_tests": True,
    }
    summary = {
        "status": "SUCCEEDED",
        "final_tests_passed": False,
        "attempts_used": 2,
        "total_format_retries_used": 1,
        "changed_files": ["a.py"],
        "stop_reason": "x",
    }
    row = m.build_run_metrics(
        task=task,
        summary=summary,
        hidden_pass=False,
        eval_status="ARTIFACT_INCONSISTENT",
    )
    assert row["product_status"] == "SUCCEEDED"
    assert row["public_pass"] is False
    assert row["artifact_consistent"] is False
    assert row["public_first_attempt_success"] is False
    assert row["eval_first_attempt_success"] is False
    assert row["benchmark_success"] is False


def test_artifact_consistency_and_resolve_eval_status():
    m = _load()
    ok_summary = {"status": "SUCCEEDED", "final_tests_passed": True}
    bad_summary = {"status": "SUCCEEDED", "final_tests_passed": False}
    assert m.artifact_is_consistent(ok_summary)
    assert not m.artifact_is_consistent(bad_summary)
    assert (
        m.resolve_eval_status(
            summary=bad_summary,
            hidden_pass=None,
            hidden_eval_status=None,
            ran_hidden=False,
        )
        == "ARTIFACT_INCONSISTENT"
    )
    assert (
        m.resolve_eval_status(
            summary=ok_summary,
            hidden_pass=True,
            hidden_eval_status="SUCCEEDED",
            ran_hidden=True,
        )
        == "SUCCEEDED"
    )


def test_extract_patch_apply_metrics(tmp_path: Path):
    import json

    m = _load()
    trace = tmp_path / "trace.jsonl"
    trace.write_text(
        "\n".join(
            [
                json.dumps(
                    {"event": "patch_applied", "payload": {"ok": False, "error": "x"}}
                ),
                json.dumps(
                    {
                        "event": "patch_applied",
                        "payload": {"ok": True, "files": ["a.py"]},
                    }
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    row = m.extract_patch_apply_metrics(trace)
    assert row["patch_proposals"] == 2
    assert row["patch_apply_failures"] == 1
    assert row["patch_apply_success_rate"] == 0.5
    assert row["first_patch_applicable"] is False
