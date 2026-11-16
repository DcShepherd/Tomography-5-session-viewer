"""Window-owned movie history: this window's unrecorded exports, the film counts and the Movies menu.

An unrecorded movie is an export whose history write is pending, failed or was
refused. It stays in an ordered map, newest last, until its own write succeeds,
and at most 200 stay. Each export has a window-local token; only the success
of that same export removes it, in whatever order the writes report back.
"""
from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
from collections.abc import Mapping
from types import MappingProxyType
import time

from PySide6.QtCore import QThreadPool

from tomography_session_browser.presentation.tomogram_movie import FrozenView
from tomography_session_browser.presentation.tomogram_movie_history import (MOVIE_KIND, MovieCounts, MovieWrite,
                                                                           movie_record)
from tomography_session_browser.presentation.tomogram_state import TomogramViewState
from tomography_session_browser.services.movie.models import MovieResult
from tomography_session_browser.services.volume.review_repository import MAX_PAGE_SIZE, ReviewRecord

#: The most unrecorded movies kept, the repository's limit for extra records.
MAX_UNRECORDED = MAX_PAGE_SIZE


@dataclass(frozen=True, slots=True)
class Unrecorded:
    token: int
    record: ReviewRecord


@dataclass(frozen=True, slots=True)
class MovieCountSnapshot:
    total: int
    rows: Mapping[str, int]


class MovieHistory:
    def __init__(self, history) -> None:
        self.history = history
        self.tab = history.tab
        self.unrecorded: OrderedDict[str, Unrecorded] = OrderedDict()
        self.token = 0
        self.snapshot: MovieCountSnapshot | None = None
        self.popup = None
        #: File checks only, apart from the history lane.
        self.check_pool = QThreadPool(self.tab)
        self.check_pool.setMaxThreadCount(1)
        self.tab.review_list.movie_column.requested.connect(self.show_menu)

    # Unrecorded exports and acknowledgements ------------------------------------
    def extras(self) -> tuple[ReviewRecord, ...]:
        return tuple(entry.record for entry in self.unrecorded.values())

    def saved(self, view: FrozenView, result: MovieResult) -> None:
        """Count the export at once, then record it so that it is there after a restart."""
        output = result.movie or result.path
        volume = view.volume
        state = TomogramViewState(view.row_key, volume.id, volume_signature=(volume.fingerprint.size, volume.fingerprint.mtime_ns))
        record = movie_record(state, output, time.time_ns())
        self.token += 1
        # An overwrite of the same file moves to the newest end; a plain dict would keep its old place.
        self.unrecorded.pop(record.identity, None)
        self.unrecorded[record.identity] = Unrecorded(self.token, record)
        while len(self.unrecorded) > MAX_UNRECORDED:
            self.unrecorded.popitem(last=False)
        # Kept before the write, so a write refused at once still finds its movie.
        self.changed(newest=True)
        self.tab.persistence.write(MOVIE_KIND, str(output), state, context=MovieWrite(record.identity, self.token),
                                   output=str(output), created_ns=record.order[0])

    def recorded(self, written: MovieWrite) -> None:
        """Only this export's own success removes it: an older write's success leaves a newer export."""
        entry = self.unrecorded.get(written.identity)
        if entry is not None and entry.token == written.token:
            del self.unrecorded[written.identity]
            self.changed()

    def not_recorded(self, written: MovieWrite) -> None:
        """The export stays unrecorded; nothing comes back that was replaced or dropped."""

    def changed(self, *, newest: bool = False) -> None:
        # A query that started with the old extras must not publish its result.
        self.history.revision += 1
        self.history.refresh_recent()
        if self.popup is not None and self.popup.isVisible():
            self.popup.show_newest() if newest else self.popup.refresh_page()

    # Counts ---------------------------------------------------------------------
    def counts_arrived(self, counts: MovieCounts) -> None:
        self.snapshot = MovieCountSnapshot(counts.total, MappingProxyType(dict(counts.rows)))

    def sync_slot(self) -> None:
        snapshot = self.snapshot
        self.tab.review_list.movie_column.set_counts(
            snapshot.rows if snapshot is not None else {}, available=snapshot is not None,
            refreshing=self.history.pending_counts is not None, error=self.history.refresh_error)

    def count(self, row_key: str) -> int | None:
        return self.snapshot.rows.get(row_key, 0) if self.snapshot is not None else None

    # The Movies menu --------------------------------------------------------------
    def show_menu(self, row_key: str) -> None:
        listing = self.tab.review_list
        if row_key not in listing.rows:
            return
        from tomography_session_browser.ui.tomograms.movie_popup import MoviePopup
        menu = MoviePopup(self, row_key)
        self.popup = menu
        slot = listing.movie_column
        slot.set_open(row_key)
        item = listing.items_by_key.get(row_key)
        if item is not None and listing.isVisible():
            point = listing.tree.viewport().mapToGlobal(slot.rect(listing.tree.visualItemRect(item)).bottomLeft())
        else:
            anchor = listing.compact if listing.compact.isVisible() else self.tab.movie.button
            point = anchor.mapToGlobal(anchor.rect().bottomLeft())
        try:
            menu.exec(point)
        finally:
            menu.cancel()
            slot.set_open(None)
            self.popup = None
            menu.deleteLater()

    def close_menu(self) -> None:
        if self.popup is not None:
            self.popup.cancel()
            self.popup.close()

    def scope_changed(self) -> None:
        self.snapshot = None
        self.close_menu()

    def refresh_theme(self) -> None:
        if self.popup is not None:
            for row in self.popup.rows:
                row.refresh_theme()
