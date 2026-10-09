"""Tab selection for the image viewer."""
from __future__ import annotations
from collections.abc import Callable
from typing import Any
from PySide6.QtCore import QSize, QTimer
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QAbstractItemView, QTreeWidgetItem
from tomography_session_browser.services.preview_sources import PreviewSources
from tomography_session_browser.domain.markers import ImageMarker, MarkerType
from tomography_session_browser.domain.models import SearchTile
from tomography_session_browser.presentation.viewer_items import panel_title_for_items as _panel_title_for_items
from tomography_session_browser.presentation.empty_states import viewer_empty_state
from tomography_session_browser.ui.motion import veil
from tomography_session_browser.services.item_status import ItemListStatus
from tomography_session_browser.ui.icons import themed_icon
from tomography_session_browser.ui.theme import current_palette
from tomography_session_browser.ui.viewer.constants import LIST_PRIMARY_ROLE, LIST_REFILL_VEIL_MS, LIST_REFILL_VEIL_OPACITY, LIST_ROW_HEIGHT_PX, MAX_PREVIEW_DIMENSION, SHARED_NAME_PREFIX_PROPERTY, VIEWER_OBJECT_ROLE
from tomography_session_browser.ui.viewer.list_rows import shared_name_prefix
from tomography_session_browser.ui.viewer.models import ViewerViewState, viewer_object_id
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from tomography_session_browser.ui.viewer.viewer_tab import ViewerTab


class TabSelection:
    """Tab selection operations for one ViewerTab."""

    def __init__(self, view: ViewerTab) -> None:
        self.view = view
        # (provider, item, its markers): see ``markers_of``.
        self.marker_memo: tuple[Callable[[Any], list[ImageMarker]], Any, list[ImageMarker]] | None = None

    def markers_of(self, value: Any) -> list[ImageMarker]:
        """``markers_for(value)``, kept for the item last asked about.

        An item's markers are asked for when it is selected, again when its
        image arrives and at every marker selection; an Atlas takes tens of
        milliseconds to build. Another provider (the tabs were re-rendered)
        or ``forget_markers()`` (an Atlas collection overlay toggled) builds
        them again.
        """

        memo = self.marker_memo
        if memo is not None and memo[0] is self.markers_for and memo[1] is value:
            return memo[2]
        markers = self.markers_for(value)
        self.marker_memo = (self.markers_for, value, markers)
        return markers

    def held_markers(self, value: Any) -> list[ImageMarker] | None:
        """The markers ``markers_of`` already built for ``value``, or ``None``.

        Builds nothing and keeps the memo, so a caller outside the tab (the jump
        planner finding the selected marker) can reuse them without evicting
        the item shown.
        """

        memo = self.marker_memo
        return memo[2] if memo is not None and memo[0] is self.markers_for and memo[1] is value else None

    def forget_markers(self) -> None:
        self.marker_memo = None

    def capture_view_state(self) -> ViewerViewState:
        """Snapshot what this tab should get back after a rebuild."""

        return ViewerViewState(
            object_id=viewer_object_id(self.current_value),
            marker_id=self.selected_marker_id_override,
            frame_index=self.view.parts.mrc_loading.displayed_slice_index,
            filter_text=self.view.list_filter.text(),
        )

    def index_of_object_id(self, object_id: str | None) -> int | None:
        if not object_id:
            return None
        for index, item in enumerate(self.items):
            if viewer_object_id(item) == object_id:
                return index
        return None

    def apply_restored_state(
        self,
        state: ViewerViewState,
        *,
        auto_load_preview: bool,
    ) -> bool:
        """Re-select a remembered object, if it is still in this scope.

        Returns False when the object has gone, so the caller falls back to
        the ordinary initial-preview path rather than showing nothing.
        """

        index = self.view._index_of_object_id(state.object_id)
        if index is None:
            return False

        if self.view.list.isVisible() or self.view.list.topLevelItemCount():
            previous = self.view.list.blockSignals(True)
            try:
                self.view.list.setCurrentItem(self.view.list.topLevelItem(index))
            finally:
                self.view.list.blockSignals(previous)

        if not auto_load_preview:
            # Hidden tabs stay lazy: remember the intent and apply it when the
            # tab is actually shown.
            self.pending_view_state = state
            return True

        self.view._load_value(
            self.items[index], max(state.frame_index or 0, 0),
            restore_frame=state.frame_index is not None,
        )
        if state.marker_id:
            self.view.select_marker(state.marker_id)
        return True

    def set_items(
        self,
        items: list[Any],
        sources_for: Callable[[Any], PreviewSources],
        label_for: Callable[[Any], str],
        *,
        markers_for: Callable[[Any], list[ImageMarker]] | None = None,
        item_status_for: Callable[[Any], ItemListStatus] | None = None,
        prepared_sources: dict[str, PreviewSources] | None = None,
        auto_load_preview: bool = True,
        restore_state: ViewerViewState | None = None,
    ) -> None:
        self.items = items
        self.view.parts.mrc_loading.sources_for = sources_for
        self.view.parts.mrc_loading.prepared_sources = prepared_sources or {}
        self.view.parts.list_pane.item_status_for = item_status_for
        if markers_for is not None:
            self.markers_for = markers_for
        self.forget_markers()
        self.view.parts.mrc_loading.current_path = None
        self.view.parts.mrc_loading.current_fallback = None
        self.view.parts.mrc_loading.displayed_path = None
        self.view.parts.mrc_loading.displayed_slice_index = None
        self.current_value = None
        self.selected_marker_id_override = None
        self.marker_selection_explicitly_cleared = False
        self.pending_view_state = None
        self.view.parts.list_pane.empty_state_override = None
        self.view._update_atlas_collection_overlay_controls(None)
        self.view.parts.mrc_loading.current_mrc_max_size = MAX_PREVIEW_DIMENSION
        self.view.parts.mrc_loading.request_id += 1
        self.view.parts.frame_scrubber.slider_debounce.stop()
        self.view.list.blockSignals(True)
        self.view.list.setUpdatesEnabled(False)
        try:
            self.view.list.clear()
            # One generic message used to cover six different situations. The
            # variant is chosen from what is actually true here; the caller
            # supplies the scope so the message can name it.
            if items:
                empty_text = "Select an item"
            else:
                empty_text = viewer_empty_state(
                    has_sessions=self.view.parts.list_pane.has_sessions,
                    total_items=0,
                    filter_text=self.view.list_filter.text().strip(),
                    scope_name=self.view.parts.list_pane.scope_name,
                    entity_label=self.view._current_entity_label(),
                ).title
            self.view.viewer.clear(empty_text)
            self.view.export_image_button.setEnabled(False)
            self.view._set_image_badge_wanted(False)
            self.view.scale_bar.setVisible(False)
            self.view._set_status_text(empty_text)
            self.view._set_header_title(empty_text)
            self.view._set_header_chips([])
            self.view._update_navigation_actions(None)
            self.view.list_count.setText(str(len(items)))
            panel_title = _panel_title_for_items(items)
            self.view.list_header.setText(panel_title)
            self.view.list_filter.setPlaceholderText(f"Filter {panel_title.lower()}...")
            self.view.list_filter.setAccessibleName(f"Filter {panel_title.lower()}")
            self.view._set_slice_state(1, 0, valid_stack=False)
            tree_items: list[QTreeWidgetItem] = []
            for item in items:
                label = label_for(item)
                tree_item = QTreeWidgetItem([label])
                if self.view.parts.canvas_controls.entity_icon is not None:
                    tree_item.setIcon(
                        0,
                        themed_icon(self.view.parts.canvas_controls.entity_icon, size=16),
                    )
                tree_item.setData(0, VIEWER_OBJECT_ROLE, item)
                self.view._decorate_row(tree_item, item, label)
                tree_item.setSizeHint(0, QSize(0, LIST_ROW_HEIGHT_PX))
                tree_items.append(tree_item)
            self.view.list.addTopLevelItems(tree_items)
            self.view.list.setProperty(
                SHARED_NAME_PREFIX_PROPERTY,
                shared_name_prefix(
                    [str(row.data(0, LIST_PRIMARY_ROLE) or row.text(0)) for row in tree_items]
                ),
            )
            # The filter must be applied before an initial preview is chosen,
            # otherwise the tab opens on a row the user has filtered out.
            #
            # Signals are blocked around ``setText`` so the single explicit
            # pass below is the only one: the edit signal would otherwise run
            # ``_filter_list`` over every row a second time, and drive a
            # context-header refresh, in the middle of a rebuild.
            if restore_state is not None:
                blocked = self.view.list_filter.blockSignals(True)
                try:
                    self.view.list_filter.setText(restore_state.filter_text)
                finally:
                    self.view.list_filter.blockSignals(blocked)
            self.view._filter_list(self.view.list_filter.text())
            restored = (
                restore_state is not None
                and items
                and self.view._apply_restored_state(
                    restore_state,
                    auto_load_preview=auto_load_preview,
                )
            )
            if items and auto_load_preview and not restored:
                self.view._load_initial_item()
        finally:
            self.view.list.setUpdatesEnabled(True)
            self.view.list.blockSignals(False)
        self.view._refresh_empty_state_panel()
        if items and self.view.list.isVisible():
            # The list was refilled, and says so. The image needs nothing: it
            # dissolves into the new one (F5.4). Both used to fade through an
            # opacity effect over the whole viewer, image view included.
            QTimer.singleShot(0, self.view._veil_refilled_list)

    def veil_refilled_list(self) -> None:
        veil(
            self.view.list.viewport(),
            QColor(current_palette().panel),
            start_opacity=LIST_REFILL_VEIL_OPACITY,
            duration_ms=LIST_REFILL_VEIL_MS,
        )

    def ensure_initial_preview_loaded(self, *, notify_selection: bool = True) -> None:
        pending = self.pending_view_state
        if pending is not None and self.current_value is None and self.items:
            self.pending_view_state = None
            if self.view._apply_restored_state(pending, auto_load_preview=True):
                if notify_selection and self.on_item_selected is not None:
                    self.on_item_selected(self.current_value)
                return
        if self.current_value is None and self.items:
            self.view._load_initial_item(notify_selection=notify_selection)
        elif notify_selection and self.current_value is not None and self.on_item_selected is not None:
            self.on_item_selected(self.current_value)

    def load_initial_item(self, *, notify_selection: bool = True) -> None:
        if not self.items:
            return
        # Honour the active filter: opening on a row the user filtered out
        # contradicts the visible count and hides the item they are reviewing.
        visible_indexes = [
            index
            for index in range(min(self.view.list.topLevelItemCount(), len(self.items)))
            if not self.view.list.topLevelItem(index).isHidden()
        ]
        if self.view.list_filter.text().strip() and not visible_indexes:
            self.view._refresh_empty_state_panel()
            return
        candidate_indexes = visible_indexes or list(range(len(self.items)))
        initial_index = candidate_indexes[0]
        for index in candidate_indexes:
            source = self.view._source_for_value(self.items[index]).primary
            if source is not None:
                initial_index = index
                break
        if self.view.list.isVisible():
            previous = self.view.list.blockSignals(True)
            try:
                self.view.list.setCurrentItem(self.view.list.topLevelItem(initial_index))
            finally:
                self.view.list.blockSignals(previous)
            self.view._load_value(self.items[initial_index], 0)
        else:
            self.view._load_value(self.items[initial_index], 0)
        if notify_selection and self.on_item_selected is not None:
            self.on_item_selected(self.items[initial_index])

    def select_object(self, value: Any) -> bool:
        """Select ``value`` in the list.

        Returns True when an incompatible filter had to be cleared to reveal
        it. Unhiding the single row instead would leave the row visible while
        the count still read 0, so the whole filter is dropped and the caller
        announces why.
        """

        filter_cleared = False
        for index in range(self.view.list.topLevelItemCount()):
            item = self.view.list.topLevelItem(index)
            if item.data(0, VIEWER_OBJECT_ROLE) is value:
                if item.isHidden():
                    self.view.list_filter.clear()
                    self.view._filter_list("")
                    filter_cleared = True
                self.view.list.setCurrentItem(item)
                self.view.list.scrollToItem(item, QAbstractItemView.ScrollHint.PositionAtCenter)
                return filter_cleared
        self.view._load_value(value, 0)
        return filter_cleared

    def discard_zoom(self) -> None:
        """Show the current image fitted and forget any zoom (the tab was left)."""

        self.view.viewer.discard_user_view()

    def item_changed(self, current: QTreeWidgetItem | None, previous: QTreeWidgetItem | None) -> None:
        if current is None:
            return
        value = current.data(0, VIEWER_OBJECT_ROLE)
        self.view._load_value(value, 0)
        if self.on_item_selected is not None:
            self.on_item_selected(value)

    def active_selected_marker_id(self, value: Any, markers: list[ImageMarker] | None = None) -> str | None:
        markers = markers if markers is not None else self.markers_of(value)
        if self.selected_marker_id_override is not None:
            if MarkerType.EXPOSURE_AREA in self.view.parts.overlay_panel.unavailable_marker_types:
                selected = next((marker for marker in markers if marker.id == self.selected_marker_id_override), None)
                if selected is not None and selected.marker_type == MarkerType.EXPOSURE_AREA:
                    area_name = str(selected.metadata.get("area_name") or "").strip().casefold()
                    camera = next(
                        (
                            marker
                            for marker in markers
                            if marker.marker_type == MarkerType.CAMERA_FOV
                            and str(marker.metadata.get("area_name") or "").strip().casefold() == area_name
                        ),
                        None,
                    )
                    if camera is not None:
                        return camera.id
            return self.selected_marker_id_override
        if self.marker_selection_explicitly_cleared:
            return None
        if isinstance(value, SearchTile):
            area_name = str(value.metadata.get("ExposureAreaName") or "").strip().casefold()
            if area_name:
                if MarkerType.EXPOSURE_AREA not in self.view.parts.overlay_panel.unavailable_marker_types:
                    for marker in markers:
                        marker_area = str(marker.metadata.get("area_name") or "").strip().casefold()
                        if marker.marker_type == MarkerType.EXPOSURE_AREA and marker_area == area_name:
                            return marker.id
                for marker in markers:
                    marker_area = str(marker.metadata.get("area_name") or "").strip().casefold()
                    if marker.marker_type == MarkerType.CAMERA_FOV and marker_area == area_name:
                        return marker.id
        object_id = getattr(value, "id", None)
        if object_id is None:
            return None
        for marker in markers:
            if marker.linked_object_id == object_id:
                return marker.id
        return None

    def refresh_marker_selection(self) -> None:
        if self.current_value is None:
            return
        markers = self.markers_of(self.current_value)
        self.view._update_marker_type_availability(markers)
        layer = self.view.viewer.parts.marker_layer
        selected = self.view._active_selected_marker_id(self.current_value, markers)
        if markers is layer.input_markers:
            layer.select_marker(selected)
        else:
            self.view.viewer.set_markers(markers, selected_marker_id=selected)
        if self.view.parts.overlay_panel.atlas_lod:
            self.view.atlas_marker_legend.set_markers(markers)

    def select_marker(self, marker_id: str | None, *, arrive: bool = False, frame: bool = False) -> None:
        """Select a marker; ``arrive`` marks it as where a jump landed.

        An arrival also brings the marker into view and pulses it once (plan
        F5.7), and ``frame`` zooms in on it. Which marker is selected is the
        same either way.
        """

        self.selected_marker_id_override = marker_id
        self.marker_selection_explicitly_cleared = marker_id is None
        self.view._refresh_marker_selection()
        self.view._update_navigation_actions(self.current_value)
        if arrive and marker_id:
            self.view.viewer.arrive_at_marker(marker_id, ready=self.view._current_value_image_shown, frame=frame)

    def current_value_image_shown(self) -> bool:
        """Whether the viewer shows the current item's image yet, rather than
        the previous item's while the new one decodes."""

        key = self.view.viewer.current_logical_image_key
        return (
            self.current_value is not None
            and isinstance(key, tuple)
            and len(key) == 3
            and key[2] == id(self.current_value)
        )

    def marker_selected(self, marker: ImageMarker) -> None:
        self.view.select_marker(marker.id)
        if self.on_marker_selected is not None:
            self.on_marker_selected(marker)

    def marker_selection_cleared(self) -> None:
        self.view.select_marker(None)
        if self.on_marker_selection_cleared is not None:
            self.on_marker_selection_cleared()

    def marker_opened(self, marker: ImageMarker) -> None:
        self.view.select_marker(marker.id)
        if self.on_marker_opened is not None:
            self.on_marker_opened(marker)

    def selected_marker_id(self) -> str | None:
        return self.selected_marker_id_override

