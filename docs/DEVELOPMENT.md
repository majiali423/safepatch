# Development

## Environment

Use Python 3.10+ and install `python -m pip install -e ".[dev]"` in a virtual
environment. Docker is required for product execution and Docker E2E tests.
An API key is required only for live-model runs.

## Test groups

```bash
python -m pytest -q -m "not docker_e2e and not packaging_network"
python -m ruff check code_agent tests devtools
python -m mypy
git diff --check
```

The offline group includes deterministic session replay and its negative tests.
Mypy checks the configured analysis-loop boundary, not the whole repository.
On hosts with temporary-directory permission problems, provide a new
`--basetemp .test-artifacts/run-name` after creating its parent directory.

Run Docker checks separately after `safepatch --build-image`:

```bash
SAFEPATCH_REQUIRE_DOCKER_E2E=1 python -m pytest -q -m docker_e2e
```

PowerShell equivalent:

```powershell
$env:SAFEPATCH_REQUIRE_DOCKER_E2E = "1"
python -m pytest -q -m docker_e2e
Remove-Item Env:SAFEPATCH_REQUIRE_DOCKER_E2E
```

The required-Docker gate fails when no selected Docker test passes. Keep
platform-specific skips separate from passes in reports.

Packaging may need network access to download build dependencies:

```bash
python -m pytest -q -m packaging_network
python -m build
```

CI runs Linux/Python 3.10 and 3.12, Windows/Python 3.12, type/lint checks,
published-evidence verification, packaging and real Docker tests.
Each pull request runs one CI workflow; pushes to `master` and manual dispatches
also run it. A new commit cancels the older run for the same PR or branch.
Failed unit jobs retain synthetic replay captures for seven days as GitHub
Actions artifacts, and replay comparison reports the first differing event.

## Session replay

```bash
python -m devtools.session_replay.capture --out .tmp-session-replay
python -m devtools.session_replay.compare --baseline tests/fixtures/session_replay/current_v2 --after .tmp-session-replay
```

Capture refuses to overwrite existing output. Choose another directory on
subsequent runs. The [analysis-loop guide](ANALYSIS_LOOP.md) describes the
difference between historical name-only evidence and the current full-payload
baseline. Never regenerate a frozen fixture merely to hide a failing test.

## Repository hygiene

- Keep product code in `code_agent/`, regression tests in `tests/`, and
  maintained developer commands in `devtools/`.
- Name tests by behavior or component. Avoid review-round numbers in filenames.
- Keep a regression when it reproduces a distinct failure. Remove a duplicate
  only when its assertions are covered elsewhere.
- Commit published benchmark bundles, their verifiers and required task inputs.
  Raw runtime results, caches, build products and local review output are ignored.
- Keep credentials in local `.env`; only `.env.example` belongs in version control.
- Update existing guides when behavior changes instead of adding another
  acceptance report or copying old test counts into the README.

See [Evaluation](EVALUATION.md) for evidence commands and
[Release checklist](RELEASE_CHECKLIST.md) for release sign-off.
