"""Navigation in both directions across verified reconstruction/acquisition links."""
from __future__ import annotations
from typing import TYPE_CHECKING
from tomography_session_browser.domain.identity import entity_key_text
from tomography_session_browser.domain.models import TiltSeries
from tomography_session_browser.presentation.messages import ItemAccepted, ItemChoice, MessageContent
from tomography_session_browser.presentation.navigation_plan import ReconstructionJump
from tomography_session_browser.ui.widgets.message_sheet import choose_item
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

    def open_reconstruction(self, value: object, jump: ReconstructionJump) -> None:
        workspace = self.parts.shell.state.review.workspace
        keys = jump.candidates
        if not keys:
            self.parts.context.post_navigation_message("No verified tomograms for this tilt series.")
            return
        if len(keys) == 1:
            key = keys[0]
        else:
            rows = {row.id: row for row in workspace.tomogram_placement.rows}
            sources = {source.id: source for source in workspace.processing_sources}
            choices = tuple(
                ItemChoice(row.id, f"{row.name} · {sources[row.source_id].path} · "
                           + (", ".join(dict.fromkeys(volume.version for volume in row.volumes)) or "No full reconstruction"))
                for key in keys if (row := rows.get(key)) is not None
            )
            if len(choices) != len(keys):
                self.parts.context.post_navigation_message("The verified tomogram choices have changed.")
                return
            choice = choose_item(self.parts.viewers.window, MessageContent(
                "Open linked tomogram", "Choose a verified reconstruction to open.",
            ), choices, label="Reconstruction", accept_label="Open")
            if not isinstance(choice, ItemAccepted):
                return
            key = choice.item_id
        current = self.parts.navigation.navigation_plan_for(value).get("tomograms")
        if (self.parts.shell.state.review.workspace is not workspace or current is None
                or current.reconstruction is None or current.reconstruction.parent_key != jump.parent_key
                or key not in current.reconstruction.candidates):
            self.parts.context.post_navigation_message("The selected tomogram link has changed.")
            return
        self.parts.viewers.open_tomogram_arrival(key, jump.parent_key)

    def open(self, row_key: str, key: str) -> None:
        if key == "tilt_series":
            self.parts.viewers.open_tomogram_parent(row_key)
        else:
            parent = self.parent(row_key)
            if parent is not None:
                self.parts.navigation.viewer_navigation_requested(parent, key)
            else:
                self.parts.viewers.open_tomogram_parent(row_key)  # Reports the stale/missing relationship.
