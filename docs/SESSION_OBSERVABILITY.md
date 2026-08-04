# Session Observability

Status: **Unreleased reliability design; current product tag v0.3.1**. SafePatch writes a machine-readable
`observability` object into each session `summary.json`. Counters are
updated at the event site (model call, tool dispatch, pytest start) — not
by parsing `trace.jsonl` after the fact.

SafePatch does **not** send external telemetry. Observability is for local
audit, demos, and benchmarks. It does **not** estimate monetary cost from
token counts or current model prices.

## Fields (summary)

`summary_schema_version` is `1` when the observability card is present.
Existing top-level summary keys (`status`, `attempts_used`, format/regen
counters, preflight stats, …) remain unchanged.

```json
{
  "summary_schema_version": 1,
  "status": "SUCCEEDED",
  "attempts_used": 1,
  "observability": {
    "started_at": "2026-07-27T12:00:00+00:00",
    "finished_at": "2026-07-27T12:00:12+00:00",
    "duration_ms": 12050,
    "model": {
      "provider": "openai_compatible",
      "name": "deepseek-chat",
      "tool_calling_protocol": "custom_json",
      "temperature": 0.1,
      "max_format_retries": 2,
      "max_patch_regeneration_retries": 2,
      "max_repair_attempts": 3,
      "calls": 4,
      "usage": {
        "prompt_tokens": 1200,
        "completion_tokens": 350,
        "total_tokens": 1550,
        "cached_tokens": null,
        "source": "provider",
        "available": true,
        "complete": true,
        "calls_with_usage": 4,
        "calls_without_usage": 0
      }
    },
    "tools": {
      "total_calls": 4,
      "read_calls": 3,
      "proposal_calls": 1
    },
    "retries": {
      "format": 0,
      "patch_regeneration": 0,
      "repair_attempts": 1
    },
    "tests": {
      "total_runs": 2,
      "baseline_runs": 1,
      "post_apply_runs": 1
    }
  }
}
```

## Counter definitions

| Counter | Definition |
|---|---|
| `model.calls` | **Provider invocation attempts** (counted when the live API or dry-run fetch starts), not merely successful responses. Includes timeouts, HTTP/SDK errors, illegal JSON, and schema failures. Local config errors before any provider contact (e.g. missing API key) do **not** increment this counter. Distinct from `retries.format`. |
| `retries.format` | Format / schema retries only (`total_format_retries_used`) |
| `retries.patch_regeneration` | Inapplicable-patch regenerations (`total_patch_regeneration_retries`) |
| `retries.repair_attempts` | Successful exact apply followed by post-apply pytest (`attempts_used`) |
| `tools.proposal_calls` | Schema-valid `propose_patch` proposals that parse successfully (policy failures still count) |
| `tools.read_calls` | Attempts to run inspection tools: `list_tree`, `read_file`, `search_text`, `search_symbol`, `get_repo_map`, `get_current_diff` (success or `ToolError`) |
| `tools.total_calls` | Schema-valid tool names handled in the analyze loop (reads, `propose_patch`, `finish`, unknown tools) |
| `tests.baseline_runs` | Times the product pytest runner was started for baseline |
| `tests.post_apply_runs` | Times the runner was started after a successful apply |
| `tests.total_runs` | `baseline_runs + post_apply_runs` (product only) |

Hidden evaluation pytest runs are recorded only under eval metrics as
`hidden_pytest_runs` and are **never** added to product `tests.*`.

## Tokens

Token fields come only from the provider response `usage` object
(normalized in `code_agent.llm.normalize_token_usage`). Character-count
estimates are never used. Missing usage on one call does not fail the
session.

| Field | Meaning |
|---|---|
| `prompt_tokens` / `completion_tokens` / `total_tokens` / `cached_tokens` | Sum of values the provider explicitly returned. `null` = never reported on any call that contributed. |
| `calls_with_usage` | Model calls whose provider response included usable usage |
| `calls_without_usage` | Model calls with no usable usage (including transport failures after invocation started) |
| `complete` | `true` only when every `model.calls` attempt had usage |
| `source` | `provider` (all calls had usage), `provider_partial` (some did), or `unavailable` (none did) |
| `available` | `true` when at least one call contributed token totals |

When `complete` is `false`, token totals are **only the known partial sum** —
they do **not** represent full session consumption. Observability is not a
billing invoice and must not be treated as a cost bill.

`0` is a reported zero. `null` means “not provided”.

## Duration

- `started_at` / `finished_at`: UTC ISO 8601 wall-clock timestamps for display
- `duration_ms`: `perf_counter` (monotonic) elapsed time; not wall-clock subtraction
- Terminal paths (`ERROR`, `PATCH_NOT_APPLICABLE`, `PATCH_BASE_CHANGED`, …)
  still write `finished_at` and `duration_ms`

## Protocol metadata

The product uses a **custom JSON** tool-call protocol (`tool` + `args` in
the model message body), not native provider function-calling APIs.
`tool_calling_protocol` is therefore `custom_json`.

API keys, raw provider payloads, and base-URL query strings are not stored
in `observability`.
