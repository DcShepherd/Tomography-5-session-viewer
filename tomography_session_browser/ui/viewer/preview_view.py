"""Preview view for the image viewer."""
from __future__ import annotations
from tomography_session_browser.presentation.help_content import LocalBinding, InteractionKind
from collections.abc import Callable, Iterable
import math
from pathlib import Path
from typing import Any
from PySide6.QtCore import QPointF, QRect, Qt
from PySide6.QtGui import QImage, QKeyEvent, QMouseEvent, QPainter, QWheelEvent
from PySide6.QtWidgets import QGraphicsView, QWidget
from tomography_session_browser.domain.markers import ImageMarker
from tomography_session_browser.ui.motion import MarkerPulse
import logging
LOGGER = logging.getLogger("tomography_session_browser.ui.image_viewer")
from tomography_session_browser.ui.viewer.constants import WHEEL_ZOOM_BASE, WHEEL_ZOOM_UNITS_PER_PIXEL
from tomography_session_browser.ui.viewer.marker_layer import MarkerLayer
from tomography_session_browser.ui.viewer.preview_camera import PreviewCamera
from tomography_session_browser.ui.viewer.preview_image import PreviewImage
from tomography_session_browser.ui.viewer.preview_interaction import PreviewInteraction
from tomography_session_browser.ui.viewer.parts import PreviewParts


class ImagePreviewView(QGraphicsView):

    def __init__(self, parent: QWidget | None = None) -> None:

        self._constructed = False
        super().__init__(parent)

        preview_interaction = PreviewInteraction(self)
        preview_camera = PreviewCamera(self, interaction=preview_interaction)
        preview_image = PreviewImage(self, camera=preview_camera, interaction=preview_interaction)
        self.parts = PreviewParts(
            marker_layer=MarkerLayer(self, image=preview_image, camera=preview_camera, interaction=preview_interaction),
            preview_camera=preview_camera,
            preview_image=preview_image,
            preview_interaction=preview_interaction,
        )

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
        return self.parts.preview_camera.set_zoom_changed_callback(callback)

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

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt signature
        if not self._constructed:
            return super().resizeEvent(event)
        super().resizeEvent(event)
        self.parts.preview_camera.viewport_resized()

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
        self.parts.preview_camera.zoom_toward(factor, event.position())
        event.accept()

    def ease_next_refit(self) -> None:
        return self.parts.preview_camera.ease_next_refit()

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
        self.parts.preview_camera.retry_arrival()

    @property
    def pulse(self) -> MarkerPulse | None:
        return self.parts.preview_camera.pulse

    def crossfade_next_image(self) -> None:
        return self.parts.preview_image.crossfade_next_image()

    @property
    def crossfade_in_progress(self) -> bool:
        return self.parts.preview_image.transition.in_progress

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if not self._constructed or not self.parts.preview_interaction.mouse_pressed(event):
            super().mousePressEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:  # noqa: N802 - Qt signature
        super().mouseReleaseEvent(event)
        if self._constructed:
            self.parts.preview_interaction.mouse_released()

    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:
        if not self._constructed or not self.parts.preview_interaction.mouse_double_clicked(event):
            super().mouseDoubleClickEvent(event)

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802 - Qt signature
        if not self._constructed or not self.parts.preview_interaction.key_pressed(event):
            super().keyPressEvent(event)

    def highlighted_exposure_marker_ids(self) -> frozenset[str]:
        return self.parts.preview_interaction.highlighted_exposure_marker_ids()

    def clear_exposure_highlights(self, *, redraw: bool = True) -> None:
        return self.parts.preview_interaction.clear_exposure_highlights(redraw=redraw)



# Contextual keys handled by this module; consumed by the shared Help sheet.
LOCAL_KEYS = (
    LocalBinding('viewer.preview_view.image-markers-left', 'Image markers', InteractionKind.KEYBOARD, ('Left', 'Right', 'Up', 'Down'), 'Select the next marker in that direction'),
    LocalBinding('viewer.preview_view.image-markers-enter', 'Image markers', InteractionKind.KEYBOARD, ('Enter', 'Return'), 'Open a uniquely resolved destination, or select an unresolved marker'),
    LocalBinding('viewer.preview_view.image-markers-esc', 'Image markers', InteractionKind.KEYBOARD, ('Esc',), 'Clear marker interaction'),
    LocalBinding('image.zoom', 'Image view', InteractionKind.MOUSE, ('Wheel',), 'Zoom the image around the pointer'),
    LocalBinding('image.pan', 'Image view', InteractionKind.MOUSE, ('Drag',), 'Drag the image to pan; overlay markers keep their own selection action'),
    LocalBinding('image.marker-open', 'Image markers', InteractionKind.MOUSE, ('Double-click',), 'Open a uniquely resolved marker destination; unresolved markers keep their explanation'),
)
