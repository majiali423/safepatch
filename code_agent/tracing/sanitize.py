"""Shared redaction for traces, summaries, logs, and CLI errors.

Does not rewrite working-copy source, pending diffs, or file revisions.
"""

from __future__ import annotations

import re
from typing import Any, Iterable

REDACT_PLACEHOLDER = "***REDACTED***"

REDACT_KEYS = {
    "api_key",
    "api-key",
    "authorization",
    "openai_api_key",
    "code_agent_api_key",
    "safepatch_api_key",
    "token",
    "password",
    "secret",
}

# Hash identifiers are not secret material. Diff bodies are still redacted in logs.
SKIP_REDACT_KEYS = {
    "patch_hash",
    "working_tree_hash",
}

_SECRET_VALUE_RE = re.compile(
    r"(?i)\b(sk-[A-Za-z0-9_\-]{8,}|api[_-]?key\s*[:=]\s*\S+|bearer\s+\S+)"
)


def sanitize(
    value: Any,
    *,
    extra_secrets: Iterable[str] = (),
    skip_keys: Iterable[str] | None = None,
) -> Any:
    """Redact known secret keys, exact secret values, and secret-like substrings."""
    skip = {str(k).lower() for k in (skip_keys or ())} | {
        k.lower() for k in SKIP_REDACT_KEYS
    }
    secrets = tuple(s for s in extra_secrets if s)
    return _sanitize(value, extra_secrets=secrets, skip_keys=skip)


def sanitize_text(text: str, *, extra_secrets: Iterable[str] = ()) -> str:
    redacted = sanitize(text, extra_secrets=extra_secrets)
    return redacted if isinstance(redacted, str) else str(redacted)


def _sanitize(
    value: Any,
    *,
    extra_secrets: tuple[str, ...],
    skip_keys: set[str],
) -> Any:
    if isinstance(value, dict):
        out = {}
        for key, item in value.items():
            lowered = str(key).lower()
            if lowered in REDACT_KEYS:
                out[key] = REDACT_PLACEHOLDER
            elif lowered in skip_keys:
                out[key] = item
            else:
                out[key] = _sanitize(
                    item, extra_secrets=extra_secrets, skip_keys=skip_keys
                )
        return out
    if isinstance(value, list):
        return [
            _sanitize(item, extra_secrets=extra_secrets, skip_keys=skip_keys)
            for item in value
        ]
    if isinstance(value, str):
        return _sanitize_string(value, extra_secrets=extra_secrets)
    return value


def _sanitize_string(text: str, *, extra_secrets: tuple[str, ...]) -> str:
    out = text
    for secret in extra_secrets:
        if secret and secret in out:
            out = out.replace(secret, REDACT_PLACEHOLDER)
    return _SECRET_VALUE_RE.sub(REDACT_PLACEHOLDER, out)
