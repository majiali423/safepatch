"""Discover protected test files without executing untrusted pytest/conftest."""

from __future__ import annotations

import ast
import configparser
import fnmatch
import json
import shlex
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

from code_agent.repository.workspace import WorkspaceError, rel_posix, safe_resolve

DEFAULT_PYTHON_FILES = ("test_*.py", "*_test.py")
UNSAFE_PATTERN_MARKERS = ("**", "..")
CONTAINER_PREFIXES = ("/work/", "/workspace/", "/app/")
INVENTORY_VERSION = 1
INVENTORY_FILENAME = ".safepatch_pytest_inventory.json"
PLUGIN_INVENTORY_SOURCE = "safepatch_inventory_plugin"
MAX_INVENTORY_BYTES = 262_144
MAX_INVENTORY_FILES = 500
OPTIONS_WITH_VALUE = {
    "-k",
    "-m",
    "-p",
    "-c",
    "-o",
    "-n",
    "-w",
    "-W",
    "--tb",
    "--maxfail",
    "--basetemp",
    "--rootdir",
    "--confcutdir",
    "--override-ini",
    "--durations",
    "--import-mode",
    "--doctest-glob",
    "--ignore",
    "--ignore-glob",
    "--deselect",
    "--python-files",
    "--python-classes",
    "--python-functions",
}

# Supported pytest.ini / tool.pytest.ini_options keys. Any other key is
# treated as an unknown collection/config extension.
SUPPORTED_INI_KEYS = frozenset(
    {
        "pythonpath",
        "python_files",
        "addopts",
        "testpaths",
        "filterwarnings",
        "minversion",
        "console_output_style",
        "pytest_plugins",
    }
)
PLUGIN_ADDOPTS = frozenset({"-p", "-o", "--override-ini"})
ALLOWED_ADDOPTS_FLAGS = frozenset(
    {
        "-q",
        "--quiet",
        "-v",
        "--verbose",
        "-s",
        "-x",
        "--exitfirst",
        "-l",
        "--showlocals",
        "--tb",
        "--maxfail",
        "--durations",
        "--import-mode",
        "--color",
        "--code-highlight",
        "--strict-markers",
        "--strict-config",
        "-k",
        "-m",
        "-W",
        "--pythonpath",
        "--ignore",
        "--ignore-glob",
        "--deselect",
        "--basetemp",
        "--rootdir",
        "--confcutdir",
        "--doctest-glob",
        "-r",
        "--disable-warnings",
        "--no-header",
        "--no-summary",
        "-ra",
        "-rs",
    }
)
class InventoryCompleteness(str, Enum):
    """Explicit collection states. Do not infer these from None/[]/non-empty lists."""

    MISSING = "missing"
    FAILED = "failed"
    PARTIAL = "partial"
    COMPLETE = "complete"
    COMPLETE_EMPTY = "complete_empty"
    UNVERIFIED = "unverified"


@dataclass(frozen=True)
class TestInventoryReport:
    __test__ = False
    completeness: InventoryCompleteness = InventoryCompleteness.MISSING
    files: tuple[str, ...] = ()
    unmapped: tuple[str, ...] = ()
    source: str = ""
    version: int | None = None
    truncated: bool = False
    error: str | None = None
    origin_trusted: bool = False

    def to_dict(self) -> dict[str, object]:
        return {
            "completeness": self.completeness.value,
            "files": list(self.files),
            "unmapped": list(self.unmapped),
            "source": self.source,
            "version": self.version,
            "truncated": self.truncated,
            "error": self.error,
            "origin_trusted": self.origin_trusted,
        }


@dataclass
class ProtectedTestInventory:
    __test__ = False
    files: set[str] = field(default_factory=set)
    fail_closed: bool = False
    unsupported_scope: bool = False
    reasons: list[str] = field(default_factory=list)
    completeness: InventoryCompleteness = InventoryCompleteness.MISSING


def discover_protected_test_files(
    workspace_root: Path,
    *,
    isolated_nodeids: list[str] | None = None,
    report: TestInventoryReport | None = None,
) -> set[str]:
    """Return repo-relative paths that must be treated as existing tests."""
    return build_protected_inventory(
        workspace_root, isolated_nodeids=isolated_nodeids, report=report
    ).files


def build_protected_inventory(
    workspace_root: Path,
    *,
    isolated_nodeids: list[str] | None = None,
    report: TestInventoryReport | None = None,
) -> ProtectedTestInventory:
    """Conservative names + config paths, plus extras that never prove completeness.

    Never executes pytest or conftest on the host. Log extracts and isolated
    nodeids may only add files. A workspace JSON report is shape-checked only
    and cannot prove origin; fail-closed lifts only when origin_trusted is set
    by a channel the product does not currently have.
    """
    root = workspace_root.resolve()
    inventory = ProtectedTestInventory()
    py_files = [
        path for path in root.rglob("*.py") if path.is_file() and not path.is_symlink()
    ]
    all_rels = {_rel(root, path) for path in py_files}
    report = report or TestInventoryReport()
    if report.completeness is InventoryCompleteness.MISSING and isolated_nodeids:
        inventory.completeness = InventoryCompleteness.PARTIAL
    else:
        inventory.completeness = report.completeness

    hard_fail = False
    custom_collector = False

    config, config_error = _load_pytest_config(root)
    if config_error:
        inventory.fail_closed = True
        inventory.unsupported_scope = True
        hard_fail = True
        inventory.reasons.append(config_error)
        inventory.files = set(all_rels)
        return _finish_inventory(
            inventory,
            root=root,
            all_rels=all_rels,
            isolated_nodeids=isolated_nodeids,
            report=report,
            hard_fail=True,
            custom_collector=False,
        )

    patterns, unsafe = _python_file_patterns(config)
    if unsafe:
        inventory.fail_closed = True
        inventory.unsupported_scope = True
        hard_fail = True
        inventory.reasons.append("unsafe python_files pattern")
        inventory.files = set(all_rels)
        return _finish_inventory(
            inventory,
            root=root,
            all_rels=all_rels,
            isolated_nodeids=isolated_nodeids,
            report=report,
            hard_fail=True,
            custom_collector=False,
        )

    scope_issues = _pytest_scope_issues(root, config)
    if scope_issues:
        custom_collector = True
        inventory.fail_closed = True
        inventory.unsupported_scope = True
        inventory.reasons.extend(scope_issues)

    for path in py_files:
        rel = _rel(root, path)
        name = path.name
        if name == "conftest.py":
            inventory.files.add(rel)
            continue
        if "/tests/" in f"/{rel}" or rel.startswith("tests/"):
            inventory.files.add(rel)
            continue
        for pattern in patterns:
            if fnmatch.fnmatch(name, pattern) or fnmatch.fnmatch(rel, pattern):
                inventory.files.add(rel)
                break

    for raw in config.explicit_paths:
        if not str(raw).endswith(".py"):
            continue
        mapped = map_pytest_nodeid_to_workspace(raw, root)
        if mapped is None:
            inventory.fail_closed = True
            inventory.unsupported_scope = True
            hard_fail = True
            inventory.reasons.append(f"unmappable configured path: {raw}")
        else:
            inventory.files.add(mapped)

    return _finish_inventory(
        inventory,
        root=root,
        all_rels=all_rels,
        isolated_nodeids=isolated_nodeids,
        report=report,
        hard_fail=hard_fail,
        custom_collector=custom_collector,
    )


def _finish_inventory(
    inventory: ProtectedTestInventory,
    *,
    root: Path,
    all_rels: set[str],
    isolated_nodeids: list[str] | None,
    report: TestInventoryReport,
    hard_fail: bool,
    custom_collector: bool,
) -> ProtectedTestInventory:
    extra_items: list[str] = list(isolated_nodeids or ())
    if report.completeness in {
        InventoryCompleteness.MISSING,
        InventoryCompleteness.FAILED,
        InventoryCompleteness.PARTIAL,
    }:
        extra_items.extend(report.files)

    unmapped_extras = _add_mapped_paths(inventory, extra_items, root)
    if unmapped_extras:
        inventory.reasons.append(
            "unmapped inventory paths: " + ", ".join(unmapped_extras[:8])
        )

    complete_mapped: list[str] = []
    complete_unmapped: list[str] = []
    trusted_complete = bool(report.origin_trusted) and report.completeness in {
        InventoryCompleteness.COMPLETE,
        InventoryCompleteness.COMPLETE_EMPTY,
    }
    if trusted_complete:
        for item in report.files:
            mapped = map_pytest_nodeid_to_workspace(item, root)
            if mapped is None:
                complete_unmapped.append(item)
            else:
                complete_mapped.append(mapped)
        if complete_unmapped:
            inventory.fail_closed = True
            hard_fail = True
            inventory.reasons.append(
                "unmapped complete-inventory paths: "
                + ", ".join(complete_unmapped[:8])
            )

    if unmapped_extras and (
        custom_collector
        or trusted_complete
    ):
        inventory.fail_closed = True
        hard_fail = True

    can_lift = (
        custom_collector
        and not hard_fail
        and report.origin_trusted
        and report.completeness is InventoryCompleteness.COMPLETE
        and report.version == INVENTORY_VERSION
        and not report.truncated
        and not complete_unmapped
        and not unmapped_extras
        and not report.error
    )
    if can_lift:
        inventory.fail_closed = False
        inventory.files.update(complete_mapped)
        inventory.reasons.append("complete docker pytest inventory")
        return inventory

    if inventory.fail_closed:
        inventory.unsupported_scope = True
        inventory.files = set(all_rels)
        inventory.reasons.append("fail-closed: treat every Python file as a test")
        return inventory

    if complete_mapped:
        inventory.files.update(complete_mapped)

    if (
        isolated_nodeids is not None
        and len(isolated_nodeids) == 0
        and report.completeness
        in {
            InventoryCompleteness.MISSING,
            InventoryCompleteness.PARTIAL,
            InventoryCompleteness.FAILED,
            InventoryCompleteness.COMPLETE_EMPTY,
            InventoryCompleteness.UNVERIFIED,
        }
        and not inventory.files
    ):
        inventory.fail_closed = True
        inventory.unsupported_scope = True
        inventory.reasons.append("isolated collection returned an empty test list")
        inventory.files = set(all_rels)
    return inventory


def _add_mapped_paths(
    inventory: ProtectedTestInventory, items: list[str], root: Path
) -> list[str]:
    unmapped: list[str] = []
    seen: set[str] = set()
    for item in items:
        if item in seen:
            continue
        seen.add(item)
        mapped = map_pytest_nodeid_to_workspace(item, root)
        if mapped is None:
            unmapped.append(item)
        else:
            inventory.files.add(mapped)
    return unmapped


def map_pytest_nodeid_to_workspace(nodeid: str, workspace_root: Path) -> str | None:
    """Map a pytest nodeid or file arg onto a workspace-relative path.

    Container prefixes such as ``/work/`` are accepted. Host absolute paths,
    ``..`` escapes, and missing files are rejected.
    """
    raw = str(nodeid).strip().strip("\"'")
    if not raw:
        return None
    raw = raw.split("::", 1)[0].replace("\\", "/")
    for prefix in CONTAINER_PREFIXES:
        if raw.startswith(prefix):
            raw = raw[len(prefix) :]
            break
    if not raw.endswith(".py"):
        return None
    if Path(raw).is_absolute() or (len(raw) > 1 and raw[1] == ":"):
        return None
    if ".." in Path(raw).parts:
        return None
    try:
        path = safe_resolve(workspace_root, raw)
    except WorkspaceError:
        return None
    if not path.is_file() or path.is_symlink():
        return None
    try:
        return rel_posix(workspace_root, path)
    except ValueError:
        return None


def load_test_inventory_file(path: Path) -> TestInventoryReport:
    """Read a bounded plugin inventory from the disposable Docker copy."""
    if not path.is_file() or path.is_symlink():
        return TestInventoryReport(
            completeness=InventoryCompleteness.MISSING,
            error="inventory file missing",
        )
    try:
        size = path.stat().st_size
    except OSError as exc:
        return TestInventoryReport(
            completeness=InventoryCompleteness.FAILED,
            error=f"inventory stat failed: {exc}",
        )
    if size > MAX_INVENTORY_BYTES:
        return TestInventoryReport(
            completeness=InventoryCompleteness.FAILED,
            truncated=True,
            error="inventory exceeds size limit",
        )
    try:
        raw = path.read_bytes()
    except OSError as exc:
        return TestInventoryReport(
            completeness=InventoryCompleteness.FAILED,
            error=f"inventory read failed: {exc}",
        )
    if len(raw) > MAX_INVENTORY_BYTES:
        return TestInventoryReport(
            completeness=InventoryCompleteness.FAILED,
            truncated=True,
            error="inventory exceeds size limit",
        )
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        return TestInventoryReport(
            completeness=InventoryCompleteness.FAILED,
            error=f"inventory JSON is corrupt: {exc}",
        )
    if not isinstance(payload, dict):
        return TestInventoryReport(
            completeness=InventoryCompleteness.FAILED,
            error="inventory JSON must be an object",
        )
    version = payload.get("version")
    source = payload.get("source")
    if version != INVENTORY_VERSION or source != PLUGIN_INVENTORY_SOURCE:
        return TestInventoryReport(
            completeness=InventoryCompleteness.FAILED,
            source=str(source or ""),
            version=version if isinstance(version, int) else None,
            error="unsupported inventory version or source",
        )
    files_raw = payload.get("files")
    if not isinstance(files_raw, list):
        return TestInventoryReport(
            completeness=InventoryCompleteness.FAILED,
            source=str(source),
            version=version,
            error="inventory files must be a list",
        )
    if len(files_raw) > MAX_INVENTORY_FILES:
        return TestInventoryReport(
            completeness=InventoryCompleteness.PARTIAL,
            source=str(source),
            version=version,
            truncated=True,
            error="inventory file list exceeds limit",
        )
    files: list[str] = []
    for item in files_raw:
        if not isinstance(item, str) or not item.strip():
            return TestInventoryReport(
                completeness=InventoryCompleteness.FAILED,
                source=str(source),
                version=version,
                error="unsupported inventory path entry",
            )
        files.append(item.strip())
    truncated = bool(payload.get("truncated"))
    complete = bool(payload.get("complete")) and not truncated
    if truncated or not complete:
        return TestInventoryReport(
            completeness=InventoryCompleteness.PARTIAL,
            files=tuple(files),
            source=str(source),
            version=version,
            truncated=truncated,
            origin_trusted=False,
            error=None if complete or truncated else "inventory is incomplete",
        )
    return TestInventoryReport(
        completeness=InventoryCompleteness.UNVERIFIED,
        files=tuple(files),
        source=str(source),
        version=version,
        origin_trusted=False,
        error=(
            "workspace inventory cannot prove controlled-plugin origin "
            "(target-writable /work, same uid as tests)"
        ),
    )


def extract_pytest_nodeids(*texts: str) -> list[str]:
    """Pull file::node identifiers out of isolated pytest stdout/stderr."""
    found: list[str] = []
    seen: set[str] = set()
    for text in texts:
        for line in (text or "").splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            for token in stripped.replace("(", " ").replace(")", " ").split():
                if ".py::" not in token:
                    continue
                if token not in seen:
                    seen.add(token)
                    found.append(token)
    return found


def _rel(root: Path, path: Path) -> str:
    return path.resolve().relative_to(root).as_posix()


@dataclass
class _PytestConfig:
    python_files: list[str] | None = None
    explicit_paths: list[str] = field(default_factory=list)
    pytest_plugins: list[str] = field(default_factory=list)
    addopts_plugins: list[str] = field(default_factory=list)
    unsupported_addopts: list[str] = field(default_factory=list)
    unsupported_keys: list[str] = field(default_factory=list)


def _python_file_patterns(config: _PytestConfig) -> tuple[tuple[str, ...], bool]:
    configured = config.python_files
    if not configured:
        return DEFAULT_PYTHON_FILES, False
    if any(_unsafe_pattern(item) for item in configured):
        return DEFAULT_PYTHON_FILES, True
    return tuple(configured) + DEFAULT_PYTHON_FILES, False


def _unsafe_pattern(pattern: str) -> bool:
    text = pattern.strip()
    if not text:
        return True
    return any(marker in text for marker in UNSAFE_PATTERN_MARKERS) or (
        len(text) > 1 and text[1] == ":"
    )


def _load_pytest_config(root: Path) -> tuple[_PytestConfig, str | None]:
    """Load at most one pytest config file.

    Multiple files that pytest might treat as an inifile are rejected rather
    than ranked with a homemade precedence (pytest's real order is
    pytest.ini, .pytest.ini, pyproject.toml, tox.ini, setup.cfg).
    """
    candidates: list[tuple[str, _PytestConfig]] = []

    for name in ("pytest.ini", ".pytest.ini"):
        path = root / name
        if not path.is_file():
            continue
        parsed, error = _from_ini(path, section="pytest")
        if error:
            return _PytestConfig(), error
        candidates.append((name, parsed or _PytestConfig()))

    pyproject = root / "pyproject.toml"
    if pyproject.is_file():
        parsed, error = _from_pyproject(pyproject)
        if error:
            return _PytestConfig(), error
        if parsed is not None:
            candidates.append(("pyproject.toml", parsed))

    tox = root / "tox.ini"
    if tox.is_file():
        parsed, error = _from_ini(tox, section="pytest")
        if error:
            return _PytestConfig(), error
        if parsed is not None:
            candidates.append(("tox.ini", parsed))

    setup_cfg = root / "setup.cfg"
    if setup_cfg.is_file():
        parsed, error = _from_ini(setup_cfg, section="tool:pytest")
        if error:
            return _PytestConfig(), error
        if parsed is not None:
            candidates.append(("setup.cfg", parsed))

    if len(candidates) > 1:
        names = ", ".join(item[0] for item in candidates)
        return _PytestConfig(), (
            "multiple pytest configuration files "
            f"({names}); automatic repair requires exactly one"
        )
    if not candidates:
        return _PytestConfig(), None
    return candidates[0][1], None


def _from_ini(path: Path, *, section: str) -> tuple[_PytestConfig | None, str | None]:
    parser = configparser.ConfigParser()
    try:
        parser.read(path, encoding="utf-8")
    except (OSError, configparser.Error) as exc:
        return None, f"pytest config parse failed: {exc}"
    if not parser.has_section(section):
        return None, None
    cfg = _PytestConfig()
    for key in parser.options(section):
        if key not in SUPPORTED_INI_KEYS:
            cfg.unsupported_keys.append(key)
    if parser.has_option(section, "python_files"):
        cfg.python_files = parser.get(section, "python_files").replace("\n", " ").split()
    if parser.has_option(section, "addopts"):
        paths, plugins, unknown = _parse_addopts(parser.get(section, "addopts"))
        cfg.explicit_paths.extend(paths)
        cfg.addopts_plugins.extend(plugins)
        cfg.unsupported_addopts.extend(unknown)
    if parser.has_option(section, "testpaths"):
        cfg.explicit_paths.extend(
            parser.get(section, "testpaths").replace("\n", " ").split()
        )
    if parser.has_option(section, "pytest_plugins"):
        cfg.pytest_plugins.extend(
            parser.get(section, "pytest_plugins").replace("\n", " ").split()
        )
    return cfg, None


def _from_pyproject(path: Path) -> tuple[_PytestConfig | None, str | None]:
    try:
        import tomllib
    except ImportError:  # pragma: no cover - Python < 3.11
        try:
            import tomli as tomllib  # type: ignore[no-redef]
        except ImportError:
            return None, "pytest pyproject.toml could not be parsed"
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        return None, f"pytest pyproject.toml parse failed: {exc}"
    options = data.get("tool", {}).get("pytest", {}).get("ini_options", {})
    if not options:
        return None, None
    cfg = _PytestConfig()
    for key in options:
        if str(key) not in SUPPORTED_INI_KEYS:
            cfg.unsupported_keys.append(str(key))
    raw = options.get("python_files")
    if isinstance(raw, str):
        cfg.python_files = raw.split()
    elif isinstance(raw, list):
        cfg.python_files = [str(item) for item in raw]
    addopts = options.get("addopts")
    if isinstance(addopts, str):
        paths, plugins, unknown = _parse_addopts(addopts)
        cfg.explicit_paths.extend(paths)
        cfg.addopts_plugins.extend(plugins)
        cfg.unsupported_addopts.extend(unknown)
    elif isinstance(addopts, list):
        paths, plugins, unknown = _parse_addopts(" ".join(str(x) for x in addopts))
        cfg.explicit_paths.extend(paths)
        cfg.addopts_plugins.extend(plugins)
        cfg.unsupported_addopts.extend(unknown)
    testpaths = options.get("testpaths")
    if isinstance(testpaths, str):
        cfg.explicit_paths.extend(testpaths.split())
    elif isinstance(testpaths, list):
        cfg.explicit_paths.extend(str(item) for item in testpaths)
    plugins = options.get("pytest_plugins")
    if isinstance(plugins, str):
        cfg.pytest_plugins.extend(plugins.split())
    elif isinstance(plugins, list):
        cfg.pytest_plugins.extend(str(item) for item in plugins)
    return cfg, None


def _parse_addopts(raw: str) -> tuple[list[str], list[str], list[str]]:
    try:
        tokens = shlex.split(raw, posix=True)
    except ValueError:
        tokens = raw.replace("\n", " ").split()
    paths: list[str] = []
    plugins: list[str] = []
    unknown: list[str] = []
    skip_next = False
    pending_plugin = False
    for token in tokens:
        if skip_next:
            skip_next = False
            pending_plugin = False
            continue
        if token.startswith("-"):
            key = token.split("=", 1)[0]
            if key in PLUGIN_ADDOPTS:
                plugins.append(token)
                if "=" not in token:
                    skip_next = True
                    pending_plugin = True
                continue
            if key in ALLOWED_ADDOPTS_FLAGS:
                if "=" not in token and key in OPTIONS_WITH_VALUE:
                    skip_next = True
                continue
            unknown.append(token)
            if "=" not in token and key in OPTIONS_WITH_VALUE:
                skip_next = True
            continue
        if pending_plugin:
            pending_plugin = False
            continue
        paths.append(token)
    return paths, plugins, unknown


def _explicit_paths_from_addopts(raw: str) -> list[str]:
    paths, _plugins, _unknown = _parse_addopts(raw)
    return paths


def _pytest_scope_issues(root: Path, config: _PytestConfig) -> list[str]:
    issues: list[str] = []
    if config.pytest_plugins:
        issues.append("pytest_plugins is outside the supported pytest subset")
    if config.addopts_plugins:
        issues.append("addopts plugin entry (-p/-o) is outside the supported pytest subset")
    if config.unsupported_addopts:
        issues.append(
            "unsupported addopts: " + ", ".join(config.unsupported_addopts[:8])
        )
    if config.unsupported_keys:
        issues.append(
            "unsupported pytest config keys: " + ", ".join(config.unsupported_keys[:8])
        )
    for conftest in root.rglob("conftest.py"):
        if not conftest.is_file() or conftest.is_symlink():
            continue
        issues.extend(_conftest_scope_issues(root, conftest))
    return issues


def _conftest_scope_issues(root: Path, path: Path) -> list[str]:
    """Allow only inert conftest AST: empty, comments, string docs, and pass."""
    rel = _rel(root, path)
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, SyntaxError):
        return [f"unreadable conftest {rel}; treating collection as unknown"]
    issues: list[str] = []
    for stmt in tree.body:
        if _is_inert_conftest_statement(stmt):
            continue
        kind = type(stmt).__name__
        issues.append(
            f"{rel} contains {kind}; executable conftest is outside the "
            "automatic-repair subset (fixtures are not auto-repaired)"
        )
    return issues


def _is_inert_conftest_statement(stmt: ast.stmt) -> bool:
    if isinstance(stmt, ast.Pass):
        return True
    return (
        isinstance(stmt, ast.Expr)
        and isinstance(stmt.value, ast.Constant)
        and isinstance(stmt.value.value, str)
    )
