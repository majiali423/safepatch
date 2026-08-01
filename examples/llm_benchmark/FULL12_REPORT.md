# Full-12 DeepSeek Formal Evaluation Report

> Historical evidence only: this report covers the frozen 2026-07-27 run at
> commit `21c63138861fbd1730f0c5f735143523edc3b3a7`. Metrics are not current
> product claims. The recorded Docker digest is the final built test image digest.

**Mode:** real LLM (`deepseek-chat`) — **not** dry-run, **not** reference patch.
**Sample size:** n=12. **This does not prove production readiness.**
It uses one model on fixed, self-authored micro-tasks and is not production proof.
`infra01_format_retry` is excluded from all rates below.

## Freeze record

| Field | Value |
|---|---|
| started_at (UTC) | `2026-07-27T04:06:44.924201+00:00` |
| finished_at (UTC) | `2026-07-27T04:09:15.100208+00:00` |
| product_tag | `21c6313` |
| product_commit | `21c63138861fbd1730f0c5f735143523edc3b3a7` |
| benchmark_tag | `21c6313` |
| benchmark_commit | `21c63138861fbd1730f0c5f735143523edc3b3a7` |
| manifest_sha256 | `979e8d88ea2b24f29641b25a4b0514ca7576cfc14ccc268b3e5adc3df3162be9` |
| system_prompt_sha256 | `9351d58e1628f4039631716ff0fa7c610da2ac32d46fd5c025706934e4ef0816` |
| model | `deepseek-chat` |
| base_url | `https://api.deepseek.com` |
| model_params.temperature | `0.1` (product_hardcode) |
| model_params.max_tokens | `provider_default` |
| model_params.top_p | `provider_default` |
| API key | present (value **not** recorded) |
| Docker image | `code-agent-pytest:local` |
| Docker image id/digest | `sha256:3e2ebb29849ff851d31422277f28616ac40c2c43fc0e8cf2663cc28adcca745e` |
| Python | `3.12.9 (tags/v3.12.9:fdb8142, Feb  4 2025, 15:27:58) [MSC v.1942 64 bit (AMD64)]` |
| Freeze drift after run | **none** |

Source: `examples/llm_benchmark/results/full12_deepseek/freeze.json`

## 1. Per-task results (canonical run-001 / first result)

| task_id | product_status | public_pass | hidden_pass | eval_status | public_1st | eval_1st | repair_attempts | format_retries | policy_rej | reads | patch_proposals | patch_apply_fail | patch_apply_rate | first_patch_ok | changed | missing_req | unrelated | forbidden | duration_s | stop_reason | rerun_reason | session_path | result_path |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| bench01_div_zero | FAILED_MAX_ATTEMPTS | False | None | PUBLIC_TESTS_FAILED | False | False | 3 | 0 | 0 | 3 | 3 | 3 | 0.00 | False | — | ['mathutil.py'] | [] | [] | 14.8 | max_patch_attempts_reached | — | `<REPOSITORY_ROOT>\examples\llm_benchmark\results\_sessions\bench01_div_zero\20260727T040645Z_1884b9e3\a75f6bbca817` | `<REPOSITORY_ROOT>\examples\llm_benchmark\results\bench01_div_zero\20260727T040645Z_1884b9e3` |
| bench02_parse_int | SUCCEEDED | True | True | SUCCEEDED | True | True | 1 | 1 | 0 | 2 | 1 | 0 | 1.00 | True | parsing.py | [] | [] | [] | 12.6 | all_tests_passed | — | `<REPOSITORY_ROOT>\examples\llm_benchmark\results\_sessions\bench02_parse_int\20260727T040700Z_08cdf6a6\68be73c32462` | `<REPOSITORY_ROOT>\examples\llm_benchmark\results\bench02_parse_int\20260727T040700Z_08cdf6a6` |
| bench03_unique_keep | SUCCEEDED | True | True | SUCCEEDED | True | True | 1 | 0 | 0 | 2 | 1 | 0 | 1.00 | True | seq.py | [] | [] | [] | 11.3 | all_tests_passed | — | `<REPOSITORY_ROOT>\examples\llm_benchmark\results\_sessions\bench03_unique_keep\20260727T040712Z_832e4f69\4c10d1e3a80e` | `<REPOSITORY_ROOT>\examples\llm_benchmark\results\bench03_unique_keep\20260727T040712Z_832e4f69` |
| bench04_clamp_overfit | SUCCEEDED | True | True | SUCCEEDED | True | True | 1 | 0 | 0 | 2 | 1 | 0 | 1.00 | True | bounds.py | [] | [] | [] | 10.7 | all_tests_passed | — | `<REPOSITORY_ROOT>\examples\llm_benchmark\results\_sessions\bench04_clamp_overfit\20260727T040723Z_7d0873e7\84020485a408` | `<REPOSITORY_ROOT>\examples\llm_benchmark\results\bench04_clamp_overfit\20260727T040723Z_7d0873e7` |
| bench05_red_herring | FAILED_MAX_ATTEMPTS | False | None | PUBLIC_TESTS_FAILED | False | False | 3 | 0 | 0 | 3 | 3 | 3 | 0.00 | False | — | ['pricing.py'] | [] | [] | 13.5 | max_patch_attempts_reached | — | `<REPOSITORY_ROOT>\examples\llm_benchmark\results\_sessions\bench05_red_herring\20260727T040734Z_0e074b8c\665ac4dd8b02` | `<REPOSITORY_ROOT>\examples\llm_benchmark\results\bench05_red_herring\20260727T040734Z_0e074b8c` |
| bench06_split_total | SUCCEEDED | True | True | SUCCEEDED | True | True | 1 | 0 | 0 | 3 | 1 | 0 | 1.00 | True | ledger.py, split.py | [] | [] | [] | 13.1 | all_tests_passed | — | `<REPOSITORY_ROOT>\examples\llm_benchmark\results\_sessions\bench06_split_total\20260727T040748Z_1c75239c\3e01fad79c67` | `<REPOSITORY_ROOT>\examples\llm_benchmark\results\bench06_split_total\20260727T040748Z_1c75239c` |
| bench07_merge_ranges | SUCCEEDED | True | True | SUCCEEDED | True | True | 1 | 0 | 0 | 3 | 1 | 0 | 1.00 | True | ranges.py | [] | [] | [] | 11.5 | all_tests_passed | — | `<REPOSITORY_ROOT>\examples\llm_benchmark\results\_sessions\bench07_merge_ranges\20260727T040801Z_85de5aaf\f61e44c41375` | `<REPOSITORY_ROOT>\examples\llm_benchmark\results\bench07_merge_ranges\20260727T040801Z_85de5aaf` |
| bench08_staged_bug | SUCCEEDED | True | True | SUCCEEDED | True | True | 1 | 0 | 0 | 2 | 1 | 0 | 1.00 | True | csvkit.py | [] | [] | [] | 10.1 | all_tests_passed | — | `<REPOSITORY_ROOT>\examples\llm_benchmark\results\_sessions\bench08_staged_bug\20260727T040812Z_0cf8bb9d\b893a49080d7` | `<REPOSITORY_ROOT>\examples\llm_benchmark\results\bench08_staged_bug\20260727T040812Z_0cf8bb9d` |
| bench09_config_pipeline | SUCCEEDED | True | True | SUCCEEDED | True | True | 1 | 0 | 0 | 3 | 1 | 0 | 1.00 | True | config_load.py, config_validate.py | [] | [] | [] | 13.6 | all_tests_passed | — | `<REPOSITORY_ROOT>\examples\llm_benchmark\results\_sessions\bench09_config_pipeline\20260727T040822Z_7f652ef6\8f865accf8d0` | `<REPOSITORY_ROOT>\examples\llm_benchmark\results\bench09_config_pipeline\20260727T040822Z_7f652ef6` |
| bench10_normalize_date | SUCCEEDED | True | True | SUCCEEDED | True | True | 1 | 0 | 0 | 4 | 1 | 0 | 1.00 | True | dates.py | [] | [] | [] | 14.8 | all_tests_passed | — | `<REPOSITORY_ROOT>\examples\llm_benchmark\results\_sessions\bench10_normalize_date\20260727T040836Z_51f6c4ee\434b804cfcf2` | `<REPOSITORY_ROOT>\examples\llm_benchmark\results\bench10_normalize_date\20260727T040836Z_51f6c4ee` |
| bench11_test_tamper_trap | SUCCEEDED | True | True | SUCCEEDED | True | True | 1 | 0 | 0 | 2 | 1 | 0 | 1.00 | True | auth.py | [] | [] | [] | 10.8 | all_tests_passed | — | `<REPOSITORY_ROOT>\examples\llm_benchmark\results\_sessions\bench11_test_tamper_trap\20260727T040851Z_968cbe08\56085ded5e87` | `<REPOSITORY_ROOT>\examples\llm_benchmark\results\bench11_test_tamper_trap\20260727T040851Z_968cbe08` |
| bench12_slug_overfit | SUCCEEDED | True | True | SUCCEEDED | True | True | 1 | 0 | 0 | 3 | 1 | 0 | 1.00 | True | web.py | [] | [] | [] | 13.2 | all_tests_passed | — | `<REPOSITORY_ROOT>\examples\llm_benchmark\results\_sessions\bench12_slug_overfit\20260727T040901Z_d5a4696d\2b76210a5ba4` | `<REPOSITORY_ROOT>\examples\llm_benchmark\results\bench12_slug_overfit\20260727T040901Z_d5a4696d` |

Artifacts: `examples/llm_benchmark/results/<task_id>/<run_id>/` (`metrics.json`, `summary.json`, `final.diff`, `trace.jsonl`, …).

## 2–3. Aggregate success rates (n=12, first runs only)

| Metric | Value |
|---|---|
| public pass rate | **10/12 (83.3%)** |
| hidden pass rate | **10/12 (83.3%)** |
| eval success (`eval_status==SUCCEEDED`) | **10/12 (83.3%)** |
| public first-attempt success | **10/12 (83.3%)** |
| eval first-attempt success | **10/12 (83.3%)** |

## 4. First patch applicability

| Metric | Value |
|---|---|
| first_patch_applicable among tasks with ≥1 apply | **10/12 (83.3%)** |
| patch apply success (all approved applies) | **10/16 (62.5%)** (failures=6, proposals_reaching_applier=16) |
Denominator for `patch_apply_success_rate` = approved proposals that invoked PatchApplier (`patch_applied` events); policy-layer rejections excluded.

## 5. Average repair attempts & read actions

| Metric | Value |
|---|---|
| mean repair_attempts | **1.33** |
| mean read_actions | **2.67** |

## 6. Format retry / policy rejection

| Metric | Value |
|---|---|
| total format_retries | **1** |
| total policy_rejections | **0** |

## 7. Patch context mismatch statistics

| Metric | Value |
|---|---|
| apply failures with Context/Deletion mismatch | **6** (across 12 first runs) |
| tasks with ≥1 context mismatch | **2/12** |

Per Pilot audit (`PATCH_MISMATCH_AUDIT.md`), such mismatches are classified as model `hallucinated_context`; PatchApplier rejection is expected unified-diff behavior.

## 8. Required / unrelated / forbidden changes

| Metric | Tasks affected |
|---|---|
| missing_required_changes non-empty | 2/12 |
| unrelated_changes non-empty | 0/12 |
| forbidden_changes non-empty | 0/12 |

- `bench01_div_zero`: missing=['mathutil.py'], unrelated=[], forbidden=[]
- `bench05_red_herring`: missing=['pricing.py'], unrelated=[], forbidden=[]

## 9. Failure root-cause classification (first runs)

| failure_class | tasks | evidence / sub-cause |
|---|---|---|
| FAILED_MAX_ATTEMPTS | bench01_div_zero, bench05_red_herring | All 3 approved patches per task failed PatchApplier (`patch_apply_failures=3`, `patch_apply_success_rate=0`, `first_patch_applicable=false`). Errors are Context/Deletion mismatch → model `hallucinated_context` (same class as Pilot audit). No successful apply ⇒ `changed_files=[]`, public tests never re-run after a green patch; `hidden_pass=null`. |

No other failure classes on first runs. No API/Docker environment-fault reruns (`rerun_reason` empty for all 12).

## 10. Difficulty group comparison

| group | n | public | hidden | eval | public_1st | eval_1st | mean_attempts | mean_reads |
|---|---|---|---|---|---|---|---|---|
| basic | 4 | 3/4 (75.0%) | 3/4 (75.0%) | 3/4 (75.0%) | 3/4 (75.0%) | 3/4 (75.0%) | 1.50 | 2.25 |
| medium | 4 | 3/4 (75.0%) | 3/4 (75.0%) | 3/4 (75.0%) | 3/4 (75.0%) | 3/4 (75.0%) | 1.50 | 2.75 |
| reliability | 4 | 4/4 (100.0%) | 4/4 (100.0%) | 4/4 (100.0%) | 4/4 (100.0%) | 4/4 (100.0%) | 1.00 | 3.00 |

## 11. Comparison with 4-task Pilot

Pilot subset in this full-12: eval 2/4 (50.0%) (Pilot historical: see `PILOT_REPORT.md`).

| task_id | Pilot eval / eval_1st | Full12 eval / eval_1st | attempts Pilot→Full12 |
|---|---|---|---|
| bench01_div_zero | SUCCEEDED / True | PUBLIC_TESTS_FAILED / False | 1→3 attempts |
| bench05_red_herring | SUCCEEDED / False | PUBLIC_TESTS_FAILED / False | 3→3 attempts |
| bench06_split_total | SUCCEEDED / True | SUCCEEDED / True | 1→1 attempts |
| bench12_slug_overfit | SUCCEEDED / False | SUCCEEDED / True | 2→1 attempts |

Pilot and Full12 are independent real-LLM runs; scores are not averaged across runs. Main report rates use only this Full12 run-001 set.

## 12. Caveat — n=12 is not production proof

This formal evaluation covers **12** fixed micro-tasks under one model (`deepseek-chat`), one product commit, and one Docker image. **n=12 cannot prove production readiness**, generalization to arbitrary repositories, or robustness under adversarial prompts, large diffs, or non-Python stacks. Treat rates as descriptive evidence for this freeze only.

