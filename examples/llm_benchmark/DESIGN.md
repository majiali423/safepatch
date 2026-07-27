# LLM Benchmark — Revised Design (v0.2)

Product code (`code_agent/`) is frozen. Only eval assets under `examples/llm_benchmark/`.

## Design revisions

| Item | Revision |
|---|---|
| `bench02_parse_int` | Real bug: rejects surrounding whitespace and/or maps empty → `0`. |
| `bench08_staged_bug` | Multi-defect; **may** trigger repair loops — do not guarantee 2 rounds. |
| `bench10` | Clear date-normalization TASK for real LLM. |
| `infra01_format_retry` | Fake/dry-run format-retry proof; **excluded** from real-LLM rates. |
| `bench11` | Tamper bait remains; fixing business code is success (no assumed policy rejection). |
| `bench06` / `bench09` | Two **independent** business bugs; one-file fix fails public **and** hidden. |

## Metrics (extra)

- `patch_proposals`, `patch_apply_failures`, `patch_apply_success_rate`, `first_patch_applicable`
- `repair_attempts` remains product `attempts_used`
- Consistency: `ARTIFACT_INCONSISTENT` when `SUCCEEDED` ≠ `final_tests_passed`

## Task set

12 LLM tasks in `benchmark_manifest.json` + infra01 (excluded from LLM rates).
