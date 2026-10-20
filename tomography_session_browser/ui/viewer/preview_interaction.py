"""Preview interaction for the image viewer."""
from __future__ import annotations
from collections.abc import Callable, Iterable
from PySide6.QtCore import QPoint, QPointF, Qt
from PySide6.QtWidgets import QFrame
from tomography_session_browser.domain.markers import ImageMarker, MarkerType
from tomography_session_browser.services.marker_clustering import MAX_ATLAS_ZOOM
from tomography_session_browser.presentation.design_tokens import CANVAS_CHROME
import logging
LOGGER = logging.getLogger("tomography_session_browser.ui.image_viewer")
from tomography_session_browser.ui.viewer.atlas_lod_items import AtlasClusterPopup
from tomography_session_browser.ui.viewer.marker_paint import marker_scene_geometry
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from tomography_session_browser.ui.viewer.preview_view import ImagePreviewView


class PreviewInteraction:
    """Preview interaction operations for one ImagePreviewView."""

    def __init__(self, view: ImagePreviewView) -> None:
        self.view = view

    def set_zoom_changed_callback(self, callback: Callable[[float], None]) -> None:
        self.view._zoom_changed = callback

    def set_marker_selected_callback(self, callback: Callable[[ImageMarker], None]) -> None:
        self.view._marker_selected = callback

    def set_marker_opened_callback(self, callback: Callable[[ImageMarker], None]) -> None:
        self.view._marker_opened = callback

    def set_cluster_member_activated_callback(
        self,
        callback: Callable[[ImageMarker], None] | None,
    ) -> None:
        self.cluster_member_activated = callback

    def set_selection_cleared_callback(self, callback: Callable[[], None]) -> None:
        self.selection_cleared = callback

    def set_pan_through_marker_types(self, marker_types: Iterable[str]) -> None:
        """Let a single press on these markers pan instead of selecting.

        For large area overlays, such as the search-map tile grid on an
        Overview, where selecting on press would leave most of the image
        impossible to drag. Double-click still opens the marker.
        """

        self.pan_through_marker_types = frozenset(marker_types)

    def interactive_markers(self) -> list[ImageMarker]:
        """Markers the keyboard can reach, on Atlas *and* every other tab.

        Arrow-key marker traversal used to exist only when Atlas LOD was on, so
        Overview, Search map, Search and Batch position overlays were reachable
        by pointer alone.
        """

        if self.view.parts.marker_layer.atlas_lod_enabled:
            return sorted(
                [
                    marker
                    for marker in self.view.parts.marker_layer.last_display_markers
                    if marker.marker_type == MarkerType.BATCH_CLUSTER
                    or marker.metadata.get("atlas_lod_role")
                    in {"batch_position", "unattributed"}
                ],
                key=lambda marker: marker.id,
            )
        return sorted(
            [
                marker
                for marker in self.view.parts.marker_layer.last_display_markers
                if marker.linked_object_id
                and marker.marker_type
                not in {
                    MarkerType.CAMERA_FOV,
                    MarkerType.BATCH_LABEL,
                    MarkerType.LINK_LINE,
                    MarkerType.STAGE_CROSSHAIR,
                }
            ],
            key=lambda marker: marker.id,
        )

    def marker_in_direction(
        self,
        markers: list[ImageMarker],
        key: Qt.Key,
    ) -> ImageMarker | None:
        """Nearest marker in the pressed direction, geometrically.

        Traversal used to step through markers sorted by ID, which bears no
        relation to where they are on the image — pressing Right could jump
        across the micrograph. Movement now follows the overlay's geometry, so
        the keyboard order matches what the reviewer sees.

        Area overlays (a search-map tile on an Overview, a template area) are
        a ``bbox`` or ``polygon`` with no ``x``/``y``; they are placed by their
        centre. Reading ``x``/``y`` directly raised a TypeError on the first
        arrow press over such a marker.
        """

        positioned: list[tuple[ImageMarker, float, float]] = []
        for marker in markers:
            found = marker_scene_geometry(marker)
            if found is not None:
                positioned.append((marker, found[0].x(), found[0].y()))
        if not positioned:
            return None

        current = next(
            (entry for entry in positioned if entry[0].id == self.keyboard_marker_id),
            None,
        )
        if current is None:
            # No cursor yet: enter from the edge the key implies.
            reverse = key in {Qt.Key.Key_Left, Qt.Key.Key_Up}
            vertical = key in {Qt.Key.Key_Up, Qt.Key.Key_Down}
            return sorted(
                positioned,
                key=lambda entry: (entry[2], entry[1]) if vertical else (entry[1], entry[2]),
                reverse=reverse,
            )[0][0]

        current_marker, current_x, current_y = current
        dx_sign = {Qt.Key.Key_Left: -1, Qt.Key.Key_Right: 1}.get(key, 0)
        dy_sign = {Qt.Key.Key_Up: -1, Qt.Key.Key_Down: 1}.get(key, 0)

        candidates: list[tuple[float, ImageMarker]] = []
        for marker, x, y in positioned:
            if marker.id == current_marker.id:
                continue
            dx = x - current_x
            dy = y - current_y
            # Must lie predominantly in the direction of travel.
            along = dx * dx_sign + dy * dy_sign
            across = abs(dy if dx_sign else dx)
            if along <= 0:
                continue
            # Prefer close and well-aligned over merely close.
            candidates.append((along + across * 2.0, marker))
        if candidates:
            return min(candidates, key=lambda item: item[0])[1]
        # Nothing that way — stay put rather than wrapping to the far side.
        return current_marker

    def activate_cluster(self, marker: ImageMarker) -> None:
        bounds = marker.metadata.get("member_bounds_scene")
        if not (
            isinstance(bounds, tuple | list)
            and len(bounds) == 4
            and all(isinstance(value, int | float) for value in bounds)
        ):
            return
        x, y, width, height = (float(value) for value in bounds)
        current_scale = self.view._zoom_scale()
        spread_now = max(width, height) * current_scale
        if self.view.parts.preview_camera.zoom >= MAX_ATLAS_ZOOM - 1e-6 or spread_now <= 3.0:
            self.view._show_cluster_member_popup(marker)
            return

        center = QPointF(x + width / 2, y + height / 2)
        target_zoom = min(
            MAX_ATLAS_ZOOM,
            max(2.2, self.view.parts.preview_camera.zoom * 2.2),
        )
        factor = target_zoom / max(self.view.parts.preview_camera.zoom, 0.01)
        self.keyboard_marker_id = marker.id
        # Flies in rather than cutting (plan F5.7), and lands where the cut
        # did: this scale, this centre, this zoom.
        self.view.fly_to(center, self.view._zoom_scale() * factor, zoom=target_zoom)

    def show_cluster_member_popup(self, marker: ImageMarker) -> None:
        self.view._close_cluster_popup()
        member_ids = tuple(marker.metadata.get("member_ids", ()))
        by_id = {value.id: value for value in self.view.parts.marker_layer.source_markers}
        members = [by_id[member_id] for member_id in member_ids if member_id in by_id]
        if not members:
            return
        self.keyboard_marker_id = marker.id
        marker.selected = True
        self.view._redraw_markers()
        popup = AtlasClusterPopup(
            members,
            on_member_selected=self.view._select_popup_member,
            parent=self.view.viewport(),
        )
        popup.adjustSize()
        center = self.view.mapFromScene(QPointF(float(marker.x or 0.0), float(marker.y or 0.0)))
        diameter = float(marker.metadata.get("diameter_px") or 22.0)
        right_space = self.view.viewport().width() - center.x()
        left_space = center.x()
        right_x = center.x() + diameter / 2.0 + 16.0
        left_x = center.x() - diameter / 2.0 - 16.0 - popup.width()
        right_fits = right_x + popup.width() <= self.view.viewport().width() - 4
        left_fits = left_x >= 4
        use_right = (
            (right_space >= left_space and right_fits)
            or (not left_fits and right_fits)
            or (not left_fits and not right_fits and right_space >= left_space)
        )
        x_pos = right_x if use_right else left_x
        x_pos = max(4, min(round(x_pos), self.view.viewport().width() - popup.width() - 4))
        if use_right:
            connector_left = center.x() + diameter / 2.0
            connector_right = x_pos
        else:
            connector_left = x_pos + popup.width()
            connector_right = center.x() - diameter / 2.0
        y_pos = max(
            4,
            min(
                round(center.y() - 13.0),
                self.view.viewport().height() - popup.height() - 4,
            ),
        )
        popup.move(x_pos, y_pos)
        popup.show()
        popup.raise_()

        connector = QFrame(self.view.viewport())
        connector.setStyleSheet(
            f"background: {CANVAS_CHROME.border_strong}; border: none;"
        )
        connector_x = round(min(connector_left, connector_right))
        connector_width = max(1, round(abs(connector_right - connector_left)))
        connector.setGeometry(connector_x, round(center.y()), connector_width, 1)
        connector.show()
        connector.raise_()
        popup.raise_()
        self.cluster_member_popup = popup
        self.cluster_popup_connector = connector

    def select_popup_member(self, marker: ImageMarker) -> None:
        self.view._close_cluster_popup()
        self.keyboard_marker_id = marker.id
        if self.cluster_member_activated is not None:
            self.cluster_member_activated(marker)
        elif self.view._marker_selected is not None:
            self.view._marker_selected(marker)

    def highlighted_exposure_marker_ids(self) -> frozenset[str]:
        return frozenset(self.exposure_highlights)

    def clear_exposure_highlights(self, *, redraw: bool = True) -> None:
        if not self.exposure_highlights:
            return
        self.exposure_highlights.clear()
        if redraw:
            self.view._redraw_markers()

    def highlight_linked_exposures(self, marker: ImageMarker) -> None:
        batch_id = str(
            marker.metadata.get("batch_id")
            or marker.linked_object_id
            or ""
        )
        if not batch_id:
            self.exposure_highlights = {marker.id}
        else:
            self.exposure_highlights = {
                candidate.id
                for candidate in self.view.parts.marker_layer.source_markers
                if candidate.marker_type
                in {MarkerType.EXPOSURE_AREA, MarkerType.CAMERA_FOV}
                and str(
                    candidate.metadata.get("batch_id")
                    or candidate.linked_object_id
                    or ""
                )
                == batch_id
                and candidate.source_object_id == marker.source_object_id
            }
        self.view._redraw_markers()

    def image_contains_view_position(self, position: QPoint) -> bool:
        if self.view.parts.preview_image.pixmap_item is None:
            return False
        return self.view.parts.preview_image.pixmap_item.boundingRect().contains(self.view.mapToScene(position))

    def clear_marker_interaction(self) -> None:
        self.view._close_cluster_popup()
        self.keyboard_marker_id = None
        self.exposure_highlights.clear()
        for marker in self.view.parts.marker_layer.source_markers:
            marker.selected = False
        if self.selection_cleared is not None:
            self.selection_cleared()
        # Always a full redraw: the tab's path redraws only markers whose
        # selection it changes, and they were all unselected above, so it would
        # leave the old selection and the highlight rings on screen.
        self.view._redraw_markers()

    def close_cluster_popup(self) -> None:
        if self.cluster_member_popup is not None:
            self.cluster_member_popup.deleteLater()
            self.cluster_member_popup = None
        if self.cluster_popup_connector is not None:
            self.cluster_popup_connector.deleteLater()
            self.cluster_popup_connector = None

    def mark_user_interacted(self, reason: str) -> None:
        if not self.view.parts.preview_camera.has_user_interacted or self.view.parts.preview_camera.pending_initial_fit_flag:
            LOGGER.debug(
                "viewer user interaction logical_key=%s reason=%s pending_initial_fit=%s",
                self.view.parts.preview_image.current_image_key,
                reason,
                self.view.parts.preview_camera.pending_initial_fit_flag,
            )
        self.view.parts.preview_camera.has_user_interacted = True
        self.view.parts.preview_camera.pending_initial_fit_flag = False
        if reason in {"wheel_zoom", "button_zoom"}:
            self.view.parts.preview_camera.fit_mode = False

    def scroll_position(self) -> tuple[int, int]:
        return (self.view.horizontalScrollBar().value(), self.view.verticalScrollBar().value())

