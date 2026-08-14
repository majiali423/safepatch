import json
import re
import subprocess
from pathlib import Path

from examples.real_bug_benchmark.run_model_eval import (
    TaskImageRunner,
    ensure_mirror,
    estimate_cost,
    pricing_for_model,
)
from examples.real_bug_benchmark.verify import patch_adds_only_new_files

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
        assert candidate["hidden_test_status"] == "accepted"
        assert acceptance["hidden_result"]["outcome"] == "expected_failure_then_pass"
        assert acceptance["task_id"] == candidate["id"] == task["id"]
        assert task["buggy_commit"] == candidate["buggy_commit"]
        assert task["fixed_commit"] == candidate["fixed_commit"]
        assert acceptance["repository"]["buggy_commit"] == task["buggy_commit"]
        assert acceptance["repository"]["fixed_commit"] == task["fixed_commit"]
        assert candidate["hidden_test_status"] in {"accepted", "recheck_required"}
        assert acceptance["hidden_result"]["outcome"] in {
            "expected_failure_then_pass",
            "recheck_required",
        }
        assert re.fullmatch(r"sha256:[0-9a-f]{64}", acceptance["image"]["digest"])
        hidden_test = (task_dir / task["hidden_test_patch"]).read_text(encoding="utf-8")
        assert "safepatch_hidden/test_hidden_regression.py" in hidden_test
        assert task["hidden_test_command"]
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
        assert "safepatch_public/test_public_regression.py" in public_test or (
            candidate["upstream_test"].split("::")[-1].split(".")[-1] in public_test
        )

        hidden_test = (task_dir / task["hidden_test_patch"]).read_text(encoding="utf-8")
        assert hidden_test.startswith("diff --git ")
        assert "safepatch_hidden/test_hidden_regression.py" in hidden_test
        assert task["hidden_test_command"]
        assert task["hidden_test_patch"] not in task["prompt"]


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


def test_hidden_patch_is_applied_inside_non_git_workspace(tmp_path):
    from examples.real_bug_benchmark.run_model_eval import apply_patch_inside_workspace

    workspace = tmp_path / "working_copy"
    workspace.mkdir()
    patch = tmp_path / "hidden.patch"
    patch.write_text(
        "diff --git a/safepatch_hidden/check.txt b/safepatch_hidden/check.txt\n"
        "new file mode 100644\n"
        "index 0000000..8baef1b\n"
        "--- /dev/null\n"
        "+++ b/safepatch_hidden/check.txt\n"
        "@@ -0,0 +1 @@\n"
        "+inside\n",
        encoding="utf-8",
    )

    apply_patch_inside_workspace(workspace, patch)

    assert (workspace / "safepatch_hidden" / "check.txt").read_text(
        encoding="utf-8"
    ) == "inside\n"
    assert not (tmp_path / "safepatch_hidden" / "check.txt").exists()


def test_new_file_public_overlay_is_added_to_both_revisions():
    tqdm_public_patch = BENCHMARK_ROOT / "tasks" / "tqdm-3" / "public_test.patch"
    sanic_public_patch = BENCHMARK_ROOT / "tasks" / "sanic-5" / "public_test.patch"

    assert patch_adds_only_new_files(tqdm_public_patch) is True
    assert patch_adds_only_new_files(sanic_public_patch) is False


def test_multifile_candidates_are_natural_frozen_and_environment_accepted():
    manifest = load_json(BENCHMARK_ROOT / "candidate_manifest.json")
    candidates = manifest["multifile_candidates"]

    assert {candidate["id"] for candidate in candidates} == {"tornado-10", "thefuck-16"}
    for candidate in candidates:
        task_dir = BENCHMARK_ROOT / "tasks" / candidate["id"]
        task = load_json(task_dir / "task.json")
        acceptance = load_json(BENCHMARK_ROOT / candidate["acceptance_file"])

        assert candidate["status"] == "accepted"
        assert 2 <= candidate["patch_files"] <= 5
        assert len(candidate["product_patch_files"]) == candidate["patch_files"]
        assert candidate["measured_repo_files"] <= 500
        assert candidate["measured_repo_bytes"] <= 5 * 1024 * 1024
        assert task["buggy_commit"] == candidate["buggy_commit"]
        assert task["fixed_commit"] == candidate["fixed_commit"]
        assert (task_dir / "Dockerfile").is_file()
        assert (task_dir / "constraints.txt").is_file()
        assert (task_dir / task["public_test_patch"]).is_file()
        assert acceptance["accepted"] is True
        assert acceptance["task_id"] == candidate["id"]
        assert acceptance["repository"]["buggy_commit"] == task["buggy_commit"]
        assert acceptance["repository"]["fixed_commit"] == task["fixed_commit"]
        assert re.fullmatch(r"sha256:[0-9a-f]{64}", acceptance["image"]["digest"])
        assert candidate["hidden_test_status"] == "accepted"
        assert acceptance["hidden_result"]["outcome"] == "expected_failure_then_pass"
        hidden_test = (task_dir / task["hidden_test_patch"]).read_text(encoding="utf-8")
        assert "safepatch_hidden/test_hidden_regression.py" in hidden_test
        assert task["hidden_test_command"]


def test_extension_pilot_tasks_are_environment_accepted_but_unscored():
    manifest = load_json(BENCHMARK_ROOT / "candidate_manifest.json")
    candidates = manifest["extension_pilot_candidates"]

    assert {candidate["id"] for candidate in candidates} == {
        "black-3",
        "httpie-4",
        "tqdm-4",
    }
    for candidate in candidates:
        task_dir = BENCHMARK_ROOT / "tasks" / candidate["id"]
        task = load_json(task_dir / "task.json")
        acceptance = load_json(BENCHMARK_ROOT / candidate["acceptance_file"])

        assert candidate["status"] == "accepted_environment_and_extension_model_score"
        assert candidate["hidden_test_status"] == "accepted"
        assert acceptance["accepted"] is True
        assert acceptance["task_id"] == task["id"] == candidate["id"]
        assert task["buggy_commit"] == acceptance["repository"]["buggy_commit"]
        assert task["fixed_commit"] == acceptance["repository"]["fixed_commit"]
        assert acceptance["hidden_result"]["outcome"] == "expected_failure_then_pass"
        assert (task_dir / task["public_test_patch"]).is_file()
        assert (task_dir / task["hidden_test_patch"]).is_file()


def test_empty_or_incomplete_mirror_is_recreated(tmp_path, monkeypatch):
    import examples.real_bug_benchmark.run_model_eval as model_eval

    cache = tmp_path / "_cache" / "black-3.git"
    cache.mkdir(parents=True)
    monkeypatch.setattr(model_eval, "RESULTS", tmp_path)
    calls = []

    def fake_run(command, **kwargs):
        calls.append(command)
        if "cat-file" in command:
            return subprocess.CompletedProcess(command, 1)
        cache.mkdir(parents=True, exist_ok=True)
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(model_eval.subprocess, "run", fake_run)

    result = ensure_mirror({"id": "black-3", "repository": "https://example.invalid/black", "buggy_commit": "abc"})

    assert result == cache
    assert any("cat-file" in command for command in calls)
    assert any(command[:3] == ["git", "clone", "--mirror"] for command in calls)
