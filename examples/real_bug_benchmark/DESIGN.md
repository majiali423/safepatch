# Real-bug benchmark design

Status: **candidate freeze with five accepted environments**. A candidate is not
counted as runnable or scored until its buggy and fixed revisions pass the
environment acceptance gate.

## Goal

Evaluate SafePatch on a small, auditable set of real historical Python bugs
without mixing model quality with dependency-installation failures. The first
freeze targets five primary tasks from five projects, with two reserve tasks.

The source dataset is the official
[BugsInPy repository](https://github.com/soarsmu/bugsinpy), which accompanies
the [BugsInPy paper](https://arxiv.org/abs/2401.15481). Candidate identifiers,
commits, tests, measured sizes, and exclusion reasons are recorded in
`candidate_manifest.json`.

## Non-negotiable rules

1. Primary metrics use the full upstream buggy repository, not a minimized
   reproduction.
2. Candidate selection is frozen before any model repair run.
3. The reference patch and fixed revision are never copied into the Agent
   workspace or prompt.
4. Every project uses a prebuilt, pinned Docker image. Tests run with network
   disabled; no runtime dependency installation is allowed.
5. Environment acceptance requires the selected test to fail on the buggy
   revision for the expected reason and pass on the fixed revision.
6. Both revisions must stay under SafePatch's existing 500-file and 5 MiB
   import caps. The benchmark does not silently widen product limits.
7. An environment error, timeout, collection error, or incompatible Python
   version is infrastructure failure, not a failed repair.
8. The original exposing test is public. Supplemental hidden tests may be
   authored from the documented bug semantics, but must pass on the upstream
   fixed revision and remain outside the Agent workspace.

## Candidate states

- `environment_pending`: size and patch checks passed; pinned environment has
  not passed buggy/fixed acceptance yet.
- `size_and_environment_recheck_required`: reserve task has not been fully
  measured at its exact commit.
- `accepted`: buggy fails as expected, fixed passes, image digest recorded.
- `excluded`: reason is retained in the manifest.

No report may include `environment_pending` tasks in its success-rate
denominator.

## Environment strategy

BugsInPy tasks were originally captured mostly on Python 3.6–3.8. SafePatch's
controller and CI baseline is Python 3.11, but forcing historical projects to
run on 3.11 would measure migration breakage instead of the recorded bug.
The benchmark therefore separates controller Python from task Python:

1. Run SafePatch and the benchmark orchestrator on Python 3.11.
2. Build each project image from the Python version recorded by BugsInPy.
3. Install only the pinned packages required to collect and run the selected
   upstream test.
4. Record the Dockerfile, resolved package list, and resulting image digest.
5. Run the selected test against buggy and fixed commits with `--network none`.
6. Exclude the task if it still requires source compatibility changes
   unrelated to the historical bug on its recorded runtime.

The product's default pytest image remains minimal. Benchmark dependencies must
not be added to the product image.

## First candidate freeze

| Role | Candidate | Project | Bug type | Current gate |
|---|---|---|---|---|
| primary | `pysnooper-3` | PySnooper | wrong output path variable | accepted |
| primary | `tornado-11` | Tornado | case-sensitive HTTP header value | accepted |
| primary | `tqdm-3` | tqdm | undefined boolean semantics | accepted |
| primary | `sanic-5` | Sanic | root logger namespace collision | accepted |
| primary | `thefuck-19` | thefuck | unsafe force-push suggestion | accepted |
| reserve | `tqdm-4` | tqdm | scaling with unknown total | exact revision recheck required |
| reserve | `tqdm-8` | tqdm | wrong user bar variables | exact revision recheck required |

Measured exclusions include `youtube-dl-2` and `fastapi-5`, both of which
exceed the product's file and byte caps. These exclusions are evidence of the
selection protocol, not benchmark failures.

## Acceptance artifact per task

Each accepted task contains only metadata and benchmark-owned assets, not a
committed copy of the upstream repository:

```text
<task-id>/
  task.json
  Dockerfile
  constraints.txt
  TASK.md
  public_test.patch
  acceptance.json
```

Run `python examples/real_bug_benchmark/verify.py` to rebuild and verify all
accepted environments. The verifier clones upstream revisions into a temporary
directory, applies only the public regression-test patch to the buggy revision,
and runs both revisions with Docker networking disabled.

The `thefuck-19` command intentionally starts pytest through `sh`. That
historical revision discovers its shell through the parent process; launching
Python as container PID 1 changes application behavior and is therefore not an
equivalent test environment.

`acceptance.json` records repository URL, commits, file/byte counts, expected
failing node, buggy result, fixed result, image digest, and verification time.

## Experiment matrix after acceptance

For five accepted primary tasks:

- Primary model + full SafePatch: 3 runs per task (15 sessions).
- Primary model + preflight disabled: 3 runs per task (15 sessions).
- Second model: three stratified tasks × 2 runs (6 sessions).

The initial target is therefore 36 sessions. A sixth primary task would raise
the total to roughly 42. Report task-level results in addition to pooled rates;
with five tasks, pooled percentages alone are not statistically persuasive.
