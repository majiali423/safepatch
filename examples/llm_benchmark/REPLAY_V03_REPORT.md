# v0.3 Mismatch Diff Replay Report

> Historical evidence only: scripted replay run on 2026-07-27. It demonstrates
> a mechanism and is not a current model-quality or production-readiness claim.

**Mode:** scripted / fake LLM using v0.2 Full12 real context-mismatch diffs.
**Not counted** toward real-LLM success rates.

started_at: `2026-07-27T07:45:06.383144+00:00`
finished_at: `2026-07-27T07:45:12.310560+00:00`

## Results

| task | product | eval | public | hidden | preflight_fail | regen | approvals | repair | pytest_after | after_pf | mechanism_pass |
|---|---|---|---|---|---|---|---|---|---|---|---|
| bench01_div_zero | SUCCEEDED | SUCCEEDED | True | True | 1 | 1 | 1 | 1 | 1 | 0 | True |
| bench05_red_herring | SUCCEEDED | SUCCEEDED | True | True | 1 | 1 | 1 | 1 | 1 | 0 | True |

### `bench01_div_zero`

Checks:
- preflight_rejected_bad_diff: **True**
- no_approval_on_preflight_fail: **True**
- repair_zero_until_good_apply: **True**
- regen_requested: **True**
- public_and_hidden: **True**
- eval_succeeded: **True**
- after_preflight_apply_fail_zero: **True**

Trace event order:

```
session_created -> repository_imported -> repo_map_generated -> baseline_test_finished -> model_request -> model_response -> patch_proposed -> patch_preflight_started -> patch_preflight_failed -> patch_regeneration_requested -> model_request -> model_response -> tool_call -> tool_result -> model_request -> model_response -> patch_proposed -> patch_preflight_started -> patch_preflight_succeeded -> approval_decision -> patch_applied -> pytest_finished -> session_finished
```

result: `<REPOSITORY_ROOT>\examples\llm_benchmark\results\replay_v03\bench01_div_zero`

### `bench05_red_herring`

Checks:
- preflight_rejected_bad_diff: **True**
- no_approval_on_preflight_fail: **True**
- repair_zero_until_good_apply: **True**
- regen_requested: **True**
- public_and_hidden: **True**
- eval_succeeded: **True**
- after_preflight_apply_fail_zero: **True**

Trace event order:

```
session_created -> repository_imported -> repo_map_generated -> baseline_test_finished -> model_request -> model_response -> patch_proposed -> patch_preflight_started -> patch_preflight_failed -> patch_regeneration_requested -> model_request -> model_response -> tool_call -> tool_result -> model_request -> model_response -> patch_proposed -> patch_preflight_started -> patch_preflight_succeeded -> approval_decision -> patch_applied -> pytest_finished -> session_finished
```

result: `<REPOSITORY_ROOT>\examples\llm_benchmark\results\replay_v03\bench05_red_herring`

Overall mechanism: **PASS**

