"""The app's status vocabularies, one typed enum per question.

Each answers a different question about the same object, so they stay
separate (``services/status_taxonomy.py`` maps them onto shared dimensions):

* ``ValidationStatus`` — the tilt-series validator's scientific verdict;
* ``RowStatus`` — a viewer list row's status, which the dashboard, the header
  and the PDF reuse;
* ``AtlasMarkerStatus`` — an Atlas batch-position marker's glyph.

The values are the words these statuses have always had, and a ``StrEnum``
member equals its word, so a status read back from a plain string compares
the same. ``parse_recorded_status`` is the one reading of the raw word the
microscope records for a batch position.
"""

from __future__ import annotations

from enum import StrEnum


class ValidationStatus(StrEnum):
    COMPLETE = "complete"
    INCOMPLETE = "incomplete"
    FAILED = "failed"
    UNKNOWN = "unknown"


class RowStatus(StrEnum):
    DONE = "done"
    #: Some of what was planned was acquired, or some of it failed.
    PARTIAL = "partial"
    INCOMPLETE = "incomplete"
    FAILED = "failed"
    #: A file that should be on disk and is not.
    MISSING = "missing"
    UNKNOWN = "unknown"
    #: Planned but not acquired yet: queued exposures, or a recorded "Queued".
    PENDING = "pending"
    #: An atlas whose mosaic is on disk.
    AVAILABLE = "available"
    #: An overview with recorded downstream acquisitions, and one without.
    WITH_ACQUISITIONS = "with acquisitions"
    NO_ACQUISITIONS = "no acquisitions"


class AtlasMarkerStatus(StrEnum):
    """Members run from least to most severe: a cluster shows its worst."""

    COLLECTED = "collected"
    QUEUED = "queued"
    PARTIAL = "partial"
    UNATTRIBUTED = "unattributed"
    FAILED = "failed"


_MARKER_SEVERITY: dict[str, int] = {status: index for index, status in enumerate(AtlasMarkerStatus)}


def marker_severity(status: str) -> int:
    """How severe a marker status is; an unrecognised word ranks as queued."""

    return _MARKER_SEVERITY.get(status, _MARKER_SEVERITY[AtlasMarkerStatus.QUEUED])


_RECORDED_WORDS: dict[str, RowStatus] = {
    **dict.fromkeys(("acquired", "done", "complete", "completed"), RowStatus.DONE),
    **dict.fromkeys(("failed", "error"), RowStatus.FAILED),
    **dict.fromkeys(("pending", "queued", "scheduled"), RowStatus.PENDING),
    "missing": RowStatus.MISSING,
    **dict.fromkeys(("partial", "incomplete", "warning"), RowStatus.PARTIAL),
}


def parse_recorded_status(word: str | None) -> RowStatus | None:
    """Read the status the microscope recorded for a batch position.

    The dashboard tiles, the list rows and the fallback labels all read the
    raw word here, so it cannot mean "pending" in one place and "warning" in
    another. ``None`` for an empty or unrecognised word.
    """

    return _RECORDED_WORDS.get((word or "").strip().lower())
