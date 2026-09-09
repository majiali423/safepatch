"""Replace only declared volatile fields and known path strings."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

ISO_TIMESTAMP = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})?$"
)

TIME_KEYS = {"ts", "started_at", "finished_at"}
DURATION_KEYS = {"duration_ms", "duration_sec"}
SESSION_ID_KEYS = {"session_id"}


def slash(path: str) -> str:
    return path.replace("\\", "/")


def path_replacements(known_paths: dict[str, str]) -> list[tuple[str, str]]:
    pairs: list[tuple[str, str]] = []
    for raw, token in known_paths.items():
        if not raw:
            continue
        text = str(raw)
        pairs.append((slash(text), token))
        pairs.append((text, token))
    pairs.sort(key=lambda item: len(item[0]), reverse=True)
    return pairs


def replace_known_paths(text: str, replacements: list[tuple[str, str]]) -> str:
    out = text
    for source, token in replacements:
        if source:
            out = out.replace(source, token)
    return out


def normalize_string(text: str, *, key: str | None, replacements: list[tuple[str, str]]) -> str:
    # Trace payloads can embed source snippets and tool results. Normalize their
    # line endings independently of the host so payload comparison is semantic.
    out = replace_known_paths(text, replacements)
    # JSON trace fields may contain either literal newlines or escaped
    # ``\\r\\n`` sequences (for example a model response carrying a diff).
    # Canonicalize both representations so captures compare across hosts.
    out = (
        out.replace("\r\n", "\n")
        .replace("\r", "\n")
        .replace("\\r\\n", "\\n")
        .replace("\\r", "\\n")
    )
    if key in TIME_KEYS or ISO_TIMESTAMP.fullmatch(out):
        return "<TS>"
    if key in SESSION_ID_KEYS:
        return "<SESSION>"
    return out


def normalize_value(value: Any, replacements: list[tuple[str, str]], *, key: str | None = None) -> Any:
    if key in DURATION_KEYS and isinstance(value, (int, float)):
        return 0
    if isinstance(value, str):
        return normalize_string(value, key=key, replacements=replacements)
    if isinstance(value, Path):
        return normalize_string(str(value), key=key, replacements=replacements)
    if isinstance(value, list):
        return [normalize_value(item, replacements) for item in value]
    if isinstance(value, dict):
        return {
            inner_key: normalize_value(inner, replacements, key=str(inner_key))
            for inner_key, inner in value.items()
        }
    return value


def parse_trace(text: str) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for line in text.splitlines():
        if not line.strip():
            continue
        rec = json.loads(line)
        if not isinstance(rec, dict):
            raise ValueError("trace line is not an object")
        records.append(rec)
    return records


def dump_trace(records: list[dict[str, Any]]) -> str:
    return "".join(json.dumps(record, ensure_ascii=False) + "\n" for record in records)
