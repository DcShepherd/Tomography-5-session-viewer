"""When the app read each source folder: app state, never written to the folder.

Processing folders and acquisition sessions (D16) share one record and one
text. An entry belongs to the object that was read, so a folder read again
(a new object) gets a new time, and a session kept after a failed refresh
keeps the time of the data it still shows.
"""
from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime

from tomography_session_browser.presentation.formatting import format_timestamp

READ_INTO_APP = "Read into app"


@dataclass(frozen=True, slots=True)
class SourceReadTimes:
    entries: tuple[tuple[object, datetime], ...] = ()

    def admitted(self, sources: Iterable[object], now: datetime) -> SourceReadTimes:
        """The read times of ``sources``: each keeps its time, a new one gets ``now``."""

        return SourceReadTimes(tuple((source, next((at for previous, at in self.entries if previous is source), now))
                                     for source in sources))

    def read_at(self, source: object) -> datetime | None:
        return next((at for previous, at in self.entries if previous is source), None)


def read_time_text(value: datetime | None) -> str:
    """The app's display timestamp ("2026-11-12 22:17:58"), as acquisition times read."""

    return format_timestamp(value) if value is not None else "Not recorded"
