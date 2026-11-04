"""Versioned app-owned scalar review records. Atomic, independent per identity.

Windows share records: last atomic view write wins for that source/version;
captures have unique identities and do not overwrite another window's captures.
No source is opened or loaded by this repository.
"""
from __future__ import annotations

import hashlib
import heapq
import json
import os
from pathlib import Path
import re
from collections.abc import Callable, Iterator
from dataclasses import dataclass

from tomography_session_browser.services.atomic_files import atomic_write
from tomography_session_browser.services.settings_service import settings_path
MAX_RECORD_BYTES = 65536
MAX_PAGE_SIZE = 200


def record_id(identity: str) -> str:
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class ReviewRecord:
    identity: str
    order: tuple[int, str]
    data: dict


@dataclass(frozen=True, slots=True)
class RecordPage:
    records: tuple[ReviewRecord, ...]
    next_cursor: tuple[int, str] | None
    total: int
    counts: tuple[tuple[str, int], ...] = ()

class ReviewRepository:
    def __init__(self, directory: Path | None = None) -> None:
        self.directory = directory or settings_path().parent / "tomogram-review-v1"

    def records(self, kind: str) -> list[dict]:
        """Compatibility read for non-interactive callers; UI consumers use pages."""
        try:
            return [record.data for record in sorted(self.iter_records(kind), key=lambda r: r.order)]
        except OSError:
            return []

    def iter_records(self, kind: str, *, cancelled: Callable[[], bool] = lambda: False) -> Iterator[ReviewRecord]:
        """Stream bounded app-owned JSON files; never inspect a saved source path."""
        if cancelled():
            raise InterruptedError("Obsolete history query")
        pattern = re.compile(re.escape(kind) + r"-([a-f0-9]{64})\.json\Z")
        try:
            paths = os.scandir(self.directory)
        except FileNotFoundError:
            return
        with paths:
            for entry in paths:
                if cancelled():
                    raise InterruptedError("Obsolete history query")
                match = pattern.fullmatch(entry.name)
                if match is None:
                    continue
                try:
                    if not entry.is_file(follow_symlinks=False) or entry.is_symlink():
                        continue
                    stat = entry.stat(follow_symlinks=False)
                    if stat.st_size > MAX_RECORD_BYTES:
                        continue
                    with open(entry.path, "rb") as handle:
                        content = handle.read(MAX_RECORD_BYTES + 1)
                    if len(content) > MAX_RECORD_BYTES:
                        continue
                    data = json.loads(content)
                    if isinstance(data, dict) and data.get("schema") == 1 and data.get("kind") == kind:
                        created = data.get("created_ns", stat.st_mtime_ns)
                        if type(created) is not int or created < 0:
                            created = stat.st_mtime_ns
                        yield ReviewRecord(match[1], (created, match[1]), data)
                except (OSError, ValueError, TypeError, RecursionError):
                    continue

    def page(self, kind: str, identities: frozenset[tuple[str, str]], *, before: tuple[int, str] | None = None,
             limit: int = 50, accept: Callable[[ReviewRecord], bool] = lambda _: True,
             extra: tuple[ReviewRecord, ...] = (), cancelled: Callable[[], bool] = lambda: False) -> RecordPage:
        """Filter before limiting; retain only one bounded page, newest first.

        Extra records are window-local exports whose history write failed. They
        have the same canonical IDs as durable records, and never multiply them.
        """
        if not 1 <= limit <= MAX_PAGE_SIZE or len(extra) > MAX_PAGE_SIZE:
            raise ValueError("History pages and window-local extras are limited to 200 records.")
        if not identities:
            return RecordPage((), None, 0)
        selected: list[tuple[tuple[int, str], ReviewRecord]] = []
        extras = {record.identity: record for record in extra}
        total = 0
        counts: dict[str, int] = {}

        def consider(record: ReviewRecord) -> None:
            nonlocal total
            state = record.data.get("state")
            if not isinstance(state, dict):
                return
            row, volume = state.get("row_key"), state.get("volume_id")
            if not isinstance(row, str) or not isinstance(volume, str) or (row, volume) not in identities or not accept(record):
                return
            total += 1
            counts[row] = counts.get(row, 0) + 1
            if before is not None and record.order >= before:
                return
            value = record.order, record
            if len(selected) < limit + 1:
                heapq.heappush(selected, value)
            elif record.order > selected[0][0]:
                heapq.heapreplace(selected, value)

        for record in self.iter_records(kind, cancelled=cancelled):
            if record.identity not in extras:
                consider(record)
        for record in extras.values():
            if cancelled():
                raise InterruptedError("Obsolete history query")
            consider(record)
        ordered = tuple(value[1] for value in sorted(selected, key=lambda value: value[0], reverse=True))
        page = ordered[:limit]
        return RecordPage(page, page[-1].order if len(ordered) > limit else None, total, tuple(sorted(counts.items())))

    def write(self, kind: str, identity: str, state: dict, **extra) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        key = record_id(identity)
        target = self.directory / f"{kind}-{key}.json"
        data = {"schema": 1, "kind": kind, "state": state, **extra}
        encoded = json.dumps(data, allow_nan=False)
        if len(encoded.encode("utf-8")) > MAX_RECORD_BYTES:
            raise ValueError("Review metadata exceeds the size limit.")
        atomic_write(target, encoded.encode("utf-8"))

    def has_capture(self, identity: str) -> bool:
        """History retains its output name even after the image is deleted."""
        return (self.directory / f"capture-{record_id(identity)}.json").exists()

    def remove_capture(self, identity: str) -> None:
        """Remove exactly one app-owned history record, never its exported image."""
        self.remove_capture_id(record_id(identity))

    def remove_capture_id(self, identity: str) -> None:
        if re.fullmatch(r"[a-f0-9]{64}", identity) is None:
            raise ValueError("Invalid capture history identity")
        (self.directory / f"capture-{identity}.json").unlink(missing_ok=True)
