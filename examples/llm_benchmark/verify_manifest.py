from __future__ import annotations

import hashlib
import json
from pathlib import Path

from code_agent.llm import SYSTEM_PROMPT

ROOT = Path(__file__).resolve().parents[2]
MANIFEST = Path(__file__).with_name("manifest.json")


def _tree_hash(paths: list[Path]) -> str:
    digest = hashlib.sha256()
    for path in sorted(paths, key=lambda item: item.as_posix()):
        relative = path.relative_to(ROOT).as_posix()
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        # Git checkouts may materialize Python sources with LF or CRLF. The
        # benchmark content is the same in either case, so hash canonical LF
        # bytes to keep the frozen fingerprint stable across CI platforms.
        digest.update(path.read_bytes().replace(b"\r\n", b"\n"))
        digest.update(b"\0")
    return f"sha256:{digest.hexdigest()}"


def current_fingerprints() -> dict[str, str]:
    benchmark = ROOT / "examples" / "llm_benchmark"
    task_files = [
        path
        for directory in benchmark.glob("bench[0-9][0-9]_*")
        if directory.is_dir() and not directory.name.endswith(".hidden")
        for path in directory.rglob("*.py")
        if "__pycache__" not in path.parts
    ]
    hidden_files = [
        path
        for directory in benchmark.glob("bench[0-9][0-9]_*.hidden")
        if directory.is_dir()
        for path in directory.rglob("*.py")
        if "__pycache__" not in path.parts
    ]
    runner_files = [
        benchmark / "run_full12_x3.py",
        benchmark / "metrics_lib.py",
    ]
    return {
        "task_tree": _tree_hash(task_files),
        "hidden_test_tree": _tree_hash(hidden_files),
        "system_prompt": f"sha256:{hashlib.sha256(SYSTEM_PROMPT.encode('utf-8')).hexdigest()}",
        "benchmark_runner": _tree_hash(runner_files),
    }


def main() -> int:
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    expected = manifest["frozen_fingerprints"]
    actual = current_fingerprints()
    if actual != expected:
        print(json.dumps({"expected": expected, "actual": actual}, indent=2))
        return 1
    print("benchmark manifest verified")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
