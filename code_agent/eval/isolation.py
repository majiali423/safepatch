"""Isolated evaluation copies for hidden-test runners.

Creates a temporary copy of a product workspace, yields it for patch injection
and Docker evaluation, and always removes the copy in ``finally``.
Never mutates the source workspace.
"""

from __future__ import annotations

import hashlib
import shutil
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

_IGNORE = shutil.ignore_patterns(
    ".git",
    ".safepatch_sessions",
    "__pycache__",
    ".pytest_cache",
    "*.pyc",
)


def workspace_content_fingerprint(root: Path) -> str:
    """Stable fingerprint of relative paths and file bytes under ``root``."""
    digest = hashlib.sha256()
    root = root.resolve()
    for path in sorted(p for p in root.rglob("*") if p.is_file()):
        digest.update(path.relative_to(root).as_posix().encode())
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


@contextmanager
def isolated_eval_copy(workspace: Path, *, prefix: str = "safepatch-eval-copy-") -> Iterator[Path]:
    """Copy ``workspace`` to a temp directory; always delete the copy afterward."""
    parent = Path(tempfile.mkdtemp(prefix=prefix))
    eval_copy = parent / "workspace"
    try:
        shutil.copytree(workspace, eval_copy, ignore=_IGNORE, symlinks=False)
        yield eval_copy
    finally:
        shutil.rmtree(parent, ignore_errors=True)
