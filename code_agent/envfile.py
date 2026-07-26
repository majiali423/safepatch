"""Optional local .env loader (no python-dotenv dependency)."""

from __future__ import annotations

import os
from pathlib import Path


def load_dotenv(path: Path | None = None, *, override: bool = False) -> Path | None:
    """Load KEY=VALUE pairs from .env into os.environ.

    - Skips blank lines and comments (# ...)
    - Does not override existing env vars unless override=True
    - Returns the path loaded, or None if missing
    """
    candidates: list[Path] = []
    if path is not None:
        candidates.append(path)
    else:
        # Prefer CWD, then package project root (two levels above this file).
        candidates.append(Path.cwd() / ".env")
        candidates.append(Path(__file__).resolve().parents[1] / ".env")

    env_path = next((p for p in candidates if p.is_file()), None)
    if env_path is None:
        return None

    for raw in env_path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].strip()
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip("'").strip('"')
        if not key:
            continue
        if not override and key in os.environ and os.environ[key] != "":
            continue
        os.environ[key] = value
    return env_path
