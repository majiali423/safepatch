"""Unified eligibility rules for repository measurement, copy, and post-check."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

IGNORE_DIR_NAMES = {
    ".git",
    ".venv",
    "venv",
    "__pycache__",
    "node_modules",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    "dist",
    "build",
    ".eggs",
    ".tox",
    ".idea",
    ".vscode",
    ".safepatch_sessions",
}

SECRET_DIR_NAMES = {
    ".ssh",
    ".gnupg",
    ".aws",
    "secrets",
    "credentials",
}

SESSION_DIR_NAMES = {".safepatch_sessions"}

ENV_EXAMPLE_NAMES = {".env.example", ".env.sample", ".env.template"}

SECRET_FILE_NAMES = {
    ".env",
    ".env.local",
    ".env.production",
    ".env.staging",
    ".env.development",
    ".netrc",
    "id_rsa",
    "id_dsa",
    "id_ecdsa",
    "id_ed25519",
}

SECRET_SUFFIXES = {".pem", ".key", ".p12", ".pfx", ".kdbx"}


@dataclass
class ImportDecision:
    include: bool
    category: str | None = None


@dataclass
class ImportFilter:
    """Eligibility shared by measure, copytree ignore, and post-copy audit."""

    source: Path
    session_root: Path | None
    extra_exclude_roots: tuple[Path, ...] = ()
    excluded_counts: dict[str, int] = field(default_factory=dict)

    def note(self, category: str) -> None:
        self.excluded_counts[category] = self.excluded_counts.get(category, 0) + 1

    def decide_dir(self, path: Path) -> ImportDecision:
        name = path.name
        if name in IGNORE_DIR_NAMES or name in SESSION_DIR_NAMES:
            return ImportDecision(False, "ignored_directory")
        if name in SECRET_DIR_NAMES:
            return ImportDecision(False, "secret_directory")
        if self._is_excluded_root(path):
            return ImportDecision(False, "session_root")
        return ImportDecision(True)

    def decide_file(self, path: Path) -> ImportDecision:
        if path.is_symlink():
            return ImportDecision(False, "symlink")
        name = path.name
        if name in ENV_EXAMPLE_NAMES:
            return ImportDecision(True)
        if name in SECRET_FILE_NAMES:
            return ImportDecision(False, "secret_file")
        if name.startswith(".env."):
            return ImportDecision(False, "secret_file")
        suffix = path.suffix.lower()
        if suffix in SECRET_SUFFIXES:
            return ImportDecision(False, "secret_file")
        if self._is_under_excluded_root(path):
            return ImportDecision(False, "session_root")
        return ImportDecision(True)

    def should_ignore_name(self, directory: Path, name: str) -> bool:
        candidate = directory / name
        if candidate.is_dir() or name in IGNORE_DIR_NAMES or name in SECRET_DIR_NAMES:
            decision = self.decide_dir(candidate)
        else:
            decision = self.decide_file(candidate)
        if not decision.include:
            self.note(decision.category or "excluded")
            return True
        return False

    def _is_excluded_root(self, path: Path) -> bool:
        try:
            resolved = path.resolve()
        except OSError:
            resolved = path
        roots = []
        if self.session_root is not None:
            roots.append(self.session_root)
        roots.extend(self.extra_exclude_roots)
        for root in roots:
            try:
                if resolved == root.resolve():
                    return True
            except OSError:
                if resolved == root:
                    return True
        return False

    def _is_under_excluded_root(self, path: Path) -> bool:
        try:
            resolved = path.resolve()
            source = self.source.resolve()
        except OSError:
            return False
        roots = []
        if self.session_root is not None:
            roots.append(self.session_root)
        roots.extend(self.extra_exclude_roots)
        for root in roots:
            try:
                root_res = root.resolve()
            except OSError:
                continue
            try:
                root_res.relative_to(source)
            except ValueError:
                # Session lives outside the source tree; source files are not under it.
                continue
            try:
                resolved.relative_to(root_res)
            except ValueError:
                continue
            return True
        return False


def resolve_session_base(session_base: Path | None) -> Path:
    return Path(session_base or Path.cwd() / ".safepatch_sessions").expanduser()


def validate_session_location(source: Path, session_base: Path) -> None:
    """Reject session paths that would copy into their own destination tree."""
    source_res = source.resolve()
    session_res = session_base.expanduser()
    try:
        session_res = session_res.resolve()
    except OSError:
        pass
    if session_res == source_res:
        raise ValueError("session_base cannot be the source repository root")
    for parent in [session_res, *session_res.parents]:
        if parent.name != "working_copy":
            continue
        try:
            parent.resolve().relative_to(source_res)
        except (ValueError, OSError):
            continue
        raise ValueError("session_base cannot be nested inside a working_copy")
