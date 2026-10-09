"""Preview image for the image viewer."""
from __future__ import annotations
import math
from pathlib import Path
import time
from typing import Any
from PySide6.QtCore import QPointF, QRect, QRectF, QSize, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QImage, QPainter, QPixmap
from tomography_session_browser.domain.coerce import as_int
from tomography_session_browser.domain.models import Atlas, BatchPosition, MrcMetadata, Overview, SearchMap, SearchTile, TiltSeries
from tomography_session_browser.ui.motion import PictureFade, motion_enabled
from tomography_session_browser.parsers.xml_parser import find_first
from tomography_session_browser.services.pixel_size import batch_mrc_metadata_for_path
from tomography_session_browser.ui.theme import current_palette
from tomography_session_browser.presentation.design_tokens import SANS_FONT_NAME
import logging
LOGGER = logging.getLogger("tomography_session_browser.ui.image_viewer")
from tomography_session_browser.ui.viewer.constants import EXPORT_RENDER_SCALE, IMAGE_CROSSFADE_MS, MAX_EXPORT_DIMENSION_PX, MAX_EXPORT_PIXELS
from tomography_session_browser.ui.viewer.list_rows import path_size_bytes
from tomography_session_browser.ui.viewer.marker_paint import absolute_point, relative_point
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from tomography_session_browser.ui.viewer.preview_view import ImagePreviewView


def source_image_size_for_item(value: Any, displayed_path: Path | None = None) -> tuple[int, int] | None:
    if isinstance(value, BatchPosition):
        metadata = batch_mrc_metadata_for_path(value, displayed_path)
        return mrc_image_size(metadata) or metadata_image_size(value.metadata)
    if isinstance(value, SearchTile):
        return metadata_image_size(value.metadata.get("Tile.xml")) or mrc_image_size(value.mrc_metadata)
    if isinstance(value, SearchMap):
        return mrc_image_size(value.mrc_metadata) or metadata_image_size(value.metadata.get("SearchMap.xml")) or metadata_image_size(value.metadata)
    if isinstance(value, Atlas):
        return mrc_image_size(value.mrc_metadata) or metadata_image_size(value.metadata)
    if isinstance(value, Overview):
        return mrc_image_size(value.mrc_metadata) or metadata_image_size(value.metadata)
    if isinstance(value, TiltSeries):
        return mrc_image_size(value.mrc_metadata)
    metadata = getattr(value, "mrc_metadata", None)
    return mrc_image_size(metadata if isinstance(metadata, MrcMetadata) else None)


def mrc_image_size(metadata: MrcMetadata | None) -> tuple[int, int] | None:
    if metadata is None or metadata.nx is None or metadata.ny is None:
        return None
    return metadata.nx, metadata.ny


def metadata_image_size(metadata: Any) -> tuple[int, int] | None:
    for key in ("ImageSize", "ReadoutArea"):
        value = find_first(metadata, key)
        if isinstance(value, dict):
            width = as_int(value.get("width"))
            height = as_int(value.get("height"))
            if width is not None and height is not None:
                return width, height
    return None


class PreviewImage:
    """Preview image operations for one ImagePreviewView."""

    def __init__(self, view: ImagePreviewView) -> None:
        self.view = view

    def clear(self, message: str = "") -> None:
        self.view._stop_view_motion()
        # Nothing to dissolve into: a message or an empty canvas is not an image.
        self.crossfade_armed = False
        self.view._dismiss_crossfade()
        self.view._dismiss_pulse()
        # Bank the crop before the key is dropped, so a rebuild that lands the
        # user back on the same image returns them to where they were.
        self.view.remember_current_view()
        self.view._close_cluster_popup()
        self.scene.clear()
        self.pixmap_item = None
        self.view.parts.marker_layer.marker_items = []
        self.view.parts.marker_layer.last_display_markers = []
        self.view.parts.marker_layer.cluster_cache.clear()
        self.view.parts.preview_interaction.keyboard_marker_id = None
        self.view.parts.preview_interaction.exposure_highlights.clear()
        self.view.parts.preview_camera.zoom = 1.0
        self.current_image_key = None
        self.view.parts.preview_camera.has_user_interacted = False
        self.view.parts.preview_camera.pending_initial_fit_flag = False
        self.view.parts.preview_camera.fit_mode = False
        self.view.parts.preview_camera.last_view_range = None
        self.current_image_shape = None
        self.placeholder_item = None
        # Drop the outgoing image's scale. Without this the message below was
        # drawn through the previous shrink-to-fit transform and rendered a
        # few pixels high in the corner of the canvas.
        self.view.resetTransform()
        if message:
            theme = current_palette()
            text_item = self.scene.addText(message, QFont(SANS_FONT_NAME, 10))
            text_item.setDefaultTextColor(QColor(theme.text_muted))
            text_item.setVisible(self.placeholder_visible)
            self.placeholder_item = text_item
            # The scene rect still described the old image; shrink it to the
            # message so the view centres the text instead of a stale corner.
            self.scene.setSceneRect(text_item.boundingRect())
        else:
            self.scene.setSceneRect(QRectF())

    def refresh_theme(self) -> None:
        """Redraw the markers already shown in the new palette (plan F5.8a).

        Marker items bake their colours in when drawn, so they are drawn
        again from the same markers; nothing is derived again. The canvas
        message keeps the colour it was made with, so it is recoloured too.
        """

        if self.placeholder_item is not None:
            self.placeholder_item.setDefaultTextColor(QColor(current_palette().text_muted))
        self.view._redraw_markers()
        self.view.viewport().update()

    def set_placeholder_visible(self, visible: bool) -> None:
        """Show or hide the canvas message without changing the image state."""

        self.placeholder_visible = bool(visible)
        if self.placeholder_item is not None:
            self.placeholder_item.setVisible(self.placeholder_visible)

    @property
    def placeholder_text(self) -> str:
        item = self.placeholder_item
        return item.toPlainText() if item is not None and item.isVisible() else ""

    def load_raster_path(
        self,
        path: Path,
        preserve_view: bool = False,
        *,
        logical_image_key: Any | None = None,
        pyramid_level: int | None = None,
    ) -> list[str]:
        start = time.perf_counter()
        pixmap = QPixmap(str(path))
        load_seconds = time.perf_counter() - start
        LOGGER.debug(
            "Raster preview load path=%s size=%sx%s bytes=%s total=%.4fs",
            path,
            pixmap.width(),
            pixmap.height(),
            path_size_bytes(path),
            load_seconds,
        )
        if pixmap.isNull():
            self.view.clear("Preview unavailable")
            return [f"Could not load image preview: {path}"]
        self.view._set_pixmap(
            pixmap,
            preserve_view=preserve_view,
            logical_image_key=logical_image_key,
            pyramid_level=pyramid_level,
        )
        return []

    def has_image(self) -> bool:
        return self.pixmap_item is not None

    def displayed_image_shape(self) -> tuple[int, int] | None:
        return self.current_image_shape

    def set_image(
        self,
        image: QImage,
        preserve_view: bool = False,
        *,
        logical_image_key: Any | None = None,
        pyramid_level: int | None = None,
    ) -> None:
        self.view._set_pixmap(
            QPixmap.fromImage(image),
            preserve_view=preserve_view,
            logical_image_key=logical_image_key,
            pyramid_level=pyramid_level,
        )

    def rendered_image_rect(self) -> QRect | None:
        """Where the image is actually drawn, in viewport coordinates.

        A fitted image rarely fills the viewport — an image wider than it is
        tall leaves letterbox bands above and below. Overlaid chrome that
        belongs to the image (the scale bar) needs this rather than the
        widget rect, or it floats in the empty band.
        """

        if self.pixmap_item is None:
            return None
        mapped = self.view.mapFromScene(self.pixmap_item.sceneBoundingRect()).boundingRect()
        visible = mapped.intersected(self.view.viewport().rect())
        return visible if not visible.isEmpty() else None

    def render_current_view(self) -> tuple[QImage, float] | None:
        """Render the visible image and scene overlays without viewer chrome.

        ``QGraphicsView.render`` replays the scene rather than copying pixels
        from the desktop. Rendering at a larger target size preserves the
        vector marker edges and text antialiasing in the exported PNG.
        """

        source_rect = self.view.rendered_image_rect()
        if source_rect is None or source_rect.isEmpty():
            return None
        source_width = max(1, source_rect.width())
        source_height = max(1, source_rect.height())
        export_scale = min(
            EXPORT_RENDER_SCALE,
            MAX_EXPORT_DIMENSION_PX / source_width,
            MAX_EXPORT_DIMENSION_PX / source_height,
            math.sqrt(MAX_EXPORT_PIXELS / (source_width * source_height)),
        )
        export_scale = max(0.1, export_scale)
        target_size = QSize(
            max(1, round(source_width * export_scale)),
            max(1, round(source_height * export_scale)),
        )
        image = QImage(
            target_size,
            QImage.Format.Format_ARGB32_Premultiplied,
        )
        image.fill(QColor(Qt.GlobalColor.black))
        # Use a high-DPI paint device rather than only enlarging the render
        # target. Screen-fixed overlay items intentionally ignore the view
        # transform; without a device pixel ratio they stayed at 1x while the
        # microscope image became 2x, making numeric labels look half-sized.
        image.setDevicePixelRatio(export_scale)
        painter = QPainter(image)
        painter.setRenderHints(
            QPainter.RenderHint.Antialiasing
            | QPainter.RenderHint.TextAntialiasing
            | QPainter.RenderHint.SmoothPixmapTransform,
            True,
        )
        try:
            self.view.render(
                painter,
                QRectF(
                    0.0,
                    0.0,
                    target_size.width() / export_scale,
                    target_size.height() / export_scale,
                ),
                source_rect,
                Qt.AspectRatioMode.IgnoreAspectRatio,
            )
        finally:
            painter.end()
        # PNG consumers use the physical pixel matrix. Resetting the DPR keeps
        # the saved image dimensions and subsequent scale-bar composition in
        # that coordinate system.
        image.setDevicePixelRatio(1.0)
        return image, export_scale

    def set_pixmap(
        self,
        pixmap: QPixmap,
        preserve_view: bool = False,
        *,
        logical_image_key: Any | None = None,
        pyramid_level: int | None = None,
    ) -> None:
        # A new level of the same image (a pyramid upgrade) moves the scene
        # coordinates under a flight to a jump's target; fly on from here once
        # the new level is in.
        resume_arrival = (
            self.view.parts.preview_camera.arrival_in_flight
            if logical_image_key is not None and logical_image_key == self.current_image_key
            else None
        )
        self.view._stop_view_motion()
        self.view._close_cluster_popup()
        old_shape = self.current_image_shape
        old_range = self.view.view_range()
        old_rect = self.pixmap_item.boundingRect() if self.pixmap_item is not None else None
        old_center = self.view.mapToScene(self.view.viewport().rect().center()) if self.pixmap_item is not None else None
        old_transform = self.view.transform()
        old_relative_center = relative_point(old_rect, old_center) if old_rect is not None and old_center is not None else None
        same_logical_image = logical_image_key is not None and logical_image_key == self.current_image_key
        is_new_logical_image = logical_image_key is None or not same_logical_image
        # An armed crossfade is spent on this image whatever it is, so it can
        # never carry over to a later frame change.
        dissolve_from = None
        if self.crossfade_armed:
            self.crossfade_armed = False
            if is_new_logical_image and self.view.isVisible():
                dissolve_from = self.view._image_layer_snapshot()
        if is_new_logical_image:
            # The ring belonged to the outgoing image.
            self.view._dismiss_pulse()
            # Bank the outgoing image's crop before its key is replaced.
            self.view.remember_current_view()
            self.current_image_key = logical_image_key
            self.view.parts.preview_camera.has_user_interacted = False
            self.view.parts.preview_camera.pending_initial_fit_flag = True
            self.view.parts.preview_camera.fit_mode = False
            self.view.parts.preview_camera.zoom = 1.0
        preserve_current_view = (preserve_view or same_logical_image) and old_center is not None
        self.scene.clear()
        self.placeholder_item = None
        self.view.parts.marker_layer.marker_items = []
        self.pixmap_item = self.scene.addPixmap(pixmap)
        self.pixmap_item.setTransformationMode(Qt.TransformationMode.SmoothTransformation)
        self.pixmap_item.setZValue(0)
        self.current_image_shape = (pixmap.height(), pixmap.width())
        LOGGER.debug(
            "setSceneRect logical_key=%s rect=%s",
            self.current_image_key,
            self.pixmap_item.boundingRect(),
        )
        self.scene.setSceneRect(self.pixmap_item.boundingRect())
        if preserve_current_view:
            LOGGER.debug("setTransform preserving logical_key=%s", self.current_image_key)
            self.view.setTransform(old_transform)
            if old_rect is not None and old_rect.width() > 0 and old_rect.height() > 0:
                new_rect = self.pixmap_item.boundingRect()
                scale_x = old_rect.width() / (new_rect.width() or old_rect.width())
                scale_y = old_rect.height() / (new_rect.height() or old_rect.height())
                LOGGER.debug(
                    "setRange equivalent preserve logical_key=%s scale_x=%s scale_y=%s old_range=%s",
                    self.current_image_key,
                    scale_x,
                    scale_y,
                    old_range,
                )
                self.view.scale(scale_x, scale_y)
            new_center = absolute_point(self.pixmap_item.boundingRect(), old_relative_center) if old_relative_center is not None else old_center
            self.view.centerOn(new_center)
            self.view.parts.preview_camera.pending_initial_fit_flag = False
        elif not (
            is_new_logical_image
            and logical_image_key is not None
            and self.view._restore_remembered_view(logical_image_key)
        ):
            self.view.maybe_fit_initial_view()
        new_range = self.view.view_range()
        self.view.parts.preview_camera.last_view_range = new_range
        self.view._redraw_markers()
        if self.view._zoom_changed is not None:
            self.view._zoom_changed(self.view.parts.preview_camera.zoom)
        if dissolve_from is not None:
            self.view._start_crossfade(dissolve_from)
        if resume_arrival is not None and self.view.parts.preview_camera.pending_arrival is None:
            self.view.parts.preview_camera.pending_arrival = resume_arrival
        if self.view.parts.preview_camera.pending_arrival is not None:
            self.view.parts.preview_camera.arrival_timer.start(0)
        LOGGER.debug(
            "set_image: logical_key=%s pyramid_level=%s new_logical=%s "
            "user_interacted=%s pending_initial_fit=%s preserve_view=%s "
            "old_shape=%s new_shape=%s old_range=%s new_range=%s",
            self.current_image_key,
            pyramid_level,
            is_new_logical_image,
            self.view.parts.preview_camera.has_user_interacted,
            self.view.parts.preview_camera.pending_initial_fit_flag,
            preserve_current_view,
            old_shape,
            self.current_image_shape,
            old_range,
            new_range,
        )

    def crossfade_next_image(self) -> None:
        """Dissolve into the next new image instead of cutting to it.

        For selecting another item: the image on screen stays while the next
        one decodes (as before), then fades out over it. Spent by the very
        next image set, whatever it is, so a later frame change never fades.
        """

        self.crossfade_armed = self.pixmap_item is not None and motion_enabled(self.view)

    def image_layer_snapshot(self) -> QPixmap | None:
        """The displayed image alone, exactly as the viewport shows it now.

        Markers are left out on purpose: the next item's markers are set
        before its image arrives, so a grab of the viewport would show them
        over the wrong image. They appear with the new image instead.
        """

        if self.pixmap_item is None:
            return None
        viewport = self.view.viewport()
        ratio = viewport.devicePixelRatioF()
        picture = QPixmap(max(1, round(viewport.width() * ratio)), max(1, round(viewport.height() * ratio)))
        picture.setDevicePixelRatio(ratio)
        picture.fill(self.view.backgroundBrush().color())
        painter = QPainter(picture)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        painter.setTransform(self.pixmap_item.sceneTransform() * self.view.viewportTransform())
        painter.drawPixmap(QPointF(0.0, 0.0), self.pixmap_item.pixmap())
        painter.end()
        return picture

    def start_crossfade(self, picture: QPixmap) -> None:
        self.view._dismiss_crossfade()
        viewport = self.view.viewport()
        fade = PictureFade(viewport, viewport.rect(), picture, duration_ms=IMAGE_CROSSFADE_MS)
        self.crossfade = fade
        # One turn later, once the caller has finished with the new image
        # (its marker selection), picture the new scene and begin. Until then
        # the fade shows the old image, which is exactly what was on screen.
        QTimer.singleShot(0, lambda: self.view._begin_crossfade(fade))

    def begin_crossfade(self, fade: PictureFade) -> None:
        if self.crossfade is not fade:
            return  # dismissed or replaced in the meantime
        try:
            fade.start(self.view._scene_picture())
        except RuntimeError:
            self.crossfade = None  # the view went away

    def scene_picture(self) -> QPixmap:
        """The scene (image and markers) as the viewport shows it, without
        the widgets floating over it."""

        viewport = self.view.viewport()
        ratio = viewport.devicePixelRatioF()
        picture = QPixmap(max(1, round(viewport.width() * ratio)), max(1, round(viewport.height() * ratio)))
        picture.setDevicePixelRatio(ratio)
        picture.fill(self.view.backgroundBrush().color())
        painter = QPainter(picture)
        painter.setRenderHints(
            QPainter.RenderHint.Antialiasing
            | QPainter.RenderHint.TextAntialiasing
            | QPainter.RenderHint.SmoothPixmapTransform,
            True,
        )
        self.view.render(painter, QRectF(0.0, 0.0, viewport.width(), viewport.height()), viewport.rect())
        painter.end()
        return picture

    def dismiss_crossfade(self) -> None:
        fade, self.crossfade = self.crossfade, None
        if fade is not None:
            try:
                fade.dismiss()
            except RuntimeError:
                pass  # it had already finished and deleted itself

    @property
    def crossfade_in_progress(self) -> bool:
        fade = self.crossfade
        try:
            return fade is not None and fade.isVisible()
        except RuntimeError:
            return False

