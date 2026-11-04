"""The acquisition dimension: what the microscope achieved, in one word.

Every status vocabulary in ``domain/status.py`` (validator verdicts, list rows,
Atlas marker glyphs) and every raw recorded batch word maps onto these six
words. An Atlas marker announces its status with them.

**It renames nothing.** The mapping is deliberately identical in meaning to
``item_status.display_status_label()``; this is the same decision expressed
as a dimension rather than a second opinion.
"""

from __future__ import annotations

from tomography_session_browser.domain.status import (
    AtlasMarkerStatus,
    RowStatus,
    ValidationStatus,
    parse_recorded_status,
)

ACQUISITION_COMPLETE = "Complete"
ACQUISITION_INCOMPLETE = "Incomplete"
ACQUISITION_FAILED = "Failed"
ACQUISITION_PENDING = "Pending"
# A file that should be on disk and is not, and an item the evidence cannot
# classify. One word, "Unavailable", used to cover both (decision D3).
ACQUISITION_MISSING = "Missing"
ACQUISITION_UNKNOWN = "Unknown"

_STATUS_TO_ACQUISITION: dict[str, str] = {
    RowStatus.DONE: ACQUISITION_COMPLETE,
    ValidationStatus.COMPLETE: ACQUISITION_COMPLETE,
    AtlasMarkerStatus.COLLECTED: ACQUISITION_COMPLETE,
    RowStatus.PARTIAL: ACQUISITION_INCOMPLETE,
    RowStatus.INCOMPLETE: ACQUISITION_INCOMPLETE,
    RowStatus.FAILED: ACQUISITION_FAILED,
    RowStatus.PENDING: ACQUISITION_PENDING,
    AtlasMarkerStatus.QUEUED: ACQUISITION_PENDING,
    RowStatus.MISSING: ACQUISITION_MISSING,
    RowStatus.UNKNOWN: ACQUISITION_UNKNOWN,
    "unavailable": ACQUISITION_UNKNOWN,
    "n/a": ACQUISITION_UNKNOWN,
    # An orphaned failed tilt series that no batch claims. What it means as
    # *acquisition* data for a batch is unknown; its link confidence is what
    # makes it special, and that is a different dimension.
    AtlasMarkerStatus.UNATTRIBUTED: ACQUISITION_UNKNOWN,
}


def acquisition_state(status: str | None) -> str:
    """Map any existing status string onto the acquisition dimension."""

    word = (status or "").strip().lower()
    if word in _STATUS_TO_ACQUISITION:
        return _STATUS_TO_ACQUISITION[word]
    recorded = parse_recorded_status(word)
    return _STATUS_TO_ACQUISITION[recorded] if recorded is not None else ACQUISITION_UNKNOWN
