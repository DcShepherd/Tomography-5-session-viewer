"""Saved movie exports: counts per row for the film slot and pages for the Movies menu.

A record says that an export finished: an MP4, PNG frames or both. It lives in
the app-owned review repository, never beside the movie. Its identity is the
output path, so an export that overwrites a file replaces that file's record. The
records and counts do not check that a file is still there; the Movies menu
checks each row it shows. A cancelled or failed export writes no record.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path

from tomography_session_browser.domain.display_names import count_phrase
from tomography_session_browser.presentation.tomogram_state import TomogramViewState
from tomography_session_browser.services.volume.review_repository import ReviewRecord, ReviewRepository, record_id

MOVIE_KIND = "movie"
#: Rows on one page of the Movies menu.
PAGE_SIZE = 12


@dataclass(frozen=True, slots=True)
class MovieWrite:
    """What a movie's history write reports back: the record and this window's export token.

    The token never goes into the record on disk.
    """
    identity: str
    token: int


@dataclass(frozen=True, slots=True)
class MovieEntry:
    identity: str
    created_ns: int
    row_key: str
    volume_id: str
    #: The movie file, or the frames folder of a frames-only export.
    output: Path


@dataclass(frozen=True, slots=True)
class MovieCounts:
    total: int
    rows: tuple[tuple[str, int], ...] = ()


@dataclass(frozen=True, slots=True)
class MoviePage:
    entries: tuple[MovieEntry, ...]
    next_cursor: tuple[int, str] | None
    total: int


def decode_movie(record: ReviewRecord) -> MovieEntry | None:
    """The entry a record gives, or None for a record that cannot be used."""
    state, output = record.data.get("state"), record.data.get("output")
    if not isinstance(state, dict) or not isinstance(output, str) or not Path(output).is_absolute():
        return None
    row, volume = state.get("row_key"), state.get("volume_id")
    if not isinstance(row, str) or not isinstance(volume, str):
        return None
    return MovieEntry(record.identity, record.order[0], row, volume, Path(output))


def movie_record(state: TomogramViewState, output: Path, created_ns: int) -> ReviewRecord:
    """The record that the history write of this export saves, for use before it is on disk."""
    identity = record_id(str(output))
    return ReviewRecord(identity, (created_ns, identity), {
        "schema": 1, "kind": MOVIE_KIND, "state": asdict(state), "output": str(output), "created_ns": created_ns})


def usable(record: ReviewRecord) -> bool:
    return decode_movie(record) is not None


def movie_counts(repository: ReviewRepository, identities: frozenset[tuple[str, str]], *,
                 extra: tuple[ReviewRecord, ...] = (), cancelled: Callable[[], bool] = lambda: False) -> MovieCounts:
    """The movies of each row in the scope; an unrecorded export and its durable record count once."""
    page = repository.page(MOVIE_KIND, identities, limit=1, accept=usable, extra=extra, cancelled=cancelled)
    return MovieCounts(page.total, page.counts)


def movie_page(repository: ReviewRepository, identities: frozenset[tuple[str, str]], *,
               before: tuple[int, str] | None = None, limit: int = PAGE_SIZE,
               extra: tuple[ReviewRecord, ...] = (), cancelled: Callable[[], bool] = lambda: False) -> MoviePage:
    """One page of movies, newest first."""
    page = repository.page(MOVIE_KIND, identities, before=before, limit=limit, accept=usable, extra=extra,
                           cancelled=cancelled)
    entries = tuple(entry for entry in map(decode_movie, page.records) if entry is not None)
    return MoviePage(entries, page.next_cursor, page.total)


def movie_count_tooltip(count: int, *, available: bool, refreshing: bool, error: str) -> str:
    if not available:
        return "Movie count unavailable." + (" Loading movie history." if refreshing else "")
    text = f"Show the {count_phrase(count, 'movie')} of this tomogram."
    if refreshing:
        text += " Refreshing; this is the last known count."
    if error:
        text += " Refresh unavailable."
    return text


#: Why a Movies… command is off for a row with no saved movie (D8).
NO_MOVIES = "Not available: no saved movies. Press Ctrl+E to save one."
