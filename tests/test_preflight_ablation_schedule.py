from __future__ import annotations

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "examples" / "real_bug_benchmark" / "run_preflight_ablation.py"


def load_script():
    spec = importlib.util.spec_from_file_location("preflight_ablation", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_schedule_has_28_unique_frozen_slots_and_balanced_conditions():
    module = load_script()
    schedule = module.build_schedule()

    assert [slot["slot"] for slot in schedule] == list(range(1, 29))
    assert module.schedule_sha256(schedule) == (
        "69c1cb4d76b5101b3b4954e93155cb2dd32ac28e148a88e43aede4c395412e4a"
    )
    assert len({(slot["task_id"], slot["repeat"], slot["preflight_enabled"]) for slot in schedule}) == 28
    for task_id in module.TASK_IDS:
        for repeat in (1, 2):
            pair = [
                slot["preflight_enabled"]
                for slot in schedule
                if slot["task_id"] == task_id and slot["repeat"] == repeat
            ]
            assert set(pair) == {True, False}


def test_condition_order_reverses_between_repeats():
    module = load_script()
    schedule = module.build_schedule()
    for task_id in module.TASK_IDS:
        first_by_repeat = [
            next(
                slot["preflight_enabled"]
                for slot in schedule
                if slot["task_id"] == task_id and slot["repeat"] == repeat
            )
            for repeat in (1, 2)
        ]
        assert first_by_repeat[0] is not first_by_repeat[1]


def test_environment_failure_classification_is_narrow():
    module = load_script()
    assert module.is_environment_failure({"failure_type": "llm_error"})
    assert module.is_environment_failure({"product_status": "TEST_ENVIRONMENT_ERROR"})
    assert not module.is_environment_failure({"failure_type": "hidden_test_failure"})
    assert not module.is_environment_failure({"failure_type": "patch_not_applicable"})


def test_totals_keep_conditions_separate():
    module = load_script()
    base = {
        "public_pass": True,
        "hidden_pass": True,
        "overall_pass": True,
        "first_patch_applicable": True,
        "attempts_used": 1,
        "model_calls": 2,
        "token_usage": {"total_tokens": 10},
        "estimated_cost": {"estimated_usd": 0.01},
        "duration_sec": 3,
    }
    rows = [
        {**base, "preflight_enabled": True, "preflight_failures": 1},
        {
            **base,
            "preflight_enabled": False,
            "overall_pass": False,
            "apply_failures_without_preflight": 1,
        },
    ]

    result = module.totals(rows)
    assert result["preflight_enabled"]["overall_successes"] == 1
    assert result["preflight_disabled"]["overall_successes"] == 0
    assert result["preflight_disabled"]["apply_failures_without_preflight"] == 1
    assert result["total_estimated_cost_usd"] == 0.02
