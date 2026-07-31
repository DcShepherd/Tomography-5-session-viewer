"""Central icon mappings for the app's entity-navigation surfaces.

These names resolve through :func:`tomography_session_browser.ui.icons.themed_icon`.
Keeping the mappings together prevents the tab bar, project tree, viewer lists,
and report-scope dialog from drifting onto different visual nouns.
"""

from __future__ import annotations

from typing import Final

from tomography_session_browser.ui.project_model import ProjectGroupKind


TAB_ICONS: Final[dict[str, str]] = {
    "Session": "session-dashboard",
    "Atlas": "atlas",
    "Overview": "overview",
    "Search map": "search-map",
    "Search": "search-tile",
    "Batch position": "batch-position",
    "Tilt series": "tilt-series",
}

PROJECT_GROUP_ICONS: Final[dict[ProjectGroupKind, str]] = {
    "linked": "layers",
    "atlas": "atlas",
    "collection": "folder-open",
    "unresolved": "folder-open",
}

TREE_ENTITY_GROUP_ICONS: Final[dict[str, str]] = {
    "Overviews": TAB_ICONS["Overview"],
    "Search maps": TAB_ICONS["Search map"],
    "Search": TAB_ICONS["Search"],
    "Batch positions": TAB_ICONS["Batch position"],
    "Tilt series": TAB_ICONS["Tilt series"],
}
