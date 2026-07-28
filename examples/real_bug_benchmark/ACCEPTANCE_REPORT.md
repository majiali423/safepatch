# Real-bug benchmark environment acceptance

Verified: 2026-07-28

All five frozen primary candidates pass the environment gate. This report is
about task reproducibility only; it contains no model repair results and makes
no repair-success claim.

| Task | Project runtime | Buggy result | Fixed result | Public cases |
|---|---:|---|---|---:|
| `pysnooper-3` | Python 3.8.20 | expected `NameError` | pass | 1 |
| `tornado-11` | Python 3.7.17 | uppercase chunk body lost | pass | 1 |
| `tqdm-3` | Python 3.6.15 | expected bool `TypeError` | pass | 1 |
| `sanic-5` | Python 3.8.20 | expected `sanic.root` `KeyError` | pass | 1 |
| `thefuck-19` | Python 3.7.17 | unsafe `--force` output | pass | 3 |

Each task records its immutable base-image digest, complete resolved dependency
pins, public upstream regression-test patch, expected failure signature, fixed
result, resource limits, and verified local image digest in `acceptance.json`.
Tests run as an unprivileged user with networking disabled, one CPU, 512 MiB of
memory, and a 128-process limit.

## Reproduce

From the repository root, with Git and Docker available:

```bash
python examples/real_bug_benchmark/verify.py
```

The verifier rebuilds each image, clones the two upstream revisions into a
temporary directory, overlays only the public regression test on the buggy
revision, and validates that buggy fails for the recorded reason while fixed
passes. The fixed revision and `acceptance.json` are benchmark infrastructure;
they must not be copied into the Agent workspace or prompt during model runs.

## Interpretation

Environment acceptance prevents dependency, collection, and runtime migration
errors from being counted as model failures. The next experiment phase is the
frozen 36-session matrix described in `DESIGN.md`: full SafePatch, preflight
ablation, and a smaller second-model comparison. Task-level outcomes, tokens,
latency, failure class, and estimated cost must be reported together.
