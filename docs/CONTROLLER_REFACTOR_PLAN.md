# Controller Refactor Plan

Status: deferred design plan. This is not a feature commitment and no behavior
change is included with it.

`code_agent/controller.py` currently owns orchestration, model interaction,
patch gating, repair execution, observability, and artifact writing. Its size
is acceptable for the current release candidate, but it is the clearest future
maintenance hotspot. A structural refactor should happen only after a release
candidate is frozen and must not be mixed with feature work.

## Invariants to preserve

- State transitions and terminal `SessionStatus` values.
- Counter semantics: format retries, patch-regeneration retries, repair
  attempts, read-budget accounting, model calls, and pytest runs.
- The order of policy validation, preflight, hash-bound approval, apply, and
  test execution.
- Fail-closed approval and all workspace/runtime containment boundaries.
- Artifact names and machine-readable `summary.json` schema.

## Proposed extraction order

1. **SessionFinalizer** — move `_init_observability`, `_finish_observability`,
   `_finalize`, `_docker_config_payload`, and `_write_artifacts` behind a
   narrow session-artifact interface. This is low-risk because it does not
   decide whether a patch is applied.
2. **RepairExecutor** — extract the approved-patch branch from `run`: apply,
   classify apply failure, invoke pytest, and update repair counters. Inject
   the existing patch execution service and runner; do not alter their policy.
3. **PatchGate** — extract validation, preflight, regeneration accounting,
   approval binding, and required-read registration. Keep it deterministic and
   free of provider calls.
4. **AnalysisLoop** — extract `_analyze_phase`, evidence error handling,
   format retries, and prompt construction last. This has the densest model and
   budget state, so it should be changed only after the other seams are stable.

## Safety procedure per extraction

1. Add or retain a characterization test for every affected terminal status.
2. Make one extraction-only commit with no prompt, policy, or schema change.
3. Run the deterministic suite, evidence verifiers, wheel smoke test, and
   Docker E2E gate.
4. Compare representative `summary.json`, `trace.jsonl`, and `final.diff`
   artifacts before and after the extraction.
5. Stop if any counter, artifact schema, or state transition differs; fix the
   characterization test or revert the extraction before continuing.

## Explicit non-goals

- No new model tools or provider protocol.
- No change to the 12-read exploration budget or retry limits.
- No behavior change to test-integrity policy, Docker restrictions, or hidden
  evaluation isolation.
- No frontend, database, or multi-agent expansion.
