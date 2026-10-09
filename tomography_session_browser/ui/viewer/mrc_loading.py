"""Mrc loading for the image viewer."""
from __future__ import annotations
from pathlib import Path
from dataclasses import dataclass
from typing import Any, Literal
from PySide6.QtCore import QObject, Signal
from PySide6.QtGui import QImage
from tomography_session_browser.services.preview_sources import PreviewSources
from tomography_session_browser.presentation.empty_states import missing_preview_state
import logging
LOGGER = logging.getLogger("tomography_session_browser.ui.image_viewer")
from tomography_session_browser.ui.viewer.constants import FIRST_ZOOM_PREVIEW_DIMENSION, MAX_PREVIEW_DIMENSION, MRC_JPEG_FALLBACK_GRACE_MS, PYRAMID_LEVELS
from tomography_session_browser.ui.viewer.list_rows import warning_tooltip
from tomography_session_browser.ui.viewer.preview_cache import CachedPreview
from tomography_session_browser.ui.viewer.preview_scheduler import cached_preview, preview_scheduler
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from tomography_session_browser.ui.viewer.viewer_tab import ViewerTab


@dataclass(frozen=True, slots=True)
class RasterRequest:
    request_id: int
    primary: Path | None
    slice_index: int
    path: Path
    mode: Literal["image", "delayed", "failed"]
    warnings: tuple[str, ...] = ()


class MrcLoadSignals(QObject):
    loaded = Signal(int, Path, int, QImage, int, list, int, int)
    failed = Signal(int, Path, int, int, list)
    raster_loaded = Signal(object, object)


def emit_if_open(signal, *args) -> None:
    try:
        signal.emit(*args)
    except RuntimeError:
        LOGGER.debug("Dropped preview result; viewer was closed")


class PreviewLoader:
    """Mrc loading operations for one ViewerTab."""

    def __init__(self, view: ViewerTab) -> None:
        self.view = view
        self.native_size = 0
        self.outgoing_image_stale = False

    def discard_outgoing_image(self) -> None:
        """Blank the canvas while a decode is awaited for another item.

        Decoding is asynchronous. Never label the outgoing item's pixels as the
        newly selected scientific item. A preview already in the cache is
        installed at once and never reaches here, so it keeps the dissolve.
        """
        if not self.outgoing_image_stale:
            return
        self.outgoing_image_stale = False
        self.view.viewer.clear("Loading preview...")
        self.displayed_path = None
        self.displayed_slice_index = None
        self.view._update_image_badge()
        self.view._update_scale_bar()

    def load_value(self, value: Any, slice_index: int, *, restore_frame: bool = False) -> None:
        preview_scheduler().cancel(self.mrc_signals)
        preview_scheduler().cancel((self.mrc_signals, "raster"))
        self.request_id += 1
        self.native_size = 0
        sources = self.view._source_for_value(value)
        path = sources.primary
        self.outgoing_image_stale = (path is not None and value is not self.view.parts.tab_selection.current_value
                                     and path != self.current_path)
        # Empty-state overrides such as ``missing_preview`` describe the row
        # that is being left.  Clear the realised panel as well as the model
        # before starting the next preview; MRC loading is asynchronous, so
        # waiting for its completion otherwise leaves the old panel stacked
        # over the canvas while the new section loads.
        self.view.clear_empty_state_override()
        if self.frames_share_zoom:
            # Another tilt series opens fitted, and the zoom on the one being
            # left is dropped: coming back to it shows it fitted too.
            self.view.viewer.begin_view_group(id(value))
        if value is not self.view.parts.tab_selection.current_value:
            # Another item: dissolve into its image when it arrives (F5.4).
            # Frame changes of the same item go through ``_load_path`` and
            # stay a crisp cut.
            self.view.viewer.crossfade_next_image()
            # A jump still waiting to land belonged to the previous item; a
            # jump to this one asks again once it is selected (F5.7).
            self.view.viewer.arrive_at_marker(None)
        self.view.parts.tab_selection.current_value = value
        self.view.parts.tab_selection.selected_marker_id_override = None
        self.view.parts.tab_selection.marker_selection_explicitly_cleared = False
        self.view.viewer.clear_exposure_highlights(redraw=False)
        self.view._update_atlas_collection_overlay_controls(value)
        self.view._update_header(value)
        markers = self.view.parts.tab_selection.markers_of(value)
        self.view._update_marker_type_availability(markers)
        self.view.viewer.set_markers(markers, selected_marker_id=self.view._active_selected_marker_id(value, markers))
        if self.view.parts.overlay_panel.atlas_lod:
            self.view.atlas_marker_legend.set_markers(markers)
        if path is None:
            self.current_path = None
            self.current_fallback = None
            self.displayed_path = None
            self.displayed_slice_index = None
            self.view.viewer.clear("Preview unavailable")
            self.view.export_image_button.setEnabled(False)
            self.view._set_status_text("No preview image found for this item.", tone="warn")
            self.view._set_slice_state(1, 0, valid_stack=False)
            self.view.show_empty_state(
                missing_preview_state(
                    name=str(getattr(value, "name", None) or getattr(value, "id", "this item")),
                    metadata_available=True,
                )
            )
            return
        self.current_path = path
        self.current_fallback = sources.fallback
        self.current_mrc_max_size = self.view._initial_mrc_preview_size(path)
        if not restore_frame:
            slice_index = self.view._default_frame_index(value, path, slice_index)
        self.view._load_path(path, sources.fallback, slice_index)

    def source_for_value(self, value: Any) -> PreviewSources:
        key = getattr(value, "id", None)
        if key is not None:
            prepared = self.prepared_sources.get(str(key))
            if prepared is not None:
                return prepared
        return self.sources_for(value)

    def load_path(self, path: Path, fallback: Path | None, slice_index: int) -> None:
        if path.suffix.lower() == ".mrc":
            self.view._load_mrc_path(path, fallback, slice_index)
            return
        self.request_id += 1
        self.request_raster(path, mode="image")

    def request_raster(self, path: Path, *, mode: Literal["image", "delayed", "failed"], warnings: list[str] | None = None) -> None:
        request = RasterRequest(self.request_id, self.current_path, self.view.slice_slider.value(), path, mode, tuple(warnings or ()))
        cached = cached_preview((path, 0, 0))
        if cached is not None:
            self.outgoing_image_stale = False
            self.raster_loaded(request, cached)
            return
        self.discard_outgoing_image()
        signal = self.mrc_signals.raster_loaded
        preview_scheduler().request((path, 0, 0), client=(self.mrc_signals, "raster"),
            receive=lambda result: emit_if_open(signal, request, result))

    def raster_loaded(self, request: RasterRequest, preview: CachedPreview) -> None:
        request_id, primary, index = request.request_id, request.primary, request.slice_index
        path, mode = request.path, request.mode
        if request_id != self.request_id or primary != self.current_path:
            return
        if mode == "delayed" and (self.displayed_path == primary or index != self.view.slice_slider.value()):
            return
        warnings = list(request.warnings) + preview.warnings
        if not preview.image.isNull():
            self.view.viewer.set_image(preview.image, logical_image_key=self.view._logical_image_key(primary or path, index))
        else:
            self.view.viewer.clear("Preview unavailable")
        self.view._refresh_marker_selection()
        if not preview.image.isNull():
            self.displayed_path = path
            self.displayed_slice_index = 0
            self.view.parts.list_pane.empty_state_override = None
        elif not self.view.viewer.has_image():
            self.view.parts.list_pane.empty_state_override = missing_preview_state(
                name=str(
                    getattr(self.view.parts.tab_selection.current_value, "name", None)
                    or getattr(self.view.parts.tab_selection.current_value, "id", "this item")
                ),
                expected_path=str(path),
                metadata_available=True,
                warnings=tuple(warnings),
            )
        self.view._update_image_badge()
        self.view._update_scale_bar()
        if mode != "delayed":
            self.view._set_slice_state(1, 0, valid_stack=False)
        caption = {"image": "Image", "delayed": "Loading MRC preview · JPEG fallback", "failed": "JPEG fallback · MRC unavailable"}[mode]
        tooltip = self.view._path_status_tooltip(caption, path, warnings=warnings)
        if mode != "image":
            tooltip = f"MRC: {primary}\nFallback: {path}{warning_tooltip(warnings)}"
        self.view._set_status_text(
            self.view._compact_path_status(caption, path, warnings=warnings),
            tooltip=tooltip,
            tone="warn" if warnings or mode == "failed" else "",
        )
        self.view._refresh_empty_state_panel()

    def load_mrc_path(self, path: Path, fallback: Path | None, slice_index: int) -> None:
        self.fallback_debounce.stop()
        self.pending_fallback_request = None
        frame_count = self.view._known_frame_count_for_path(path)
        clamped_index = max(0, min(slice_index, frame_count - 1)) if frame_count else max(0, slice_index)
        self.view._set_slice_state(frame_count or 1, clamped_index, valid_stack=(frame_count or 0) > 1)
        logical_key = self.view._logical_image_key(path, clamped_index)
        self.request_id += 1
        request_id = self.request_id
        cached = cached_preview((path, clamped_index, self.current_mrc_max_size))
        if cached is not None:
            # Already decoded: no worker, no blank canvas, and the dissolve
            # armed for an item change still plays.
            self.outgoing_image_stale = False
            self.view._set_slice_state(cached.slice_count, clamped_index, valid_stack=cached.slice_count > 1)
            self.mrc_loaded(request_id, path, clamped_index, cached.image, cached.slice_count,
                            cached.warnings, cached.estimated_bytes, cached.source_max)
            return
        self.discard_outgoing_image()
        if fallback is not None:
            self.pending_fallback_request = (request_id, path, clamped_index, fallback, logical_key)
            self.view._set_status_text(
                f"Loading MRC preview: {path.name}",
                tooltip=f"MRC: {path}\nJPEG fallback will display if the MRC preview is not ready within {MRC_JPEG_FALLBACK_GRACE_MS} ms.",
            )
            self.fallback_debounce.start()
        elif self.view.viewer.has_image():
            self.view._set_status_text(
                f"Loading frame {clamped_index + 1} of {frame_count}: {path.name}",
                tooltip=str(path),
            )
        else:
            self.view.viewer.clear("Loading MRC preview...")
            self.view.export_image_button.setEnabled(False)
            self.view._set_status_text(f"Loading MRC preview: {path.name}", tooltip=str(path))
        signals = self.mrc_signals
        def receive(result: CachedPreview) -> None:
            if result.image.isNull():
                emit_if_open(signals.failed, request_id, path, clamped_index, result.slice_count, result.warnings)
            else:
                emit_if_open(signals.loaded, request_id, path, clamped_index, result.image,
                             result.slice_count, result.warnings, result.estimated_bytes, result.source_max)
        preview_scheduler().request((path, clamped_index, self.current_mrc_max_size),
                                    client=signals, receive=receive)

    def show_delayed_mrc_fallback(self) -> None:
        pending = self.pending_fallback_request
        if pending is None:
            return
        request_id, path, slice_index, fallback, logical_key = pending
        if request_id != self.request_id or path != self.current_path or slice_index != self.view.slice_slider.value():
            return
        if self.displayed_path == path:
            return
        self.request_raster(fallback, mode="delayed")

    def known_frame_count_for_path(self, path: Path) -> int:
        value = self.view.parts.tab_selection.current_value
        metadata = getattr(value, "mrc_metadata", None)
        if metadata is not None and getattr(metadata, "path", None) == path and getattr(metadata, "nz", None):
            return int(metadata.nz)
        number_of_frames = getattr(value, "number_of_frames", None)
        if isinstance(number_of_frames, int) and number_of_frames > 0:
            return number_of_frames
        sections = getattr(value, "sections", None)
        if sections:
            return len(sections)
        return 0

    def initial_mrc_preview_size(self, path: Path | None) -> int:
        if path is None or path.suffix.lower() != ".mrc":
            return MAX_PREVIEW_DIMENSION
        source_max = self.view._known_mrc_source_max_for_path(path)
        if source_max > 0:
            return max(1, min(FIRST_ZOOM_PREVIEW_DIMENSION, source_max))
        return FIRST_ZOOM_PREVIEW_DIMENSION

    def mrc_loaded(
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
        if request_id != self.request_id or path != self.current_path or slice_index != self.view.slice_slider.value():
            LOGGER.debug("Ignored stale MRC preview path=%s slice=%s request=%s", path, slice_index, request_id)
            return
        self.native_size = source_max
        self.fallback_debounce.stop()
        self.pending_fallback_request = None
        self.view.viewer.set_image(
            image,
            logical_image_key=self.view._logical_image_key(path, slice_index),
            pyramid_level=self.current_mrc_max_size,
        )
        self.view.clear_empty_state_override()
        self.view._refresh_marker_selection()
        self.displayed_path = path
        self.displayed_slice_index = slice_index
        self.view._update_image_badge()
        self.view._update_scale_bar()
        self.view._set_slice_state(slice_count, slice_index, valid_stack=slice_count > 1)
        self.view._set_status(path, slice_count, warnings, "MRC")
        self.view._prefetch_neighbors(path, slice_index, slice_count)
        self.view._prefetch_current_high_detail(path, slice_index)
        LOGGER.debug("Displayed MRC preview path=%s slice=%s estimated_bytes=%s", path, slice_index, estimated_bytes)

    def mrc_failed(self, request_id: int, path: Path, slice_index: int, slice_count: int, warnings: list[str]) -> None:
        if request_id != self.request_id or path != self.current_path:
            return
        self.fallback_debounce.stop()
        self.pending_fallback_request = None
        if self.current_fallback is not None:
            self.request_raster(self.current_fallback, mode="failed", warnings=warnings)
            return
        self.view.viewer.clear("Preview unavailable")
        self.view.export_image_button.setEnabled(False)
        self.view._set_image_badge_wanted(False)
        self.view.scale_bar.setVisible(False)
        self.view._set_slice_state(slice_count, slice_index, valid_stack=False)
        self.view._set_status_text(
            self.view._compact_path_status("MRC unavailable", path, slice_count=slice_count, warnings=warnings),
            tooltip=self.view._path_status_tooltip("MRC unavailable", path, slice_count=slice_count, warnings=warnings),
            tone="bad",
        )
        self.view.parts.list_pane.empty_state_override = missing_preview_state(
            name=str(
                getattr(self.view.parts.tab_selection.current_value, "name", None)
                or getattr(self.view.parts.tab_selection.current_value, "id", "this item")
            ),
            expected_path=str(path),
            metadata_available=True,
            warnings=tuple(warnings),
        )
        self.view._refresh_empty_state_panel()

    def logical_image_key(self, path: Path, slice_index: int) -> tuple[str, int, int | None]:
        return (str(path), slice_index, id(self.view.parts.tab_selection.current_value) if self.view.parts.tab_selection.current_value is not None else None)

    def prefetch_neighbors(self, path: Path, slice_index: int, slice_count: int) -> None:
        if slice_count <= 1:
            return
        max_size = min(max(self.current_mrc_max_size, MAX_PREVIEW_DIMENSION), FIRST_ZOOM_PREVIEW_DIMENSION)
        source_max = self.view._known_mrc_source_max_for_path(path)
        if source_max > 0:
            max_size = min(max_size, source_max)
        for offset in (1, -1, 2, -2):
            neighbor = slice_index + offset
            if 0 <= neighbor < slice_count:
                self.view._prefetch_mrc_frame(path, neighbor, max_size)

    def prefetch_current_high_detail(self, path: Path, slice_index: int) -> None:
        if self.current_mrc_max_size >= FIRST_ZOOM_PREVIEW_DIMENSION:
            return
        target = self.view._next_mrc_detail_level(path, self.current_mrc_max_size)
        if target is None:
            return
        target = min(target, FIRST_ZOOM_PREVIEW_DIMENSION)
        if target <= self.current_mrc_max_size:
            return
        self.view._prefetch_mrc_frame(path, slice_index, target)

    def next_mrc_detail_level(self, path: Path, current_size: int) -> int | None:
        source_max = self.view._known_mrc_source_max_for_path(path)
        if source_max <= current_size:
            return None
        for level in PYRAMID_LEVELS:
            if level > current_size:
                return min(level, source_max)
        return source_max

    def prefetch_mrc_frame(self, path: Path, slice_index: int, max_size: int = MAX_PREVIEW_DIMENSION) -> None:
        preview_scheduler().request((path, slice_index, max_size))
