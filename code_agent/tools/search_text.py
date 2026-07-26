from __future__ import annotations

from pathlib import Path

from code_agent.repository.search import search_text as _search_text


def search_text(workspace_root: Path, query: str) -> str:
    hits = _search_text(workspace_root, query)
    if not hits:
        return f"No matches for {query!r}"
    lines = [f"{h.path}:{h.line}: {h.text}" for h in hits]
    return "\n".join(lines)
