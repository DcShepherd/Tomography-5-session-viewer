"""Viewer tab for the image viewer."""
from __future__ import annotations
from tomography_session_browser.presentation.help_content import LocalBinding, InteractionKind
from collections.abc import Callable, Iterable, Sequence
from pathlib import Path
from typing import Any
from PySide6.QtCore import QEvent, QObject, QRect, Qt, QThreadPool, Signal
from PySide6.QtGui import QImage, QKeyEvent
from PySide6.QtWidgets import QTreeWidgetItem, QVBoxLayout, QWidget
from tomography_session_browser.services.preview_sources import PreviewSources
from tomography_session_browser.domain.markers import ImageMarker
from tomography_session_browser.presentation.empty_states import EmptyState
from tomography_session_browser.presentation.viewer_entries import Entry
from tomography_session_browser.presentation.design_tokens import SPACE_S
from tomography_session_browser.ui.widgets.empty_state_panel import EmptyStatePanel
from tomography_session_browser.services.item_status import ItemListStatus
from tomography_session_browser.ui.viewer.models import AtlasCollectionOverlayOption, ViewerNavigationAction, ViewerViewState
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
from tomography_session_browser.presentation.viewer_tabs import PreparedViewerTab
from tomography_session_browser.ui.viewer.prepared import install_prepared_raster


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
        protected_roots: Callable[[], tuple[Path, ...]] = lambda: (),
    ) -> None:

        self._constructed = False
        super().__init__()

        # Each part is handed the parts built before it; parts built later
        # reach these through ``parts``. The loader first, then the selection.
        loader = PreviewLoader(self, frames_share_zoom=frames_share_zoom)
        selection = TabSelection(
            self,
            loader=loader,
            on_item_selected=on_item_selected,
            markers_for=markers_for,
            on_marker_selected=on_marker_selected,
            on_marker_opened=on_marker_opened,
            on_marker_selection_cleared=on_marker_selection_cleared,
        )
        controls = CanvasControls(self, loader=loader, selection=selection, entity_icon=entity_icon)
        overlays = OverlayPanel(self, selection=selection, controls=controls,
                                show_marker_controls=show_marker_controls, atlas_lod=atlas_lod)
        pane = ViewerListPane(self, loader=loader, selection=selection)
        viewer_header = ViewerHeader(self, selection=selection, pane=pane, navigation_actions_for=navigation_actions_for,
                                     on_navigation_requested=on_navigation_requested,
                                     show_exposure_chooser=show_exposure_chooser)
        self.parts = TabParts(
            canvas_controls=controls,
            export=ViewerExport(self, loader=loader, overlays=overlays, protected_roots=protected_roots),
            frame_scrubber=FrameScrubber(
                self, loader=loader, selection=selection, on_frame_changed=on_frame_changed,
                tilt_angle_for=tilt_angle_for, show_tilt_controls=show_tilt_controls,
            ),
            list_pane=pane,
            mrc_loading=loader,
            overlay_panel=overlays,
            tab_selection=selection,
            viewer_header=viewer_header,
        )

        # Worker results reach the GUI thread through this QWidget's slots.
        self.parts.mrc_loading.mrc_signals.loaded.connect(self._mrc_loaded)
        self.parts.mrc_loading.mrc_signals.failed.connect(self._mrc_failed)
        self.parts.mrc_loading.mrc_signals.raster_loaded.connect(self._raster_loaded)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(SPACE_S)
        self.viewer = ImagePreviewView(self)
        self.viewer.set_atlas_lod_enabled(atlas_lod)
        self.viewer.set_pan_through_marker_types(pan_through_marker_types)
        if frames_share_zoom:
            # One crop per item, whichever frame is shown: see
            # ``_logical_image_key`` for the key's shape.
            self.viewer.share_view_within_groups(lambda key: key[2] if isinstance(key, tuple) else key)
        self.viewer.set_zoom_changed_callback(self.parts.canvas_controls.zoom_changed)
        self.viewer.set_marker_selected_callback(selection.marker_selected)
        self.viewer.set_marker_opened_callback(selection.marker_opened)
        self.viewer.set_cluster_member_activated_callback(
            on_cluster_member_activated
        )
        self.viewer.set_selection_cleared_callback(selection.marker_selection_cleared)

        header = self.parts.viewer_header.create_title()
        controls, zoom_label_width = self.parts.canvas_controls.create_controls(empty_text, atlas_lod)
        self.parts.frame_scrubber.create_widgets(show_tilt_controls)
        self.parts.overlay_panel.create_widgets()
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
        self.parts.viewer_header.fit_header()
        if self.parts.canvas_controls.canvas_theme_stale:
            self.parts.canvas_controls.recolour_canvas()  # the theme changed while this tab was hidden

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

    def refresh_theme(self) -> None:
        return self.parts.canvas_controls.refresh_theme()

    def selected_marker_id(self) -> str | None:
        return self.parts.tab_selection.selected_marker_id()

    def held_markers(self, value: Any) -> list[ImageMarker] | None:
        return self.parts.tab_selection.held_markers(value)

    def query(self) -> str:
        """The list's filter, whole (``is:failed violet``)."""

        return self.parts.list_pane.query()

    def set_query(self, text: str, *, notify: bool = True,
                  row_keys: frozenset[str] | None = None, scope_label: str = "") -> None:
        """Make ``text`` the list's filter; ``set_query("")`` clears all of it."""

        self.parts.list_pane.set_query(text, notify=notify, row_keys=row_keys, scope_label=scope_label)

    def filter_description(self) -> str:
        """The visible status/text query and any summary scope restriction."""
        return self.parts.list_pane.filter_bar.description()

    def item_status_for_value(self, value: Any) -> ItemListStatus | None:
        return self.parts.list_pane.item_status_for_value(value)

    def has_same_items(self, items: list[Any]) -> bool:
        return self.parts.list_pane.has_same_items(items)

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

    def empty_state(self) -> EmptyState:
        return self.parts.list_pane.empty_state()

    def show_empty_state(self, state: EmptyState) -> None:
        return self.parts.list_pane.show_empty_state(state)

    def clear_empty_state_override(self) -> None:
        return self.parts.list_pane.clear_empty_state_override()

    def capture_view_state(self) -> ViewerViewState:
        return self.parts.tab_selection.capture_view_state()

    def current_object(self) -> object | None:
        """The item whose image occupies this page, including an unlisted item."""
        return self.parts.tab_selection.current_value

    def object_for_key(self, key: str) -> object | None:
        """Resolve a canonical row key without exposing the list implementation."""
        return self.parts.list_pane.rows.value_for_key(key)

    def contains_object(self, value: object) -> bool:
        return self.parts.list_pane.rows.row_for_value(value) is not None

    def restore_list_anchor(self, key: str, offset: int) -> None:
        self.parts.tab_selection.restore_list_anchor(key, offset)

    def list_counts(self) -> tuple[int, int]:
        return self.parts.list_pane.rows.counts()

    def list_column(self) -> QWidget:
        return self.parts.list_pane.list_panel

    def install_prepared(
        self, payload: PreparedViewerTab, *,
        label_for: Callable[[Any], str],
        markers_for: Callable[[Any], list[ImageMarker]] | None,
        auto_load_preview: bool,
        restore_state: ViewerViewState | None,
    ) -> None:
        """Install raster rows; raster source and provider policy stays in this owner."""
        install_prepared_raster(self, payload, label_for=label_for, markers_for=markers_for,
                                auto_load_preview=auto_load_preview, restore_state=restore_state)

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

    def ensure_initial_preview_loaded(self, *, notify_selection: bool = True) -> None:
        return self.parts.tab_selection.ensure_initial_preview_loaded(notify_selection=notify_selection)

    def select_object(self, value: Any) -> bool:
        return self.parts.tab_selection.select_object(value)

    def discard_zoom(self) -> None:
        return self.parts.tab_selection.discard_zoom()

    def select_frame(self, frame_index: int) -> None:
        return self.parts.frame_scrubber.select_frame(frame_index)

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

    def select_marker(self, marker_id: str | None, *, arrive: bool = False, frame: bool = False) -> None:
        return self.parts.tab_selection.select_marker(marker_id, arrive=arrive, frame=frame)

    def close_overlay_panel(self) -> bool:
        return self.parts.overlay_panel.close_overlay_panel()

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt signature
        if not self._constructed:
            return super().resizeEvent(event)
        super().resizeEvent(event)
        self.parts.list_pane.fit_list_minimum()
        self.parts.overlay_panel.constrain_overlay_panel()
        self.parts.viewer_header.fit_header()

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
            self.parts.canvas_controls.place_bottom_anchors()
        return super().eventFilter(watched, event)



# Contextual keys handled by this module; consumed by the shared Help sheet.
LOCAL_KEYS = (
    LocalBinding('viewer.viewer_tab.overlays-panel-esc', 'Overlays panel', InteractionKind.KEYBOARD, ('Esc',), 'Close Overlays and return focus to its button'),
)
