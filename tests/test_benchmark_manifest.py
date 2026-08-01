import json
from pathlib import Path

from examples.llm_benchmark.verify_manifest import current_fingerprints


def test_frozen_benchmark_manifest_has_not_drifted() -> None:
    root = Path(__file__).resolve().parents[1]
    manifest = json.loads(
        (root / "examples" / "llm_benchmark" / "manifest.json").read_text(
            encoding="utf-8"
        )
    )
    assert current_fingerprints() == manifest["frozen_fingerprints"]
    assert manifest["scope"] == {
        "models": 1,
        "fixed_micro_tasks": 12,
        "repetitions": 3,
        "self_authored": True,
        "production_proof": False,
    }
    assert manifest["future_provider_matrix"] == []
