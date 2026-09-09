# Three engineering demonstrations

Each case is a 5-minute walkthrough from a real committed artifact to the
control path. None of these rewrite historical benchmark numbers.

## 1. A successful repair

- Mechanism: policy → exact preflight → hash-bound approval → apply → Docker pytest.
- Deterministic local walkthrough: [DEMO.md](DEMO.md) on `examples/buggy_calculator`
  with `examples/dry_run_fix_divide.json`.
- Frozen real-bug success evidence lives in
  `examples/real_bug_benchmark/published/synthesis-21-run/` (17/21 overall).
  Pick a passing run in `runs.json` and follow `status`, patch metrics, and
  observability. The verifier is
  `examples/real_bug_benchmark/verify_published_results.py`.

## 2. Preflight recovery (stale / mismatch context, no write)

- Mechanism: inapplicable unified diff is rejected before approval.
- Offline replay: `python examples/agent_experiments/run_offline.py`
  uses frozen mismatch diffs under
  `examples/llm_benchmark/replays/v02_full12_mismatch/`.
- Product tests: `tests/test_patch_preflight.py` (mismatch feedback, required
  re-read, regeneration budget).
- Do **not** cite the published 11/14 vs 9/14 ablation as causal preflight
  gain; that freeze did not record a preflight rejection.

## 3. Public green, hidden red (false positive)

- Mechanism: hidden tests run only after the product session, on an isolated
  copy, and cannot flow back into the repair loop.
- Committed example:
  `examples/real_bug_benchmark/published/synthesis-21-run/failures/tqdm-3-hidden-total-precedence.diff`
  plus the matching `.txt` hidden output.
- Product isolation tests: `tests/test_hidden_eval.py`,
  `tests/test_hidden_benchmark_isolation.py`.
- Summary field `request_verified` stays about public-session oracles, not hidden.

## What not to claim

- 26/30 is not recomputed by the current 21-run verifier.
- Extension 9/9 is a historical report unless a per-run bundle is published.
- Offline mechanism reports are not model accuracy scores.
- Paid model reruns need an explicit budget and are not part of default CI.
