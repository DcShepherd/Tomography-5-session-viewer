"""User-owned tags: immutable values, separate from scientific source models."""
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import TypeAlias

from tomography_session_browser.domain.tomograms import VolumeGrid

# Objects are tuples of key/value pairs; arrays are tuples. Capture records are
# frozen through the service admission boundary, then thawed for the history codec.
JsonScalar: TypeAlias = str | int | float | bool | None


@dataclass(frozen=True, slots=True)
class JsonObject:
    items: tuple[tuple[str, "JsonValue"], ...]


JsonValue: TypeAlias = JsonScalar | JsonObject | tuple["JsonValue", ...]


@dataclass(frozen=True, slots=True)
class Tag:
    id: str
    name: str
    created: str


@dataclass(frozen=True, slots=True)
class TagSource:
    id: str
    path: Path
    name: str
    previous_paths: tuple[Path, ...] = ()


@dataclass(frozen=True, slots=True)
class VolumeEvidence:
    version: str
    relative: str
    size: int
    mtime_ns: int
    grid: VolumeGrid
    dtype: str


class ChangeKind(StrEnum):
    MISSING = "missing"
    FILE_FACTS = "file-facts"
    DATE = "date"


@dataclass(frozen=True, slots=True)
class SourceAcceptance:
    accepted_at: str
    kind: ChangeKind


@dataclass(frozen=True, slots=True)
class TomogramRef:
    source: str
    relative: str
    volumes: tuple[VolumeEvidence, ...] = ()


@dataclass(frozen=True, slots=True)
class TagAssignment:
    tag_id: str
    count: int | None
    version_relative: str
    version_label: str
    recorded_at: str


@dataclass(frozen=True, slots=True, order=True)
class CaptureKey:
    created_ns: int
    history_id: str


@dataclass(frozen=True, slots=True)
class SavedCapture:
    key: CaptureKey
    record: JsonObject


@dataclass(frozen=True, slots=True)
class CaptureLink:
    tag_id: str
    capture_key: CaptureKey
    linked_at: str


@dataclass(frozen=True, slots=True)
class TomogramTags:
    ref: TomogramRef
    counting_complete: bool = False
    note: str = ""
    tags: tuple[TagAssignment, ...] = ()
    captures: tuple[SavedCapture, ...] = ()
    links: tuple[CaptureLink, ...] = ()
    acceptances: tuple[SourceAcceptance, ...] = ()


@dataclass(frozen=True, slots=True)
class TagProject:
    project_id: str
    created: str
    sources: tuple[TagSource, ...] = ()
    catalogue: tuple[Tag, ...] = ()
    tomograms: tuple[TomogramTags, ...] = ()
    revision: int = 0
