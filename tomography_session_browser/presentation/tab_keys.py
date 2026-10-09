"""The window's tabs, by identity.

A ``TabKey`` is its tab's label: ``TabKey.SEARCH_MAP == "Search map"``, so it
can replace a label string anywhere without changing a lookup, a saved
setting or a snapshot. Use it where a string names a tab (a lookup, a
destination, a comparison); text that merely reads like a tab's name, such as
a title or a header, stays a string.
"""
from __future__ import annotations

from enum import StrEnum


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
