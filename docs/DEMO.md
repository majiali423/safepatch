# SafePatch Demo Guide

This guide runs a minimal end-to-end session on
`examples/buggy_calculator`: `divide` currently raises `ZeroDivisionError`
when `b == 0`, but the tests require `ValueError`.

## Prerequisites

- Python 3.11+
- Docker Desktop (or compatible daemon) running
- Package installed editable: `pip install -e ".[dev]"`

```powershell
cd <repo-root>
code-agent --docker-check
```

Expected: `OK: docker ok; image=code-agent-pytest:local`

If the image is missing:

```powershell
code-agent --build-image
```

## Demo repository

| Path | Role |
|---|---|
| `examples/buggy_calculator/` | Broken calculator under test |
| `examples/dry_run_fix_divide.json` | Deterministic tool script (no API key) |

Inspect the bug:

```powershell
# calculator.py — divide lacks a zero-divisor ValueError guard
# tests/test_calculator.py — expects ValueError on divide-by-zero
```

## Recommended command (dry-run)

Stable and suitable for documentation or CI-less local demos:

```powershell
code-agent examples\buggy_calculator `
  "divide raises ZeroDivisionError on b==0; it should raise ValueError." `
  --dry-run-script examples\dry_run_fix_divide.json `
  --yes
```

`--yes` auto-approves policy- and preflight-valid patches. Omit it to
practice interactive `approve` / `reject`.

## Expected flow

1. Repository imported into an isolated `working_copy`
2. Baseline Docker pytest fails on
   `tests/test_calculator.py::test_divide_by_zero_raises_value_error`
3. Tools: `get_repo_map` → `read_file` → `propose_patch`
4. Policy validation and exact patch preflight succeed
5. Approval (automatic with `--yes`), showing `patch_hash` / `working_tree_hash`
6. Exact apply; `attempts_used` becomes `1`
7. Docker pytest attempt 1 passes
8. Session status `SUCCEEDED`

## Live model run (optional)

Configure `.env` from `.env.example` (`OPENAI_API_KEY`,
`OPENAI_BASE_URL`, `CODE_AGENT_MODEL`), then omit `--dry-run-script`.

Live runs depend on model sampling and network availability; dry-run is
preferred when the goal is a reproducible demonstration of the control
path.

## Approving patches

Without `--yes`, the CLI prints diagnosis, affected files, risk notes,
and the full unified diff. Type `approve` or `reject`.

Only proposals that already passed policy and exact preflight are shown.
A new diff always re-runs policy, preflight, and approval.

## Docker testing

The model cannot invoke Docker. The controller calls
`DockerPytestRunner` with a fixed configuration (network disabled,
resource limits, non-root, timeout classification). Logs are written to
session artifacts as `baseline.log` and `attempt-*.log`.

## Artifacts to inspect

After the session finishes, open the printed `artifacts/` directory:

| File | What to verify |
|---|---|
| `summary.json` | `status`, `attempts_used`, preflight counters |
| `final.diff` | Contains the ValueError guard |
| `trace.jsonl` | Event order: propose → preflight → approval → apply → pytest |
| `attempt-1.log` | Public pytest success |

Do not commit `.env` or session directories. Runtime results under
`examples/llm_benchmark/results/` are gitignored.

## Troubleshooting

| Symptom | Check |
|---|---|
| `TEST_ENVIRONMENT_ERROR` | Docker daemon / image (`--docker-check`, `--build-image`) |
| `PATCH_NOT_APPLICABLE` | Regenerated diffs still fail exact context match |
| `REJECTED` | Patch was declined at approval |
| Dry-run script exhausted | Script length shorter than required tool turns |
