from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VERIFY = ROOT / "examples" / "real_bug_benchmark" / "verify_preflight_ablation.py"
BUNDLE = (
    ROOT
    / "examples"
    / "real_bug_benchmark"
    / "published"
    / "preflight-ablation-28-run"
)


def run_verify(bundle: Path = BUNDLE) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(VERIFY), str(bundle)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )


def test_published_preflight_comparison_recalculates():
    result = run_verify()
    assert result.returncode == 0, result.stderr
    summary = json.loads((BUNDLE / "summary.json").read_text(encoding="utf-8"))
    assert summary["completed_slots"] == 28
    assert summary["preflight_enabled"]["overall_successes"] == 11
    assert summary["preflight_disabled"]["overall_successes"] == 9
    assert summary["preflight_enabled"]["preflight_rejections"] == 0
    assert summary["preflight_disabled"]["apply_failures_without_preflight"] == 0


def test_published_preflight_comparison_contains_no_local_paths_or_key_metadata():
    for path in BUNDLE.rglob("*"):
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8")
        assert "C:\\Users\\" not in text
        assert "/Users/" not in text
        assert "api_key" not in text.lower()


def test_published_preflight_comparison_rejects_tampering(tmp_path: Path):
    tampered = tmp_path / "published"
    shutil.copytree(BUNDLE, tampered)
    summary_path = tampered / "summary.json"
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    summary["preflight_enabled"]["overall_successes"] = 14
    summary_path.write_text(json.dumps(summary), encoding="utf-8")

    result = run_verify(tampered)
    assert result.returncode != 0
    assert "summary.json does not match" in result.stderr
