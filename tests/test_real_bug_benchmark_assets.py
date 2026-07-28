import json
import re
from pathlib import Path

from examples.real_bug_benchmark.run_model_eval import (
    TaskImageRunner,
    estimate_cost,
    pricing_for_model,
)

BENCHMARK_ROOT = Path(__file__).parents[1] / "examples" / "real_bug_benchmark"


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def test_accepted_benchmark_tasks_have_auditable_assets():
    manifest = load_json(BENCHMARK_ROOT / "candidate_manifest.json")
    accepted = [
        candidate
        for candidate in manifest["primary_candidates"]
        if candidate["status"] == "accepted"
    ]

    assert len(accepted) == 5
    for candidate in accepted:
        task_dir = BENCHMARK_ROOT / "tasks" / candidate["id"]
        task = load_json(task_dir / "task.json")
        acceptance = load_json(BENCHMARK_ROOT / candidate["acceptance_file"])

        assert acceptance["accepted"] is True
        assert acceptance["task_id"] == candidate["id"] == task["id"]
        assert task["buggy_commit"] == candidate["buggy_commit"]
        assert task["fixed_commit"] == candidate["fixed_commit"]
        assert acceptance["repository"]["buggy_commit"] == task["buggy_commit"]
        assert acceptance["repository"]["fixed_commit"] == task["fixed_commit"]
        assert re.fullmatch(r"sha256:[0-9a-f]{64}", acceptance["image"]["digest"])
        assert set(acceptance["image"]["digest"].removeprefix("sha256:")) != {"0"}

        dockerfile = (task_dir / "Dockerfile").read_text(encoding="utf-8")
        assert dockerfile.splitlines()[0].startswith("FROM python:")
        assert "@sha256:" in dockerfile.splitlines()[0]

        constraints = (task_dir / "constraints.txt").read_text(encoding="utf-8")
        pins = [line for line in constraints.splitlines() if line and not line.startswith("#")]
        assert pins
        assert all("==" in pin for pin in pins)

        public_test = (task_dir / task["public_test_patch"]).read_text(encoding="utf-8")
        assert public_test.startswith("diff --git ")
        assert candidate["upstream_test"].split("::")[-1].split(".")[-1] in public_test


def test_model_eval_uses_task_image_command_and_cache_aware_cost():
    runner = TaskImageRunner(image="benchmark:test", test_command=["sh", "-c", "pytest -q"])
    config = runner.make_run_config("benchmark:test")

    assert config.image == "benchmark:test"
    assert config.pytest_argv == ("sh", "-c", "pytest -q")

    cost = estimate_cost(
        {
            "prompt_tokens": 1_000_000,
            "cached_tokens": 250_000,
            "completion_tokens": 100_000,
        }
    )
    expected = 0.25 * 0.0028 + 0.75 * 0.14 + 0.1 * 0.28
    assert cost["estimated_usd"] == round(expected, 8)

    pro = estimate_cost(
        {
            "prompt_tokens": 1_000_000,
            "cached_tokens": 250_000,
            "completion_tokens": 100_000,
        },
        model="deepseek-v4-pro",
    )
    expected_pro = 0.25 * 0.003625 + 0.75 * 0.435 + 0.1 * 0.87
    assert pro["estimated_usd"] == round(expected_pro, 8)
    assert pricing_for_model("deepseek-chat") == pricing_for_model("deepseek-v4-flash")
