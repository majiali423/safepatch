"""Bounded, revision-aware evidence retained across analysis rounds."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable


@dataclass(frozen=True)
class EvidenceItem:
    path: str
    revision: str
    start_line: int
    end_line: int
    content: str
    source: str
    total_lines: int = 0
    complete: bool = True

    @property
    def key(self) -> tuple[str, str, int, int]:
        return (self.path, self.revision, self.start_line, self.end_line)

    def serialized(self) -> str:
        return (
            f"\n# {self.path} lines {self.start_line}-{self.end_line} "
            f"of {self.total_lines} revision={self.revision} "
            f"source={self.source}\n{self.content}"
        )


@dataclass
class EvidenceStore:
    """Keep valid read evidence without replaying unbounded chat history."""

    max_items: int = 32
    max_chars: int = 24_000
    items: list[EvidenceItem] = field(default_factory=list)
    presented_keys: set[tuple[str, str, int, int]] = field(default_factory=set)

    def record(self, item: EvidenceItem) -> None:
        if item.start_line < 1 or item.end_line < item.start_line:
            return
        if not item.content and item.total_lines == 0:
            return
        self.items = [existing for existing in self.items if existing.key != item.key]
        self.items.append(item)
        self._trim()
        if item.complete and any(existing.key == item.key for existing in self.items):
            self.presented_keys.add(item.key)
        else:
            self.presented_keys.discard(item.key)

    def invalidate_path(self, path: str) -> None:
        norm = path.replace("\\", "/").removeprefix("./")
        self.items = [item for item in self.items if item.path != norm]
        self.presented_keys = {key for key in self.presented_keys if key[0] != norm}

    def invalidate_paths(self, paths: Iterable[str]) -> None:
        for path in paths:
            self.invalidate_path(path)

    def covers(
        self,
        path: str,
        start_line: int,
        end_line: int,
        revision: str | None = None,
    ) -> bool:
        norm = path.replace("\\", "/").removeprefix("./")
        merged = self._merged_ranges(norm, revision)
        for seen_start, seen_end in merged:
            if start_line >= seen_start and end_line <= seen_end:
                return True
        return False

    def ranges_by_path(self) -> dict[str, list[tuple[int, int]]]:
        out: dict[str, list[tuple[int, int]]] = {}
        for item in self.items:
            if item.key in self.presented_keys and item.complete:
                out.setdefault(item.path, []).append((item.start_line, item.end_line))
        return out

    def format_for_prompt(self, *, max_chars: int | None = None) -> str:
        budget = max_chars if max_chars is not None else self.max_chars
        if not self.items:
            self.presented_keys = set()
            return ""
        header = (
            "Retained evidence (valid at the recorded revision; do not treat "
            "evicted or stale revisions as already provided):"
        )
        parts = [header]
        used = len(header)
        included: list[EvidenceItem] = []
        omitted = False
        for item in self.items:
            block = item.serialized()
            if used + len(block) > budget:
                omitted = True
                continue
            parts.append(block)
            used += len(block)
            included.append(item)
        self.presented_keys = {item.key for item in included if item.complete}
        if omitted:
            note = (
                "\n[evidence omitted from this window exceeded the serialized "
                "budget and may be re-read]"
            )
            if used + len(note) <= budget:
                parts.append(note)
        return "".join(parts)

    def _merged_ranges(
        self, path: str, revision: str | None
    ) -> list[tuple[int, int]]:
        ranges = [
            (item.start_line, item.end_line)
            for item in self.items
            if item.path == path
            and item.complete
            and item.key in self.presented_keys
            and (revision is None or item.revision == revision)
        ]
        if not ranges:
            return []
        ranges.sort()
        merged: list[tuple[int, int]] = [ranges[0]]
        for start, end in ranges[1:]:
            last_start, last_end = merged[-1]
            if start <= last_end + 1:
                merged[-1] = (last_start, max(last_end, end))
            else:
                merged.append((start, end))
        return merged

    def _trim(self) -> None:
        while len(self.items) > self.max_items:
            removed = self.items.pop(0)
            self.presented_keys.discard(removed.key)
        while self.items and sum(len(item.serialized()) for item in self.items) > self.max_chars:
            removed = self.items.pop(0)
            self.presented_keys.discard(removed.key)
        kept: list[EvidenceItem] = []
        for item in self.items:
            if len(item.serialized()) > self.max_chars:
                self.presented_keys.discard(item.key)
                continue
            kept.append(item)
        self.items = kept
