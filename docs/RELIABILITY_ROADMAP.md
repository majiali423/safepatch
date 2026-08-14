# SafePatch Reliability Roadmap

Goal: raise SafePatch from a strong self-hosted Agent prototype to a reliable code-repair system with public engineering gates, real-task evidence, and reproducible experiments.

## Current status

Much of the original three-week plan is already landed on `master`. Treat the
sections below as historical planning notes plus remaining open work, not as an
unstarted schedule.

**Done (representative):**
- Cross-platform CI (unit matrix, product quality/wheel smoke, Docker E2E)
- Real-bug benchmark environments, published evidence bundles, and verification scripts under `examples/real_bug_benchmark/`
- Fail-closed approval, disposable Docker test copies, timeout container cleanup, path redaction
- Engineering-facing README positioning (product status, safety model, evaluation caveats)

**Still open:**
- Publish an open-source license (or keep the explicit “no license” notice)
- Fill package metadata (`urls`, authors, classifiers) and keep the package and CLI
  name aligned with SafePatch.
- Optional maintainer polish: restore a CI badge to `majiali423/safepatch`, refresh dated audits, keep demo GIF discoverable under Demo docs

Suggested horizon when the plan was written: three weeks. When time is short, keep the strict P0 → P1 → P2 order and do not expand product scope before real evaluation is complete. The original track standardized on Python 3.11 to reduce cross-version noise; the package currently supports Python 3.10+ and CI covers multiple versions.

## 1. Acceptance gates

Ship criteria for a reliability milestone:

- Working tree is clean; every version-controlled test file is non-empty and intentional.
- Windows and Linux CI pass all non-Docker tests on Python 3.11.
- Docker security E2E passes at least on Linux CI or a dedicated release workflow.
- README first screen includes a 30–60 second demo, architecture diagram, copy-pasteable commands, and an honest evaluation summary.
- At least one real-repository bug result set and one preflight ablation are present.
- All raw session results are traceable; summary reports are regenerable by script.
- Architecture and failure docs explain the problem, design, trade-offs, data, and limits.

## 2. P0: Engineering trustworthiness (week 1)

### 2.1 Restore test integrity

`tests/test_p0_multifile_apply_rollback.py` was previously empty in the working tree while `HEAD` still contained three tests. Confirm whether clearing was intentional; if not, restore and re-run the full suite.

Also fix test-environment portability: test doubles that launch pytest must use `sys.executable`, not a hard-coded `python`. Touch points:

- `tests/test_end_to_end_local.py`
- `tests/test_hidden_eval.py`
- `tests/test_security_acceptance.py`

Acceptance: a clean clone reaches a stable result with only install commands and `python -m pytest`.

### 2.2 Establish CI gates

Suggested job split:

1. `unit`: Windows + Ubuntu, Python 3.11, non-Docker tests.
2. `quality`: Ruff on `code_agent/`, build a wheel, install the wheel in a clean env, run a CLI smoke test.
3. `docker-release`: Ubuntu-only, serial `docker_e2e`; the historical plan
   proposed tag/release/manual triggering. The current `ci.yml` also runs this
   gate on ordinary pushes and PRs, so it is a required CI check today.

Real LLM benchmarks stay out of ordinary CI; place them in a manual release workflow so cost and sampling noise do not block commits.

Ruff should not scan intentionally broken fixtures under `examples/`, and benchmark task code is not a product quality gate. Tests can remain pytest-constrained first; lint for tests can arrive later.

Acceptance: PRs must pass unit and quality; README shows CI status; Docker results in the release workflow have downloadable logs.

### 2.3 Establish a reproducible demo

Keep the existing dry-run demo as the deterministic path, then add one real-model demo. Keep the video structure to about 60 seconds:

1. Show baseline test failure.
2. Show read-only tools locating code.
3. Show a patch passing policy and exact preflight.
4. Show human approval bound to two hashes.
5. Show Docker tests passing and final artifacts.

README first screen keeps one primary command:

```powershell
safepatch examples\buggy_calculator `
  "divide should raise ValueError when b is zero" `
  --dry-run-script examples\dry_run_fix_divide.json `
  --yes
```

Acceptance: a new contributor can reproduce from a clean clone in about 10 minutes; the GIF is understandable without narration for the key control chain.

### 2.4 Document design rationale

Keep short, written answers to these maintainer questions in architecture or failure docs:

- Why can a model patch fail even when business logic looks correct?
- Why separate format retry, patch regeneration, and repair attempt?
- Why choose exact apply over fuzzy apply?
- Why does human approval bind both patch hash and working-tree hash?
- Why must hidden tests stay outside the product loop?

Prefer the structure “real failure → design → cost → data”, with at least one committed failure-case trace.

## 3. P1: Real validity evidence (week 2)

### 3.1 Real-bug benchmark

Select **5–8** tasks from historical fixes in public Python projects. The first version may draw from BugsInPy or real GitHub bug-fix commits. With a small set, coverage still matters: single-file logic bugs, boundary conditions, cross-module edits, red-herring localization, and hidden-test generalization. Freeze selection criteria in advance:

- Python + pytest; no online dependency installs during evaluation.
- Repair scope at most 5 files and 300 changed lines.
- Public tests fail before repair; public and hidden tests pass after the reference fix.
- No database, browser, or external service requirements.
- The Agent never sees the reference patch or hidden tests.

Do not only pick tasks the system already solves. Record the candidate pool, exclusions, and exclusion reasons to prevent benchmark selection bias. The first version is a trustworthy small-sample case study, not a general success-rate claim built from 5–8 tasks.

Suggested layout:

```text
benchmarks/real_bugs/
  manifest.json
  tasks/<task_id>/
  hidden/<task_id>/
  references/<task_id>/
  reports/
```

### 3.2 Preflight ablation

Hold tasks, model, prompt, temperature, and max call budget fixed; compare:

- A: full SafePatch.
- B: preflight disabled; patches go straight to approval and apply.

The three-week track does not add a fuzzy-apply arm. Fuzzy apply changes both matching strategy and safety semantics, so it is a poor single-variable contrast with “preflight on/off”; study precision, mis-apply risk, and recovery separately later.

Primary metrics:

- Final public/hidden pass rates.
- First-patch applicability.
- Apply failure after approval.
- Invalid repair-attempt count.
- Total model calls, tokens, wall time, and cost.

Ablation conclusions should answer whether preflight reduces invalid repair attempts and approval noise, not only whether final success rose.

### 3.3 Controlled experiment scale

Use “enough repeats on the main experiment, small cross-model checks”:

- Main model + full SafePatch: about 6 real tasks × 3 runs ≈ 18 runs.
- Main model + preflight off: same task set × 3 ≈ 18 runs.
- Second-model cross-check: stratified sample of 3 tasks × 2 ≈ 6 runs.

Total about 42 runs. If the final task count is 5 or 8, keep the same principles rather than forcing two models through three repeats on every task.

Report per model:

- Success rate and stratification by task difficulty.
- First-patch applicability and regeneration recovery.
- Mean/median tokens, calls, duration, and estimated cost.
- Failure-type distribution: format, policy, preflight, public tests, hidden tests, environment.

Reports must keep raw session IDs and config fingerprints; summary tables are script-generated, never hand-filled.

### 3.4 Multifile coverage in real tasks

Do not invent large tasks only to look complex. Let the real candidate pool decide multifile coverage; if none of the 5–8 tasks needs cross-module repair, add 1–2 clearly sourced tasks that truly require multi-file edits. Prefer:

1. Cross-module config propagation: parse, defaults, and validation change together.
2. Data pipeline: schema, cleaning, and aggregation share a coupled defect.
3. API/service boundary: business and adapter layers both need fixes, while tests remain immutable.

Cross-module tasks should include red-herring files and hidden boundary tests, without artificial line-count thresholds. The reference fix must genuinely need multi-file changes, with upstream issue/commit evidence retained.

## 4. P2: Engineering quality and maintainability (week 3)

### 4.1 Quality tooling

Preferred development dependencies:

- `ruff`
- `pytest-cov`
- `build`

Ruff is a hard gate only for `code_agent/`. Coverage finds blind spots and produces reports; avoid aggressive global thresholds and low-value tests written only to chase 100%. Focus on controller, patching, workspace, and Docker error-classification branches.

`mypy`/`pyright` are not whole-repo hard gates yet. When capacity allows, type-check clearly bounded core modules such as `state.py`, `patching/`, and `repository/`, report-only first, without blocking the reliability milestone.

### 4.2 Release and dependency reproducibility

- Add a LICENSE.
- Fill project URL, authors, and Python classifiers.
- Split dependencies: product runtime in `project.dependencies`; test/Ruff/build tools in a dev extra; benchmark runners and data tooling maintained separately.
- Prefer `uv.lock` or layered constraints for development and benchmark environments so benchmark-only packages do not inflate product installs.
- CI builds a wheel and runs a CLI smoke test after installing that wheel in a fresh environment.
- Benchmark records Python version, Docker image digest, model name, and parameters.

### 4.3 Controller refactor (after the reliability milestone)

Do not perform a large Controller refactor inside the three-week window. Priority is CI, real evaluation, and ablation evidence; broad refactors expand the regression surface without directly improving trustworthiness of the evidence.

After the milestone, if maintenance continues, split by state-transition responsibility:

- `AnalysisLoop`: model requests, tool calls, format retries.
- `PatchGate`: policy, preflight, approval binding.
- `RepairExecutor`: apply, rollback, pytest, failure classification.
- `SessionFinalizer`: summary, diff, trace, observability.

Freeze behavioral tests before refactoring; refactor commits must not ship new features. Acceptance focus: state transitions and counter semantics stay unchanged.
See [Controller Refactor Plan](CONTROLLER_REFACTOR_PLAN.md) for the proposed
extraction order and invariants.

### 4.4 Observability presentation

Static HTML reports are optional capacity work, not part of the three-week acceptance gates. If P0, real benchmark, and ablation are already done, a pure static generator over session artifacts can emit:

- Timeline.
- Model/tool/test call stats.
- Patch and failure classification.
- Token, duration, and cost cards.

That supports demos without turning the project into a frontend system. Add SQLite indexing or a light API only when multi-session retrieval is clearly required.

## 5. Three-week execution order

### Week 1: reliability baseline

- Day 1: restore tests, fix `sys.executable`, establish a clean test baseline.
- Day 2–3: Python 3.11 Windows/Linux CI, product-only Ruff, wheel + CLI smoke test.
- Day 4: Docker release workflow and log upload.
- Day 5: record GIF and tighten README first screen.
- Day 6–7: prepare architecture walkthrough and two failure cases.

### Week 2: from toy eval to real evidence

- Day 1–2: freeze real-task selection protocol, candidate pool, and manifest.
- Day 3–4: onboard 5–8 real tasks.
- Day 5: implement a preflight switch for benchmark ablation only.
- Day 6–7: run about 42 controlled experiments and auto-generate reports.

### Week 3: engineering wrap-up

- Finish layered dependency locking, LICENSE, package metadata, and coverage reports.
- Optionally type-check core modules; still no whole-repo hard gate.
- Consider a static session report page only after core evidence is complete.
- Update public engineering evidence to cite only reproducible frozen reports.

## 6. Numbers that belong in engineering evidence

Prefer metrics that explain design value:

- Real-task public and hidden pass rates.
- How much preflight reduced post-approval apply failures.
- How many invalid repair attempts were avoided.
- Regeneration recovery rate.
- Cost/success trade-offs across models.
- CI platform count, deterministic test count, and critical security E2E count.

Do not present 100% on self-built micro-tasks alone as “repair accuracy 100%”. Always state task scale, run count, model, and limits.
