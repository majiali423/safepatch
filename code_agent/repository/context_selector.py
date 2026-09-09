"""Deterministic context ranking from tracebacks, symbols, and imports."""

from __future__ import annotations

import ast
import re
from pathlib import Path
from typing import Any

from code_agent.repository.workspace import WorkspaceError, safe_resolve

_TRACE_PATH_RE = re.compile(r"([A-Za-z0-9_./\\-]+\.py)")
_SYMBOL_RE = re.compile(r"\b([A-Za-z_][A-Za-z0-9_]*)\b")
_CONTAINER_PREFIXES = ("/work/", "/workspace/", "/app/")


def select_context_files(
    *,
    workspace_root: Path,
    repo_map_data: dict[str, Any],
    traceback_summary: str = "",
    bug_description: str = "",
    limit: int = 12,
) -> list[str]:
    """Rank Python files already present in the imported workspace map.

    Traceback strings are mapped onto that allowlist. Paths with ``..``, host
    absolute locations, symlink escapes, or ambiguous basenames are ignored
    and never opened.
    """
    known = _known_workspace_files(workspace_root, repo_map_data)
    scores: dict[str, float] = {rel: 0.0 for rel in known}
    for raw in _TRACE_PATH_RE.findall(traceback_summary or ""):
        rel = _map_traceback_path(raw, known)
        if rel is not None:
            scores[rel] += 8.0
    tokens = set(_SYMBOL_RE.findall(bug_description or "")) | set(
        _SYMBOL_RE.findall(traceback_summary or "")
    )
    imports = _import_neighbors(known, traceback_summary)
    for rel in scores:
        name = Path(rel).stem
        if name in tokens:
            scores[rel] += 3.0
        if rel in imports:
            scores[rel] += 4.0
    ranked = sorted(scores, key=lambda item: (-scores[item], item))
    return [path for path in ranked if scores[path] > 0][:limit]


def format_ranked_repo_map(
    repo_map_text: str,
    ranked_paths: list[str],
) -> str:
    if not ranked_paths:
        return repo_map_text
    header = "Ranked context files (traceback/symbol/import adjacency):\n" + "\n".join(
        f"- {path}" for path in ranked_paths
    )
    return header + "\n\n" + repo_map_text


def _known_workspace_files(
    workspace_root: Path, repo_map_data: dict[str, Any]
) -> dict[str, Path]:
    known: dict[str, Path] = {}
    for item in repo_map_data.get("files", []):
        rel = str(item.get("path", "")).replace("\\", "/").removeprefix("./")
        if not rel:
            continue
        try:
            path = safe_resolve(workspace_root, rel)
        except WorkspaceError:
            continue
        if path.is_file() and not path.is_symlink():
            known[rel] = path
    return known


def _map_traceback_path(raw: str, known: dict[str, Path]) -> str | None:
    norm = raw.replace("\\", "/").strip().strip("\"'")
    for prefix in _CONTAINER_PREFIXES:
        if norm.startswith(prefix):
            norm = norm[len(prefix) :]
            break
    if not norm.endswith(".py"):
        return None
    if Path(norm).is_absolute() or (len(norm) > 1 and norm[1] == ":"):
        return None
    if ".." in Path(norm).parts:
        return None
    if norm in known:
        return norm
    basename = Path(norm).name
    matches = [rel for rel in known if Path(rel).name == basename]
    if len(matches) == 1:
        return matches[0]
    return None


def _import_neighbors(known: dict[str, Path], traceback_summary: str) -> set[str]:
    neighbors: set[str] = set()
    for raw in _TRACE_PATH_RE.findall(traceback_summary or ""):
        rel = _map_traceback_path(raw, known)
        if rel is None:
            continue
        path = known[rel]
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (OSError, SyntaxError, UnicodeDecodeError):
            continue
        for node in ast.walk(tree):
            candidates: list[str] = []
            if isinstance(node, ast.ImportFrom) and node.module:
                candidates.append(node.module.replace(".", "/") + ".py")
            if isinstance(node, ast.Import):
                for alias in node.names:
                    candidates.append(alias.name.replace(".", "/") + ".py")
            for candidate in candidates:
                if candidate in known:
                    neighbors.add(candidate)
        neighbors.add(rel)
    return neighbors
