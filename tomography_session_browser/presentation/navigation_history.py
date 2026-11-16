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

from tomography_session_browser.presentation.context_stack import ENTITY_DISPLAY_LABELS
from tomography_session_browser.presentation.tomogram_state import TomogramViewState
from tomography_session_browser.presentation.tomogram_selection import TomogramLocation

from tomography_session_browser.presentation.tomogram_tag_filters import TagFilter

DEFAULT_HISTORY_LIMIT = 50

#: The Back and Forward tooltips when there is nowhere to go.
IDLE_TEXT = {
    "Back": "Return to the previous navigation step.",
    "Forward": "Move forward again after going back.",
}


@dataclass(frozen=True, slots=True)
class HistoryLocation:
    """One place the reviewer explicitly navigated to.

    ``viewport`` is deliberately absent: S2 already remembers the crop per
    logical image, so restoring ``object_key`` restores the zoom and pan with
    it. Storing a second copy here would give two sources of truth for the
    same thing and let them disagree.

    ``object_key`` is ``domain.identity.entity_key_text`` of the object, so two
    objects sharing an ``id`` stay two places. The list anchor (the object row
    at the top of the list and its offset) puts the list back where it was; it
    is not part of what makes a place the same place.

    ``label`` names the object and ``scope_label`` the scope it was in, for
    the Back and Forward tooltips; neither is part of the place.
    """

    scope_key: str
    tab: str
    object_type: str = ""
    object_key: str = ""
    marker_id: str = ""
    frame_index: int | None = None
    filter_text: str = ""
    filter_keys: frozenset[str] | None = None
    filter_scope_label: str = ""
    label: str = ""
    scope_label: str = ""
    list_anchor_key: str = ""
    list_anchor_offset: int = 0
    tomogram_state: TomogramViewState | None = None
    tomogram_selection: TomogramLocation | None = None
    tag_filter: TagFilter = TagFilter()
    list_membership: frozenset[str] | None = None
    list_population: frozenset[str] = frozenset()
    list_population_scope: str = ""

    def same_place_as(self, other: "HistoryLocation | None") -> bool:
        """Whether two entries denote the same destination.

        Filter text, frame and list anchor are excluded: arriving at the same
        row with a different filter or scroll is not a new place to go back to.
        """

        if other is None:
            return False
        return (
            self.scope_key == other.scope_key
            and self.tab == other.tab
            and self.object_key == other.object_key
            and self.marker_id == other.marker_id
        )


@dataclass(slots=True)
class NavigationHistory:
    """A bounded back/forward stack over :class:`HistoryLocation`."""

    limit: int = DEFAULT_HISTORY_LIMIT
    _entries: list[HistoryLocation] = field(default_factory=list)
    _index: int = -1
    revision: int = field(default=0, init=False)

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

    def peek_back(self) -> HistoryLocation | None:
        """Where Back goes, without going there."""

        return self._entries[self._index - 1] if self.can_go_back else None

    def peek_forward(self) -> HistoryLocation | None:
        """Where Forward goes, without going there."""

        return self._entries[self._index + 1] if self.can_go_forward else None

    # -- mutation -----------------------------------------------------------

    def record(self, location: HistoryLocation) -> bool:
        """Append an explicit jump, discarding any forward branch.

        Returns False when the location was not recorded because it repeats
        where we already are — re-selecting the current row is not a new
        history step.
        """

        self.revision += 1
        if location.same_place_as(self.current()):
            # Still refresh the stored entry so frame/filter stay accurate.
            self._entries[self._index] = location
            return False

        del self._entries[self._index + 1 :]
        self._entries.append(location)
        self._index = len(self._entries) - 1
        self._enforce_limit()
        return True

    def commit_cursor(self, index: int, revision: int) -> bool:
        if revision != self.revision or not 0 <= index < len(self._entries):
            return False
        self._index = index
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
        self.revision += 1
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
        if removed:
            self.revision += 1
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


def destination_text(direction: str, location: HistoryLocation) -> str:
    """The Back or Forward tooltip: where the step goes.

    "Back to Position_2 in Grid_A · Tilt series". The scope is left out when
    it is the object itself; a place with no object (the Summary tab) is
    named by its scope. The shortcut hint is the caller's.
    """

    item, scope = location.label, location.scope_label
    if item:
        text = f"{direction} to {item}" + (f" in {scope}" if scope and scope != item else "")
    elif scope:
        text = f"{direction} to {scope}"
    else:
        text = f"{direction} to the {'previous' if direction == 'Back' else 'next'} step"
    tab = ENTITY_DISPLAY_LABELS.get(location.tab, location.tab)
    return f"{text} · {tab}" if tab else text
