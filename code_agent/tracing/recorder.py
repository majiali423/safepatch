from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

# Back-compat aliases used by tests and llm.raw_preview.
from code_agent.tracing.sanitize import REDACT_KEYS as REDACT_KEYS  # noqa: F401
from code_agent.tracing.sanitize import sanitize
from code_agent.tracing.sanitize import sanitize as _redact  # noqa: F401


class TraceRecorder:
    def __init__(self, path: Path, *, extra_secrets: Iterable[str] = ()) -> None:
        self.path = path
        self.extra_secrets = tuple(secret for secret in extra_secrets if secret)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
            self.path.write_text("", encoding="utf-8")

    def emit(self, event_type: str, **payload: Any) -> None:
        record = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "event": event_type,
            "payload": sanitize(payload, extra_secrets=self.extra_secrets),
        }
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")
