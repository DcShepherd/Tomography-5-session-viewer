"""Bounded Back/Forward history for explicit navigation (plan item H1).

The workspace had no history at all before this module. The design constraint
that shapes everything here: a history entry stores **identifiers and scalars
only, never entity references**. Holding a ``Session`` or ``TiltSeries`` in the
stack would keep a removed session alive and quietly defeat the rule that
viewer state dies with the session that owned it.

What counts as history is deliberately narrow. Only *explicit* jumps are
recorded — a navigation button, a marker activation, a dashboard drill-down, a
tree activation. Passive selection, preview loading and ordinary tab switching
are not navigation events, and recording them would make Back useless by
filling it with steps the reviewer never chose to take.

Pure and Qt-free so the stack semantics can be tested without a window.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

DEFAULT_HISTORY_LIMIT = 50


@dataclass(frozen=True, slots=True)
class HistoryLocation:
    """One place the reviewer explicitly navigated to.

    ``viewport`` is deliberately absent: S2 already remembers the crop per
    logical image, so restoring ``object_id`` restores the zoom and pan with
    it. Storing a second copy here would give two sources of truth for the
    same thing and let them disagree.
    """

    scope_key: str
    tab: str
    object_type: str = ""
    object_id: str = ""
    marker_id: str = ""
    frame_index: int | None = None
    filter_text: str = ""
    label: str = ""

    def same_place_as(self, other: "HistoryLocation | None") -> bool:
        """Whether two entries denote the same destination.

        Filter text and frame are excluded: arriving at the same row with a
        different filter is not a new place to go back to.
        """

        if other is None:
            return False
        return (
            self.scope_key == other.scope_key
            and self.tab == other.tab
            and self.object_id == other.object_id
            and self.marker_id == other.marker_id
        )


@dataclass(slots=True)
class NavigationHistory:
    """A bounded back/forward stack over :class:`HistoryLocation`."""

    limit: int = DEFAULT_HISTORY_LIMIT
    _entries: list[HistoryLocation] = field(default_factory=list)
    _index: int = -1

    # -- state --------------------------------------------------------------

    @property
    def entries(self) -> tuple[HistoryLocation, ...]:
        return tuple(self._entries)

    @property
    def index(self) -> int:
        return self._index

    def current(self) -> HistoryLocation | None:
        if 0 <= self._index < len(self._entries):
            return self._entries[self._index]
        return None

    @property
    def can_go_back(self) -> bool:
        return self._index > 0

    @property
    def can_go_forward(self) -> bool:
        return -1 < self._index < len(self._entries) - 1

    # -- mutation -----------------------------------------------------------

    def record(self, location: HistoryLocation) -> bool:
        """Append an explicit jump, discarding any forward branch.

        Returns False when the location was not recorded because it repeats
        where we already are — re-selecting the current row is not a new
        history step.
        """

        if location.same_place_as(self.current()):
            # Still refresh the stored entry so frame/filter stay accurate.
            self._entries[self._index] = location
            return False

        del self._entries[self._index + 1 :]
        self._entries.append(location)
        self._index = len(self._entries) - 1
        self._enforce_limit()
        return True

    def back(self) -> HistoryLocation | None:
        if not self.can_go_back:
            return None
        self._index -= 1
        return self._entries[self._index]

    def forward(self) -> HistoryLocation | None:
        if not self.can_go_forward:
            return None
        self._index += 1
        return self._entries[self._index]

    def clear(self) -> None:
        self._entries.clear()
        self._index = -1

    def prune(self, is_valid: Callable[[HistoryLocation], bool]) -> int:
        """Drop entries that no longer resolve, keeping the cursor sensible.

        Called after a session is removed or replaced. The cursor moves to the
        nearest surviving entry at or before where it was, so Back does not
        silently jump somewhere unrelated.
        """

        if not self._entries:
            return 0

        surviving: list[HistoryLocation] = []
        new_index = -1
        for position, entry in enumerate(self._entries):
            if not is_valid(entry):
                continue
            surviving.append(entry)
            if position <= self._index:
                new_index = len(surviving) - 1

        removed = len(self._entries) - len(surviving)
        self._entries = surviving
        if not surviving:
            self._index = -1
        elif new_index < 0:
            # Everything at or before the cursor went away.
            self._index = 0
        else:
            self._index = new_index
        return removed

    def _enforce_limit(self) -> None:
        overflow = len(self._entries) - max(self.limit, 1)
        if overflow > 0:
            del self._entries[:overflow]
            self._index = max(self._index - overflow, 0)
