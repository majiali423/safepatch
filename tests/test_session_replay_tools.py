"""Negative and replay tests for session capture/compare tools."""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from devtools.session_replay.artifacts import (
    compute_fingerprint,
    fingerprint_from_dir,
    write_manifest,
    write_v2_scenario,
)
from devtools.session_replay.capture import capture_to
from devtools.session_replay.compare import check_invariants, compare_directories
from devtools.session_replay.compare import main as compare_main
from devtools.session_replay.constants import EXPECTED_INVARIANTS, SCENARIO_NAMES
from devtools.session_replay.normalize import normalize_string

ROOT = Path(__file__).resolve().parents[1]
HISTORICAL = ROOT / "tests" / "fixtures" / "session_replay" / "historical_v1"
CURRENT = ROOT / "tests" / "fixtures" / "session_replay" / "current_v2"
LONG_HASH_A = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
LONG_HASH_B = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"


def _summary(*, stop_reason: str = "all_tests_passed") -> dict:
    return {
        "summary_schema_version": 1,
        "status": "SUCCEEDED",
        "attempts_used": 1,
        "changed_files": ["mod.py"],
        "baseline_tests_passed": False,
        "final_tests_passed": True,
        "request_verified": True,
        "verification_basis": "baseline_failure_resolved",
        "stop_reason": stop_reason,
        "consecutive_format_retries": 0,
        "total_format_retries_used": 0,
        "consecutive_patch_regeneration_retries": 0,
        "total_patch_regeneration_retries": 0,
        "patch_preflight_failures": 0,
        "patch_preflight_successes": 1,
        "patch_preflight_success_rate": 1.0,
        "patch_apply_failures_after_preflight": 0,
        "patch_apply_failures_without_preflight": 0,
        "first_patch_applicable": True,
        "analysis": {
            "phase": "PROPOSE",
            "evidence_requests_used": 0,
            "max_evidence_requests": 2,
            "parameter_corrections_used": 0,
            "max_parameter_corrections": 1,
            "consecutive_no_progress_actions": 0,
            "total_no_progress_actions": 0,
            "hard_policy_violations": 0,
        },
        "read_budget": {
            "used": 0,
            "max": 12,
            "remaining": 12,
            "total_read_actions": 0,
            "required_recovery_reads": 0,
            "consecutive_violations": 0,
            "total_violations": 0,
        },
        "observability": {
            "started_at": "2026-01-01T00:00:00+00:00",
            "finished_at": "2026-01-01T00:00:01+00:00",
            "duration_ms": 12,
            "model": {
                "provider": "dry_run",
                "name": "gpt-4o-mini",
                "calls": 1,
                "logical_calls": 1,
                "transport_attempts": 1,
            },
            "tools": {"total_calls": 1, "read_calls": 0, "proposal_calls": 1},
            "retries": {"format": 0, "patch_regeneration": 0, "repair_attempts": 1},
            "tests": {"total_runs": 2, "baseline_runs": 1, "post_apply_runs": 1},
        },
    }


def _records(patch_hash: str = LONG_HASH_A) -> list[dict]:
    return [
        {
            "ts": "2026-01-01T00:00:00+00:00",
            "event": "session_created",
            "payload": {"session_id": "deadbeef"},
        },
        {
            "ts": "2026-01-01T00:00:01+00:00",
            "event": "approval_decision",
            "payload": {
                "patch_hash": patch_hash,
                "working_tree_hash": LONG_HASH_B,
                "args": {"path": "mod.py", "start_line": 1},
            },
        },
    ]


def write_dummy_tree(
    root: Path,
    *,
    final_diff: str = "+value = 12345678\n",
    patch_hash: str = LONG_HASH_A,
    stop_reason: str = "all_tests_passed",
    stamp: str = "2026-01-01T00:00:00+00:00",
) -> Path:
    root.mkdir(parents=True)
    write_manifest(root, label="synthetic")
    for name in SCENARIO_NAMES:
        summary = _summary(stop_reason=stop_reason)
        summary["observability"]["started_at"] = stamp
        records = _records(patch_hash=patch_hash)
        records[0]["ts"] = stamp
        write_v2_scenario(
            root / name,
            summary=summary,
            records=records,
            final_diff=final_diff,
            workspace_mod="def f():\n    return 2\n",
            last_error="",
            replacements=[],
            extras={"pytest_scope_unsupported": False},
        )
    return root


@pytest.fixture(scope="module")
def isolated_replay(tmp_path_factory: pytest.TempPathFactory) -> Path:
    dest = tmp_path_factory.mktemp("iso") / "v2"
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "devtools.session_replay.capture",
            "--out",
            str(dest),
            "--label",
            "isolated",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    return dest


def test_empty_baseline_fails(tmp_path: Path) -> None:
    after = write_dummy_tree(tmp_path / "after")
    empty = tmp_path / "empty"
    empty.mkdir()
    report = compare_directories(empty, after)
    assert report.exit_code() == 1
    assert any("empty" in item for item in report.errors)


def test_missing_scenario_and_file_and_field_fail(tmp_path: Path) -> None:
    baseline = write_dummy_tree(tmp_path / "baseline")
    after = write_dummy_tree(tmp_path / "after")
    shutil.rmtree(after / "success")
    report = compare_directories(baseline, after)
    assert report.exit_code() == 1
    assert any("missing scenarios" in item for item in report.errors)

    after2 = write_dummy_tree(tmp_path / "after2")
    (after2 / "success" / "summary.json").unlink()
    report = compare_directories(baseline, after2)
    assert report.exit_code() == 1
    assert any("missing file summary.json" in item for item in report.errors)

    after3 = write_dummy_tree(tmp_path / "after3")
    summary = json.loads((after3 / "success" / "summary.json").read_text(encoding="utf-8"))
    del summary["stop_reason"]
    (after3 / "success" / "summary.json").write_text(json.dumps(summary), encoding="utf-8")
    report = compare_directories(baseline, after3)
    assert report.exit_code() == 1
    assert any("missing fields" in item and "stop_reason" in item for item in report.errors)


def test_changed_summary_stop_reason_with_stale_fingerprint_fails(tmp_path: Path) -> None:
    baseline = write_dummy_tree(tmp_path / "baseline")
    after = write_dummy_tree(tmp_path / "after")
    summary_path = after / "success" / "summary.json"
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    summary["stop_reason"] = "intentionally_wrong_stop_reason"
    summary_path.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    report = compare_directories(baseline, after)
    assert report.exit_code() == 1
    joined = "\n".join(report.errors)
    assert "fingerprint.json does not match" in joined or "stop_reason" in joined


def test_trace_hash_change_same_event_names_fails(tmp_path: Path) -> None:
    baseline = write_dummy_tree(tmp_path / "baseline")
    after = write_dummy_tree(tmp_path / "after", patch_hash=LONG_HASH_B)
    report = compare_directories(baseline, after)
    assert report.exit_code() == 1
    assert any("trace.jsonl" in item for item in report.errors)


def test_final_diff_digit_literals_are_distinct(tmp_path: Path) -> None:
    baseline = write_dummy_tree(tmp_path / "baseline", final_diff="+value = 12345678\n")
    after = write_dummy_tree(tmp_path / "after", final_diff="+value = 87654321\n")
    report = compare_directories(baseline, after)
    assert report.exit_code() == 1
    assert any("final.diff" in item for item in report.errors)
    left = normalize_string("+value = 12345678", key=None, replacements=[])
    right = normalize_string("+value = 87654321", key=None, replacements=[])
    assert left != right
    assert normalize_string(LONG_HASH_A, key=None, replacements=[]) != normalize_string(
        LONG_HASH_B, key=None, replacements=[]
    )


def test_capture_invariants_fail_on_wrong_reason_or_counts() -> None:
    good = {name: dict(EXPECTED_INVARIANTS[name]) for name in SCENARIO_NAMES}
    assert check_invariants(good) == []
    bad_reason = {name: dict(row) for name, row in good.items()}
    bad_reason["success"]["stop_reason"] = "hidden_eval_unavailable"
    errors = check_invariants(bad_reason)
    assert errors
    assert any("success.stop_reason" in item for item in errors)
    bad_count = {name: dict(row) for name, row in good.items()}
    bad_count["format_exhausted"]["total_format_retries_used"] = 0
    errors = check_invariants(bad_count)
    assert any("format_exhausted.total_format_retries_used" in item for item in errors)


def test_declared_volatile_fields_do_not_fail_compare(tmp_path: Path) -> None:
    baseline = write_dummy_tree(tmp_path / "baseline", stamp="2026-01-01T00:00:00+00:00")
    after = write_dummy_tree(tmp_path / "after", stamp="2026-09-09T12:00:00+00:00")
    report = compare_directories(baseline, after)
    assert report.errors == []
    assert report.exit_code() == 0


def test_capture_refuses_existing_directory(tmp_path: Path) -> None:
    dest = tmp_path / "once"
    capture_to(dest, label="first")
    digest = hashlib.sha256((dest / "success" / "summary.json").read_bytes()).hexdigest()
    with pytest.raises(SystemExit, match="refusing to overwrite"):
        capture_to(dest, label="second")
    assert hashlib.sha256((dest / "success" / "summary.json").read_bytes()).hexdigest() == digest


def test_live_replay_matches_historical_retained_fields(isolated_replay: Path) -> None:
    assert HISTORICAL.is_dir()
    report = compare_directories(HISTORICAL, isolated_replay)
    assert report.exit_code() == 0, report.errors


def test_live_replay_matches_current_v2_full_payload(isolated_replay: Path) -> None:
    assert CURRENT.is_dir()
    report = compare_directories(CURRENT, isolated_replay)
    assert report.exit_code() == 0, report.errors


def test_two_live_captures_are_equivalent(tmp_path: Path) -> None:
    first = tmp_path / "first"
    second = tmp_path / "second"
    capture_to(first, label="first")
    capture_to(second, label="second")
    report = compare_directories(first, second)
    assert report.exit_code() == 0, report.errors


def test_compare_cli_nonzero_on_empty(tmp_path: Path) -> None:
    after = write_dummy_tree(tmp_path / "after")
    empty = tmp_path / "empty"
    empty.mkdir()
    code = compare_main(["--baseline", str(empty), "--after", str(after)])
    assert code == 1
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "devtools.session_replay.compare",
            "--baseline",
            str(empty),
            "--after",
            str(after),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    assert proc.returncode == 1


def test_fingerprint_tracks_summary_not_stale_file(tmp_path: Path) -> None:
    tree = write_dummy_tree(tmp_path / "tree")
    scenario = tree / "success"
    original = fingerprint_from_dir(scenario)
    summary_path = scenario / "summary.json"
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    summary["stop_reason"] = "intentionally_wrong_stop_reason"
    summary_path.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    recomputed = fingerprint_from_dir(scenario)
    assert recomputed["stop_reason"] == "intentionally_wrong_stop_reason"
    stored = json.loads((scenario / "fingerprint.json").read_text(encoding="utf-8"))
    assert stored == original
    assert stored != recomputed
    assert (
        compute_fingerprint(
            summary=summary,
            records=_records(),
            final_diff="+value = 12345678\n",
            workspace_mod="def f():\n    return 2\n",
            extras={"pytest_scope_unsupported": False},
        )["stop_reason"]
        == "intentionally_wrong_stop_reason"
    )
