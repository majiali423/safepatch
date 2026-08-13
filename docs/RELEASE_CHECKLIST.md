# SafePatch Release Checklist

Use this checklist for a tagged release candidate. It records what was
verified for the exact commit under review; it is not a claim that a later
commit or an arbitrary environment has passed.

## 1. Scope and provenance

- [ ] Working tree is clean before building.
- [ ] Version in `pyproject.toml` matches the intended tag.
- [ ] `LICENSE`, package metadata, and README links are present and accurate.
- [ ] Record the commit SHA, date, operator, Python version, and Docker image
      digest with the release artifacts.

## 2. Deterministic product gates

Run from a new virtual environment after installing `.[dev]`:

```bash
python -m pytest --collect-only -q
python -m pytest -q -p no:cacheprovider --basetemp .test-artifacts
python -m ruff check code_agent tests
python examples/llm_benchmark/verify_manifest.py
python examples/real_bug_benchmark/verify_published_results.py
python examples/real_bug_benchmark/verify_preflight_ablation.py
git diff --check
```

Record the complete command output or link to the successful CI run. The
published benchmark checks verify frozen evidence only; they do not evaluate a
live model and must not be represented as a fresh capability result.

## 3. Package and CLI smoke gate

```bash
python -m build
python -m venv .smoke-venv
.smoke-venv/bin/python -m pip install dist/*.whl
.smoke-venv/bin/safepatch --help
```

On Windows, use `.smoke-venv\Scripts\python.exe` and
`.smoke-venv\Scripts\safepatch.exe`. Confirm that the installed wheel contains
`code_agent/runtime/Dockerfile.pytest`.

## 4. Docker gate

With Docker available, build the pinned local image and run the security E2E
suite:

```bash
safepatch --build-image
python -m pytest -q -m docker_e2e -p no:cacheprovider --basetemp .docker-test-artifacts
```

Review the Docker E2E logs for network isolation, non-root execution, timeout
cleanup, and disposable-copy cleanup. A missing daemon is an environment
failure, not a passing skip for release sign-off.

## 5. Sign-off record

For each candidate, retain a short immutable release note containing:

- tag and commit SHA;
- CI run URLs for unit, package, and Docker gates;
- Python versions and Docker image digest;
- benchmark evidence bundle identifiers; and
- known limitations or intentionally deferred changes.
