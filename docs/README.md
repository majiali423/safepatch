# Documentation

## Start here

| Guide | Purpose |
| --- | --- |
| [Engineering review](RECRUITER_BRIEF.md) | Five-minute overview and evidence route |
| [Demo](DEMO.md) | Run a repair without a model account |
| [Pytest support](PYTEST_SUPPORT.md) | Supported repositories and explicit stop conditions |
| [Development](DEVELOPMENT.md) | Installation, test groups, replay and maintenance |

## Implementation

| Guide | Purpose |
| --- | --- |
| [Architecture](ARCHITECTURE.md) | Components, state transitions and trade-offs |
| [Analysis loop](ANALYSIS_LOOP.md) | Model-loop ownership and deterministic replay |
| [Patch recovery](PATCH_RECOVERY.md) | Read-before-regenerate and exact patch application |
| [Patch applicability](V0.3_PATCH_APPLICABILITY.md) | Preflight design and failure categories |
| [Session observability](SESSION_OBSERVABILITY.md) | Summary fields, costs and counters |

## Evidence and release

| Guide | Purpose |
| --- | --- |
| [Evaluation](EVALUATION.md) | Current verification commands and historical evidence boundaries |
| [Interview demos](INTERVIEW_DEMOS.md) | Three concrete cases to walk through |
| [Failure case study](FAILURE_CASE_STUDY.md) | Public passes that failed hidden tests |
| [Release checklist](RELEASE_CHECKLIST.md) | Checks for a specific release candidate |

Obsolete acceptance counts and completed refactor plans have been removed.
Earlier versions remain in Git history. Published benchmark reports and their
source evidence remain under `examples/`; they are historical experiments,
not results from the latest checkout.
