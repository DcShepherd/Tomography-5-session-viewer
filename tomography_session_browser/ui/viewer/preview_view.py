"""Preview view for the image viewer."""
from __future__ import annotations
from collections import OrderedDict
from collections.abc import Callable, Iterable
import math
from pathlib import Path
import time
from typing import Any
from PySide6.QtCore import QPoint, QPointF, QRect, Qt, QTimer
from PySide6.QtGui import QColor, QImage, QKeyEvent, QMouseEvent, QPainter, QPixmap, QWheelEvent
from PySide6.QtWidgets import QFrame, QGraphicsItem, QGraphicsPixmapItem, QGraphicsScene, QGraphicsTextItem, QGraphicsView, QWidget
from tomography_session_browser.domain.markers import ImageMarker, MarkerType
from tomography_session_browser.ui.motion import DURATION_CAMERA_MS, MOTION_PANEL, MOTION_ZOOM, MarkerPulse, PictureFade, Spring, Tween, ZoomPanPath, ease_in_out_cubic
from tomography_session_browser.services.marker_clustering import ZOOM_DEBOUNCE_MS, MarkerCluster
import logging
LOGGER = logging.getLogger("tomography_session_browser.ui.image_viewer")
from tomography_session_browser.ui.viewer.atlas_lod_items import AtlasClusterPopup
from tomography_session_browser.ui.viewer.constants import ATLAS_LOD_DEFAULTS, DEFAULT_VISIBLE_MARKER_TYPES, WHEEL_ZOOM_BASE, WHEEL_ZOOM_UNITS_PER_PIXEL
from tomography_session_browser.ui.viewer.marker_layer import MarkerLayer
from tomography_session_browser.ui.viewer.preview_camera import PreviewCamera
from tomography_session_browser.ui.viewer.preview_image import PreviewImage
from tomography_session_browser.ui.viewer.preview_interaction import PreviewInteraction
from tomography_session_browser.ui.viewer.parts import PreviewParts


class ImagePreviewView(QGraphicsView):

    def __init__(self, parent: QWidget | None = None) -> None:

        self._constructed = False
        super().__init__(parent)

        self.parts = PreviewParts(
            marker_layer=MarkerLayer(self),
            preview_camera=PreviewCamera(self),
            preview_image=PreviewImage(self),
            preview_interaction=PreviewInteraction(self),
        )

        self.parts.preview_image.scene = QGraphicsScene(self)
        self.parts.preview_image.pixmap_item: QGraphicsPixmapItem | None = None
        self.parts.preview_camera.zoom = 1.0
        self._zoom_changed: Callable[[float], None] | None = None
        self.parts.preview_image.current_image_key: Any | None = None
        self.parts.preview_camera.has_user_interacted = False
        self.parts.preview_camera.pending_initial_fit_flag = False
        # True while the view tracks fit-to-viewport: after the initial fit or
        # the Fit button, until the user zooms or actually pans. Resizing the
        # viewer (window resize, maximise, dock toggle) re-fits in this mode,
        # so the image never keeps a stale scale. Separate from
        # ``_has_user_interacted``, which a plain click also sets.
        self.parts.preview_camera.fit_mode = False
        self.parts.preview_camera.refitting = False
        self.parts.preview_interaction.press_scroll: tuple[int, int] | None = None
        # The canvas message ("Loading MRC preview...", "Preview unavailable").
        # Hidden whenever the viewer tab's empty-state panel is showing, so the
        # two never say the same thing on top of each other.
        self.parts.preview_image.placeholder_item: QGraphicsTextItem | None = None
        self.parts.preview_image.placeholder_visible = True
        self.parts.preview_camera.last_view_range: tuple[tuple[float, float], tuple[float, float]] | None = None
        # Viewport memory, keyed by *logical image* rather than by tab, so a
        # different image can never inherit another's crop. Stores only the
        # scene-space rectangle the user was looking at.
        self.parts.preview_camera.view_memory: OrderedDict[Any, tuple[float, float, float, float]] = OrderedDict()
        # Tilt series: one crop for all the frames of the series on show (the
        # active group), and none for a series already left.
        self.parts.preview_camera.view_group_of: Callable[[Any], Any] | None = None
        self.parts.preview_camera.active_view_group: Any = None
        self.parts.preview_image.current_image_shape: tuple[int, int] | None = None
        self.parts.marker_layer.source_markers: list[ImageMarker] = []
        self.parts.marker_layer.marker_items: list[QGraphicsItem] = []
        self._marker_selected: Callable[[ImageMarker], None] | None = None
        self._marker_opened: Callable[[ImageMarker], None] | None = None
        self.parts.preview_interaction.cluster_member_activated: Callable[[ImageMarker], None] | None = None
        self.parts.marker_layer.visible_marker_types: set[str] = set(DEFAULT_VISIBLE_MARKER_TYPES)
        # Marker types a single press passes through, so it pans the image
        # like a press on empty image; double-click still opens them.
        self.parts.preview_interaction.pan_through_marker_types: frozenset[str] = frozenset()
        self.parts.marker_layer.atlas_lod_enabled = False
        self.parts.marker_layer.atlas_lod_options = dict(ATLAS_LOD_DEFAULTS)
        self.parts.marker_layer.cluster_cache: dict[tuple[object, ...], tuple[MarkerCluster, ...]] = {}
        self.parts.marker_layer.cluster_compute_count = 0
        self.parts.marker_layer.last_display_markers: list[ImageMarker] = []
        self.parts.preview_interaction.keyboard_marker_id: str | None = None
        self.parts.preview_interaction.cluster_member_popup: AtlasClusterPopup | None = None
        self.parts.preview_interaction.cluster_popup_connector: QFrame | None = None
        self.parts.preview_interaction.selection_cleared: Callable[[], None] | None = None
        self.parts.preview_interaction.exposure_highlights: set[str] = set()
        self.parts.marker_layer.marker_redraw_debounce = QTimer(self)
        self.parts.marker_layer.marker_redraw_debounce.setSingleShot(True)
        self.parts.marker_layer.marker_redraw_debounce.setInterval(ZOOM_DEBOUNCE_MS)
        self.parts.marker_layer.marker_redraw_debounce.timeout.connect(self._redraw_markers)
        # Wheel zoom eases toward a retargetable target (plan F5.1). The spring
        # runs in log-zoom, so a notch takes the same time at any scale, and it
        # moves only the view transform. The anchor is the cursor's viewport
        # position and the scene point under it when the notch arrived.
        self.parts.preview_camera.wheel_zoom = Spring(0.0, spec=MOTION_ZOOM, on_update=self._wheel_zoom_step, widget=self)
        self.parts.preview_camera.wheel_anchor: tuple[QPointF, QPointF] | None = None
        # A side panel opening or closing eases a fitted image to its new fit
        # rather than snapping (plan F5.2). The spring runs 0 → 1 between the
        # (scale, scene centre) pairs below; ``ease_next_refit`` arms it.
        self.parts.preview_camera.refit_ease = Spring(
            0.0,
            spec=MOTION_PANEL,  # the same spring as the panel's slide
            on_update=self._refit_ease_step,
            on_settled=self._refit_ease_done,
            widget=self,
            # The last frame lands on the exact fit, so the tail can stop a
            # fraction of a pixel short.
            settle_distance=2e-3,
            settle_velocity=0.05,
        )
        self.parts.preview_camera.refit_ease_armed_until = 0.0
        self.parts.preview_camera.refit_from: tuple[float, QPointF] | None = None
        self.parts.preview_camera.refit_to: tuple[float, QPointF] | None = None
        self.parts.preview_camera.scroll_policies: tuple[Qt.ScrollBarPolicy, Qt.ScrollBarPolicy] | None = None
        # Selecting another item arms a crossfade for the next new image
        # (plan F5.4); frame scrubbing never arms it, so it stays crisp.
        self.parts.preview_image.crossfade_armed = False
        self.parts.preview_image.crossfade: PictureFade | None = None
        # The camera flies to a target instead of cutting to it, and a jump's
        # target pulses once where it landed (plan F5.7). Only the view
        # transform moves; markers and their projection are untouched.
        self.parts.preview_camera.flight = Tween(
            DURATION_CAMERA_MS,
            on_update=self._flight_step,
            on_finished=self._land_flight,
            widget=self,
            easing=ease_in_out_cubic,
        )
        self.parts.preview_camera.flight_path: ZoomPanPath | None = None
        self.parts.preview_camera.flight_from: tuple[float, float] | None = None  # scale and zoom at take-off
        self.parts.preview_camera.flight_to: tuple[float, QPointF, float] | None = None  # scale, scene centre, zoom
        self.parts.preview_camera.flight_landed: Callable[[], None] | None = None
        self.parts.preview_camera.arrival_in_flight: str | None = None
        self.parts.preview_camera.pending_arrival: str | None = None
        self.parts.preview_camera.arrival_ready: Callable[[], bool] | None = None
        self.parts.preview_camera.arrival_frames = False
        self.parts.preview_camera.arrival_timer = QTimer(self)
        self.parts.preview_camera.arrival_timer.setSingleShot(True)
        self.parts.preview_camera.arrival_timer.timeout.connect(self._try_arrival)
        self.parts.preview_camera.pulse_overlay: MarkerPulse | None = None

        # The shared ticker must never step a view that has been destroyed
        # mid-motion; the handler holds the springs, not the view. Only while
        # they animate: at exit the ticker's own timer may already be gone.
        def _stop_springs(*_args, springs=(self.parts.preview_camera.wheel_zoom, self.parts.preview_camera.refit_ease), flight=self.parts.preview_camera.flight) -> None:
            for spring in springs:
                if spring.is_animating:
                    spring.reset(spring.value)
            if flight.is_animating:
                flight.stop()

        self.destroyed.connect(_stop_springs)
        self.setScene(self.parts.preview_image.scene)
        self.setObjectName("viewerCanvas")
        self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setResizeAnchor(QGraphicsView.ViewportAnchor.AnchorViewCenter)
        self.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        self.setBackgroundBrush(Qt.GlobalColor.black)
        self._constructed = True

    def clear(self, message: str = "") -> None:
        return self.parts.preview_image.clear(message)

    def refresh_theme(self) -> None:
        return self.parts.preview_image.refresh_theme()

    def set_placeholder_visible(self, visible: bool) -> None:
        return self.parts.preview_image.set_placeholder_visible(visible)

    @property
    def placeholder_text(self) -> str:
        return self.parts.preview_image.placeholder_text

    def load_raster_path(
        self,
        path: Path,
        preserve_view: bool = False,
        *,
        logical_image_key: Any | None = None,
        pyramid_level: int | None = None,
    ) -> list[str]:
        return self.parts.preview_image.load_raster_path(path, preserve_view, logical_image_key=logical_image_key, pyramid_level=pyramid_level)

    def has_image(self) -> bool:
        return self.parts.preview_image.has_image()

    def zoom_scale(self) -> float:
        return self.parts.preview_camera.zoom_scale()

    def displayed_image_shape(self) -> tuple[int, int] | None:
        return self.parts.preview_image.displayed_image_shape()

    def set_zoom_changed_callback(self, callback: Callable[[float], None]) -> None:
        return self.parts.preview_interaction.set_zoom_changed_callback(callback)

    def set_marker_selected_callback(self, callback: Callable[[ImageMarker], None]) -> None:
        return self.parts.preview_interaction.set_marker_selected_callback(callback)

    def set_marker_opened_callback(self, callback: Callable[[ImageMarker], None]) -> None:
        return self.parts.preview_interaction.set_marker_opened_callback(callback)

    def set_cluster_member_activated_callback(
        self,
        callback: Callable[[ImageMarker], None] | None,
    ) -> None:
        return self.parts.preview_interaction.set_cluster_member_activated_callback(callback)

    def set_selection_cleared_callback(self, callback: Callable[[], None]) -> None:
        return self.parts.preview_interaction.set_selection_cleared_callback(callback)

    def set_atlas_lod_enabled(self, enabled: bool) -> None:
        return self.parts.marker_layer.set_atlas_lod_enabled(enabled)

    def set_pan_through_marker_types(self, marker_types: Iterable[str]) -> None:
        return self.parts.preview_interaction.set_pan_through_marker_types(marker_types)

    def set_atlas_lod_option(self, key: str, enabled: bool) -> None:
        return self.parts.marker_layer.set_atlas_lod_option(key, enabled)

    def atlas_lod_option(self, key: str) -> bool:
        return self.parts.marker_layer.atlas_lod_options[key]

    def set_marker_type_visible(self, marker_type: str, visible: bool) -> None:
        return self.parts.marker_layer.set_marker_type_visible(marker_type, visible)

    def hide_all_markers(self) -> None:
        return self.parts.marker_layer.hide_all_markers()

    def set_markers(self, markers: list[ImageMarker], selected_marker_id: str | None = None) -> None:
        return self.parts.marker_layer.set_markers(markers, selected_marker_id)

    def set_image(
        self,
        image: QImage,
        preserve_view: bool = False,
        *,
        logical_image_key: Any | None = None,
        pyramid_level: int | None = None,
        overlay_reference_size: tuple[int, int] | None = None,
    ) -> None:
        return self.parts.preview_image.set_image(
            image, preserve_view, logical_image_key=logical_image_key,
            pyramid_level=pyramid_level, overlay_reference_size=overlay_reference_size,
        )

    def zoom_in(self) -> None:
        return self.parts.preview_camera.zoom_in()

    def zoom_out(self) -> None:
        return self.parts.preview_camera.zoom_out()

    def ease_zoom_by(self, factor: float) -> None:
        return self.parts.preview_camera.ease_zoom_by(factor)

    def ease_to_fit(self) -> None:
        return self.parts.preview_camera.ease_to_fit()

    def set_atlas_zoom(self, zoom: float) -> None:
        return self.parts.preview_camera.set_atlas_zoom(zoom)

    def fit_image(self) -> None:
        return self.parts.preview_camera.fit_image()

    def maybe_fit_initial_view(self) -> None:
        return self.parts.preview_camera.maybe_fit_initial_view()

    def remember_current_view(self) -> None:
        return self.parts.preview_camera.remember_current_view()

    def share_view_within_groups(self, group_of: Callable[[Any], Any]) -> None:
        return self.parts.preview_camera.share_view_within_groups(group_of)

    def begin_view_group(self, group: Any) -> None:
        return self.parts.preview_camera.begin_view_group(group)

    def discard_user_view(self) -> None:
        return self.parts.preview_camera.discard_user_view()

    def _view_memory_key(self, logical_image_key: Any) -> Any:
        return self.parts.preview_camera.view_memory_key(logical_image_key)

    def _restore_remembered_view(self, logical_image_key: Any) -> bool:
        return self.parts.preview_camera.restore_remembered_view(logical_image_key)

    def forget_remembered_views(self) -> None:
        return self.parts.preview_camera.forget_remembered_views()

    def view_range(self) -> tuple[tuple[float, float], tuple[float, float]] | None:
        return self.parts.preview_camera.view_range()

    def rendered_image_rect(self) -> QRect | None:
        return self.parts.preview_image.rendered_image_rect()

    def render_current_view(self) -> tuple[QImage, float] | None:
        # A regroup's glyphs are still fading in; the picture shows them whole.
        self.parts.marker_layer.dismiss_goo()
        return self.parts.preview_image.render_current_view()

    @property
    def current_logical_image_key(self) -> Any | None:
        return self.parts.preview_camera.current_logical_image_key

    @property
    def markers(self) -> tuple[ImageMarker, ...]:
        return self.parts.marker_layer.markers

    @property
    def pending_initial_fit(self) -> bool:
        return self.parts.preview_camera.pending_initial_fit

    @property
    def is_fit_mode(self) -> bool:
        return self.parts.preview_camera.is_fit_mode

    def _fit_image(self) -> None:
        return self.parts.preview_camera.apply_fit()

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt signature
        if not self._constructed:
            return super().resizeEvent(event)
        super().resizeEvent(event)
        if self.is_fit_mode and not self.parts.preview_camera.refitting and (
            self.parts.preview_camera.refit_ease.is_animating or time.monotonic() < self.parts.preview_camera.refit_ease_armed_until
        ):
            # A side panel opened or closed: ride along to the new fit, and
            # retarget if the layout resizes the viewport again mid-way.
            self.parts.preview_camera.refit_ease_armed_until = 0.0
            self._ease_to_fit()
        elif self.is_fit_mode and not self.parts.preview_camera.refitting:
            # The viewport changed size (window resize, maximise, dock toggle)
            # while the user was looking at the fitted image: keep it fitted.
            # Only the view transform changes; marker scene coordinates and
            # projection are untouched. Markers are redrawn once the resize
            # settles rather than on every intermediate size.
            self.parts.preview_camera.refitting = True
            try:
                self.fitInView(self.parts.preview_image.pixmap_item, Qt.AspectRatioMode.KeepAspectRatio)
                self.parts.preview_camera.zoom = 1.0
                self.parts.preview_camera.last_view_range = self.view_range()
            finally:
                self.parts.preview_camera.refitting = False
            self.parts.marker_layer.marker_redraw_debounce.start()
        if self._zoom_changed is not None:
            QTimer.singleShot(0, lambda: self._zoom_changed(self.parts.preview_camera.zoom))

    def wheelEvent(self, event: QWheelEvent) -> None:
        # The angle delta where there is one: wheels and most touchpads, and
        # Qt calls the pixel delta unreliable on X11. Some trackpads report
        # only pixels, which used to be accepted without zooming.
        if not self._constructed:
            return super().wheelEvent(event)
        delta = float(event.angleDelta().y()) or event.pixelDelta().y() * WHEEL_ZOOM_UNITS_PER_PIXEL
        if delta == 0:
            # A horizontal tilt or swipe, or a gesture's empty start or end.
            # It used to zoom out by 0.8.
            event.accept()
            return
        factor = math.pow(WHEEL_ZOOM_BASE, delta)
        LOGGER.debug(
            "wheel zoom logical_key=%s factor=%s position=%s",
            self.parts.preview_image.current_image_key,
            factor,
            event.position(),
        )
        self._zoom_toward(factor, event.position())
        event.accept()

    def _set_pixmap(
        self,
        pixmap: QPixmap,
        preserve_view: bool = False,
        *,
        logical_image_key: Any | None = None,
        pyramid_level: int | None = None,
        overlay_reference_size: tuple[int, int] | None = None,
    ) -> None:
        # A new level of the same image (a pyramid upgrade) moves the scene
        # coordinates under a flight to a jump's target; fly on from here once
        # the new level is in.
        return self.parts.preview_image.set_pixmap(
            pixmap, preserve_view, logical_image_key=logical_image_key,
            pyramid_level=pyramid_level, overlay_reference_size=overlay_reference_size,
        )

    def _zoom_toward(self, factor: float, anchor_view: QPointF) -> None:
        return self.parts.preview_camera.zoom_toward(factor, anchor_view)

    def _wheel_zoom_step(self, log_zoom: float) -> None:
        return self.parts.preview_camera.wheel_zoom_step(log_zoom)

    def _stop_wheel_zoom(self) -> None:
        return self.parts.preview_camera.stop_wheel_zoom()

    def ease_next_refit(self) -> None:
        return self.parts.preview_camera.ease_next_refit()

    def _fit_target(self) -> tuple[float, QPointF] | None:
        return self.parts.preview_camera.fit_target()

    def _ease_to_fit(self) -> None:
        return self.parts.preview_camera.start_fit_ease()

    def _refit_ease_step(self, progress: float) -> None:
        return self.parts.preview_camera.refit_ease_step(progress)

    def _refit_ease_done(self) -> None:
        return self.parts.preview_camera.refit_ease_done()

    def _end_refit_ease(self) -> None:
        return self.parts.preview_camera.end_refit_ease()

    def _hold_scroll_bars(self) -> None:
        return self.parts.preview_camera.hold_scroll_bars()

    def _release_scroll_bars(self) -> None:
        return self.parts.preview_camera.release_scroll_bars()

    def _stop_view_motion(self, *, finish_fit: bool = False) -> None:
        return self.parts.preview_camera.stop_view_motion(finish_fit=finish_fit)

    def fly_to(
        self,
        centre: QPointF,
        scale: float | None = None,
        *,
        zoom: float | None = None,
        on_landed: Callable[[], None] | None = None,
    ) -> None:
        return self.parts.preview_camera.fly_to(centre, scale, zoom=zoom, on_landed=on_landed)

    @property
    def is_flying(self) -> bool:
        return self.parts.preview_camera.is_flying

    def _flight_step(self, progress: float) -> None:
        return self.parts.preview_camera.flight_step(progress)

    def _land_flight(self) -> None:
        return self.parts.preview_camera.land_flight()

    def _stop_flight(self) -> None:
        return self.parts.preview_camera.stop_flight()

    def arrive_at_marker(
        self,
        marker_id: str | None,
        *,
        ready: Callable[[], bool] | None = None,
        frame: bool = False,
    ) -> None:
        return self.parts.preview_camera.arrive_at_marker(marker_id, ready=ready, frame=frame)

    def showEvent(self, event) -> None:  # noqa: N802 - Qt signature
        if not self._constructed:
            return super().showEvent(event)
        super().showEvent(event)
        if self.parts.preview_camera.pending_arrival is not None:
            self.parts.preview_camera.arrival_timer.start(0)

    def _try_arrival(self) -> None:
        return self.parts.preview_camera.try_arrival()

    def _own_marker_geometry(self, marker_id: str) -> tuple[QPointF, float] | None:
        return self.parts.preview_camera.own_marker_geometry(marker_id)

    def _framing_for(self, radius: float) -> tuple[float, float] | None:
        return self.parts.preview_camera.framing_for(radius)

    def _arrival_target(self, marker_id: str) -> tuple[QPointF, float] | None:
        return self.parts.preview_camera.arrival_target(marker_id)

    def _marker_screen_radius(self, marker_id: str) -> float:
        return self.parts.preview_camera.marker_screen_radius(marker_id)

    def _comfortably_in_view(self, scene_point: QPointF) -> bool:
        return self.parts.preview_camera.comfortably_in_view(scene_point)

    def _reveal_marker(self, marker: ImageMarker) -> None:
        return self.parts.preview_camera.reveal_marker(marker)

    def _pulse_marker(self, marker_id: str) -> None:
        return self.parts.preview_camera.pulse_marker(marker_id)

    def _dismiss_pulse(self) -> None:
        return self.parts.preview_camera.dismiss_pulse()

    @property
    def pulse(self) -> MarkerPulse | None:
        return self.parts.preview_camera.pulse

    def crossfade_next_image(self) -> None:
        return self.parts.preview_image.crossfade_next_image()

    def _image_layer_snapshot(self) -> QPixmap | None:
        return self.parts.preview_image.image_layer_snapshot()

    def _start_crossfade(self, picture: QPixmap) -> None:
        return self.parts.preview_image.start_crossfade(picture)

    def _begin_crossfade(self, fade: PictureFade) -> None:
        return self.parts.preview_image.begin_crossfade(fade)

    def _scene_picture(self) -> QPixmap:
        return self.parts.preview_image.scene_picture()

    def _dismiss_crossfade(self) -> None:
        return self.parts.preview_image.dismiss_crossfade()

    @property
    def crossfade_in_progress(self) -> bool:
        return self.parts.preview_image.crossfade_in_progress

    def _scale_by_center(self, factor: float) -> None:
        return self.parts.preview_camera.scale_by_center(factor)

    def _schedule_marker_redraw(self) -> None:
        return self.parts.marker_layer.schedule_marker_redraw()

    def mousePressEvent(self, event: QMouseEvent) -> None:
        # A press (the start of a pan) takes the view from an easing zoom; a
        # refit still in flight lands on the fit it promised.
        if not self._constructed:
            return super().mousePressEvent(event)
        self._stop_view_motion(finish_fit=True)
        if self.parts.preview_image.pixmap_item is None:
            super().mousePressEvent(event)
            return

        item = self.itemAt(event.position().toPoint())
        marker = item.data(0) if item is not None else None
        if event.button() == Qt.MouseButton.RightButton:
            self._clear_marker_interaction()
            event.accept()
            return
        linked_exposure_gesture = (
            event.button() == Qt.MouseButton.MiddleButton
            or (
                event.button() == Qt.MouseButton.LeftButton
                and bool(event.modifiers() & Qt.KeyboardModifier.ShiftModifier)
            )
        )
        if (
            linked_exposure_gesture
            and isinstance(marker, ImageMarker)
            and marker.marker_type
            in {MarkerType.EXPOSURE_AREA, MarkerType.CAMERA_FOV}
        ):
            self._highlight_linked_exposures(marker)
            if self._marker_selected is not None:
                self._marker_selected(marker)
            event.accept()
            return
        if event.button() == Qt.MouseButton.LeftButton:
            if (
                self.parts.preview_interaction.cluster_member_popup is not None
                and not self.parts.preview_interaction.cluster_member_popup.geometry().contains(
                    event.position().toPoint()
                )
            ):
                self._close_cluster_popup()
            if (
                isinstance(marker, ImageMarker)
                and marker.marker_type not in self.parts.preview_interaction.pan_through_marker_types
            ):
                if marker.marker_type == MarkerType.BATCH_CLUSTER:
                    self._activate_cluster(marker)
                    event.accept()
                    return
                if self._marker_selected is not None:
                    self._marker_selected(marker)
                event.accept()
                return
            if self._image_contains_view_position(event.position().toPoint()):
                self._clear_marker_interaction()
            self._mark_user_interacted("pan")
            self.parts.preview_interaction.press_scroll = self._scroll_position()
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:  # noqa: N802 - Qt signature
        if not self._constructed:
            return super().mouseReleaseEvent(event)
        super().mouseReleaseEvent(event)
        # A click is not a pan. Leave fit mode only when a drag actually moved
        # the view; a fitted image usually has nothing to scroll at all.
        if self.parts.preview_interaction.press_scroll is not None and self.parts.preview_interaction.press_scroll != self._scroll_position():
            self.parts.preview_camera.fit_mode = False
        self.parts.preview_interaction.press_scroll = None

    def _scroll_position(self) -> tuple[int, int]:
        return self.parts.preview_interaction.scroll_position()

    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:
        if not self._constructed:
            return super().mouseDoubleClickEvent(event)
        if self.parts.preview_image.pixmap_item is not None and event.button() == Qt.MouseButton.LeftButton:
            item = self.itemAt(event.position().toPoint())
            marker = item.data(0) if item is not None else None
            if isinstance(marker, ImageMarker):
                if marker.marker_type == MarkerType.BATCH_CLUSTER:
                    self._activate_cluster(marker)
                    event.accept()
                    return
                if self._marker_opened is not None:
                    self._marker_opened(marker)
                event.accept()
                return
        super().mouseDoubleClickEvent(event)

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802 - Qt signature
        if not self._constructed:
            return super().keyPressEvent(event)
        if event.key() == Qt.Key.Key_Escape and self.parts.preview_interaction.keyboard_marker_id is not None:
            self._clear_marker_interaction()
            event.accept()
            return
        if event.key() in {
            Qt.Key.Key_Left,
            Qt.Key.Key_Right,
            Qt.Key.Key_Up,
            Qt.Key.Key_Down,
        }:
            interactive = self._interactive_markers()
            if interactive:
                selected = self._marker_in_direction(interactive, event.key())
                if selected is not None:
                    self.parts.preview_interaction.keyboard_marker_id = selected.id
                    self._redraw_markers()
                    # Zoomed in, the next marker may be off-screen: go to it.
                    self._reveal_marker(selected)
                    if (
                        self._marker_selected is not None
                        and selected.marker_type != MarkerType.BATCH_CLUSTER
                    ):
                        self._marker_selected(selected)
                    event.accept()
                    return
        if (
            event.key() in {Qt.Key.Key_Return, Qt.Key.Key_Enter}
            and self.parts.preview_interaction.keyboard_marker_id is not None
        ):
            marker = next(
                (
                    value
                    for value in self._interactive_markers()
                    if value.id == self.parts.preview_interaction.keyboard_marker_id
                ),
                None,
            )
            if marker is not None:
                if marker.marker_type == MarkerType.BATCH_CLUSTER:
                    self._activate_cluster(marker)
                elif bool(marker.metadata.get("navigation_enabled", False)) and self._marker_opened is not None:
                    # ``navigation_enabled`` is the resolver's verdict carried
                    # on the marker. Enter must never open a marker the
                    # resolver declined to resolve uniquely, so the keyboard
                    # path cannot bypass what the pointer path obeys.
                    self._marker_opened(marker)
                elif self._marker_selected is not None:
                    self._marker_selected(marker)
                event.accept()
                return
        super().keyPressEvent(event)

    def _interactive_markers(self) -> list[ImageMarker]:
        return self.parts.preview_interaction.interactive_markers()

    def _marker_in_direction(
        self,
        markers: list[ImageMarker],
        key: Qt.Key,
    ) -> ImageMarker | None:
        return self.parts.preview_interaction.marker_in_direction(markers, key)

    def _activate_cluster(self, marker: ImageMarker) -> None:
        return self.parts.preview_interaction.activate_cluster(marker)

    def _show_cluster_member_popup(self, marker: ImageMarker) -> None:
        return self.parts.preview_interaction.show_cluster_member_popup(marker)

    def _select_popup_member(self, marker: ImageMarker) -> None:
        return self.parts.preview_interaction.select_popup_member(marker)

    def highlighted_exposure_marker_ids(self) -> frozenset[str]:
        return self.parts.preview_interaction.highlighted_exposure_marker_ids()

    def clear_exposure_highlights(self, *, redraw: bool = True) -> None:
        return self.parts.preview_interaction.clear_exposure_highlights(redraw=redraw)

    def _highlight_linked_exposures(self, marker: ImageMarker) -> None:
        return self.parts.preview_interaction.highlight_linked_exposures(marker)

    def _image_contains_view_position(self, position: QPoint) -> bool:
        return self.parts.preview_interaction.image_contains_view_position(position)

    def _clear_marker_interaction(self) -> None:
        return self.parts.preview_interaction.clear_marker_interaction()

    def _close_cluster_popup(self) -> None:
        return self.parts.preview_interaction.close_cluster_popup()

    def _mark_user_interacted(self, reason: str) -> None:
        return self.parts.preview_interaction.mark_user_interacted(reason)

    def _redraw_markers(self) -> None:
        return self.parts.marker_layer.redraw_markers()

    def _add_batch_label(self, marker: ImageMarker) -> None:
        return self.parts.marker_layer.add_batch_label(marker)

    def _add_exposure_highlight_edge(self, marker: ImageMarker, radius: float) -> None:
        return self.parts.marker_layer.add_exposure_highlight_edge(marker, radius)

    def _update_marker_detail(self) -> None:
        return self.parts.marker_layer.update_marker_detail()

    def _display_markers(self) -> list[ImageMarker]:
        return self.parts.marker_layer.display_markers()

    def _atlas_lod_display_markers(self, markers: list[ImageMarker]) -> list[ImageMarker]:
        return self.parts.marker_layer.atlas_lod_display_markers(markers)

    def _zoom_scale(self) -> float:
        return self.parts.preview_camera.transform_scale()

    def _add_cross_marker(self, marker: ImageMarker, radius: float, color: QColor) -> None:
        return self.parts.marker_layer.add_cross_marker(marker, radius, color)

    def _add_axis_crosshair(self, marker: ImageMarker, length: float, color: QColor) -> None:
        return self.parts.marker_layer.add_axis_crosshair(marker, length, color)

    def _project_marker(self, marker: ImageMarker) -> ImageMarker:
        return self.parts.marker_layer.project_marker(marker)

