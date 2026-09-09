"""Run pytest with the controlled inventory plugin loaded by absolute path.

Invoked only as ``python /opt/safepatch/run_pytest.py ...`` from the
controller-owned Docker command. Workspace ``pythonpath``, ``-p`` names, and
``PYTHONPATH`` must not select this plugin.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

CONTROLLED_PLUGIN = Path("/opt/safepatch/safepatch_inventory.py")
CONTROLLED_MODULE = "_safepatch_controlled_inventory"
CONTROLLED_DIR = Path("/opt/safepatch")
WORKSPACE_ROOT = Path("/work")


def load_controlled_plugin():
    path = CONTROLLED_PLUGIN.resolve()
    if not path.is_file() or path.is_symlink():
        sys.stderr.write("SAFEPATCH_CONTROLLED_PLUGIN_MISSING\n")
        raise SystemExit(2)
    spec = importlib.util.spec_from_file_location(CONTROLLED_MODULE, path)
    if spec is None or spec.loader is None:
        sys.stderr.write("SAFEPATCH_CONTROLLED_PLUGIN_SPEC_FAILED\n")
        raise SystemExit(2)
    module = importlib.util.module_from_spec(spec)
    sys.modules[CONTROLLED_MODULE] = module
    spec.loader.exec_module(module)
    loaded = Path(getattr(module, "__file__", "")).resolve()
    if loaded != path:
        sys.stderr.write(f"SAFEPATCH_CONTROLLED_PLUGIN_ORIGIN_ERROR={loaded}\n")
        raise SystemExit(2)
    sys.stderr.write(f"SAFEPATCH_CONTROLLED_PLUGIN_ORIGIN={loaded}\n")
    return module


def restore_python_m_sys_path() -> None:
    """Match ``python -m pytest``: cwd is import root; script dir is not.

    Called only after the controlled plugin and pytest are already imported
    from trusted locations so ``/work`` cannot shadow them.
    """
    controlled = CONTROLLED_DIR.resolve()
    kept: list[str] = []
    for entry in sys.path:
        if entry in ("", "."):
            continue
        try:
            resolved = Path(entry).resolve()
        except OSError:
            kept.append(entry)
            continue
        if resolved == controlled:
            continue
        kept.append(entry)
    sys.path[:] = ["", *kept]


def _assert_pytest_not_from_workspace() -> None:
    import pytest

    origin = Path(getattr(pytest, "__file__", "") or ".").resolve()
    work = WORKSPACE_ROOT
    try:
        work = work.resolve()
    except OSError:
        return
    if not work.exists():
        return
    if origin == work or work in origin.parents:
        sys.stderr.write(f"SAFEPATCH_PYTEST_ORIGIN_ERROR={origin}\n")
        raise SystemExit(2)


def main(argv: list[str]) -> int:
    load_controlled_plugin()
    import pytest  # noqa: F401 — pin origin before restoring cwd imports

    _assert_pytest_not_from_workspace()
    restore_python_m_sys_path()
    _assert_pytest_not_from_workspace()
    plugin = sys.modules[CONTROLLED_MODULE]
    return int(pytest.main(argv, plugins=[plugin]))


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
