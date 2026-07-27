"""Text helpers (unused by slugify public API)."""


def collapse_spaces(s: str) -> str:
    return " ".join(s.split())
