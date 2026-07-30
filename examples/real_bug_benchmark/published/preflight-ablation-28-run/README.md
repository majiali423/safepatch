# Published preflight controlled comparison

This sanitized bundle records a frozen 28-slot comparison over seven real
historical bugs: preflight enabled and disabled, two runs per task per condition.
The schedule was fixed before execution and reversed condition order in repeat two.

| Condition | Runs | Public | Public + hidden | Tokens | Duration | Estimated cost |
|---|---:|---:|---:|---:|---:|---:|
| Enabled | 14 | 13/14 | 11/14 | 1,873,354 | 696.968s | $0.02879704 |
| Disabled | 14 | 14/14 | 9/14 | 1,914,303 | 775.179s | $0.03145705 |

| Task | Enabled | Disabled |
|---|---:|---:|
| `pysnooper-3` | 2/2 | 2/2 |
| `tornado-11` | 2/2 | 2/2 |
| `tqdm-3` | 2/2 | 0/2 |
| `sanic-5` | 2/2 | 1/2 |
| `thefuck-19` | 2/2 | 2/2 |
| `tornado-10` | 0/2 | 0/2 |
| `thefuck-16` | 1/2 | 2/2 |

## The important result

Enabled finished 11/14 and disabled finished 9/14, but **no run triggered a
preflight rejection and no run had an apply failure**. The experiment therefore
did not exercise preflight's core interception mechanism. The two-run outcome
difference cannot be attributed to preflight; model sampling produced different
semantic patches between conditions. `first_patch_applicable` is `null` when
preflight is disabled because that property is not observed in that condition.

This is a controlled small-sample comparison, not a same-random-seed paired
experiment or a causal accuracy claim. It is useful negative evidence: future
preflight evaluation should deliberately include naturally occurring context
mismatches or a frozen replay corpus, while keeping semantic accuracy separate.

Files:

- `freeze.json`: candidate commit, input hashes, fixed schedule and resource limits.
- `runs.json`: sanitized per-slot outcomes, usage, cost and failure classification.
- `summary.json`: aggregates recalculated from `runs.json`.
- `failures/`: submitted diffs and hidden-test output for every failed slot.

Verify without an API account or Docker:

```bash
python examples/real_bug_benchmark/verify_preflight_ablation.py
```
