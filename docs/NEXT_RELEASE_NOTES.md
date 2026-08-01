# SafePatch — Unreleased

This document describes reliability work that has not been released. It makes
no version, tag, publication, or production-readiness commitment.

## Reliability changes under review

- Approval fails closed when a library caller omits an approval handler.
- Declared patch files must exactly match normalized files parsed from the diff.
- A green baseline followed by green final tests is reported as
  `TESTS_PASSED_UNVERIFIED` without an independent oracle.
- Docker pytest runs against a disposable test copy with defense-in-depth
  container restrictions.
- Approval, verification, and patch execution policies have explicit services.
- Tool calls use typed schemas and distinct format, argument, execution, and
  provider error classes.
- A deterministic manifest detects benchmark task, hidden-test, prompt, and
  runner drift without invoking a paid model.

## Verification before release consideration

```bash
python -m pytest --collect-only -q
python -m pytest -q
python -m pytest -m docker_e2e -q
python -m ruff check code_agent tests
python examples/llm_benchmark/verify_manifest.py
```

Historical benchmark evidence remains scoped to its recorded commits, dates,
model, fixed self-authored tasks, and final built image digest. It is not
production proof and is not rewritten as current product evidence.
