# DeepSeek Pilot Report (4 tasks)

**Mode:** real LLM (`deepseek-chat`) — **not** dry-run, **not** reference patch.  
**Sample size:** 4 tasks only. **Do not** infer production readiness from this pilot.

## Freeze record

| Field | Value |
|---|---|
| started_at (UTC) | `2026-07-27T03:25:44.655073+00:00` |
| product_tag | `21c6313` |
| product_commit | `21c63138861fbd1730f0c5f735143523edc3b3a7` |
| benchmark_commit | `21c63138861fbd1730f0c5f735143523edc3b3a7` |
| model | `deepseek-chat` |
| base_url | `https://api.deepseek.com` |
| API key | present (value **not** recorded) |
| Docker image | `code-agent-pytest:local` |
| Docker image id | `sha256:3e2ebb29849ff851d31422277f28616ac40c2c43fc0e8cf2663cc28adcca745e` |
| Python | `3.12.9` |
| Freeze drift after run | **none** (`freeze_drift_errors: []`) |

Source: `examples/llm_benchmark/results/pilot_deepseek/freeze.json`

## Consistency gate

If `product_status == SUCCEEDED` disagrees with `final_tests_passed`, eval marks **`ARTIFACT_INCONSISTENT`** and does **not** count either side as benchmark success.  
This pilot: all 4 runs `artifact_consistent=true`.

## 1. Summary table (first / canonical runs)

| task_id | run_id | product_status | public_pass | hidden_pass | eval_status | public_1st | eval_1st | attempts | format_retries | policy_rej | reads | changed | missing_req | unrelated | forbidden | duration_s | stop_reason |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| bench01_div_zero | 20260727T032544Z_0ff4dd32 | SUCCEEDED | true | true | SUCCEEDED | true | true | 1 | 0 | 0 | 1 | mathutil.py | [] | [] | [] | 10.8 | all_tests_passed |
| bench05_red_herring | 20260727T032555Z_f23d5800 | SUCCEEDED | true | true | SUCCEEDED | false | false | 3 | 0 | 0 | 3 | pricing.py | [] | [] | [] | 15.0 | all_tests_passed |
| bench06_split_total | 20260727T032610Z_aa81fcc3 | SUCCEEDED | true | true | SUCCEEDED | true | true | 1 | 0 | 0 | 3 | ledger.py, split.py | [] | [] | [] | 13.7 | all_tests_passed |
| bench12_slug_overfit | 20260727T032624Z_8fa76e59 | SUCCEEDED | true | true | SUCCEEDED | false | false | 2 | 0 | 0 | 6 | web.py | [] | [] | [] | 21.3 | all_tests_passed |

Per-run artifacts: `examples/llm_benchmark/results/<task_id>/<run_id>/`  
(`metrics.json`, `summary.json`, `final.diff`, `trace.jsonl`, `baseline.log`, `attempt-*.log`, `hidden.log`, `eval_trace.jsonl`)

## 2–5. Aggregate rates (n=4, first runs)

| Metric | Value |
|---|---|
| public pass rate | **4/4 (100%)** |
| hidden pass rate | **4/4 (100%)** |
| eval success (`eval_status==SUCCEEDED`) | **4/4 (100%)** |
| public first-attempt success | **2/4 (50%)** — bench01, bench06 |
| eval first-attempt success | **2/4 (50%)** — same |
| total format_retries | **0** across all tasks |
| total policy_rejections | **0** |
| missing_required_changes | none |
| unrelated_changes | none |
| forbidden_changes | none |
| env/timeout reruns | none |

## 6. Required / unrelated / forbidden

All four models edits stayed inside `allowed_files` / `required_files`.  
Notably:

- **bench05:** did **not** touch `legacy_pricing.py` (red herring).
- **bench06:** changed **both** `split.py` and `ledger.py` (required multi-file).
- **bench12:** generalized slugify enough to pass hidden (not a public-only hardcode).

## 7. Failure root-cause analysis

**No benchmark failures** in this pilot (`failure_class=SUCCEEDED` for all 4).

Non-failure observations (still useful):

| task | Observation |
|---|---|
| bench05 | Attempts 1–2: **patch apply context mismatch** on `pricing.py` docstring hunk; attempt 3 applied and passed public+hidden. Counts as repair loop, not format retry. |
| bench12 | Attempt 1: context mismatch in `web.py` docstring; attempt 2 succeeded with full slug rules (hidden passed). |
| bench01 / bench06 | Clean first-attempt public+hidden success. |

## 8. Reference patch vs real LLM (strictly separate)

| Layer | What it is | Result |
|---|---|---|
| **Reference** (`run_pilot_reference.py`) | Frozen gold patch; **not** an LLM | 4/4 public+hidden (infra proof only) |
| **Real DeepSeek** (this report) | Live `deepseek-chat` via `.env` | 4/4 `eval_status=SUCCEEDED` |

Do **not** mix these numbers. Reference proves task solvability; this report measures one model on n=4.

## 9. Limits / caveats

- **n=4** pilot only — remaining 8 tasks **not** implemented or run.
- No product / task / TASK.txt / tests / hidden / reference edits during this run (asset fingerprint drift empty).
- `--yes` auto-approved policy-valid patches (no human reject signal).
- Cannot claim production availability from this sample.

## Runner / infra touched (eval-only)

- `examples/llm_benchmark/metrics_lib.py` — `ARTIFACT_INCONSISTENT` gate
- `examples/llm_benchmark/run_deepseek_pilot.py` — real LLM orchestration
- `examples/llm_benchmark/PILOT_REPORT.md` — this file
- `tests/test_llm_benchmark_metrics.py` — consistency unit tests

**`code_agent/` product code was not modified.**
