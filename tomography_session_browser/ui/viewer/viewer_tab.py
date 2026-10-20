"""Viewer tab for the image viewer."""
from __future__ import annotations
from collections.abc import Callable, Iterable, Sequence
from pathlib import Path
from typing import Any
from PySide6.QtCore import QEvent, QObject, QRect, Qt, QThreadPool, QTimer, Signal
from PySide6.QtGui import QImage, QKeyEvent
from PySide6.QtWidgets import QTreeWidgetItem, QVBoxLayout, QWidget
from tomography_session_browser.services.preview_sources import PreviewSources
from tomography_session_browser.domain.markers import ImageMarker
from tomography_session_browser.presentation.empty_states import EmptyState
from tomography_session_browser.presentation.viewer_entries import Entry
from tomography_session_browser.presentation.design_tokens import SPACE_S
from tomography_session_browser.ui.widgets.empty_state_panel import EmptyStatePanel
from tomography_session_browser.services.item_status import ItemListStatus
from tomography_session_browser.ui.viewer.constants import MAX_PREVIEW_DIMENSION, MRC_JPEG_FALLBACK_GRACE_MS
from tomography_session_browser.ui.viewer.models import AtlasCollectionOverlayOption, ViewerNavigationAction, ViewerViewState
from tomography_session_browser.ui.viewer.mrc_loading import MrcLoadSignals
from tomography_session_browser.ui.viewer.preview_view import ImagePreviewView
from tomography_session_browser.ui.viewer.canvas_controls import CanvasControls
from tomography_session_browser.ui.viewer.export import ViewerExport
from tomography_session_browser.ui.viewer.frame_scrubber import FrameScrubber
from tomography_session_browser.ui.viewer.list_pane import ViewerListPane
from tomography_session_browser.ui.viewer.mrc_loading import PreviewLoader
from tomography_session_browser.ui.viewer.overlay_panel import OverlayPanel
from tomography_session_browser.ui.viewer.tab_selection import TabSelection
from tomography_session_browser.ui.viewer.viewer_header import ViewerHeader
from tomography_session_browser.ui.viewer.parts import TabParts


class ViewerTab(QWidget):

    empty_state_action_requested = Signal(str)

    #: The list's filter changed: the whole query, status tag included.
    query_changed = Signal(str)

    header_title_changed = Signal(str)

    def __init__(
        self,
        empty_text: str,
        *,
        entity_icon: str | None = None,
        show_list: bool = True,
        show_tilt_controls: bool = False,
        on_item_selected: Callable[[Any], None] | None = None,
        on_frame_changed: Callable[[Any, int | None, int], None] | None = None,
        tilt_angle_for: Callable[[Any, int], str | None] | None = None,
        markers_for: Callable[[Any], list[ImageMarker]] | None = None,
        on_marker_selected: Callable[[ImageMarker], None] | None = None,
        on_marker_opened: Callable[[ImageMarker], None] | None = None,
        on_cluster_member_activated: Callable[[ImageMarker], None] | None = None,
        on_marker_selection_cleared: Callable[[], None] | None = None,
        show_marker_controls: bool = True,
        atlas_lod: bool = False,
        pan_through_marker_types: Iterable[str] = (),
        frames_share_zoom: bool = False,
        navigation_actions_for: Callable[[Any], list[ViewerNavigationAction]] | None = None,
        on_navigation_requested: Callable[[Any, str], None] | None = None,
        status_segments: tuple[str, ...] = ("failed", "complete"),
        show_exposure_chooser: bool = False,
    ) -> None:

        self._constructed = False
        super().__init__()

        self.parts = TabParts(
            canvas_controls=CanvasControls(self),
            export=ViewerExport(self),
            frame_scrubber=FrameScrubber(self),
            list_pane=ViewerListPane(self),
            mrc_loading=PreviewLoader(self),
            overlay_panel=OverlayPanel(self),
            tab_selection=TabSelection(self),
            viewer_header=ViewerHeader(self),
        )

        self.parts.tab_selection.items: list[Any] = []
        self.parts.canvas_controls.entity_icon = entity_icon
        self.parts.mrc_loading.sources_for: Callable[[Any], PreviewSources] = lambda _value: PreviewSources(None)
        self.parts.mrc_loading.prepared_sources: dict[str, PreviewSources] = {}
        self.parts.tab_selection.on_item_selected = on_item_selected
        self.parts.frame_scrubber.on_frame_changed = on_frame_changed
        self.parts.frame_scrubber.tilt_angle_for = tilt_angle_for or (lambda _value, _index: None)
        self.parts.tab_selection.markers_for = markers_for or (lambda _value: [])
        self.parts.tab_selection.on_marker_selected = on_marker_selected
        self.parts.tab_selection.on_marker_opened = on_marker_opened
        self.parts.tab_selection.on_marker_selection_cleared = on_marker_selection_cleared
        self.parts.overlay_panel.show_marker_controls = show_marker_controls
        self.parts.overlay_panel.atlas_lod = atlas_lod
        self.parts.viewer_header.navigation_actions_for = navigation_actions_for
        self.parts.viewer_header.show_exposure_chooser = show_exposure_chooser
        self.parts.viewer_header.on_navigation_requested = on_navigation_requested
        self.parts.list_pane.item_status_for: Callable[[Any], ItemListStatus] | None = None
        self.parts.tab_selection.selected_marker_id_override: str | None = None
        self.parts.tab_selection.marker_selection_explicitly_cleared = False
        # The theme changed while this tab was hidden: recolour on show.
        self.parts.canvas_controls.canvas_theme_stale = False
        self.parts.frame_scrubber.show_tilt_controls = show_tilt_controls
        self.parts.canvas_controls.scale_bar_enabled = True
        self.parts.tab_selection.current_value: Any | None = None
        self.parts.mrc_loading.current_path: Path | None = None
        self.parts.mrc_loading.current_fallback: Path | None = None
        self.parts.mrc_loading.displayed_path: Path | None = None
        self.parts.mrc_loading.displayed_slice_index: int | None = None
        # Set when a hidden tab is rebuilt: applied on first show so lazy tabs
        # stay lazy without losing the selection they should return to.
        self.parts.tab_selection.pending_view_state: ViewerViewState | None = None
        # Supplied by the main window so an empty list can name its scope and
        # tell "nothing loaded" apart from "this scope has none of these".
        self.parts.list_pane.has_sessions = False
        self.parts.list_pane.scope_name = ""
        self.parts.list_pane.entity_label = ""
        self.parts.list_pane.empty_state_override: EmptyState | None = None
        self.parts.mrc_loading.current_mrc_max_size = MAX_PREVIEW_DIMENSION
        self.parts.mrc_loading.request_id = 0
        self.parts.mrc_loading.pending_fallback_request: tuple[int, Path, int, Path, tuple[str, int, int | None]] | None = None
        self.parts.mrc_loading.mrc_signals = MrcLoadSignals()
        self.parts.mrc_loading.mrc_signals.loaded.connect(self._mrc_loaded)
        self.parts.mrc_loading.mrc_signals.failed.connect(self._mrc_failed)
        self.parts.mrc_loading.mrc_signals.raster_loaded.connect(self._raster_loaded)
        self.parts.frame_scrubber.slider_debounce = QTimer(self)
        self.parts.frame_scrubber.slider_debounce.setSingleShot(True)
        self.parts.frame_scrubber.slider_debounce.setInterval(75)
        self.parts.frame_scrubber.slider_debounce.timeout.connect(self._load_pending_slice)
        self.parts.mrc_loading.fallback_debounce = QTimer(self)
        self.parts.mrc_loading.fallback_debounce.setSingleShot(True)
        self.parts.mrc_loading.fallback_debounce.setInterval(MRC_JPEG_FALLBACK_GRACE_MS)
        self.parts.mrc_loading.fallback_debounce.timeout.connect(self._show_delayed_mrc_fallback)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(SPACE_S)
        self.viewer = ImagePreviewView(self)
        self.viewer.set_atlas_lod_enabled(atlas_lod)
        self.viewer.set_pan_through_marker_types(pan_through_marker_types)
        self.parts.mrc_loading.frames_share_zoom = frames_share_zoom
        if frames_share_zoom:
            # One crop per item, whichever frame is shown: see
            # ``_logical_image_key`` for the key's shape.
            self.viewer.share_view_within_groups(lambda key: key[2] if isinstance(key, tuple) else key)
        self.viewer.set_zoom_changed_callback(self._zoom_changed)
        self.viewer.set_marker_selected_callback(self._marker_selected)
        self.viewer.set_marker_opened_callback(self._marker_opened)
        self.viewer.set_cluster_member_activated_callback(
            on_cluster_member_activated
        )
        self.viewer.set_selection_cleared_callback(self._marker_selection_cleared)

        header = self.parts.viewer_header.create_title()
        controls, zoom_label_width = self.parts.canvas_controls.create_controls(empty_text, atlas_lod)
        self.parts.frame_scrubber.create_widgets(show_tilt_controls)
        self.parts.overlay_panel.create_widgets(show_marker_controls, atlas_lod)
        self.parts.viewer_header.create_linked_strip(header)
        self.parts.canvas_controls.connect_controls()
        self.parts.list_pane.create_widgets(show_list, status_segments)
        status_policy = self.status.sizePolicy()
        status_policy.setRetainSizeWhenHidden(show_tilt_controls)
        self.status.setSizePolicy(status_policy)
        viewer_panel = self.parts.canvas_controls.create_canvas(zoom_label_width, atlas_lod, show_tilt_controls)
        splitter = self.parts.list_pane.create_splitter(viewer_panel, show_list)
        header.addLayout(controls)
        layout.addLayout(header)
        layout.addWidget(self.parts.viewer_header.linked_strip)
        layout.addWidget(self.parts.viewer_header.exposure_chooser)
        layout.addWidget(self.parts.viewer_header.linked_reason)
        layout.addWidget(self.parts.list_pane.compact_row)
        layout.addWidget(splitter, stretch=1)
        layout.addWidget(self.status)
        focus_order = [
            self.list, *self.parts.viewer_header.navigation_buttons.values(),
            self.parts.viewer_header.navigation_menu_button, self.viewer,
            self.overlay_button, self.export_image_button, self.zoom_in_button,
            self.zoom_out_button, self.fit_button,
        ]
        for before, after in zip(focus_order, focus_order[1:]):
            QWidget.setTabOrder(before, after)
        self._constructed = True

    def showEvent(self, event) -> None:  # noqa: N802 - Qt signature
        if not self._constructed:
            return super().showEvent(event)
        super().showEvent(event)
        self.parts.canvas_controls.export_shortcut.setEnabled(True)
        self._fit_header()
        if self.parts.canvas_controls.canvas_theme_stale:
            self._recolour_canvas()  # the theme changed while this tab was hidden

    def hideEvent(self, event) -> None:  # noqa: N802 - Qt signature
        if not self._constructed:
            return super().hideEvent(event)
        self.parts.canvas_controls.export_shortcut.setEnabled(False)
        super().hideEvent(event)

    def set_atlas_collection_overlay_provider(
        self,
        provider: Callable[
            [Any],
            list[AtlasCollectionOverlayOption],
        ]
        | None,
        on_visibility_changed: Callable[[str, bool], None] | None,
    ) -> None:
        return self.parts.overlay_panel.set_atlas_collection_overlay_provider(provider, on_visibility_changed)

    def _atlas_collection_section_toggled(self, expanded: bool) -> None:
        return self.parts.overlay_panel.atlas_collection_section_toggled(expanded)

    def _section_motion_allowed(self, section: QWidget) -> bool:
        return self.parts.overlay_panel.section_motion_allowed(section)

    def _rect_in_shell(self, widget: QWidget) -> QRect:
        return self.parts.overlay_panel.rect_in_shell(widget)

    def _set_atlas_legend_expanded(self, expanded: bool) -> None:
        return self.parts.overlay_panel.set_atlas_legend_expanded(expanded)

    def _reveal_overlay_panel(self, change: Callable[[], None], *, kind: str, opening: bool) -> None:
        return self.parts.overlay_panel.reveal_overlay_panel(change, kind=kind, opening=opening)

    def _update_atlas_collection_overlay_controls(
        self,
        value: Any,
    ) -> None:
        return self.parts.overlay_panel.update_atlas_collection_overlay_controls(value)

    def _atlas_collection_toggled(self, key: str) -> None:
        return self.parts.overlay_panel.atlas_collection_toggled(key)

    def _set_header_chips(self, values: list[str]) -> None:
        return self.parts.viewer_header.set_header_chips(values)

    def _header_available_width(self) -> int | None:
        return self.parts.viewer_header.header_available_width()

    def _fit_header(self) -> None:
        return self.parts.viewer_header.fit_header()

    def _populate_navigation_menu(self) -> None:
        return self.parts.viewer_header.populate_navigation_menu()

    def _set_canvas_control_icons(self) -> None:
        return self.parts.canvas_controls.set_canvas_control_icons()

    def refresh_theme(self) -> None:
        return self.parts.canvas_controls.refresh_theme()

    def _recolour_canvas(self) -> None:
        return self.parts.canvas_controls.recolour_canvas()

    def _set_header_title(self, text: str) -> None:
        return self.parts.viewer_header.set_header_title(text)

    def _update_header(self, value: Any) -> None:
        return self.parts.viewer_header.update_header(value)

    def _header_title_for(self, value: Any) -> str:
        return self.parts.viewer_header.header_title_for(value)

    def _update_navigation_actions(self, value: Any | None) -> None:
        return self.parts.viewer_header.update_navigation_actions(value)

    def _arrange_linked_strip(self, value: Any | None) -> None:
        return self.parts.viewer_header.arrange_linked_strip(value)

    def _sync_linked_separators(self, collapsed: bool) -> None:
        return self.parts.viewer_header.sync_linked_separators(collapsed)

    def _navigation_button_clicked(self, key: str) -> None:
        return self.parts.viewer_header.navigation_button_clicked(key)

    def selected_marker_id(self) -> str | None:
        return self.parts.tab_selection.selected_marker_id()

    def held_markers(self, value: Any) -> list[ImageMarker] | None:
        return self.parts.tab_selection.held_markers(value)

    def _filter_list(self, text: str | None = None) -> None:
        return self.parts.list_pane.filter_list(text)

    def query(self) -> str:
        """The list's filter, whole (``is:failed alpha``)."""

        return self.parts.list_pane.query()

    def set_query(self, text: str, *, notify: bool = True,
                  row_keys: frozenset[str] | None = None, scope_label: str = "") -> None:
        """Make ``text`` the list's filter; ``set_query("")`` clears all of it."""

        self.parts.list_pane.set_query(text, notify=notify, row_keys=row_keys, scope_label=scope_label)

    def filter_description(self) -> str:
        """The visible status/text query and any summary scope restriction."""
        return self.parts.list_pane.filter_bar.description()

    def _install_frame_shortcuts(self) -> None:
        return self.parts.frame_scrubber.install_frame_shortcuts()

    def _step_frame(self, step: int) -> None:
        return self.parts.frame_scrubber.step_frame(step)

    def item_status_for_value(self, value: Any) -> ItemListStatus | None:
        return self.parts.list_pane.item_status_for_value(value)

    def has_same_items(self, items: list[Any]) -> bool:
        return self.parts.list_pane.has_same_items(items)

    def _decorate_row(self, tree_item: QTreeWidgetItem, value: Any, label: str) -> None:
        return self.parts.list_pane.decorate_row(tree_item, value, label)

    def _refresh_row_decorations(self) -> None:
        return self.parts.list_pane.refresh_row_decorations()

    def refresh_providers(
        self,
        sources_for: Callable[[Any], PreviewSources],
        *,
        markers_for: Callable[[Any], list[ImageMarker]] | None = None,
        item_status_for: Callable[[Any], ItemListStatus] | None = None,
        prepared_sources: dict[str, PreviewSources] | None = None,
    ) -> None:
        return self.parts.list_pane.refresh_providers(sources_for, markers_for=markers_for, item_status_for=item_status_for, prepared_sources=prepared_sources)

    def set_scope_description(
        self,
        *,
        has_sessions: bool,
        scope_name: str,
        entity_label: str = "",
    ) -> None:
        return self.parts.list_pane.set_scope_description(has_sessions=has_sessions, scope_name=scope_name, entity_label=entity_label)

    def _current_entity_label(self) -> str:
        return self.parts.list_pane.current_entity_label()

    def empty_state(self) -> EmptyState:
        return self.parts.list_pane.empty_state()

    def show_empty_state(self, state: EmptyState) -> None:
        return self.parts.list_pane.show_empty_state(state)

    def clear_empty_state_override(self) -> None:
        return self.parts.list_pane.clear_empty_state_override()

    def _refresh_empty_state_panel(self) -> None:
        return self.parts.list_pane.refresh_empty_state_panel()

    def _set_empty_state_panel_visible(self, panel: EmptyStatePanel, visible: bool) -> None:
        return self.parts.list_pane.set_empty_state_panel_visible(panel, visible)

    def capture_view_state(self) -> ViewerViewState:
        return self.parts.tab_selection.capture_view_state()

    def _apply_restored_state(
        self,
        state: ViewerViewState,
        *,
        auto_load_preview: bool,
    ) -> bool:
        return self.parts.tab_selection.apply_restored_state(state, auto_load_preview=auto_load_preview)

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
        entries: Sequence[Entry] | None = None,
    ) -> None:
        return self.parts.tab_selection.set_items(items, sources_for, label_for, markers_for=markers_for, item_status_for=item_status_for, prepared_sources=prepared_sources, auto_load_preview=auto_load_preview, restore_state=restore_state, entries=entries)

    def _veil_refilled_list(self) -> None:
        return self.parts.tab_selection.veil_refilled_list()

    def ensure_initial_preview_loaded(self, *, notify_selection: bool = True) -> None:
        return self.parts.tab_selection.ensure_initial_preview_loaded(notify_selection=notify_selection)

    def _load_initial_item(self, *, notify_selection: bool = True) -> None:
        return self.parts.tab_selection.load_initial_item(notify_selection=notify_selection)

    def select_object(self, value: Any) -> bool:
        return self.parts.tab_selection.select_object(value)

    def discard_zoom(self) -> None:
        return self.parts.tab_selection.discard_zoom()

    def select_frame(self, frame_index: int) -> None:
        return self.parts.frame_scrubber.select_frame(frame_index)

    def _item_changed(self, current: QTreeWidgetItem | None, previous: QTreeWidgetItem | None) -> None:
        return self.parts.tab_selection.item_changed(current, previous)

    def _slider_changed(self, value: int) -> None:
        return self.parts.frame_scrubber.slider_changed(value)

    def _load_pending_slice(self) -> None:
        return self.parts.frame_scrubber.load_pending_slice()

    def _load_value(self, value: Any, slice_index: int, *, restore_frame: bool = False) -> None:
        return self.parts.mrc_loading.load_value(value, slice_index, restore_frame=restore_frame)

    def _source_for_value(self, value: Any) -> PreviewSources:
        return self.parts.mrc_loading.source_for_value(value)

    def _load_path(self, path: Path, fallback: Path | None, slice_index: int) -> None:
        return self.parts.mrc_loading.load_path(path, fallback, slice_index)

    def _load_mrc_path(self, path: Path, fallback: Path | None, slice_index: int) -> None:
        return self.parts.mrc_loading.load_mrc_path(path, fallback, slice_index)

    def _show_delayed_mrc_fallback(self) -> None:
        return self.parts.mrc_loading.show_delayed_mrc_fallback()

    def _known_frame_count_for_path(self, path: Path) -> int:
        return self.parts.mrc_loading.known_frame_count_for_path(path)

    def _initial_mrc_preview_size(self, path: Path | None) -> int:
        return self.parts.mrc_loading.initial_mrc_preview_size(path)

    def _set_slice_state(self, slice_count: int, slice_index: int, *, valid_stack: bool) -> None:
        return self.parts.frame_scrubber.set_slice_state(slice_count, slice_index, valid_stack=valid_stack)

    def _set_status(self, path: Path, slice_count: int, warnings: list[str], source: str) -> None:
        return self.parts.list_pane.set_status(path, slice_count, warnings, source)

    def _set_status_text(self, text: str, *, tooltip: str | None = None, tone: str = "") -> None:
        return self.parts.list_pane.set_status_text(text, tooltip=tooltip, tone=tone)

    def _compact_path_status(
        self,
        source: str,
        path: Path,
        *,
        slice_count: int = 1,
        warnings: list[str] | None = None,
    ) -> str:
        # The frame is in the readout beside the scrubber; the tooltip keeps it.
        return self.parts.list_pane.compact_path_status(source, path, slice_count=slice_count, warnings=warnings)

    def _path_status_tooltip(
        self,
        source: str,
        path: Path,
        *,
        slice_count: int = 1,
        warnings: list[str] | None = None,
    ) -> str:
        return self.parts.list_pane.path_status_tooltip(source, path, slice_count=slice_count, warnings=warnings)

    def _frame_text(self, slice_count: int) -> str:
        return self.parts.frame_scrubber.frame_text(slice_count)

    def _update_frame_label(self, slice_count: int) -> None:
        return self.parts.frame_scrubber.update_frame_label(slice_count)

    def _raster_loaded(self, request, preview) -> None:
        self.parts.mrc_loading.raster_loaded(request, preview)

    def _mrc_loaded(
        self,
        request_id: int,
        path: Path,
        slice_index: int,
        image: QImage,
        slice_count: int,
        warnings: list[str],
        estimated_bytes: int,
        source_max: int = 0,
    ) -> None:
        return self.parts.mrc_loading.mrc_loaded(request_id, path, slice_index, image, slice_count, warnings, estimated_bytes, source_max)

    def _mrc_failed(self, request_id: int, path: Path, slice_index: int, slice_count: int, warnings: list[str]) -> None:
        return self.parts.mrc_loading.mrc_failed(request_id, path, slice_index, slice_count, warnings)

    def _logical_image_key(self, path: Path, slice_index: int) -> tuple[str, int, int | None]:
        return self.parts.mrc_loading.logical_image_key(path, slice_index)

    def _active_selected_marker_id(self, value: Any, markers: list[ImageMarker] | None = None) -> str | None:
        return self.parts.tab_selection.active_selected_marker_id(value, markers)

    def _refresh_marker_selection(self) -> None:
        return self.parts.tab_selection.refresh_marker_selection()

    def select_marker(self, marker_id: str | None, *, arrive: bool = False, frame: bool = False) -> None:
        return self.parts.tab_selection.select_marker(marker_id, arrive=arrive, frame=frame)

    def _current_value_image_shown(self) -> bool:
        return self.parts.tab_selection.current_value_image_shown()

    def _marker_selected(self, marker: ImageMarker) -> None:
        return self.parts.tab_selection.marker_selected(marker)

    def _marker_selection_cleared(self) -> None:
        return self.parts.tab_selection.marker_selection_cleared()

    def _marker_opened(self, marker: ImageMarker) -> None:
        return self.parts.tab_selection.marker_opened(marker)

    def _remove_all_overlays(self) -> None:
        return self.parts.overlay_panel.remove_all_overlays()

    def _marker_legend_toggled(self) -> None:
        return self.parts.overlay_panel.marker_legend_toggled()

    def _update_marker_type_availability(self, markers: list[ImageMarker]) -> None:
        return self.parts.overlay_panel.update_marker_type_availability(markers)

    def _set_atlas_collection_list_visible(self, visible: bool) -> None:
        return self.parts.overlay_panel.set_atlas_collection_list_visible(visible)

    def _constrain_overlay_panel(self) -> None:
        return self.parts.overlay_panel.constrain_overlay_panel()

    def _open_panel_rect(self) -> QRect | None:
        return self.parts.overlay_panel.open_panel_rect()

    def close_overlay_panel(self) -> bool:
        return self.parts.overlay_panel.close_overlay_panel()

    def _overlay_panel_toggled(self, checked: bool) -> None:
        return self.parts.overlay_panel.overlay_panel_toggled(checked)

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt signature
        if not self._constructed:
            return super().resizeEvent(event)
        super().resizeEvent(event)
        self._fit_list_minimum()
        self._constrain_overlay_panel()
        self._fit_header()

    def _fit_list_minimum(self) -> None:
        return self.parts.list_pane.fit_list_minimum()

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802 - Qt signature
        # Escape closes the overlay panel before anything else acts on it, and
        # puts focus back on the button that opened it so a keyboard user is
        # not stranded.
        if not self._constructed:
            return super().keyPressEvent(event)
        if event.key() == Qt.Key.Key_Escape and self.close_overlay_panel():
            event.accept()
            return
        super().keyPressEvent(event)

    def _scale_bar_toggled(self) -> None:
        return self.parts.canvas_controls.scale_bar_toggled()

    def _floating_control_insets(self) -> tuple[int, int, int]:
        return self.parts.canvas_controls.floating_control_insets()

    def _reposition_floating_controls(self) -> None:
        return self.parts.canvas_controls.reposition_floating_controls()

    def _place_bottom_anchors(self) -> None:
        return self.parts.canvas_controls.place_bottom_anchors()

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802 - Qt API
        # The canvas resized, or an anchor's content did (the Atlas legend
        # expanding): the bottom anchors are placed by hand, so re-place them.
        if not self._constructed:
            return super().eventFilter(watched, event)
        if event.type() == QEvent.Type.Resize and watched in (
            getattr(self, "viewer_shell", None),
            getattr(self.parts.canvas_controls, "zoom_anchor", None),
            getattr(self.parts.canvas_controls, "scale_bar_anchor", None),
        ):
            self._place_bottom_anchors()
        return super().eventFilter(watched, event)

    def _zoom_changed(self, zoom: float) -> None:
        # Zoom is measured relative to fit-to-view, so a fitted image used to
        # read "100%", which looks like 1:1 pixels. Say "Fit" instead.
        return self.parts.canvas_controls.zoom_changed(zoom)

    def _known_mrc_source_max_for_path(self, path: Path) -> int:
        return self.parts.canvas_controls.known_mrc_source_max_for_path(path)

    def _update_image_badge(self) -> None:
        return self.parts.canvas_controls.update_image_badge()

    def _set_image_badge_wanted(self, wanted: bool) -> None:
        return self.parts.canvas_controls.set_image_badge_wanted(wanted)

    def _fit_top_floating_controls(self) -> None:
        return self.parts.canvas_controls.fit_top_floating_controls()

    def _set_overlay_button_compact(self, compact: bool) -> None:
        return self.parts.canvas_controls.set_overlay_button_compact(compact)

    def _export_current_view(self) -> None:
        return self.parts.export.export_current_view()

    def _default_export_path(self) -> Path:
        return self.parts.export.default_export_path()

    def _composite_export_overlays(
        self,
        image: QImage,
        render_scale: float,
    ) -> None:
        return self.parts.export.composite_export_overlays(image, render_scale)

    @staticmethod
    def _render_widget_for_export(
        widget: QWidget,
        render_scale: float,
    ) -> QImage:
        return ViewerExport.render_widget_for_export(widget, render_scale)

    def _update_scale_bar(self) -> None:
        return self.parts.canvas_controls.update_scale_bar()

    def _default_frame_index(self, value: Any, path: Path, requested_index: int) -> int:
        return self.parts.frame_scrubber.default_frame_index(value, path, requested_index)

    def _reliable_stack_order_angles_for(self, value: Any, frame_count: int) -> list[float] | None:
        return self.parts.frame_scrubber.reliable_stack_order_angles_for(value, frame_count)

    def _notify_frame_changed(self, slice_count: int) -> None:
        return self.parts.frame_scrubber.notify_frame_changed(slice_count)

    def _prefetch_neighbors(self, path: Path, slice_index: int, slice_count: int) -> None:
        return self.parts.mrc_loading.prefetch_neighbors(path, slice_index, slice_count)

    def _prefetch_current_high_detail(self, path: Path, slice_index: int) -> None:
        return self.parts.mrc_loading.prefetch_current_high_detail(path, slice_index)

    def _next_mrc_detail_level(self, path: Path, current_size: int) -> int | None:
        return self.parts.mrc_loading.next_mrc_detail_level(path, current_size)

    def _prefetch_mrc_frame(self, path: Path, slice_index: int, max_size: int = MAX_PREVIEW_DIMENSION) -> None:
        return self.parts.mrc_loading.prefetch_mrc_frame(path, slice_index, max_size)

