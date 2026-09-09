# SafePatch

[English](README.md) · [中文](README_zh.md) · [Documentation](docs/README.md)

SafePatch is a code-repair agent for small Python repositories. It inspects code,
proposes a patch, asks for approval, and runs pytest in a disposable Docker copy.
Each session produces a diff, test logs, a structured summary, and an audit trace.

**Start here:** [five-minute engineering review](docs/RECRUITER_BRIEF.md) ·
[deterministic demo](docs/DEMO.md) · [architecture](docs/ARCHITECTURE.md)

## How it works

```text
Import repository → baseline pytest → model inspection and proposal
    → policy validation → exact preflight → hash-bound approval
    → apply patch → Docker pytest → summary, trace and diff
```

- **Controlled editing:** exact diffs or revision-bound text replacements;
  existing tests and configuration are protected by default.
- **Explicit budgets:** exploration, evidence requests, regeneration and repair
  attempts have separate counters and stopping rules.
- **Review before execution:** approval binds both the patch and working-tree
  hashes. Changed input requires a new review.
- **Isolated tests:** pytest runs as a non-root user with networking disabled,
  a read-only container root and a disposable repository copy.
- **Honest verification:** a green baseline followed by green tests is reported
  as `TESTS_PASSED_UNVERIFIED` without an independent request oracle.

## Quick start

Requires Python 3.10+ and a running Docker daemon.

```bash
git clone https://github.com/majiali423/safepatch.git
cd safepatch
python -m venv .venv
```

Activate with `source .venv/bin/activate` on Linux/macOS or
`.venv\Scripts\Activate.ps1` in PowerShell, then:

```bash
python -m pip install -e ".[dev]"
safepatch --build-image
safepatch --docker-check
safepatch examples/buggy_calculator "divide must raise ValueError when b is zero" --dry-run-script examples/dry_run_fix_divide.json --yes
```

The demo uses fixed model responses and requires no API key. Omit `--yes` to
review the patch interactively. The source repository is unchanged; inspect the
printed session directory for the repaired working copy and artifacts.

For a live model, copy `.env.example` to `.env`, configure the provider, and omit
`--dry-run-script`. Repository snippets, tracebacks and proposed diffs are sent
to that provider. See the [demo guide](docs/DEMO.md) for the full workflow.

## Architecture

| Component | Responsibility |
| --- | --- |
| `TaskController` | Import, baseline, approval and session lifecycle |
| `AnalysisLoop` | Model calls, inspection, evidence and proposal retries |
| `PatchGate` | Preflight and approval binding |
| `RepairExecutor` | Apply the approved patch and run tests |
| `SessionFinalizer` | Metrics, trace and artifact output |

The [architecture guide](docs/ARCHITECTURE.md) explains state transitions and
trade-offs. [Analysis-loop replay](docs/ANALYSIS_LOOP.md) provides a deterministic
way to inspect component boundaries and detect behavioral drift.

## Evaluation

The published 21-run real-bug bundle records **17/21** hidden-test successes.
Its records can be checked without model access:

```bash
python examples/real_bug_benchmark/verify_published_results.py
python examples/real_bug_benchmark/verify_preflight_ablation.py
python examples/llm_benchmark/verify_manifest.py
```

These are small, historical experiments. The separate preflight comparison
observed 11/14 versus 9/14 successes, but no preflight rejection occurred, so
it does not establish a causal benefit. The later 9/9 extension is a separate
report rather than part of the verifier-backed bundle. See the
[evaluation guide](docs/EVALUATION.md) and [failure case study](docs/FAILURE_CASE_STUDY.md).

## Scope

Automatic repair targets small local Python + pytest projects. Configuration
must fit the [supported subset](docs/PYTEST_SUPPORT.md). Executable `conftest.py`,
including ordinary fixtures, is currently unsupported; the session stops before
model analysis or patch application after running the baseline.

The model has no shell, browser, network or direct-write tool. Docker reduces
execution risk but is not a complete security sandbox. Passing tests does not
prove business correctness. SafePatch does not push commits or merge PRs.

## Development

```bash
python -m pytest -q -m "not docker_e2e and not packaging_network"
python -m ruff check code_agent tests devtools
python -m mypy
```

The [development guide](docs/DEVELOPMENT.md) covers Docker tests, packaging,
replay fixtures and repository hygiene. Package version: **0.3.1**; the default
branch includes development changes and is not itself a tagged release.

```text
code_agent/  Product implementation
tests/       Behavior, security and runtime regression tests
devtools/    Deterministic session capture and comparison
examples/    Runnable demos, benchmark tasks and published evidence
docs/        Usage, architecture and evaluation guides
```

Licensed under [MIT](LICENSE). Copyright (c) 2026 Jiali Ma.
