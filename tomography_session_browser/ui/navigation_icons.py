"""Central icon mappings for the app's entity-navigation surfaces.

These names resolve through :func:`tomography_session_browser.ui.icons.themed_icon`.
Keeping the mappings together prevents the tab bar, project tree, viewer lists,
and report-scope dialog from drifting onto different visual nouns.
"""

from __future__ import annotations

from typing import Final

from tomography_session_browser.presentation.project_tree import ProjectGroupKind
from tomography_session_browser.presentation.tab_keys import TabKey


TAB_ICONS: Final[dict[str, str]] = {
    TabKey.SESSION: "session-dashboard",
    TabKey.ATLAS: "atlas",
    TabKey.OVERVIEW: "overview",
    TabKey.SEARCH_MAP: "search-map",
    TabKey.SEARCH: "search-tile",
    TabKey.BATCH_POSITION: "batch-position",
    TabKey.TILT_SERIES: "tilt-series",
}

PROJECT_GROUP_ICONS: Final[dict[ProjectGroupKind, str]] = {
    "linked": "layers",
    "atlas": "atlas",
    "collection": "folder-open",
    "unresolved": "folder-open",
}

TREE_ENTITY_GROUP_ICONS: Final[dict[str, str]] = {
    "Overviews": TAB_ICONS[TabKey.OVERVIEW],
    "Search maps": TAB_ICONS[TabKey.SEARCH_MAP],
    "Search tiles": TAB_ICONS[TabKey.SEARCH],
    "Batch positions": TAB_ICONS[TabKey.BATCH_POSITION],
    "Tilt series": TAB_ICONS[TabKey.TILT_SERIES],
}
