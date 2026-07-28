"""Reproduce the environment acceptance gate for real-bug benchmark tasks."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent
TASKS = ROOT / "tasks"


def run(
    command: list[str],
    *,
    cwd: Path | None = None,
    capture: bool = False,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        cwd=cwd,
        check=check,
        text=True,
        capture_output=capture,
    )


def load_json(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as file:
        return json.load(file)


def docker_test(
    image: str, source: Path, test_command: list[str]
) -> subprocess.CompletedProcess[str]:
    mount = f"type=bind,source={source.resolve()},target=/work"
    return run(
        [
            "docker",
            "run",
            "--rm",
            "--network",
            "none",
            "--cpus",
            "1",
            "--memory",
            "512m",
            "--pids-limit",
            "128",
            "--mount",
            mount,
            image,
            *test_command,
        ],
        capture=True,
        check=False,
    )


def verify(task_dir: Path, *, skip_build: bool) -> dict[str, Any]:
    task = load_json(task_dir / "task.json")
    acceptance = load_json(task_dir / "acceptance.json")
    image = acceptance["image"]["tag"]

    if not skip_build:
        run(
            [
                "docker",
                "build",
                "--provenance=false",
                "-t",
                image,
                str(task_dir),
            ]
        )

    with tempfile.TemporaryDirectory(prefix=f"safepatch-{task['id']}-") as raw:
        temp = Path(raw)
        buggy = temp / "buggy"
        fixed = temp / "fixed"
        run(["git", "clone", "--quiet", task["repository"], str(buggy)])
        run(["git", "checkout", "--quiet", task["buggy_commit"]], cwd=buggy)
        run(
            [
                "git",
                "worktree",
                "add",
                "--quiet",
                "--detach",
                str(fixed),
                task["fixed_commit"],
            ],
            cwd=buggy,
        )
        run(
            [
                "git",
                "apply",
                "--whitespace=nowarn",
                str((task_dir / task["public_test_patch"]).resolve()),
            ],
            cwd=buggy,
        )

        buggy_result = docker_test(image, buggy, task["test_command"])
        fixed_result = docker_test(image, fixed, task["test_command"])

    buggy_output = buggy_result.stdout + buggy_result.stderr
    fixed_output = fixed_result.stdout + fixed_result.stderr
    signature_found = task["failure_signature"] in buggy_output
    accepted = buggy_result.returncode != 0 and signature_found and fixed_result.returncode == 0
    image_id = run(
        ["docker", "image", "inspect", image, "--format", "{{.Id}}"],
        capture=True,
    ).stdout.strip()

    return {
        "task": task["id"],
        "accepted": accepted,
        "image_id": image_id,
        "recorded_image_id": acceptance["image"]["digest"],
        "buggy_exit_code": buggy_result.returncode,
        "failure_signature_found": signature_found,
        "fixed_exit_code": fixed_result.returncode,
        "buggy_output_tail": buggy_output.strip().splitlines()[-8:],
        "fixed_output_tail": fixed_output.strip().splitlines()[-8:],
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "tasks",
        nargs="*",
        help="Task ids to verify (default: every accepted task).",
    )
    parser.add_argument(
        "--skip-build",
        action="store_true",
        help="Reuse local images instead of rebuilding them.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    selected = args.tasks or [
        path.name for path in sorted(TASKS.iterdir()) if (path / "acceptance.json").exists()
    ]
    results = [verify(TASKS / task_id, skip_build=args.skip_build) for task_id in selected]
    print(json.dumps(results, indent=2))
    return 0 if all(result["accepted"] for result in results) else 1


if __name__ == "__main__":
    sys.exit(main())
