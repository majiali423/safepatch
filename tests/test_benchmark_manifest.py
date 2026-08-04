import json
from pathlib import Path

import examples.llm_benchmark.verify_manifest as verify_manifest
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


def test_tree_hash_normalizes_checkout_line_endings(
    tmp_path: Path, monkeypatch
) -> None:
    source = tmp_path / "sample.py"
    monkeypatch.setattr(verify_manifest, "ROOT", tmp_path)

    source.write_bytes(b"print('safe')\n")
    lf_hash = verify_manifest._tree_hash([source])
    source.write_bytes(b"print('safe')\r\n")
    crlf_hash = verify_manifest._tree_hash([source])

    assert crlf_hash == lf_hash
