# SafePatch: 5-Minute Engineering Review

## What it is

SafePatch is a deliberately narrow code-repair Agent for small, local Python
repositories that use pytest. Its purpose is not to make broad autonomous
software-engineering claims. It demonstrates a controlled repair path in which
an LLM can inspect code, propose a small patch, and receive verification only
through fixed policy and runtime gates.

## The engineering question

The project addresses a practical risk: a plausible LLM patch can still touch
unrelated files, weaken tests, fail to apply after review, contaminate its
working tree during testing, or leave runtime resources behind. SafePatch makes
those failure modes visible and rejects them where possible.

## Control path

```text
read-only inspection
  -> policy validation
  -> exact in-memory preflight
  -> hash-bound human approval
  -> exact apply with rollback
  -> Docker pytest on a disposable copy
  -> trace, diff, logs, and summary
```

The model has no shell, browser, network, or direct-write tool. The product
only permits small Python changes, blocks dependency/configuration edits, and
defaults to rejecting modifications to existing tests.

## Evidence to inspect

| Evidence | What it demonstrates |
|---|---|
| [Demo](DEMO.md) | A deterministic dry-run repair without an API key |
| [Architecture](ARCHITECTURE.md) | Module boundaries, state transitions, and trade-offs |
| `tests/` | Patch-path safety, approval binding, rollback, test integrity, hidden-eval isolation, and runtime cleanup |
| [Release Checklist](RELEASE_CHECKLIST.md) | Reproducible gates for a specific candidate commit |
| `examples/real_bug_benchmark/` | Frozen BugsInPy-derived task protocol and auditable historical runs |
| `examples/llm_benchmark/` | Separate micro-benchmark and replay evidence |

The current deterministic suite contains 256 collected tests. On this review
working tree it completed as **242 passed, 14 skipped**; skipped cases require
Docker or host capabilities not available in the execution environment.

The published real-bug evidence is a small historical experiment: 17 of 21
frozen runs passed both public and hidden checks. It is useful engineering
evidence, not a claim of general production accuracy. The separate preflight
comparison observed 11/14 versus 9/14 final successes, but no naturally
occurring preflight rejection, so it is explicitly not presented as causal
proof.

## Suggested review route

1. Read the [README](../README.md) for scope and safety limits.
2. Run the [dry-run demo](DEMO.md) or inspect its frozen script.
3. Follow the control path in [Architecture](ARCHITECTURE.md).
4. Inspect `workspace.py`, `patching/`, and `runtime/docker_pytest.py` for the
   key enforcement points.
5. Run the commands in [Release Checklist](RELEASE_CHECKLIST.md).

## Explicit limits

- Supports only small local Python + pytest repositories.
- Docker isolation is defense in depth, not a complete security sandbox.
- A passing public test suite is not treated as proof of business correctness.
- Human approval is required unless the operator explicitly passes `--yes`.
- Benchmark and model results are bounded by their frozen task sets, model,
  dates, and environment; they are not current capability guarantees.
