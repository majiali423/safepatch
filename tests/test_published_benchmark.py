from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VERIFY = ROOT / "examples" / "real_bug_benchmark" / "verify_published_results.py"
BUNDLE = (
    ROOT
    / "examples"
    / "real_bug_benchmark"
    / "published"
    / "synthesis-21-run"
)


def _run_verify(bundle: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(VERIFY), str(bundle)],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )


def test_published_benchmark_recalculates_and_verifies():
    result = _run_verify(BUNDLE)

    assert result.returncode == 0, result.stderr
    assert "17/21 overall successes" in result.stdout
    summary = json.loads((BUNDLE / "summary.json").read_text(encoding="utf-8"))
    assert summary["runs"] == 21
    assert summary["public_successes"] == 19
    assert summary["overall_successes"] == 17
    assert summary["modified_test_files"] == []


def test_published_benchmark_contains_no_local_user_paths_or_key_metadata():
    for path in BUNDLE.rglob("*"):
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8")
        assert "C:\\Users\\" not in text, path
        assert "/Users/" not in text, path
        assert "api_key" not in text.lower(), path


def test_published_benchmark_rejects_tampered_summary(tmp_path: Path):
    tampered = tmp_path / "published"
    shutil.copytree(BUNDLE, tampered)
    summary_path = tampered / "summary.json"
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    summary["overall_successes"] = 21
    summary_path.write_text(json.dumps(summary), encoding="utf-8")

    result = _run_verify(tampered)

    assert result.returncode != 0
    assert "does not match recalculated" in result.stderr
