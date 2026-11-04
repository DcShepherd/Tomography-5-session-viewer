"""Window-owned review facts, valid only for the source revision that was read.

Unknown and refreshing capture counts are never inferred from an empty widget.
Rows outside the current viewer keep their last verified revision's facts.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from collections.abc import Iterable, Mapping

from tomography_session_browser.domain.tomograms import ProcessingSource, Tomogram


@dataclass(frozen=True, slots=True)
class RowReview:
    opened: bool | None = None
    captures: int | None = None
    note: str = "Review history has not been read for this source revision."


@dataclass(frozen=True, slots=True)
class ProcessingReviewCounts:
    opened: int | None = None
    captures: int | None = None
    capture_tomograms: int | None = None
    note: str = "Review history has not been read for this source revision."


class TomogramReviewState:
    def __init__(self) -> None:
        self._rows: dict[str, tuple[ProcessingSource, RowReview]] = {}

    def retain_sources(self, sources: Iterable[ProcessingSource]) -> None:
        """Drop removed identities without confusing replacement with a read."""
        current = {source.id: source for source in sources}
        self._rows = {key: value for key, value in self._rows.items()
                      if current.get(value[0].id) == value[0]}

    def row(self, item: Tomogram, source: ProcessingSource) -> RowReview:
        saved = self._rows.get(item.id)
        if saved is None:
            return RowReview()
        if saved[0] != source:
            return RowReview(note="The source revision differs; review totals are unavailable.")
        return saved[1]

    def opened(self, item: Tomogram, source: ProcessingSource, value: bool) -> None:
        self._rows[item.id] = source, replace(self.row(item, source), opened=value)

    def capture_counts(self, items: Iterable[Tomogram], sources: Iterable[ProcessingSource],
                       counts: Mapping[str, int] | None, note: str = "") -> None:
        by_source = {source.id: source for source in sources}
        for item in items:
            source = by_source[item.source_id]
            self._rows[item.id] = source, replace(self.row(item, source),
                captures=counts.get(item.id, 0) if counts is not None else None,
                note=note or ("Canonical capture-history counts; independent of reconstruction version."
                              if counts is not None else "Capture totals are being refreshed."))


def review_counts(rows: Iterable[RowReview]) -> ProcessingReviewCounts:
    values = tuple(rows)
    opened = sum(bool(row.opened) for row in values) if all(row.opened is not None for row in values) else None
    captures = sum(row.captures or 0 for row in values) if all(row.captures is not None for row in values) else None
    captured = sum(bool(row.captures) for row in values) if captures is not None else None
    notes = tuple(dict.fromkeys(row.note for row in values if row.opened is None or row.captures is None))
    return ProcessingReviewCounts(opened, captures, captured, " ".join(notes) or
                                  "Canonical review-history counts; independent of reconstruction version.")
