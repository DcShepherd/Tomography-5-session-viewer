"""Navigation in both directions across verified reconstruction/acquisition links."""
from __future__ import annotations
from typing import TYPE_CHECKING
from tomography_session_browser.domain.identity import entity_key_text
from tomography_session_browser.domain.models import TiltSeries
from tomography_session_browser.presentation.tab_keys import TabKey
from tomography_session_browser.presentation.workspace import EntityKind
from tomography_session_browser.ui.viewer.models import ViewerNavigationAction

if TYPE_CHECKING:
    from tomography_session_browser.ui.window_parts.contracts import WindowParts


class TomogramNavigation:
    def __init__(self, parts: WindowParts) -> None:
        self.parts = parts

    def parent(self, row_key: str) -> TiltSeries | None:
        workspace = self.parts.shell.state.review.workspace
        link = workspace.tomogram_links.get(row_key)
        if link is None or link.status != "resolved":
            return None
        matches = [value for value in workspace.entities(EntityKind.TILT_SERIES)
                   if entity_key_text(value) == link.target_key]
        return matches[0] if len(matches) == 1 else None

    def actions(self, row_key: str) -> list[ViewerNavigationAction]:
        parent = self.parent(row_key)
        if parent is None:
            return []
        return [*(action for action in self.parts.navigation.viewer_navigation_actions(parent) if action.key != "tomograms"),
                ViewerNavigationAction("tilt_series", "Tilt series", tooltip="Open the verified tilt series of this tomogram")]

    def acquisition_actions(self, value: object) -> list[ViewerNavigationAction]:
        if not isinstance(value, TiltSeries):
            return []
        workspace = self.parts.shell.state.review.workspace
        count = len(workspace.tomogram_placement.for_parents((value,)))
        return [ViewerNavigationAction("tomograms", "Tomograms", enabled=bool(count),
            tooltip=f"Open {count} verified tomograms for this tilt series" if count else "No verified tomograms for this tilt series")]

    def open_acquisition(self, value: object) -> None:
        if not isinstance(value, TiltSeries):
            return
        workspace = self.parts.shell.state.review.workspace
        keys = frozenset(row.id for row in workspace.tomogram_placement.for_parents((value,)))
        if not keys:
            self.parts.context.post_navigation_message("No verified tomograms for this tilt series. No change to the current view.")
            return
        self.parts.viewers.cancel_scope_preparation()
        departure = self.parts.history.begin_navigation()
        self.parts.navigation.enter_navigation_scope(value)
        ui = self.parts.viewers.ui
        self.parts.viewers.switch_to_group_tab(TabKey.TOMOGRAMS)
        ui.tomogram_tab.set_query("", row_keys=keys, scope_label=f"Tilt series {value.name}")
        self.parts.context.show_arrival_message(f"Showing {len(keys)} verified tomograms for {value.name}.")
        self.parts.history.record_history(departure)

    def open(self, row_key: str, key: str) -> None:
        if key == "tilt_series":
            self.parts.viewers.open_tomogram_parent(row_key)
        else:
            parent = self.parent(row_key)
            if parent is not None:
                self.parts.navigation.viewer_navigation_requested(parent, key)
            else:
                self.parts.viewers.open_tomogram_parent(row_key)  # Reports the stale/missing relationship.
