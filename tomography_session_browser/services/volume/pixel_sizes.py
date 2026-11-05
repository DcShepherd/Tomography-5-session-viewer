"""Pixel sizes the user stated, kept in app-owned storage by file fingerprint.

A value here is what the user said about one file, not a measurement, and it never
reaches a source folder or header. A file whose size or modification time changed
no longer matches its record, so the app asks again. A record without a value means
the user took a value back: the header applies, and an older saved view cannot
return the value.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
import math
from uuid import uuid4

from tomography_session_browser.domain.tomograms import Volume
from tomography_session_browser.services.volume.review_repository import ReviewRepository, record_id

KIND = "pixel_size"
Signature = tuple[int, int]


def signature_of(volume: Volume) -> Signature:
    return volume.fingerprint.size, volume.fingerprint.mtime_ns


def new_operation() -> str:
    """One user action: it tells Undo which values that action still owns."""
    return uuid4().hex


@dataclass(frozen=True, slots=True)
class StatedPixelSize:
    volume_id: str
    signature: Signature
    value: float | None
    operation: str = ""


@dataclass(frozen=True, slots=True)
class StoredPixelSizes:
    entries: Mapping[str, StatedPixelSize] = field(default_factory=dict)
    rejected: frozenset[str] = frozenset()


def decode_stated(data: object) -> StatedPixelSize:
    """Strict: a record that is not exactly right must ask again, not calibrate."""
    if not isinstance(data, dict) or "value" not in data:
        raise ValueError("A saved pixel size must be an object.")
    volume_id, signature = data.get("volume_id"), data.get("signature")
    value, operation = data.get("value"), data.get("operation", "")
    if not isinstance(volume_id, str) or not volume_id or not isinstance(operation, str):
        raise ValueError("A saved pixel size has no valid identity.")
    if (not isinstance(signature, (list, tuple)) or len(signature) != 2
            or any(type(part) is not int or part < 0 for part in signature)):
        raise ValueError("A saved pixel size has no valid file signature.")
    if value is not None and (type(value) not in (int, float) or not math.isfinite(value) or value <= 0):
        raise ValueError("A saved pixel size must be finite and positive.")
    return StatedPixelSize(volume_id, (signature[0], signature[1]), value, operation)


def read_stated(repository: ReviewRepository, *,
                cancelled: Callable[[], bool] = lambda: False) -> StoredPixelSizes:
    stated: dict[str, StatedPixelSize] = {}
    rejected: set[str] = set()
    for record in repository.iter_records(KIND, cancelled=cancelled, rejected=rejected.add):
        try:
            entry = decode_stated(record.data.get("state"))
            if record_id(entry.volume_id) != record.identity:
                raise ValueError("A saved pixel size has the wrong identity.")
        except (ValueError, OverflowError):
            rejected.add(record.identity)
            continue
        stated[entry.volume_id] = entry
    return StoredPixelSizes(stated, frozenset(rejected))


class PixelSizes:
    """What this window knows. A value stated here outranks a later read of storage."""

    def __init__(self) -> None:
        self.entries: dict[str, StatedPixelSize] = {}
        self.stated_here: set[str] = set()
        self.revision = 0

    def entry(self, volume: Volume) -> StatedPixelSize | None:
        entry = self.entries.get(volume.id)
        return entry if entry is not None and entry.signature == signature_of(volume) else None

    def value_for(self, volume: Volume) -> float | None:
        entry = self.entry(volume)
        return entry.value if entry is not None else None

    def state(self, volume: Volume, value: float | None, operation: str) -> StatedPixelSize:
        entry = StatedPixelSize(volume.id, signature_of(volume), value, operation)
        self.entries[volume.id] = entry
        self.stated_here.add(volume.id)
        self.revision += 1
        return entry

    def merge(self, read: StoredPixelSizes) -> int:
        protected = {record_id(key) for key in self.stated_here} if read.rejected else set()
        rejected = read.rejected - protected
        removed = [key for key in self.entries if rejected and record_id(key) in rejected]
        for key in removed:
            del self.entries[key]
        updates = {key: value for key, value in read.entries.items()
                   if key not in self.stated_here and self.entries.get(key) != value}
        if updates or removed:
            self.entries.update(updates)
            self.revision += 1
        return len(rejected)
