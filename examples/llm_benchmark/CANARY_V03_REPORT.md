# v0.3 DeepSeek Canary Report (4 tasks)

> Historical evidence only: frozen on 2026-07-27 at commit
> `2370c388c98f024a2fa373983b673bd3d3f589b9`; not a current product claim.

**Mode:** real `deepseek-chat`. Not dry-run / reference. Product/Prompt/tasks/hidden/reference untouched during this run.

## Freeze

| Field | Value |
|---|---|
| started_at | `2026-07-27T07:36:25.042691+00:00` |
| product_commit | `2370c388c98f024a2fa373983b673bd3d3f589b9` |
| product_tag | `v0.3.0` |
| model | `deepseek-chat` |
| base_url | `https://api.deepseek.com` |
| docker | `code-agent-pytest:local` `sha256:3e2ebb29849ff851d31422277f28616ac40c2c43fc0e8cf2663cc28adcca745e` |
| freeze_drift | `[]` |

## Per-task results

| task | product_status | eval_status | public | hidden | preflight_fail | regen | first_applicable | regen_recovered | repair | pytest_runs | apply_after_pf | model_calls | approvals | duration_s | v02_status → now |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| bench01_div_zero | SUCCEEDED | SUCCEEDED | True | True | 0 | 0 | True | False | 1 | 2 | 0 | 2 | 1 | 9.6 | FAILED_MAX_ATTEMPTS → SUCCEEDED |
| bench05_red_herring | SUCCEEDED | SUCCEEDED | True | True | 0 | 0 | True | False | 1 | 2 | 0 | 2 | 1 | 7.9 | FAILED_MAX_ATTEMPTS → SUCCEEDED |
| bench06_split_total | SUCCEEDED | SUCCEEDED | True | True | 0 | 0 | True | False | 1 | 2 | 0 | 4 | 1 | 11.5 | SUCCEEDED → SUCCEEDED |
| bench12_slug_overfit | SUCCEEDED | SUCCEEDED | True | True | 0 | 0 | True | False | 1 | 2 | 0 | 3 | 1 | 10.6 | SUCCEEDED → SUCCEEDED |

## Special checks

### `bench01_div_zero`
- preflight fail never approved: **True** (approval_without_prior_preflight_success=0)
- preflight fail did not alone inflate repair: **True** (repair=1, preflight_fail=0, preflight_ok=1)
- repair_attempts == pytest_after_apply: **True** (1 == 1)
- patch_apply_failures_after_preflight: **0**
- recovered_from_v02_FAILED_MAX_ATTEMPTS: **True**
- session: `<REPOSITORY_ROOT>\examples\llm_benchmark\results\_sessions\bench01_div_zero\20260727T073625Z_bd358587\06c8264f4ace`
- result: `<REPOSITORY_ROOT>\examples\llm_benchmark\results\bench01_div_zero\20260727T073625Z_bd358587`

### `bench05_red_herring`
- preflight fail never approved: **True** (approval_without_prior_preflight_success=0)
- preflight fail did not alone inflate repair: **True** (repair=1, preflight_fail=0, preflight_ok=1)
- repair_attempts == pytest_after_apply: **True** (1 == 1)
- patch_apply_failures_after_preflight: **0**
- recovered_from_v02_FAILED_MAX_ATTEMPTS: **True**
- session: `<REPOSITORY_ROOT>\examples\llm_benchmark\results\_sessions\bench05_red_herring\20260727T073635Z_bcaa3a05\89d9ef1bf86c`
- result: `<REPOSITORY_ROOT>\examples\llm_benchmark\results\bench05_red_herring\20260727T073635Z_bcaa3a05`

### `bench06_split_total`
- preflight fail never approved: **True** (approval_without_prior_preflight_success=0)
- preflight fail did not alone inflate repair: **True** (repair=1, preflight_fail=0, preflight_ok=1)
- repair_attempts == pytest_after_apply: **True** (1 == 1)
- patch_apply_failures_after_preflight: **0**
- recovered_from_v02_FAILED_MAX_ATTEMPTS: **False**
- session: `<REPOSITORY_ROOT>\examples\llm_benchmark\results\_sessions\bench06_split_total\20260727T073642Z_4a422a06\c436bf42043b`
- result: `<REPOSITORY_ROOT>\examples\llm_benchmark\results\bench06_split_total\20260727T073642Z_4a422a06`

### `bench12_slug_overfit`
- preflight fail never approved: **True** (approval_without_prior_preflight_success=0)
- preflight fail did not alone inflate repair: **True** (repair=1, preflight_fail=0, preflight_ok=1)
- repair_attempts == pytest_after_apply: **True** (1 == 1)
- patch_apply_failures_after_preflight: **0**
- recovered_from_v02_FAILED_MAX_ATTEMPTS: **False**
- session: `<REPOSITORY_ROOT>\examples\llm_benchmark\results\_sessions\bench12_slug_overfit\20260727T073654Z_1c00ca8f\aab288373451`
- result: `<REPOSITORY_ROOT>\examples\llm_benchmark\results\bench12_slug_overfit\20260727T073654Z_1c00ca8f`

## Aggregate

- eval success: **4/4**
- total apply_after_preflight failures: **0**
- total preflight failures: **0**
- total regenerations: **0**

n=4 canary only — not Full12, not production proof. v0.2 Full12 numbers remain historical.

