# End-to-End Walkthrough

This document explains the SafePatch control path using the
`examples/buggy_calculator` scenario: divide-by-zero should raise
`ValueError`, not `ZeroDivisionError`.

Verification layers (do not conflate them):

1. **Unit tests** — policy, workspace, patch parsing, and related logic
2. **Integration tests with test doubles** — host or mocked runners
3. **Real Docker runtime** — requires a local Docker daemon and image

## 1. Role of pytest

pytest is the sole correctness oracle for repair attempts.

- **Baseline** — proves the repository is currently failing
- **Post-patch** — proves whether an applied change fixed public tests
- Fixed invocation style: `python -m pytest -q -p no:cacheprovider`
  (executed inside Docker by the product runner)

The model cannot choose how tests are run; the controller owns Runtime.

## 2. Why a working copy is required

Given a source repository path, SafePatch:

1. Validates the directory
2. Creates a temporary session directory
3. Copies the tree into `working_copy`
4. Restricts all reads, patches, and tests to that copy

The original repository is not modified. Large or irrelevant paths such
as `.git` and virtualenvs are skipped on import.

## 3. Repository map

An AST-based map summarizes each `.py` file (imports, classes, functions,
`test_*` entry points, syntax errors) without dumping full sources into
the prompt. The model uses the map to decide which `read_file` windows to
request.

## 4. Available tools

| Tool | Purpose |
|---|---|
| `get_repo_map` | Structural overview |
| `list_tree` / `search_text` / `search_symbol` | Locate symbols and text |
| `read_file` | Bounded line-range reads |
| `get_current_diff` | Inspect changes so far |
| `propose_patch` | Submit a unified diff proposal |
| `finish` | End without a further patch |

Typical path: map → locate failing tests / symbols → read → propose.

## 5. Unified diffs

Proposals must be unified diffs. After `propose_patch`:

1. Policy validation (paths, size, test integrity)
2. Exact patch preflight (same matcher as the applier)
3. Human approval (bound to content hashes)
4. Exact apply with rollback on failure

Context mismatches fail preflight and trigger regeneration rather than
consuming a repair attempt.

## 6. Approval

Read-only tools execute automatically. Writes require approval:

1. Model proposes a patch that already passed policy and preflight
2. Status enters `AWAITING_APPROVAL`
3. CLI shows diagnosis, files, diff, risks, planned tests
4. Operator chooses `approve` or `reject`

`--yes` auto-approves. Rejection ends the session as `REJECTED`.

## 7. Docker pytest runtime

The model has no Docker tool. `DockerPytestRunner` uses a fixed profile:

- `--network none`
- Memory / CPU / pids limits
- Non-root user
- Wall-clock timeout with distinct error classification
- `--rm` cleanup
- Mount of `working_copy` only
- No host API keys in the container

Build the image with `safepatch --build-image`.

## 8. Repair loop

Approximate product states:

```text
ANALYZING → PATCH_PROPOSED → (preflight) → AWAITING_APPROVAL
→ PATCH_APPLIED → TESTING
```

- Tests pass → `SUCCEEDED`
- Tests fail with attempts remaining → return to analyzing with failure context
- Attempts exhausted after applied patches → `FAILED_MAX_ATTEMPTS`
- Preflight regeneration exhausted → `PATCH_NOT_APPLICABLE`

Baseline tests do not consume repair attempts. Applied patches accumulate
on the working copy unless an apply failure rolls back.

## 9. Trace vs artifacts

| | Trace | Artifacts |
|---|---|---|
| What | Append-only `trace.jsonl` events | Session result bundle |
| Use | Reconstruct decisions | Take away diff, summary, logs |
| Examples | `patch_preflight_*`, `approval_decision` | `final.diff`, `summary.json` |

Traces must not retain API keys.

## 10. Example session outline

Input:

```text
repo: examples/buggy_calculator
task: divide should raise ValueError when b == 0
```

Expected product path:

1. Import `working_copy` and build repo map
2. Baseline pytest fails on the zero-divisor case
3. Read calculator and tests
4. Propose a unified diff; pass policy and exact preflight
5. Approve
6. Docker pytest passes
7. Export `final.diff`, `summary.json`, `trace.jsonl`, and logs

Step 6 requires a working Docker runtime on the host.
