# AnalysisLoop: who does what on a real run

This note walks one **deterministic** success capture, not a paid model call.

Safe capture refuses an existing `--out` path and preserves frozen fixtures:

```text
python -m devtools.session_replay.capture --out .tmp-session-replay-v2
python -m devtools.session_replay.compare --baseline tests/fixtures/session_replay/historical_v1 --after .tmp-session-replay-v2
python -m devtools.session_replay.compare --baseline tests/fixtures/session_replay/current_v2 --after .tmp-session-replay-v2
```

`historical_v1` is the frozen AnalysisLoop-split snapshot with **event names**,
selected counters, `summary` status/stop_reason/attempts, and `final.diff`.
It does **not** contain full trace payloads. Old capture also globally replaced
hex-like text, so hashes in historical `last_error` strings are not comparable.
That tree is not pre-refactor payload proof.

`current_v2` is labeled `current_working_tree_baseline`. It stores normalized
`summary.json`, complete `trace.jsonl` records, `final.diff`, and a fingerprint
recomputed from those files. Use it for later payload regression. It does **not**
claim the refactor-before and refactor-after payloads were identical.

Normalization replaces timestamps, `session_id`, known capture/session paths,
and duration fields. It does not globally rewrite hex strings, so patch hashes
and literals such as `12345678` versus `87654321` in `final.diff` stay distinct.

On the success script the dry-run model returns a single `propose_patch`
for `mod.py` (`return 1` → `return 2`). The in-process runner fails baseline
then passes after apply.

## Component path

1. **TaskController.run** imports the repo, writes the repo map, snapshots the
   tree, binds protected-test inventory, runs baseline pytest, and stops early
   only for environment errors or unsupported pytest collection.
2. **AnalysisLoop.run** sets `ANALYZING`, builds the user prompt (bug text,
   phase, read/evidence budgets, repo map, baseline traceback — same strings
   as the former controller helpers), then calls `ModelProvider.complete`.
3. On `propose_patch`, AnalysisLoop parses the proposal, runs
   **PolicyValidator.validate**, then **PatchGate.bind_if_applicable**
   (preflight → hash bind). Counters (`patch_preflight_successes`, format
   retries, required reads) are written on **TaskSession** only.
4. Control returns an **ApprovalBinding**. The controller moves to
   `AWAITING_APPROVAL`, records `approval_decision`, then re-checks
   working-tree and patch hashes.
5. **RepairExecutor.execute** applies the bound diff and runs pytest.
   **SessionFinalizer** writes `summary.json`, `trace.jsonl`, and `final.diff`.

Trace event order for the captured success run (names only; v2 also stores
payloads):

`session_created` → `repository_imported` → `repo_map_generated` →
`test_files_collected` → `baseline_test_finished` → `test_files_collected` →
`model_request` → `model_response` → `patch_proposed` →
`patch_preflight_started` → `patch_preflight_succeeded` →
`approval_decision` → `patch_applied` → `pytest_finished` →
`session_finished`.

Expected terminal: `SUCCEEDED` / `all_tests_passed` / `attempts_used=1`.
See `tests/fixtures/session_replay/current_v2/success/` after capture, or the
historical name-only snapshot under
`tests/fixtures/session_replay/historical_v1/success/`.

The maintained split-era snapshot is `tests/fixtures/session_replay/historical_v1`.
Later captures must not be presented as pre-refactor evidence.

## What stays on the controller

Import, baseline, collection-scope refusal, approval handler, post-approval
hash mismatch (`PATCH_BASE_CHANGED`), repair execute, KeyboardInterrupt /
internal_error, and finalize. Those are session-lifecycle seams, not model
turns.

## What AnalysisLoop must not do

It does not apply the patch or run post-apply pytest. Independent tests
construct AnalysisLoop without TaskController and assert the workspace is
still unpatched when a binding is returned.
