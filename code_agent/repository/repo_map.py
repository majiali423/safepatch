from __future__ import annotations

import ast
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from code_agent.repository.workspace import list_py_files, rel_posix


@dataclass
class MethodInfo:
    name: str
    args: list[str]


@dataclass
class ClassInfo:
    name: str
    methods: list[MethodInfo] = field(default_factory=list)


@dataclass
class FunctionInfo:
    name: str
    args: list[str]
    is_pytest: bool = False


@dataclass
class FileMap:
    path: str
    imports: list[str] = field(default_factory=list)
    classes: list[ClassInfo] = field(default_factory=list)
    functions: list[FunctionInfo] = field(default_factory=list)
    syntax_error: str | None = None


def _arg_names(args: ast.arguments) -> list[str]:
    names: list[str] = []
    for a in list(args.posonlyargs) + list(args.args):
        names.append(a.arg)
    if args.vararg:
        names.append("*" + args.vararg.arg)
    for a in args.kwonlyargs:
        names.append(a.arg)
    if args.kwarg:
        names.append("**" + args.kwarg.arg)
    return names


def _is_pytest_name(name: str) -> bool:
    return name.startswith("test_") or name.endswith("_test")


def map_file(workspace_root: Path, path: Path) -> FileMap:
    rel = rel_posix(workspace_root, path)
    try:
        source = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return FileMap(path=rel, syntax_error="not_utf8")

    try:
        tree = ast.parse(source, filename=rel)
    except SyntaxError as exc:
        return FileMap(path=rel, syntax_error=f"syntax_error: {exc.msg} (line {exc.lineno})")

    imports: list[str] = []
    classes: list[ClassInfo] = []
    functions: list[FunctionInfo] = []

    for node in tree.body:
        if isinstance(node, ast.Import):
            for alias in node.names:
                imports.append(alias.name)
        elif isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            for alias in node.names:
                imports.append(f"{mod}.{alias.name}" if mod else alias.name)
        elif isinstance(node, ast.ClassDef):
            methods: list[MethodInfo] = []
            for item in node.body:
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    methods.append(
                        MethodInfo(name=item.name, args=_arg_names(item.args))
                    )
            classes.append(ClassInfo(name=node.name, methods=methods))
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            functions.append(
                FunctionInfo(
                    name=node.name,
                    args=_arg_names(node.args),
                    is_pytest=_is_pytest_name(node.name),
                )
            )

    return FileMap(
        path=rel,
        imports=imports,
        classes=classes,
        functions=functions,
    )


def build_repo_map(workspace_root: Path) -> tuple[str, dict[str, Any]]:
    files = [map_file(workspace_root, p) for p in list_py_files(workspace_root)]
    text_lines: list[str] = []
    data_files: list[dict[str, Any]] = []

    for fm in files:
        data_files.append(
            {
                "path": fm.path,
                "imports": fm.imports,
                "classes": [
                    {
                        "name": c.name,
                        "methods": [
                            {"name": m.name, "args": m.args} for m in c.methods
                        ],
                    }
                    for c in fm.classes
                ],
                "functions": [
                    {
                        "name": f.name,
                        "args": f.args,
                        "is_pytest": f.is_pytest,
                    }
                    for f in fm.functions
                ],
                "syntax_error": fm.syntax_error,
            }
        )

        text_lines.append(fm.path)
        if fm.syntax_error:
            text_lines.append(f"  <{fm.syntax_error}>")
            continue
        for c in fm.classes:
            text_lines.append(f"  class {c.name}")
            for m in c.methods:
                args = ", ".join(m.args)
                text_lines.append(f"    {m.name}({args})")
        for f in fm.functions:
            args = ", ".join(f.args)
            prefix = "  " if f.is_pytest else "  def "
            if f.is_pytest:
                text_lines.append(f"  {f.name}({args})")
            else:
                text_lines.append(f"{prefix}{f.name}({args})")
        text_lines.append("")

    text = "\n".join(text_lines).rstrip() + "\n"
    return text, {"files": data_files}
