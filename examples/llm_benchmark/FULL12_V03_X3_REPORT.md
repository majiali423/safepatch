# v0.3 Full12 × 3 DeepSeek Report

**Mode:** real `deepseek-chat`, 12 tasks × 3 independent rounds = **36** canonical first results. No dry-run / reference. No product/Prompt/task changes between rounds.

**Caveat:** n=36 still cannot prove production readiness. Comparison to v0.2 single-round Full12 is **not** a paired A/B under identical sampling.

## Freeze

| Field | Value |
|---|---|
| started_at | `2026-07-27T07:46:07.742633+00:00` |
| finished_at | `2026-07-27T07:52:47.667965+00:00` |
| product_tag | `v0.3.0` |
| product_commit | `2370c388c98f024a2fa373983b673bd3d3f589b9` |
| benchmark_commit | `2370c388c98f024a2fa373983b673bd3d3f589b9` |
| manifest_sha256 | `979e8d88ea2b24f29641b25a4b0514ca7576cfc14ccc268b3e5adc3df3162be9` |
| system_prompt_sha256 | `199dbeb47d9d0a401e941bcfc4b56f62a4906ae97aa89fb5cf99aa65cbb2ee7c` |
| model | `deepseek-chat` |
| base_url | `https://api.deepseek.com` |
| temperature | `0.1` (product_hardcode) |
| docker | `code-agent-pytest:local` `sha256:3e2ebb29849ff851d31422277f28616ac40c2c43fc0e8cf2663cc28adcca745e` |
| freeze_drift | `[]` |

## 1. Round 1 results (n=12)

| task | product | eval | public | hidden | first_app | preflight_fail | regen | recovered | repair | model_calls | after_pf | NA | duration_s |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| bench01_div_zero | SUCCEEDED | SUCCEEDED | True | True | True | 0 | 0 | False | 1 | 2 | 0 | False | 10.9 |
| bench02_parse_int | SUCCEEDED | SUCCEEDED | True | True | True | 0 | 0 | False | 1 | 2 | 0 | False | 9.4 |
| bench03_unique_keep | SUCCEEDED | SUCCEEDED | True | True | True | 0 | 0 | False | 1 | 3 | 0 | False | 10.5 |
| bench04_clamp_overfit | SUCCEEDED | SUCCEEDED | True | True | True | 0 | 0 | False | 1 | 3 | 0 | False | 10.5 |
| bench05_red_herring | SUCCEEDED | SUCCEEDED | True | True | False | 2 | 2 | True | 1 | 6 | 0 | False | 15.7 |
| bench06_split_total | SUCCEEDED | SUCCEEDED | True | True | True | 0 | 0 | False | 1 | 4 | 0 | False | 12.6 |
| bench07_merge_ranges | SUCCEEDED | SUCCEEDED | True | True | True | 0 | 0 | False | 1 | 4 | 0 | False | 11.6 |
| bench08_staged_bug | SUCCEEDED | SUCCEEDED | True | True | True | 0 | 0 | False | 1 | 2 | 0 | False | 9.1 |
| bench09_config_pipeline | SUCCEEDED | SUCCEEDED | True | True | True | 0 | 0 | False | 1 | 4 | 0 | False | 12.6 |
| bench10_normalize_date | SUCCEEDED | SUCCEEDED | True | True | True | 0 | 0 | False | 1 | 5 | 0 | False | 14.0 |
| bench11_test_tamper_trap | SUCCEEDED | SUCCEEDED | True | True | True | 0 | 0 | False | 1 | 3 | 0 | False | 9.6 |
| bench12_slug_overfit | SUCCEEDED | SUCCEEDED | True | True | True | 0 | 0 | False | 1 | 3 | 0 | False | 10.5 |

Round 1 eval success: **12/12 (100.0%)**

## 1. Round 2 results (n=12)

| task | product | eval | public | hidden | first_app | preflight_fail | regen | recovered | repair | model_calls | after_pf | NA | duration_s |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| bench01_div_zero | SUCCEEDED | SUCCEEDED | True | True | True | 0 | 0 | False | 1 | 2 | 0 | False | 8.4 |
| bench02_parse_int | SUCCEEDED | SUCCEEDED | True | True | True | 0 | 0 | False | 1 | 2 | 0 | False | 9.8 |
| bench03_unique_keep | SUCCEEDED | SUCCEEDED | True | True | True | 0 | 0 | False | 1 | 3 | 0 | False | 10.9 |
| bench04_clamp_overfit | SUCCEEDED | SUCCEEDED | True | True | True | 0 | 0 | False | 1 | 3 | 0 | False | 10.2 |
| bench05_red_herring | SUCCEEDED | SUCCEEDED | True | True | True | 0 | 0 | False | 1 | 2 | 0 | False | 8.6 |
| bench06_split_total | SUCCEEDED | SUCCEEDED | True | True | True | 0 | 0 | False | 1 | 4 | 0 | False | 12.0 |
| bench07_merge_ranges | SUCCEEDED | SUCCEEDED | True | True | True | 0 | 0 | False | 1 | 4 | 0 | False | 12.3 |
| bench08_staged_bug | SUCCEEDED | SUCCEEDED | True | True | True | 0 | 0 | False | 1 | 3 | 0 | False | 10.7 |
| bench09_config_pipeline | SUCCEEDED | SUCCEEDED | True | True | True | 0 | 0 | False | 1 | 4 | 0 | False | 12.4 |
| bench10_normalize_date | SUCCEEDED | SUCCEEDED | True | True | True | 0 | 0 | False | 1 | 5 | 0 | False | 15.7 |
| bench11_test_tamper_trap | SUCCEEDED | SUCCEEDED | True | True | True | 0 | 0 | False | 1 | 3 | 0 | False | 9.9 |
| bench12_slug_overfit | SUCCEEDED | SUCCEEDED | True | True | True | 0 | 0 | False | 1 | 2 | 0 | False | 10.1 |

Round 2 eval success: **12/12 (100.0%)**

## 1. Round 3 results (n=12)

| task | product | eval | public | hidden | first_app | preflight_fail | regen | recovered | repair | model_calls | after_pf | NA | duration_s |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| bench01_div_zero | SUCCEEDED | SUCCEEDED | True | True | True | 0 | 0 | False | 1 | 2 | 0 | False | 8.7 |
| bench02_parse_int | SUCCEEDED | SUCCEEDED | True | True | True | 0 | 0 | False | 1 | 2 | 0 | False | 9.3 |
| bench03_unique_keep | SUCCEEDED | SUCCEEDED | True | True | True | 0 | 0 | False | 1 | 3 | 0 | False | 9.8 |
| bench04_clamp_overfit | SUCCEEDED | SUCCEEDED | True | True | True | 0 | 0 | False | 1 | 3 | 0 | False | 10.0 |
| bench05_red_herring | SUCCEEDED | SUCCEEDED | True | True | True | 0 | 0 | False | 1 | 2 | 0 | False | 7.7 |
| bench06_split_total | SUCCEEDED | SUCCEEDED | True | True | True | 0 | 0 | False | 1 | 4 | 0 | False | 11.3 |
| bench07_merge_ranges | SUCCEEDED | SUCCEEDED | True | True | True | 0 | 0 | False | 1 | 4 | 0 | False | 11.7 |
| bench08_staged_bug | SUCCEEDED | SUCCEEDED | True | True | True | 0 | 0 | False | 1 | 2 | 0 | False | 9.5 |
| bench09_config_pipeline | SUCCEEDED | SUCCEEDED | True | True | True | 0 | 0 | False | 1 | 4 | 0 | False | 12.2 |
| bench10_normalize_date | SUCCEEDED | SUCCEEDED | True | True | True | 0 | 0 | False | 1 | 5 | 0 | False | 12.9 |
| bench11_test_tamper_trap | SUCCEEDED | SUCCEEDED | True | True | True | 0 | 0 | False | 1 | 3 | 0 | False | 11.1 |
| bench12_slug_overfit | SUCCEEDED | SUCCEEDED | True | True | False | 2 | 2 | True | 1 | 7 | 0 | False | 17.1 |

Round 3 eval success: **12/12 (100.0%)**

## 2–3. Aggregate success (n=36 first runs)

| Metric | Value |
|---|---|
| public pass | **36/36 (100.0%)** |
| hidden pass | **36/36 (100.0%)** |
| eval success | **36/36 (100.0%)** |
| per-round eval success | R1=100.0%, R2=100.0%, R3=100.0%; mean=100.0%; range=[100.0%, 100.0%] |

## 4. First patch applicable

**34/36 (94.4%)** (among runs with ≥1 preflight outcome recorded)

## 5–6. Preflight failures & regeneration

| Metric | Value |
|---|---|
| total patch_preflight_failures | **4** |
| total regeneration retries | **4** |
| sessions with ≥1 regen | **2/36** |
| regeneration recovery rate | **2/2 (100.0%)** (recovered / sessions-with-regen) |

## 7–8. PATCH_NOT_APPLICABLE & after-preflight apply failures

- PATCH_NOT_APPLICABLE sessions: **0/36**
- total patch_apply_failures_after_preflight: **0**

## 9. Repair attempts, model calls, duration

| Metric | mean | sum |
|---|---|---|
| repair_attempts | 1.00 | 36 |
| model_call_count | 3.31 | 119 |
| duration_sec | 11.1 | 399.4 |

## 10. Required / unrelated / forbidden

- missing_required_changes non-empty: 0/36
- unrelated_changes non-empty: 0/36
- forbidden_changes non-empty: 0/36

## 11. vs v0.2 single-round Full12 (descriptive, not paired A/B)

v0.2 Full12 eval success was **10/12 (83.3%)** (single round). v0.3 ×3 pooled eval is **36/36 (100.0%)**.

| task | v0.2 eval | v0.3 R1 | R2 | R3 |
|---|---|---|---|---|
| bench01_div_zero | N | Y | Y | Y |
| bench02_parse_int | Y | Y | Y | Y |
| bench03_unique_keep | Y | Y | Y | Y |
| bench04_clamp_overfit | Y | Y | Y | Y |
| bench05_red_herring | N | Y | Y | Y |
| bench06_split_total | Y | Y | Y | Y |
| bench07_merge_ranges | Y | Y | Y | Y |
| bench08_staged_bug | Y | Y | Y | Y |
| bench09_config_pipeline | Y | Y | Y | Y |
| bench10_normalize_date | Y | Y | Y | Y |
| bench11_test_tamper_trap | Y | Y | Y | Y |
| bench12_slug_overfit | Y | Y | Y | Y |

Sampling differs across rounds and from v0.2; do not treat deltas as causal proof that preflight alone fixed bench01/05.

## 12. Caveat

n=36 fixed micro-tasks under one model, one product tag, and one Docker image **cannot prove production readiness**, generalization, or robustness beyond this freeze.

