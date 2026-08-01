# SafePatch v0.3 Test Coverage Audit

> Historical audit only. Its counts describe the snapshot reviewed when this
> document was written, not the current suite. Use the current
> `python -m pytest --collect-only -q` result for present-day coverage. The exact
> commit/date was not recorded in the original audit and is not invented here.

**Mode:** read-only audit. No product, benchmark, or test-suite changes in this phase.  
**Evidence basis:** `tests/**/*.py` (pytest `--collect-only` → **87** collected), plus referenced eval artifacts outside pytest.  
**Not used as a quality signal:** raw pass count (“86 passed”) alone.

**External (non-pytest) evidence already claimed by the project:**

| Evidence | Role | Not a substitute for |
|---|---|---|
| Docker network / non-root / timeout / cleanup negative E2E | Runtime security invariants | Controller-level terminal wiring |
| Patch mismatch scripted replay (`examples/llm_benchmark/run_replay_mismatch.py`) | Mechanism proof for preflight regen | Unit coverage of every race |
| Full12 × 3 real LLM benchmark | Sampling / end-to-end score | Deterministic failure classification |

---

## 1. Existing coverage overview

### 1.1 Test inventory (by file)

| File | Collected tests (approx.) | Primary surface |
|---|---|---|
| `tests/test_test_integrity.py` | 18 | Test-integrity policy + controller policy feedback |
| `tests/test_hidden_eval.py` | 15 | Hidden eval isolation / EvalStatus |
| `tests/test_security_acceptance.py` | 14 | Path safety, policy size, reject, Docker runner kinds, max attempts, trace redact |
| `tests/test_docker_e2e_negative.py` | 9 | Real Docker network/user/timeout/cleanup (`docker_e2e`) |
| `tests/test_patch_preflight.py` | 8 | v0.3 preflight / regen / base-changed / counter isolation |
| `tests/test_format_retry.py` | 7 | Parse kinds, format retry, CLI exit 5 |
| `tests/test_llm_benchmark_metrics.py` | 4 | Benchmark metrics helpers (not product runtime) |
| `tests/test_diff_newlines.py` | 3 | CRLF/LF apply + final.diff normalization |
| `tests/test_docker_config.py` | 3 | `DockerRunConfig` single source of truth |
| `tests/test_patching.py` | 2 | Happy apply + delete/requirements policy |
| `tests/test_workspace.py` | 1 | Import + `safe_resolve` basics |
| `tests/test_repo_map.py` | 1 | AST repo map smoke |
| `tests/test_end_to_end_local.py` | 1 | Dry-run closed loop (host pytest double) |
| `tests/test_eval_session_retention.py` | 1 | Eval result dir retention |

**Strong areas (relative to product risk model):** test integrity, format vs policy vs regeneration accounting, preflight mismatch → `PATCH_NOT_APPLICABLE`, hash-bound approval tree mutation, hidden-eval isolation, Docker negative E2E for network/non-root/timeout/cleanup.

**Thin areas:** read-tool surface, workspace import limits, multi-file apply rollback, controller-wired `TEST_*` terminals, CLI exit codes other than 5, patch-path traversal, apply-after-preflight exhaustion, artifact cross-checks beyond happy path.

### 1.2 Important taxonomy note

`POLICY_BLOCKED` is **not** a `SessionStatus` / CLI terminal. Policy failures are **non-terminal feedback** inside `_analyze_phase` (`tool_result` ok=False).  
`HIDDEN_TESTS_FAILED` is an **`EvalStatus`**, not a product `SessionStatus`. Product may remain `SUCCEEDED` while eval reports hidden failure.

---

## 2. Test matrix by module

Legend for priorities on **uncovered risks**: **P0** = reliability/security invariant with no deterministic test; **P1** = important control-plane gap; **P2** = polish / defense-in-depth.

### 2.1 Workspace isolation

| Dimension | Content |
|---|---|
| Happy path | Import copies into `working_copy`; source untouched on success; `.git` not copied |
| Failure | Missing repo → `WorkspaceError` (code); size/file caps → `WorkspaceError` (code) |
| Edges | Symlinks ignored on import; `safe_resolve` blocks `..`, absolute, symlink escape |
| Existing tests | `test_workspace.test_import_and_safe_resolve`; `test_security_acceptance.test_import_skips_git_and_only_working_copy_used`; `test_source_repo_not_modified_on_success`; `test_path_traversal_relative_fails`; `test_absolute_path_read_fails`; `test_symlink_escape_fails` (may skip) |
| Uncovered risks | `MAX_FILES` / `MAX_TOTAL_BYTES` import rejection + session dir cleanup; symlink in source not present in working_copy; empty-path `safe_resolve`; Windows drive-letter absolute (`C:...`) |
| Priority | P1 (import caps); P2 (empty path / drive letter); P1 if symlink-skip often masks escape test on Windows CI |

### 2.2 Repo map / read tools

| Dimension | Content |
|---|---|
| Happy path | `build_repo_map` lists classes/functions; tools: `list_tree`, `read_file`, `search_*`, `get_repo_map`, `get_current_diff` |
| Failure | Tool path escape via `safe_resolve`; read-action budget (`max_read_actions=12`) → `ToolError` |
| Edges | Large trees truncated by `list_tree` max_entries; binary/non-utf8 (undefined) |
| Existing tests | `test_repo_map.test_build_repo_map`; `read_file` absolute path via security test; format-retry script uses one `read_file` incidentally |
| Uncovered risks | **No dedicated tests** for `list_tree` / `search_text` / `search_symbol` / `get_current_diff`; **no test** for read-action limit; repo_map must not include ignored/hidden sibling content (only covered in hidden-eval for hidden dir, not general) |
| Priority | P1 (read budget); P2 (individual tool smoke); hidden isolation already P0-covered in eval tests |

### 2.3 Model output parsing

| Dimension | Content |
|---|---|
| Happy path | Valid JSON tool call |
| Failure | `JSON_DECODE_ERROR`, `INVALID_TOOL_SCHEMA`, `invalid_proposal_schema` |
| Edges | Non-dict args; missing proposal fields |
| Existing tests | `test_format_retry.test_parse_tool_call_kinds`; `test_invalid_proposal_schema_uses_format_retry`; `test_raw_preview_redacts_and_truncates` |
| Uncovered risks | Unknown tool name handling; extremely large raw payloads beyond preview; `LLMError` (transport) → `SessionStatus.ERROR` |
| Priority | P1 (`LLMError` → ERROR); P2 (unknown tool) |

### 2.4 Format retry

| Dimension | Content |
|---|---|
| Happy path | Bad output → hint → success; counters reset after valid tool call |
| Failure | Exhaust consecutive retries → `MODEL_OUTPUT_INVALID` |
| Edges | Policy failure must not consume format budget |
| Existing tests | `test_format_retry_then_success`; `test_format_retry_exhausted_model_output_invalid`; `test_policy_failure_does_not_count_format_retry`; `test_format_regen_repair_counters_isolated`; CLI `test_cli_exit_code_5_for_model_output_invalid` |
| Uncovered risks | Format retries in a **later** analysis window after a successful repair attempt; consecutive counter vs total after mixed success; analysis step limit reached without patch |
| Priority | P2 (multi-window format); P1 (analysis step limit → clear terminal/ERROR) |

### 2.5 Policy validation

| Dimension | Content |
|---|---|
| Happy path | Valid `.py` business patch |
| Failure | Forbidden paths (`.env`, `.git`, `Dockerfile`), delete, rename, too many files/lines, non-py requirements |
| Edges | `..` in diff paths; absolute paths in diff headers |
| Existing tests | `test_patching.test_reject_delete_and_requirements`; `test_forbid_env_git_dockerfile`; `test_too_many_files_fails`; `test_too_many_lines_fails`; integrity suite; controller policy feedback tests |
| Uncovered risks | **No test** asserting unified-diff path with `..` or absolute path is rejected; rename-forbidden explicit case beyond delete |
| Priority | **P0** (diff path traversal / absolute path in patch); P2 (rename explicit) |

### 2.6 Test integrity

| Dimension | Content |
|---|---|
| Happy path | New `tests/**/test_*.py` allowed as high-risk (default) |
| Failure | Modify/delete existing tests; conftest/pytest.ini/pyproject; skip/assert True/no asserts; helpers under tests/ |
| Edges | `--allow-test-changes` still blocks delete/skip/strip asserts |
| Existing tests | Nearly all of `test_test_integrity.py` (strongest module in suite) |
| Uncovered risks | Minor: nested package tests edge cases; encoding in test files |
| Priority | P2 only |

### 2.7 Patch parser / exact matcher

| Dimension | Content |
|---|---|
| Happy path | Exact hunk apply |
| Failure | Context/deletion mismatch; no fuzzy |
| Edges | Multi-hunk / multi-file; new-file diffs |
| Existing tests | `test_no_fuzzy_apply_one_line_off`; `test_preflight_and_apply_share_matcher`; apply happy paths; newline preserve tests |
| Uncovered risks | Multi-file second-hunk failure; new-file create + rollback; overlapping hunks; binary / encoding errors |
| Priority | **P0** (multi-file partial apply + rollback); P1 (new-file apply/preflight) |

### 2.8 Patch preflight

| Dimension | Content |
|---|---|
| Happy path | Mismatch → regen → good → one repair |
| Failure | 3 mismatches → `PATCH_NOT_APPLICABLE`, no approval, `attempts_used=0` |
| Edges | Shared matcher with applier; policy re-check on regen |
| Existing tests | All of `test_patch_preflight.py` except applying only to unit matcher cases |
| Uncovered risks | Preflight success then **workspace change before approve returns** is covered via mutate-in-approve; change **between preflight and approve entry** is same hash check but not separately named; regeneration budget **reset after a successful repair** then new mismatches in attempt 2 |
| Priority | P1 (regen budget across repair windows); P2 (explicit preflight→approve race naming) |

### 2.9 Hash-bound approval

| Dimension | Content |
|---|---|
| Happy path | Binding carries `patch_hash` + `working_tree_hash` |
| Failure | Tree mutate during approve → `PATCH_BASE_CHANGED`, `attempts_used=0` |
| Edges | Patch text hash drift after approval; reject leaves tree unchanged |
| Existing tests | `test_approval_base_changed`; `test_reject_leaves_working_copy_unchanged`; `test_tampered_source_after_approval_apply_fails` (applier-only, not controller hash path) |
| Uncovered risks | **`patch_hash` mismatch** after approval (proposal/diff mutated); `PATCH_BASE_CHANGED` assertions incomplete (no `attempt-*.log`, no CLI 7, no `patch_applied` absence); approve=False vs base-changed distinction already OK |
| Priority | **P0** (CLI exit 7 + artifact invariants for `PATCH_BASE_CHANGED`); P1 (`patch_hash` drift) |

### 2.10 Patch apply

| Dimension | Content |
|---|---|
| Happy path | Exact write; CRLF/LF preserved |
| Failure | Mismatch → fail + rollback; policy reject at apply gate |
| Edges | Apply fails **after** successful preflight (controller race path) |
| Existing tests | `test_apply_valid_patch`; newline tests; integrity reject-no-modify; `test_apply_failed_after_preflight_trace` (recovers) |
| Uncovered risks | Apply-after-preflight failures **exhaust regen** → `PATCH_NOT_APPLICABLE`; multi-file rollback leaves first file restored; apply does not increment `attempts_used` on failure (partially implied) |
| Priority | **P0** (exhaustion path + multi-file rollback); P1 (assert `attempts_used` unchanged on apply-after-preflight fail) |

### 2.11 Repair loop

| Dimension | Content |
|---|---|
| Happy path | Apply → pytest pass → `SUCCEEDED` |
| Failure | 3 applied wrong patches → `FAILED_MAX_ATTEMPTS` |
| Edges | Finish without patch; analysis step limit; baseline env error aborts before loop |
| Existing tests | `test_fix_divide_closed_loop`; `test_failed_max_attempts`; preflight success paths |
| Uncovered risks | `finish` / no-patch → `ERROR` / `agent_finished_without_patch`; analysis step limit; **baseline** `TEST_TIMEOUT` / `TEST_ENVIRONMENT_ERROR` through `TaskController.run` setting `session.status`; post-apply pytest env/timeout through controller |
| Priority | **P0** (controller-wired `TEST_*` terminals); P1 (finish/no-patch, step limit) |

### 2.12 Docker runtime

| Dimension | Content |
|---|---|
| Happy path | Config → argv single source; default timeout 120 |
| Failure | Daemon missing → environment; timeout kind; no host pytest fallback |
| Edges | Network none; uid 1000; container removed after success/fail/timeout |
| Existing tests | `test_docker_config.py`; `test_docker_e2e_negative.py`; `test_docker_unavailable_*`; `test_pytest_timeout_*` |
| Uncovered risks | Image missing **after** CLI preflight (TOCTOU) / `_resolve_image` None → `assert` → may surface as `ERROR` not `TEST_ENVIRONMENT_ERROR`; docker start failure / `docker run` non-timeout exception path unit-tested only via generic Exception monkeypatch gap; pytest-missing-in-container string detection |
| Priority | P1 (image-missing / assert vs env-error classification); P2 (pytest-missing-in-image) |

### 2.13 Hidden evaluation

| Dimension | Content |
|---|---|
| Happy path | Public success → hidden run on eval copy |
| Failure | `HIDDEN_TESTS_FAILED`, hidden timeout/env; product failure skips hidden |
| Edges | No leak into working_copy/repo_map; product summary not rewritten; unique eval dirs |
| Existing tests | Broad coverage in `test_hidden_eval.py` + `test_eval_session_retention.py` |
| Uncovered risks | Low residual: concurrent eval race; Windows path of hidden sibling |
| Priority | P2 |

### 2.14 Artifacts / trace

| Dimension | Content |
|---|---|
| Happy path | `summary.json`, `final.diff`, `trace.jsonl`, baseline/attempt logs on success |
| Failure | Redaction of secrets; MODEL_OUTPUT_INVALID summary fields |
| Edges | summary.status vs `final_tests_passed` consistency (benchmark helper) |
| Existing tests | E2E local artifacts; format exhausted summary; trace redact; `test_llm_benchmark_metrics` consistency helper; hidden eval_trace events |
| Uncovered risks | Product `_write_artifacts` for **every** terminal: e.g. `PATCH_NOT_APPLICABLE` / `PATCH_BASE_CHANGED` must include summary error fields, **no** `attempt-*.log`, `final.diff` empty or pre-apply; `patch_apply_failed_after_preflight` event payload completeness; summary counters vs session fields for all three budgets |
| Priority | P1 (terminal artifact contracts for v0.3 statuses); P2 (broader matrix) |

### 2.15 CLI exit codes

| Code | Status mapping (`cli.py`) | Deterministic CLI test? |
|---|---|---|
| 0 | `SUCCEEDED` | **No** |
| 1 | `FAILED_MAX_ATTEMPTS`, `ERROR`, other | **No** |
| 2 | missing repo/description | **No** |
| 3 | `REJECTED` | **No** |
| 4 | CLI docker preflight fail; session `TEST_*` | Session path **No**; CLI preflight partially untested as `main()` |
| 5 | `MODEL_OUTPUT_INVALID` | **Yes** — `test_cli_exit_code_5_for_model_output_invalid` |
| 6 | `PATCH_NOT_APPLICABLE` | **No** |
| 7 | `PATCH_BASE_CHANGED` | **No** |

| Uncovered risks | Exit 6/7 are v0.3 contract and undocumented by tests; exit 3/0/1 regression risk |
| Priority | **P0** (6 and 7); P1 (0, 3, 1 for max-attempts); P2 (2, 4) |

---

## 3. Terminal status checklist

Semantics derived from `controller.py` + `cli.py` + `eval/status.py`.  
“Deterministic test” means a pytest that asserts the **status** (and ideally side effects), not only a lower-layer double.

### 3.1 `MODEL_OUTPUT_INVALID`

| Check | Expected | Covered? |
|---|---|---|
| Files modified by apply? | No | Yes — `attempts_used==0` in `test_format_retry_exhausted_*` |
| Pytest after patch? | No attempt logs | Implied (no apply); not always asserting glob |
| Counters | `total_format_retries_used` / consecutive at max; repair unchanged | Yes |
| Approval entered? | No | Implied |
| Trace | `parse_failed`, `format_retry`, `retry_exhausted` | Yes |
| CLI exit | 5 | Yes |

**Verdict:** Covered at controller + CLI.

### 3.2 `PATCH_NOT_APPLICABLE`

| Check | Expected | Covered? |
|---|---|---|
| Files modified by apply? | No | Yes — no `patch_applied`; `attempts_used==0` |
| Pytest after patch? | No `attempt-*.log` | Yes |
| Counters | preflight failures / regen; not `FAILED_MAX_ATTEMPTS` | Yes |
| Approval? | No | Yes — `approve_calls==0` |
| Trace | `patch_preflight_failed`, `patch_regeneration_requested` | Yes |
| CLI exit | 6 | **No** |

**Verdict:** Controller strong; **CLI gap (P0)**. Exhaustion via **apply-after-preflight** only (not mismatch) **untested**.

### 3.3 `PATCH_BASE_CHANGED`

| Check | Expected | Covered? |
|---|---|---|
| Product apply? | No (`attempts_used==0`) | Status + attempts yes; **does not assert** absence of `patch_applied` / attempt logs |
| Working copy | May be dirty if approve callback mutated it (test does); source repo untouched | Source not asserted in this test |
| Counters | repair 0 | Yes |
| Approval? | Callback ran (approved True) then aborted | Yes |
| Trace | `approval_decision` then stop; no apply | **Not asserted** |
| CLI exit | 7 | **No** |

**Verdict:** Partial. Strengthen artifact + CLI (**P0**).

### 3.4 `POLICY_BLOCKED`

| Check | Expected | Covered? |
|---|---|---|
| Product terminal? | **Does not exist** | N/A |
| Behavior | Feedback loop; no format/regen/attempt bump; no approval | Yes — `test_policy_violation_does_not_increment_format_or_attempts`, `test_new_patch_revalidates_policy_*` |

**Verdict:** Correctly tested as **non-terminal**. Do not invent a status test.

### 3.5 `FAILED_MAX_ATTEMPTS`

| Check | Expected | Covered? |
|---|---|---|
| Files modified? | Yes (failed repairs applied) | Implied by host pytest runner on mutated tree |
| Pytest? | Yes, multiple attempts | Yes — `attempts_used==3` |
| Counters | `attempts_used==max` | Yes |
| Approval? | Yes each attempt | Implied (`approve=True`) |
| Trace | multiple apply/pytest | Not deeply asserted |
| CLI exit | 1 (generic) | **No** |

**Verdict:** Controller covered; CLI optional P1.

### 3.6 `TEST_TIMEOUT`

| Check | Expected | Covered? |
|---|---|---|
| Runner returns `error_kind=timeout` | Yes | Runner unit + Docker E2E |
| **`TaskController` sets `session.status=TEST_TIMEOUT`** | Baseline or post-apply | **No deterministic controller test** |
| Files | Baseline-only: unchanged by patch; post-apply: patch may already be applied | Untested at session layer |
| Approval / attempts | Depends on phase | Untested |
| CLI exit | 4 | **No** for session path |

**Verdict:** **High-risk gap (P0)** — runner tested, **control-plane wiring not**.

### 3.7 `TEST_ENVIRONMENT_ERROR`

| Check | Expected | Covered? |
|---|---|---|
| Runner environment error | Yes | Unit + no-host-fallback |
| **Controller `session.status`** | Yes in code paths | **No controller test** |
| CLI early preflight | return 4 | Code path exists; **no `main()` test** |
| CLI session mapping | 4 | **No** |

**Verdict:** Same as timeout — **P0** controller wiring.

### 3.8 `HIDDEN_TESTS_FAILED` (`EvalStatus`)

| Check | Expected | Covered? |
|---|---|---|
| Product files / SessionStatus | Product may stay `SUCCEEDED` | Yes — leak-prevention / summary not rewritten |
| Hidden pytest | Ran on eval copy | Yes |
| Product counters | Unchanged by eval | Yes |
| Approval | N/A (outside loop) | Yes by architecture tests |
| Trace | `eval_trace.jsonl` / hidden events | Yes |
| CLI exit | Product CLI unaffected | N/A (eval separate) |

**Verdict:** Well covered at eval layer.

### 3.9 `SUCCEEDED`

| Check | Expected | Covered? |
|---|---|---|
| Files in working_copy | Patched; source unchanged | Yes |
| Pytest | attempt log + pass | Yes |
| Counters | `attempts_used>=1` typical | Yes |
| Approval | Yes | Yes |
| Artifacts | summary/diff/trace | Yes (local E2E) |
| CLI exit | 0 | **No dedicated CLI test** |

**Verdict:** Product path strong; CLI 0 is P1 polish.

### 3.10 Other terminals (not in user list, noted)

| Status | Deterministic coverage |
|---|---|
| `REJECTED` | Yes (`test_reject_leaves_working_copy_unchanged`); CLI 3 **missing** |
| `ERROR` | No dedicated test (`LLMError`, internal exception, finish-without-patch) |

---

## 4. Focused gap review (requested scenarios)

| Scenario | Finding |
|---|---|
| Preflight OK → workspace changes **before apply** | Covered if change happens before post-approval hash check (`test_approval_base_changed`). Not covered: change **after** hash check passes and **during** `apply_proposal` (partially `test_apply_failed_after_preflight_trace` via monkeypatch). |
| Preflight OK → apply exception | Recovery path covered; **exhaustion → `PATCH_NOT_APPLICABLE`** not covered. |
| Path traversal / symlink escape | Tool `safe_resolve` covered (symlink may skip). **Patch header `../` / absolute paths: no test.** Import symlink strip: no test. |
| Docker daemon / image / start / cleanup | Daemon, timeout, cleanup, network, non-root covered. Image-missing via preflight message covered indirectly; **run_pytest assert-on-missing-image** and start-failure classification thin. |
| summary / metrics / trace / log / diff consistency | Happy path + format-invalid summary + benchmark helper. **v0.3 failure terminals lack a shared artifact contract test.** |
| Windows vs Linux paths / newlines / permissions | Newlines covered. Path drive-letter / `\` normalization only via `safe_resolve` replace in code, **few Windows-specific asserts**. Symlink often skipped on Windows. Docker E2E assumes Linux-like container uid 1000. |
| Counter cross-contamination | Good isolation test for format+regen+repair once. Missing: regen budget **across** repair attempts; policy+format+regen in one scripted session beyond existing pieces; apply-after-preflight consuming regen without touching format/repair incorrectly (partially in flaky-apply test). |

---

## 5. Truly missing high-risk scenarios (ordered)

1. **CLI exit 6 / 7** for `PATCH_NOT_APPLICABLE` / `PATCH_BASE_CHANGED` (v0.3 public contract).  
2. **`TaskController` propagation** of runner `TEST_TIMEOUT` / `TEST_ENVIRONMENT_ERROR` to `session.status` (+ no false `FAILED_MAX_ATTEMPTS`).  
3. **Patch path traversal** (`..` / absolute) rejected by policy before apply.  
4. **Multi-file apply**: second file fails → first file rolled back; working tree consistent.  
5. **Apply-after-preflight failures exhaust regeneration** → `PATCH_NOT_APPLICABLE`, `attempts_used==0`, no approval on exhausted path.  
6. **`PATCH_BASE_CHANGED` artifact contract**: no `patch_applied`, no `attempt-*.log`, summary status/error, optional CLI 7.  
7. **Read-action limit** enforcement in tool registry.  
8. **Import `MAX_FILES` / `MAX_TOTAL_BYTES`** cleanup behavior.  
9. **`finish` / no proposal** → stable `ERROR` (or documented status) without counting repair.  
10. **Image missing / `_resolve_image` None** during `run_pytest` classified as environment error (not bare assert → `ERROR`).

---

## 6. Suggested new tests (do not implement in this phase)

### P0 (add first)

| ID | Suggested test | Assert |
|---|---|---|
| T1 | `test_cli_exit_code_6_patch_not_applicable` | `main(...)==6` |
| T2 | `test_cli_exit_code_7_patch_base_changed` | `main(...)==7` |
| T3 | `test_controller_baseline_timeout_status` | FakeRunner timeout on baseline → `TEST_TIMEOUT`, `attempts_used==0` |
| T4 | `test_controller_baseline_environment_error_status` | → `TEST_ENVIRONMENT_ERROR` |
| T5 | `test_policy_rejects_diff_path_traversal` | `../secret.py` / absolute path → validation not ok |
| T6 | `test_multifile_apply_rollback_on_second_failure` | file A restored when B mismatches |
| T7 | `test_apply_after_preflight_exhausted_not_applicable` | repeated apply failures → `PATCH_NOT_APPLICABLE` |
| T8 | `test_patch_base_changed_artifact_contract` | status, attempts, no attempt logs, no `patch_applied` |

### P1

| ID | Suggested test | Assert |
|---|---|---|
| T9 | `test_controller_post_apply_timeout_status` | After apply, timeout → `TEST_TIMEOUT` (attempts already incremented) |
| T10 | `test_cli_exit_code_3_rejected` / `test_cli_exit_0_succeeded` | Exit mapping |
| T11 | `test_read_action_limit_raises` | 13th read → `ToolError` |
| T12 | `test_import_rejects_oversize_repo` | over `MAX_FILES` or bytes; session dir removed |
| T13 | `test_finish_without_patch_errors` | dry-run `finish` → terminal ERROR, attempts 0 |
| T14 | `test_patch_hash_changed_after_approval` | mutate proposal diff after approve → `PATCH_BASE_CHANGED` |
| T15 | `test_regeneration_budget_resets_after_repair_attempt` | after apply+fail tests, new window allows 2 regens again |
| T16 | `test_new_file_preflight_and_apply` | `/dev/null` → create file |

### P2

| ID | Suggested test |
|---|---|
| T17 | Windows-style absolute path in `safe_resolve` (`C:/...`) |
| T18 | Import drops symlinked files from source tree |
| T19 | Unknown tool name feedback |
| T20 | `LLMError` → `SessionStatus.ERROR` |
| T21 | Analysis step limit stop reason |
| T22 | CLI missing args → exit 2; docker preflight fail → exit 4 |
| T23 | Artifact contract table for `REJECTED` / `FAILED_MAX_ATTEMPTS` |

---

## 7. Low-value tests (do not add)

- More happy-path “propose good patch → succeed” clones of `test_fix_divide_closed_loop`.  
- Re-testing every test-integrity variant already enumerated.  
- Benchmark/Full12 wrappers inside pytest (already external evidence).  
- Asserting exact LLM prompt text.  
- Duplicate Docker network/none tests beyond current E2E.  
- Pixel-perfect Rich CLI rendering.  
- Exhaustive hunk_engine combinatorial fuzz without failure oracle.  
- Testing `POLICY_BLOCKED` as if it were a `SessionStatus`.

---

## 8. Estimated new test count

| Priority | Count | Notes |
|---|---|---|
| P0 | **8** | T1–T8 |
| P1 | **8** | T9–T16 |
| P2 | **0–7** | optional |
| **Recommended next increment** | **12–16** | All P0 + selected P1 (T9–T12, T14–T15) |
| Not recommended | 30+ | Diminishing returns vs risk model |

This keeps the suite focused on **uncovered high-risk behavior**, not raw volume.

---

## 9. CI platform matrix

| Recommendation | Rationale |
|---|---|
| **Yes — at least Linux + Windows for unit/integration (non-`docker_e2e`)** | Path separators, drive-letter absolute paths, CRLF (already unit-tested but should run on both), symlink skip vs fail divergence |
| **`docker_e2e` on Linux only (or dedicated Docker-capable runner)** | Container uid 1000 / network=none semantics; Windows Docker Desktop variability |
| Mark symlink escape as `xfail`/`skip` with explicit reason on hosts without symlink privilege | Avoid false green on Windows without Developer Mode |
| Do **not** require Full12 LLM in CI | Cost, nondeterminism; keep as tagged manual/release evidence |
| Optional: one macOS job later | Lower priority than Win+Linux for this codebase’s current risks |

---

## 10. Audit conclusions

1. Coverage quality is **uneven but intentional**: integrity, format/regen accounting, preflight NA, hidden isolation, and Docker negatives are evidenced by named tests—not by pass count.  
2. The largest **control-plane** holes are: **CLI exits 6/7**, **controller-level `TEST_*` status wiring**, **patch path traversal tests**, **multi-file rollback**, and **apply-after-preflight exhaustion**.  
3. `POLICY_BLOCKED` must not be treated as a missing terminal test; `HIDDEN_TESTS_FAILED` is eval-layer and already tested.  
4. Next phase should add ~**12–16** targeted tests (section 6), not broaden benchmarks or rewrite product code.  
5. CI should gain a **Win+Linux** unit matrix; keep Docker E2E scoped; keep real LLM out of default CI.

---

*Generated as a read-only audit against SafePatch v0.3. No `code_agent/`, benchmark assets, or tests were modified for this document.*
