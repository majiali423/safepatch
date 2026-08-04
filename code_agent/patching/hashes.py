"""Deterministic patch and working-tree hashes for approval binding."""

from __future__ import annotations

import hashlib
from pathlib import Path

_SKIP_DIR_NAMES = frozenset({"__pycache__", ".pytest_cache"})


def file_revision(path: Path) -> str:
    """Content revision used to bind a model read to a later edit."""
    return f"sha256:{hashlib.sha256(path.read_bytes()).hexdigest()}"


def patch_hash(unified_diff: str) -> str:
    return hashlib.sha256(unified_diff.encode("utf-8")).hexdigest()


def working_tree_hash(workspace_root: Path) -> str:
    """SHA256 over sorted (posix_path, content_sha256) pairs of regular files.

    Reads file bytes as-is (no newline translation). Skips ``__pycache__`` and
    ``.pytest_cache`` directory trees. Does not follow symlinks.
    """
    root = workspace_root.resolve()
    entries: list[tuple[str, str]] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.is_symlink():
            continue
        rel = path.relative_to(root)
        if any(part in _SKIP_DIR_NAMES for part in rel.parts):
            continue
        rel_posix = rel.as_posix()
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        entries.append((rel_posix, digest))

    h = hashlib.sha256()
    for rel_posix, digest in entries:
        h.update(rel_posix.encode("utf-8"))
        h.update(b"\0")
        h.update(digest.encode("ascii"))
        h.update(b"\n")
    return h.hexdigest()
