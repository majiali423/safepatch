# Evaluation and evidence

SafePatch separates product reliability from model repair quality.

| Layer | What it measures | Entry point |
| --- | --- | --- |
| Offline regression | State transitions, budgets, exact apply and policy behavior | [Development](DEVELOPMENT.md) |
| Docker E2E | Actual container execution, isolation and cleanup | [Development](DEVELOPMENT.md) |
| Session replay | Deterministic summary, trace and diff changes | [Analysis loop](ANALYSIS_LOOP.md) |
| Real-bug evaluation | Historical repairs on frozen BugsInPy-derived tasks | [Design](../examples/real_bug_benchmark/DESIGN.md) |
| Micro-benchmark | Smaller tasks, hidden checks and patch-mismatch replay | [Design](../examples/llm_benchmark/DESIGN.md) |

## Published results

| Evidence | Recorded result | Interpretation |
| --- | --- | --- |
| [21-run synthesis bundle](../examples/real_bug_benchmark/published/synthesis-21-run/README.md) | 17/21 hidden/overall passes | Recomputed from the published records |
| [Preflight comparison](../examples/real_bug_benchmark/published/preflight-ablation-28-run/README.md) | 11/14 enabled; 9/14 disabled | No preflight rejection or apply failure occurred; not causal proof |
| [Extension report](../examples/real_bug_benchmark/EXTENSION_PILOT_REPORT.md) | 9/9 | Separate historical report; per-run raw artifacts are outside the 21-run bundle |

The combined 26/30 figure is a historical aggregate, not a result fully
recomputed by the current verifier. No recorded result establishes accuracy
on arbitrary repositories or proves production readiness.

## Verify without a model account

```bash
python examples/real_bug_benchmark/verify_published_results.py
python examples/real_bug_benchmark/verify_preflight_ablation.py
python examples/llm_benchmark/verify_manifest.py
python examples/agent_experiments/run_offline.py
```

The first three commands check frozen records and fingerprints. The last
replays context selection and patch-preflight mechanisms; it does not measure
live-model accuracy. None makes paid model calls.

The benchmark tasks, reference patches, hidden tests and mismatch replay files
are reproducibility inputs. Historical filenames alone do not make them obsolete.
Existing paid-run scripts are retained where they support those experiments;
they are not part of the default development path.

## Failure analysis

Hidden tests run after the product session on an isolated copy and never feed
back into the repair loop. Inspect the [failure case study](FAILURE_CASE_STUDY.md)
and [three demonstration routes](INTERVIEW_DEMOS.md) to understand the difference
between applying a safe patch and solving the underlying problem.
