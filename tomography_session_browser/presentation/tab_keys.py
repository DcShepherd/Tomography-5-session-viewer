"""The window's tabs, by identity.

A ``TabKey`` is its tab's label: ``TabKey.SEARCH_MAP == "Search map"``, so it
can replace a label string anywhere without changing a lookup, a saved
setting or a snapshot. Use it where a string names a tab (a lookup, a
destination, a comparison); text that merely reads like a tab's name, such as
a title or a header, stays a string.
"""
from __future__ import annotations

from enum import StrEnum
from collections.abc import Mapping


class TabKey(StrEnum):
    SESSION = "Session"
    ATLAS = "Atlas"
    OVERVIEW = "Overview"
    SEARCH_MAP = "Search map"
    SEARCH = "Search"
    BATCH_POSITION = "Batch position"
    TILT_SERIES = "Tilt series"


#: The image tabs, in hierarchy order: every tab but the Session dashboard.
VIEWER_TAB_KEYS: tuple[TabKey, ...] = tuple(key for key in TabKey if key is not TabKey.SESSION)


def available_tab_keys(counts: Mapping[str, int]) -> frozenset[TabKey]:
    """Atlas-only scopes offer Summary and Atlas. Incomplete collections keep recovery views."""
    acquisition_tabs = VIEWER_TAB_KEYS[1:]
    if counts.get(TabKey.ATLAS, 0) > 0 and all(counts.get(key, 0) == 0 for key in acquisition_tabs):
        return frozenset((TabKey.SESSION, TabKey.ATLAS))
    return frozenset(TabKey)
