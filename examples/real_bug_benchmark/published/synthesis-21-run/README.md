# Published synthesis benchmark evidence

This directory is the sanitized, committed audit bundle for the frozen
seven-task, three-repeat SafePatch experiment. It contains
21 independent model runs over 7 real
historical bugs. Public tests passed 19/21;
public-plus-hidden scoring passed 17/21
(81.0%).

The bundle intentionally excludes local session paths, provider credentials,
complete model conversations, working copies, and large container logs.

Files:

- `freeze.json`: model settings and hashes binding the two source experiments.
- `runs.json`: sanitized per-run outcomes, usage, cost, phase and failure metrics.
- `summary.json`: totals recomputed from `runs.json`.
- `failures/`: one Tornado lifecycle false positive and one tqdm precedence false
  positive, each with the submitted diff and hidden-test output.

Verify the committed evidence without a model account or Docker:

```bash
python examples/real_bug_benchmark/verify_published_results.py
```

The verifier checks task/repeat counts, recomputes every aggregate, rejects
modified test files and post-preflight apply failures, checks frozen visibility
settings, and scans the bundle for local user paths or credential metadata.

These are small-sample engineering results from one model and are not a claim of
general production accuracy. Re-running the model may produce different patches.
See `../../SYNTHESIS_MODEL_EVAL_REPORT.md` for interpretation and limitations.
