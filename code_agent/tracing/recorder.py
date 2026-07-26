from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REDACT_KEYS = {
    "api_key",
    "api-key",
    "authorization",
    "openai_api_key",
    "code_agent_api_key",
    "token",
    "password",
    "secret",
}

# Scrub secret-like values even if they appear inside free-form strings.
_SECRET_VALUE_RE = re.compile(
    r"(?i)\b(sk-[A-Za-z0-9_\-]{8,}|api[_-]?key\s*[:=]\s*\S+|bearer\s+\S+)"
)


class TraceRecorder:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
            self.path.write_text("", encoding="utf-8")

    def emit(self, event_type: str, **payload: Any) -> None:
        record = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "event": event_type,
            "payload": _redact(payload),
        }
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")


def _redact(value: Any) -> Any:
    if isinstance(value, dict):
        out = {}
        for k, v in value.items():
            if str(k).lower() in REDACT_KEYS:
                out[k] = "***REDACTED***"
            else:
                out[k] = _redact(v)
        return out
    if isinstance(value, list):
        return [_redact(v) for v in value]
    if isinstance(value, str):
        return _SECRET_VALUE_RE.sub("***REDACTED***", value)
    return value
