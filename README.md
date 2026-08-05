# SafePatch

[English](README.md) | [中文](README_zh.md)

SafePatch is a controlled code-maintenance agent for small Python
repositories. It emphasizes human approval, restricted tools, isolated
tests, auditable traces, and fail-closed safety boundaries.

The public product name is **SafePatch**. The installable Python package and
CLI entry point remain `code-agent` (`pyproject.toml` / `code-agent` console
script).

## Project status

- Package version: **0.3.1** (`pyproject.toml`)
- Reliability work described in [Next Release Notes](docs/NEXT_RELEASE_NOTES.md)
  is **Unreleased** — not a published release commitment
- No open-source license has been published for this repository
- Primary target: small local Python repositories that use pytest
- Scope remains intentionally limited; this is an engineering project, not a
  general-purpose software-engineering platform

## Why SafePatch

LLM-assisted repair often fails for engineering reasons rather than pure
logic errors:

- Generated patches may touch unrelated files
- Agents may skip or weaken human approval
- Tests run on the formal working copy can pollute session state
- Docker timeouts can leave containers behind
- Model output and tool calls need strict parsing and typed boundaries
- Green automated tests do not by themselves prove an independent request
  oracle was satisfied
- Benchmarks and traces should stay auditable without overstating capability

SafePatch keeps the product path narrow so those failure modes stay
visible and enforceable.

## Safety model

Defense-in-depth controls — not a claim of absolute safety:

- The model only sees a session working copy, not an open host shell
- No arbitrary Shell, browser, or network tools for the model
- Changes require human approval before apply (unless `--yes` is explicitly
  used by the operator)
- Declared changed-file sets must match the actual normalized diff file set
- Pytest runs in Docker with `--network none`
- Fixed non-root UID inside the container
- Capability drop and `no-new-privileges`
- Read-only container root filesystem. The repository test copy is the only
  writable repository mount; a size-limited tmpfs is also mounted at `/tmp`
  for temporary runtime files
- Each pytest run uses a unique disposable test copy
- Timed-out runs force-remove the exact named container
- Cleanup failures surface as environment errors (fail-closed)
- Disposable host paths are redacted from persisted logs and result text
- Repair attempts are budget-limited; the default maximum is three attempts

The configured LLM provider still requires host network access. Repository
maps, selected snippets, tracebacks, and diffs may be sent to that provider.

## Architecture

```text
Task request
    ↓
Repository inspection
    ↓
Model tool call
    ↓
Patch proposal
    ↓
Static validation
    ↓
Human approval
    ↓
Apply to working copy
    ↓
Docker pytest on disposable copy
    ↓
Verification result
    ↓
Trace / diff / logs
```

Main pieces in `code_agent/`:

| Area | Role |
|---|---|
| `controller.py` | Session state machine / agent loop |
| `patching/` | Proposal, policy validation, preflight, exact apply |
| `workflow.py` | Fail-closed approval and verification policy |
| `runtime/docker_pytest.py` | Disposable-copy Docker pytest runner |
| `state.py` | Session status, counters, approval bindings |
| `llm.py` | Provider boundary and tool-call parsing |
| `tools/` | Restricted read-only inspection tools |
| `eval/` | Hidden evaluation outside the product loop |
| `tracing/` | Append-only redacted traces |

See [Architecture](docs/ARCHITECTURE.md) for module detail and failure
classification.

## Key behaviors

Implemented behaviors (not aspirational claims):

- Structured patch proposals (unified diff and revision-bound edits)
- Exact declared/actual changed-file validation
- Path normalization that preserves real `a/` / `b/` path components
- Fail-closed approval when no approval handler is supplied
- Typed schemas plus frozen legacy tool-call parsing
- Disposable Docker test copies per pytest run
- Exact-name timeout container cleanup
- Cleanup / disposable path redaction in persisted surfaces
- Hidden benchmark isolation on temporary evaluation copies
- Benchmark manifest drift checks without calling a paid model
- Cross-platform CI (unit, quality/wheel smoke, Docker E2E)

## Installation

```bash
git clone https://github.com/majiali423/safepatch.git
cd safepatch
python -m venv .venv
```

Activate the virtual environment:

```powershell
# Windows
.venv\Scripts\activate
```

```bash
# Linux / macOS
source .venv/bin/activate
```

Install the package (development extras optional for tests and lint):

```bash
python -m pip install -e .
# or
python -m pip install -e ".[dev]"
```

Docker is required for pytest isolation:

```bash
docker info
code-agent --build-image
code-agent --docker-check
```

Copy `.env.example` to `.env` and set provider credentials when running a
live model (not needed for dry-run demos).

## Example workflow

Minimal deterministic path (no API key): uses a small demo repo and a frozen
tool-call script.

```powershell
code-agent examples\buggy_calculator `
  "divide raises ZeroDivisionError on b==0; it should raise ValueError." `
  --dry-run-script examples\dry_run_fix_divide.json `
  --yes
```

Typical live workflow:

1. Point `code-agent` at a small Python + pytest repository copy
2. Provide a bug or change description (argument or `--description-file`)
3. Inspect the patch proposal shown by the CLI
4. Approve or reject at the human approval prompt (omit `--yes`)
5. Wait for Docker pytest on a disposable copy
6. Review `final.diff`, pytest logs, `summary.json`, and `trace.jsonl` under
   the session artifacts directory

Selected exit codes: `0` succeeded, `3` rejected, `4` environment/timeout,
`5` invalid model output, `6` patch not applicable, `7` base changed after
approval, `8` read budget exhausted, `9` tests green without an independent
request oracle (`TESTS_PASSED_UNVERIFIED`).

More detail: [Demo](docs/DEMO.md).

## Docker test isolation

- Pytest does not run against the formal session `working_copy`
- Each run creates a unique disposable test copy
- The container uses UID 1000 and defense-in-depth restrictions
- Files created during tests do not write back into the formal working copy
- Timeouts target the exact unique container name for force-remove
- Permission leftovers may be wiped by a restricted cleanup container
- Cleanup mounts only the disposable `working_copy` directory
- Cleanup failure returns an environment error rather than silent success
- Persisted logs avoid host absolute disposable paths

## Evaluation

Keep these layers distinct:

| Layer | Purpose |
|---|---|
| Unit / integration tests | Product behavior without Docker or with mocks |
| Docker E2E | Real daemon negative/security paths |
| Real-bug benchmark | Frozen historical BugsInPy-derived tasks |
| Hidden evaluation | Optional post-success checks outside the agent loop |
| Historical model reports | Recorded small-sample outcomes with stated limits |

Caution (retained as project policy):

- Benchmark sample sizes are small
- Results are auditable engineering evidence, not general capability claims
- They do not demonstrate production readiness on arbitrary repositories
- Historical model outcomes may not be fully recomputable from repository
  files alone (provider/API, sampling, and environment boundaries)
- Irreproducibility boundaries are documented in the frozen reports
- Single-run pass rates must not be packaged as stable product accuracy

Verify committed evidence without a model account:

```bash
python examples/real_bug_benchmark/verify_published_results.py
python examples/real_bug_benchmark/verify_preflight_ablation.py
python examples/llm_benchmark/verify_manifest.py
```

Docker-backed environment rebuild (when needed):

```bash
python examples/real_bug_benchmark/verify.py
```

Reports and design notes live under `examples/real_bug_benchmark/` and
`examples/llm_benchmark/`.

## Development and validation

```bash
python -m pip install -e ".[dev]"
python -m pytest -q
python -m pytest -m docker_e2e -q
python -m ruff check code_agent tests
python examples/llm_benchmark/verify_manifest.py
git diff --check
```

Optional distribution check (requires the `build` extra from `.[dev]`):

```bash
python -m build
```

Do not commit `.env`, session directories, or `examples/llm_benchmark/results/`.

## Limitations

- Targets small Python + pytest repositories only
- Does not expose arbitrary Shell to the model
- Does not auto-merge PRs or push commits
- Does not index large repositories
- Does not drive browsers
- Does not orchestrate multi-agent teams
- Model quality still bounds patch quality
- Passing tests does not guarantee business-semantic correctness
- Human approval remains a critical control point
- Docker isolation is defense-in-depth, not a complete security sandbox
- Benchmarks remain limited in scale and scope

## Repository layout

```text
code_agent/     Product package (controller, patching, runtime, tools, eval)
tests/          Unit, integration, and Docker E2E tests
examples/       Demo repos, dry-run scripts, benchmarks, frozen evidence
docs/           Architecture, recovery, observability, roadmap notes
```

## Documentation

| Document | Description |
|---|---|
| [中文 README](README_zh.md) | Chinese edition |
| [Architecture](docs/ARCHITECTURE.md) | Modules and call chain |
| [Reliability Roadmap](docs/RELIABILITY_ROADMAP.md) | Engineering hardening roadmap |
| [Next Release Notes](docs/NEXT_RELEASE_NOTES.md) | Unreleased reliability work |
| [Session Observability](docs/SESSION_OBSERVABILITY.md) | Per-session metrics |
| [Patch Recovery](docs/PATCH_RECOVERY.md) | Safe recovery design |
| [Demo](docs/DEMO.md) | End-to-end demonstration |
| [Failure Case Study](docs/FAILURE_CASE_STUDY.md) | Honest hidden-test failures |
| [Real-bug Benchmark Design](examples/real_bug_benchmark/DESIGN.md) | Frozen task protocol |

## License

No open-source license has been published for this repository.
Until that decision is made, do not assume permission beyond applicable law.
