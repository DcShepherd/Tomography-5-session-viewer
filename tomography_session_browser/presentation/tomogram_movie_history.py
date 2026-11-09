"""Saved movie exports, newest per tomogram, for the list's film mark (B5, owner 9 Nov 2026).

A record says that an export finished: MP4, PNG frames or both. It lives in the
app-owned review repository, never beside the movie. The app never checks that
the saved file is still there, so the mark means "exported", not "the file
exists". A cancelled or failed export writes no record.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from tomography_session_browser.services.volume.review_repository import ReviewRecord, ReviewRepository

MOVIE_KIND = "movie"


@dataclass(frozen=True, slots=True)
class MovieMark:
    created_ns: int
    name: str


def movie_mark(record: ReviewRecord, identities: frozenset[tuple[str, str]]) -> tuple[str, MovieMark] | None:
    """The row and mark a record gives, or None for a record outside the scope or not usable."""
    state, output = record.data.get("state"), record.data.get("output")
    if not isinstance(state, dict) or not isinstance(output, str) or not Path(output).is_absolute():
        return None
    row, volume = state.get("row_key"), state.get("volume_id")
    if not isinstance(row, str) or not isinstance(volume, str) or (row, volume) not in identities:
        return None
    return row, MovieMark(record.order[0], Path(output).name)


def movie_marks(repository: ReviewRepository, identities: frozenset[tuple[str, str]], *,
                cancelled: Callable[[], bool] = lambda: False) -> tuple[tuple[str, MovieMark], ...]:
    """The newest saved export of each row in the scope."""
    newest: dict[str, MovieMark] = {}
    for record in repository.iter_records(MOVIE_KIND, cancelled=cancelled):
        found = movie_mark(record, identities)
        if found is not None and (found[0] not in newest or found[1].created_ns > newest[found[0]].created_ns):
            newest[found[0]] = found[1]
    return tuple(sorted(newest.items()))


def newest_marks(*sources: Mapping[str, MovieMark]) -> dict[str, MovieMark]:
    """One mark per row: the newest of the stored and the window's own exports."""
    marks: dict[str, MovieMark] = {}
    for source in sources:
        for row, mark in source.items():
            if row not in marks or mark.created_ns > marks[row].created_ns:
                marks[row] = mark
    return marks


def movie_tooltip(mark: MovieMark) -> str:
    when = datetime.fromtimestamp(mark.created_ns / 1e9)
    return (f"Movie saved on {when.day} {when:%b %Y} at {when:%H:%M}: {mark.name}. "
            "The app does not check that the file is still there.")
