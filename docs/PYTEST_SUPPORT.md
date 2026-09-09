# Supported pytest subset

SafePatch decides test-file protection **statically**. It does not execute the
target repository's pytest, conftest, or plugins on the host. Docker pytest of
untrusted repos still runs in the isolated disposable copy.

Workspace JSON under `/work` is never a trusted complete inventory and cannot
lift fail-closed protection.

## Configuration selection

pytest (8.x in the product image) uses **one** inifile, discovered in this
order: `pytest.ini`, `.pytest.ini`, `pyproject.toml` (only with
`[tool.pytest.ini_options]`), `tox.ini` (`[pytest]`), `setup.cfg`
(`[tool:pytest]`).

SafePatch does **not** reimplement that precedence. Automatic repair requires
**exactly one** of those candidates:

- `pytest.ini` or `.pytest.ini` present as a file
- `pyproject.toml` that contains `[tool.pytest.ini_options]`
- `tox.ini` that contains `[pytest]`
- `setup.cfg` that contains `[tool:pytest]`

A `setup.cfg` / `pyproject.toml` / `tox.ini` **without** a pytest section is
an ordinary build file and does not count. **Two or more candidates** (for
example `setup.cfg` `[tool:pytest]` together with `pyproject.toml`
`ini_options`) stop the session: SafePatch will not guess which file pytest
will honor.

A single supported inifile may only use: `pythonpath`, `python_files` (safe
globs), `addopts` (quiet/verbose and similar flags plus explicit `.py` paths;
not `-p`, `-o`, `-c`, or `--override-ini`), `testpaths`, `filterwarnings`,
`minversion`, `console_output_style`. Any other key or addopts flag is
unsupported.

## Supported (repair may proceed)

- No pytest inifile, or exactly one candidate as above
- Tests named `test_*.py` / `*_test.py`, files under `tests/`
- **No `conftest.py`**, or a `conftest.py` whose parsed AST is inert: empty
  (including comments-only), string documentation, and `pass` only

A standard `mod.py` + `tests/test_mod.py` tree **does not** need `pythonpath`
or an installable package. The Docker bootstrap restores `python -m pytest`
cwd import semantics after loading the controlled plugin from `/opt/safepatch`.

### conftest.py (current automatic-repair subset)

This version does **not** auto-repair repositories whose `conftest.py` has
any executable module-level node. That includes `import`, function and class
definitions (ordinary fixtures too), assignments, calls, and control flow.

Fixture-only conftest is an intentional product limit here, not a supported
path. Restoring fixtures is a later independent stage; function defaults,
decorator arguments, and annotations can run at import time and are not
treated as inert.

Legal examples in the current subset:

```python
# comments only
```

```python
"""Repository conftest placeholder."""
pass
```

`import pytest` alone is **not** supported.

## Unsupported (stop before modify)

Anything outside that subset is an **unknown collection extension**. SafePatch
fail-closes (every `.py` file is protected) and ends the session as
`PATCH_NOT_APPLICABLE` with `stop_reason=unsupported_pytest_collection`
**after baseline pytest, before analysis or apply**. `attempts_used` stays 0
and the workspace is unchanged. The same inventory feeds session state,
proposal validation, and apply.

This includes:

- More than one pytest inifile candidate
- `pytest_plugins` in config or `conftest.py`
- `addopts -p` / `-o` / `-c` / `--override-ini`
- any executable `conftest.py` (imports, fixtures, hooks, assignments, calls)
- local helper/plugin imports, relative imports, and star imports from conftest
- unreadable or unparseable conftest/config
- unsafe `python_files` patterns

Indirect collectors are **not** auto-repaired. Passing tests they collect must
not be weakened. Docker pytest still loads the target `conftest.py`; SafePatch
does not ignore it to change baseline semantics.
