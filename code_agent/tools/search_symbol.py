from __future__ import annotations

from pathlib import Path

from code_agent.repository.search import search_symbol as _search_symbol


def search_symbol(workspace_root: Path, symbol: str) -> str:
    hits = _search_symbol(workspace_root, symbol)
    if not hits:
        return f"No symbol named {symbol!r}"
    lines = [
        f"{h.path}:{h.line}: [{h.kind}] {h.signature or h.name}" for h in hits
    ]
    return "\n".join(lines)
