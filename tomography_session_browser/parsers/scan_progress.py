"""Display-only progress shared by parsing and session assembly."""

from __future__ import annotations

import logging
from collections.abc import Iterator
from typing import Callable, TypeVar

LOGGER = logging.getLogger(__name__)
T = TypeVar("T")

#: ``(label, current, total)``: what is being read now, e.g.
#: ("Reading samples", 4, 13) or ("Reading tilt series", 5, 7).
ScanProgress = Callable[[str, int, int], None]
#: The parts of a collection sample, in the order they are read.
COLLECTION_SAMPLE_PARTS = (
    "atlas",
    "overviews",
    "search maps",
    "batch positions",
    "tilt series",
    "links",
    "search tiles",
)


class ScanProgressReporter:
    """Tells an optional progress callback what is being read.

    For display only: the callback's errors are logged and ignored, so a
    progress display can never change or stop what is parsed.
    """

    def __init__(self, callback: ScanProgress | None) -> None:
        self._callback = callback

    def _tell(self, label: str, current: int, total: int) -> None:
        if self._callback is None:
            return
        try:
            self._callback(label, current, total)
        except Exception:  # noqa: BLE001 - display only; never break a load
            LOGGER.debug("Load progress callback failed", exc_info=True)

    def each(self, label: str, items: list[T]) -> Iterator[T]:
        """Yield ``items``, telling the callback which one (1-based) is next."""

        for index, item in enumerate(items, 1):
            self._tell(label, index, len(items))
            yield item

    def part(self, name: str) -> None:
        index = COLLECTION_SAMPLE_PARTS.index(name) + 1
        self._tell(f"Reading {name}", index, len(COLLECTION_SAMPLE_PARTS))


