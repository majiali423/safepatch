from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path

from code_agent.repository.workspace import list_py_files, rel_posix


@dataclass
class TextHit:
    path: str
    line: int
    text: str


@dataclass
class SymbolHit:
    path: str
    kind: str
    name: str
    line: int
    signature: str = ""


def search_text(
    workspace_root: Path,
    query: str,
    *,
    max_hits: int = 40,
) -> list[TextHit]:
    if not query:
        return []
    hits: list[TextHit] = []
    for path in list_py_files(workspace_root):
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except UnicodeDecodeError:
            continue
        rel = rel_posix(workspace_root, path)
        for i, line in enumerate(lines, start=1):
            if query in line:
                hits.append(TextHit(path=rel, line=i, text=line.strip()))
                if len(hits) >= max_hits:
                    return hits
    return hits


def search_symbol(workspace_root: Path, symbol: str) -> list[SymbolHit]:
    if not symbol:
        return []
    hits: list[SymbolHit] = []
    for path in list_py_files(workspace_root):
        try:
            source = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        try:
            tree = ast.parse(source)
        except SyntaxError:
            continue
        rel = rel_posix(workspace_root, path)
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef) and node.name == symbol:
                hits.append(
                    SymbolHit(
                        path=rel,
                        kind="class",
                        name=node.name,
                        line=node.lineno,
                        signature=f"class {node.name}",
                    )
                )
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if node.name == symbol:
                    args = ", ".join(
                        a.arg
                        for a in list(node.args.posonlyargs) + list(node.args.args)
                    )
                    parent_class = _enclosing_class(tree, node)
                    kind = "method" if parent_class else "function"
                    sig = (
                        f"{parent_class}.{node.name}({args})"
                        if parent_class
                        else f"{node.name}({args})"
                    )
                    hits.append(
                        SymbolHit(
                            path=rel,
                            kind=kind,
                            name=node.name,
                            line=node.lineno,
                            signature=sig,
                        )
                    )
    return hits


def _enclosing_class(tree: ast.AST, target: ast.AST) -> str | None:
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            for child in node.body:
                if child is target:
                    return node.name
    return None
