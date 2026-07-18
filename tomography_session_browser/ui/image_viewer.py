from __future__ import annotations

from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass
import logging
import math
from pathlib import Path
import time
from typing import Any

from PIL import Image
from PySide6.QtCore import QObject, QPointF, QRectF, QRunnable, QSize, Qt, QThreadPool, QTimer, Signal, Slot
from PySide6.QtGui import QBrush, QColor, QFont, QImage, QKeySequence, QMouseEvent, QPainter, QPainterPath, QPen, QPixmap, QPolygonF, QShortcut, QWheelEvent
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QGraphicsItem,
    QGraphicsPixmapItem,
    QGraphicsScene,
    QGraphicsView,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QHeaderView,
    QPushButton,
    QSizePolicy,
    QSlider,
    QSplitter,
    QStyle,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QStyleOptionSlider,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from tomography_session_browser.domain.display_names import format_overview_display_name
from tomography_session_browser.domain.markers import ImageMarker, MarkerType
from tomography_session_browser.domain.models import Atlas, BatchPosition, MrcMetadata, Overview, SearchMap, SearchTile, TiltSeries
from tomography_session_browser.parsers.mrc_parser import MrcPreviewSource, NORMALISATION
from tomography_session_browser.parsers.xml_parser import find_first
from tomography_session_browser.services.item_status import ItemListStatus
from tomography_session_browser.services.batch_label_service import (
    LABEL_STROKE_WIDTH_PX,
    batch_label_screen_font_size_px,
)
from tomography_session_browser.services.tilt_angle_service import explicit_stack_order_tilt_angles, stack_order_tilt_angles
from tomography_session_browser.services.timeline_service import build_acquisition_timeline
from tomography_session_browser.ui.animations import fade_in
from tomography_session_browser.ui.icons import themed_icon
from tomography_session_browser.ui.widgets.elided_label import ElidedLabel


VIEWER_OBJECT_ROLE = int(Qt.ItemDataRole.UserRole)
LIST_PRIMARY_ROLE = VIEWER_OBJECT_ROLE + 1
LIST_SUMMARY_ROLE = VIEWER_OBJECT_ROLE + 2
LIST_STATUS_ROLE = VIEWER_OBJECT_ROLE + 3
LIST_TOOLTIP_ROLE = VIEWER_OBJECT_ROLE + 4
MAX_PREVIEW_DIMENSION = 2048
FIRST_ZOOM_PREVIEW_DIMENSION = 4096
MRC_JPEG_FALLBACK_GRACE_MS = 220
PYRAMID_LEVELS = (2048, 4096, 8192)
LOGGER = logging.getLogger(__name__)
MEDIUM_DETAIL_SCALE = 0.9
HIGH_DETAIL_SCALE = 1.6
MIN_SCALE_BAR_SCREEN_PX = 58.0
MAX_SCALE_BAR_SCREEN_PX = 190.0
FLOATING_CONTROL_INSET_PX = 18
# Keep every bottom-floating viewer control on the same baseline so the scale
# bar and zoom strip feel anchored to one frame edge instead of separate panes.
FLOATING_CONTROL_BOTTOM_INSET_PX = 24
DEFAULT_VISIBLE_MARKER_TYPES = (
    MarkerType.SEARCH_MAP,
    MarkerType.OVERVIEW,
    MarkerType.BATCH_POSITION,
    MarkerType.TILT_SERIES,
    MarkerType.LINK_LINE,
    MarkerType.EXPOSURE_AREA,
    MarkerType.CAMERA_FOV,
    MarkerType.BATCH_LABEL,
    MarkerType.TRACKING_AREA,
    MarkerType.FOCUS_AREA,
    MarkerType.CONDITION_AREA,
    MarkerType.STAGE_CROSSHAIR,
)
MARKER_TYPES_WITH_CONTROLS = (
    MarkerType.OVERVIEW,
    MarkerType.SEARCH_MAP,
    MarkerType.BATCH_POSITION,
    MarkerType.EXPOSURE_AREA,
    MarkerType.CAMERA_FOV,
    MarkerType.BATCH_LABEL,
    MarkerType.FOCUS_AREA,
    MarkerType.TRACKING_AREA,
)
EXPOSURE_MARKER_HIGHLIGHT_WIDTH = 4.0
MARKER_TYPE_LABELS = {
    MarkerType.SEARCH_MAP: "Search map",
    MarkerType.OVERVIEW: "Overview",
    MarkerType.BATCH_POSITION: "Batch position",
    MarkerType.TILT_SERIES: "Tilt series",
    MarkerType.TEMPLATE_AREA: "Template",
    MarkerType.EXPOSURE_AREA: "Exposure area",
    MarkerType.CAMERA_FOV: "Camera FOV",
    MarkerType.BATCH_LABEL: "Batch labels",
    MarkerType.TRACKING_AREA: "Tracking",
    MarkerType.FOCUS_AREA: "Focus",
    MarkerType.CONDITION_AREA: "Condition",
    MarkerType.LINK_LINE: "Link",
    MarkerType.STAGE_CROSSHAIR: "Other",
}


@dataclass(frozen=True, slots=True)
class PreviewSources:
    primary: Path | None
    fallback: Path | None = None


@dataclass(frozen=True, slots=True)
class ViewerNavigationAction:
    key: str
    label: str
    enabled: bool = True
    tooltip: str = ""


@dataclass(slots=True)
class CachedPreview:
    image: QImage
    slice_count: int
    warnings: list[str]
    estimated_bytes: int


PreviewCacheKey = tuple[Path, int, int, float, str]


class PreviewLruCache:
    def __init__(self, max_items: int = 32, max_bytes: int = 512 * 1024 * 1024) -> None:
        self._items: OrderedDict[PreviewCacheKey, CachedPreview] = OrderedDict()
        self._max_items = max_items
        self._max_bytes = max_bytes
        self._bytes = 0

    def get(self, key: PreviewCacheKey) -> CachedPreview | None:
        item = self._items.get(key)
        LOGGER.debug("MRC preview cache %s key=%s", "hit" if item else "miss", key)
        if item is None:
            return None
        self._items.move_to_end(key)
        return item

    def put(self, key: PreviewCacheKey, item: CachedPreview) -> None:
        old = self._items.pop(key, None)
        if old is not None:
            self._bytes -= old.estimated_bytes
        self._items[key] = item
        self._bytes += item.estimated_bytes
        self._evict()

    def clear(self) -> None:
        self._items.clear()
        self._bytes = 0
        LOGGER.debug("MRC preview cache cleared")

    def _evict(self) -> None:
        while len(self._items) > self._max_items or self._bytes > self._max_bytes:
            key, item = self._items.popitem(last=False)
            self._bytes -= item.estimated_bytes
            LOGGER.debug("MRC preview cache evicted key=%s estimated_bytes=%s", key, item.estimated_bytes)


MRC_PREVIEW_CACHE = PreviewLruCache()
MRC_SOURCE_CACHE: dict[Path, MrcPreviewSource] = {}
MRC_PREFETCH_KEYS: set[PreviewCacheKey] = set()


def clear_preview_cache() -> None:
    MRC_PREVIEW_CACHE.clear()
    MRC_SOURCE_CACHE.clear()
    MRC_PREFETCH_KEYS.clear()


class _MrcLoadSignals(QObject):
    loaded = Signal(int, Path, int, QImage, int, list, int)
    failed = Signal(int, Path, int, int, list)


class _MrcLoadTask(QRunnable):
    def __init__(self, request_id: int, path: Path, slice_index: int, max_size: int, signals: _MrcLoadSignals) -> None:
        super().__init__()
        self._request_id = request_id
        self._path = path
        self._slice_index = slice_index
        self._max_size = max_size
        self._signals = signals

    @Slot()
    def run(self) -> None:
        cache_key = _preview_cache_key(self._path, max(0, self._slice_index), self._max_size)
        try:
            source = _mrc_source(self._path)
            preview = source.get_frame_preview(self._slice_index, max_size=self._max_size)
        except Exception as exc:  # pragma: no cover - exercised through UI thread safety
            LOGGER.warning("MRC preview load failed path=%s slice=%s: %s", self._path, self._slice_index, exc)
            MRC_PREFETCH_KEYS.discard(cache_key)
            self._signals.failed.emit(self._request_id, self._path, max(0, self._slice_index), 0, [str(exc)])
            return
        if preview.image is None:
            MRC_PREFETCH_KEYS.discard(cache_key)
            self._signals.failed.emit(self._request_id, self._path, preview.slice_index, preview.slice_count, preview.warnings)
            return
        image = _pil_to_qimage(preview.image)
        estimated_bytes = image.sizeInBytes()
        cache_key = _preview_cache_key(self._path, preview.slice_index, self._max_size)
        MRC_PREVIEW_CACHE.put(
            cache_key,
            CachedPreview(image, preview.slice_count, preview.warnings, estimated_bytes),
        )
        MRC_PREFETCH_KEYS.discard(cache_key)
        self._signals.loaded.emit(
            self._request_id,
            self._path,
            preview.slice_index,
            image,
            preview.slice_count,
            preview.warnings,
            estimated_bytes,
        )
        LOGGER.debug(
            "MRC preview ready path=%s slice=%s preview_shape=%s dtype=uint8 estimated_bytes=%s",
            self._path,
            preview.slice_index,
            preview.preview_shape,
            estimated_bytes,
        )


class ImagePreviewView(QGraphicsView):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._scene = QGraphicsScene(self)
        self._pixmap_item: QGraphicsPixmapItem | None = None
        self._zoom = 1.0
        self._zoom_changed: Callable[[float], None] | None = None
        self._current_image_key: Any | None = None
        self._has_user_interacted = False
        self._pending_initial_fit = False
        self._last_view_range: tuple[tuple[float, float], tuple[float, float]] | None = None
        self._current_image_shape: tuple[int, int] | None = None
        self._markers: list[ImageMarker] = []
        self._marker_items: list[QGraphicsItem] = []
        self._marker_selected: Callable[[ImageMarker], None] | None = None
        self._marker_opened: Callable[[ImageMarker], None] | None = None
        self._visible_marker_types: set[str] = set(DEFAULT_VISIBLE_MARKER_TYPES)
        self.setScene(self._scene)
        self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setResizeAnchor(QGraphicsView.ViewportAnchor.AnchorViewCenter)
        self.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        self.setBackgroundBrush(Qt.GlobalColor.black)

    def clear(self, message: str = "") -> None:
        self._scene.clear()
        self._pixmap_item = None
        self._marker_items = []
        self._zoom = 1.0
        self._current_image_key = None
        self._has_user_interacted = False
        self._pending_initial_fit = False
        self._last_view_range = None
        self._current_image_shape = None
        if message:
            from tomography_session_browser.ui.theme import SANS_FONT_NAME, current_palette

            theme = current_palette()
            text_item = self._scene.addText(message, QFont(SANS_FONT_NAME, 10))
            text_item.setDefaultTextColor(QColor(theme.text_muted))

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
            _path_size_bytes(path),
            load_seconds,
        )
        if pixmap.isNull():
            self.clear("Preview unavailable")
            return [f"Could not load image preview: {path}"]
        self._set_pixmap(
            pixmap,
            preserve_view=preserve_view,
            logical_image_key=logical_image_key,
            pyramid_level=pyramid_level,
        )
        return []

    def has_image(self) -> bool:
        return self._pixmap_item is not None

    def zoom_scale(self) -> float:
        return self._zoom_scale()

    def displayed_image_shape(self) -> tuple[int, int] | None:
        return self._current_image_shape

    def set_zoom_changed_callback(self, callback: Callable[[float], None]) -> None:
        self._zoom_changed = callback

    def set_marker_selected_callback(self, callback: Callable[[ImageMarker], None]) -> None:
        self._marker_selected = callback

    def set_marker_opened_callback(self, callback: Callable[[ImageMarker], None]) -> None:
        self._marker_opened = callback

    def set_marker_type_visible(self, marker_type: str, visible: bool) -> None:
        if visible:
            self._visible_marker_types.add(marker_type)
        else:
            self._visible_marker_types.discard(marker_type)
        self._redraw_markers()

    def set_markers(self, markers: list[ImageMarker], selected_marker_id: str | None = None) -> None:
        self._markers = [
            ImageMarker(
                id=marker.id,
                marker_type=marker.marker_type,
                linked_object_id=marker.linked_object_id,
                source_object_id=marker.source_object_id,
                x=marker.x,
                y=marker.y,
                radius=marker.radius,
                bbox=marker.bbox,
                polygon=marker.polygon,
                label=marker.label,
                tooltip=marker.tooltip,
                status=marker.status,
                visible=marker.visible,
                selected=marker.selected or marker.id == selected_marker_id,
                unresolved=marker.unresolved,
                metadata=dict(marker.metadata),
            )
            for marker in markers
        ]
        self._redraw_markers()

    def set_image(
        self,
        image: QImage,
        preserve_view: bool = False,
        *,
        logical_image_key: Any | None = None,
        pyramid_level: int | None = None,
    ) -> None:
        self._set_pixmap(
            QPixmap.fromImage(image),
            preserve_view=preserve_view,
            logical_image_key=logical_image_key,
            pyramid_level=pyramid_level,
        )

    def zoom_in(self) -> None:
        self._scale_by_center(1.25)

    def zoom_out(self) -> None:
        self._scale_by_center(0.8)

    def fit_image(self) -> None:
        self._mark_user_interacted("fit")
        self._fit_image()

    def maybe_fit_initial_view(self) -> None:
        if not self._pending_initial_fit or self._has_user_interacted:
            LOGGER.debug(
                "skip initial fit pending=%s user_interacted=%s logical_key=%s",
                self._pending_initial_fit,
                self._has_user_interacted,
                self._current_image_key,
            )
            return
        self._fit_image()
        self._pending_initial_fit = False

    def view_range(self) -> tuple[tuple[float, float], tuple[float, float]] | None:
        if self._pixmap_item is None:
            return None
        rect = self.mapToScene(self.viewport().rect()).boundingRect()
        return ((rect.left(), rect.right()), (rect.top(), rect.bottom()))

    @property
    def current_logical_image_key(self) -> Any | None:
        return self._current_image_key

    @property
    def has_user_interacted(self) -> bool:
        return self._has_user_interacted

    @property
    def pending_initial_fit(self) -> bool:
        return self._pending_initial_fit

    def _fit_image(self) -> None:
        if self._pixmap_item is None:
            return
        LOGGER.debug("fit_to_view logical_key=%s", self._current_image_key)
        self.fitInView(self._pixmap_item, Qt.AspectRatioMode.KeepAspectRatio)
        self._zoom = 1.0
        self._last_view_range = self.view_range()
        self._redraw_markers()
        if self._zoom_changed is not None:
            self._zoom_changed(self._zoom)

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt signature
        super().resizeEvent(event)
        if self._zoom_changed is not None:
            QTimer.singleShot(0, lambda: self._zoom_changed(self._zoom))

    def wheelEvent(self, event: QWheelEvent) -> None:
        factor = 1.25 if event.angleDelta().y() > 0 else 0.8
        LOGGER.debug(
            "wheel zoom logical_key=%s factor=%s position=%s",
            self._current_image_key,
            factor,
            event.position(),
        )
        self._scale_by(factor, self.mapToScene(event.position().toPoint()))
        event.accept()

    def _set_pixmap(
        self,
        pixmap: QPixmap,
        preserve_view: bool = False,
        *,
        logical_image_key: Any | None = None,
        pyramid_level: int | None = None,
    ) -> None:
        old_shape = self._current_image_shape
        old_range = self.view_range()
        old_rect = self._pixmap_item.boundingRect() if self._pixmap_item is not None else None
        old_center = self.mapToScene(self.viewport().rect().center()) if self._pixmap_item is not None else None
        old_transform = self.transform()
        old_relative_center = _relative_point(old_rect, old_center) if old_rect is not None and old_center is not None else None
        same_logical_image = logical_image_key is not None and logical_image_key == self._current_image_key
        is_new_logical_image = logical_image_key is None or not same_logical_image
        if is_new_logical_image:
            self._current_image_key = logical_image_key
            self._has_user_interacted = False
            self._pending_initial_fit = True
            self._zoom = 1.0
        preserve_current_view = (preserve_view or same_logical_image) and old_center is not None
        self._scene.clear()
        self._marker_items = []
        self._pixmap_item = self._scene.addPixmap(pixmap)
        self._pixmap_item.setTransformationMode(Qt.TransformationMode.SmoothTransformation)
        self._pixmap_item.setZValue(0)
        self._current_image_shape = (pixmap.height(), pixmap.width())
        LOGGER.debug(
            "setSceneRect logical_key=%s rect=%s",
            self._current_image_key,
            self._pixmap_item.boundingRect(),
        )
        self._scene.setSceneRect(self._pixmap_item.boundingRect())
        if preserve_current_view:
            LOGGER.debug("setTransform preserving logical_key=%s", self._current_image_key)
            self.setTransform(old_transform)
            if old_rect is not None and old_rect.width() > 0 and old_rect.height() > 0:
                new_rect = self._pixmap_item.boundingRect()
                scale_x = old_rect.width() / (new_rect.width() or old_rect.width())
                scale_y = old_rect.height() / (new_rect.height() or old_rect.height())
                LOGGER.debug(
                    "setRange equivalent preserve logical_key=%s scale_x=%s scale_y=%s old_range=%s",
                    self._current_image_key,
                    scale_x,
                    scale_y,
                    old_range,
                )
                self.scale(scale_x, scale_y)
            new_center = _absolute_point(self._pixmap_item.boundingRect(), old_relative_center) if old_relative_center is not None else old_center
            self.centerOn(new_center)
            self._pending_initial_fit = False
        else:
            self.maybe_fit_initial_view()
        new_range = self.view_range()
        self._last_view_range = new_range
        self._redraw_markers()
        if self._zoom_changed is not None:
            self._zoom_changed(self._zoom)
        LOGGER.debug(
            "set_image: logical_key=%s pyramid_level=%s new_logical=%s "
            "user_interacted=%s pending_initial_fit=%s preserve_view=%s "
            "old_shape=%s new_shape=%s old_range=%s new_range=%s",
            self._current_image_key,
            pyramid_level,
            is_new_logical_image,
            self._has_user_interacted,
            self._pending_initial_fit,
            preserve_current_view,
            old_shape,
            self._current_image_shape,
            old_range,
            new_range,
        )

    def _scale_by(self, factor: float, anchor_scene: QPointF) -> None:
        if self._pixmap_item is None:
            return
        self._mark_user_interacted("wheel_zoom")
        anchor_view = self.mapFromScene(anchor_scene)
        self._zoom *= factor
        self.scale(factor, factor)
        shifted_anchor = self.mapToScene(anchor_view)
        delta = shifted_anchor - anchor_scene
        current_center = self.mapToScene(self.viewport().rect().center())
        self.centerOn(current_center - delta)
        if self._zoom_changed is not None:
            self._zoom_changed(self._zoom)
        self._redraw_markers()

    def _scale_by_center(self, factor: float) -> None:
        if self._pixmap_item is None:
            return
        self._mark_user_interacted("button_zoom")
        anchor_scene = self.mapToScene(self.viewport().rect().center())
        self._zoom *= factor
        self.scale(factor, factor)
        self.centerOn(anchor_scene)
        if self._zoom_changed is not None:
            self._zoom_changed(self._zoom)
        self._redraw_markers()

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if self._pixmap_item is not None and event.button() == Qt.MouseButton.LeftButton:
            item = self.itemAt(event.position().toPoint())
            marker = item.data(0) if item is not None else None
            if isinstance(marker, ImageMarker):
                if self._marker_selected is not None:
                    self._marker_selected(marker)
                event.accept()
                return
            self._mark_user_interacted("pan")
        super().mousePressEvent(event)

    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:
        if self._pixmap_item is not None and event.button() == Qt.MouseButton.LeftButton:
            item = self.itemAt(event.position().toPoint())
            marker = item.data(0) if item is not None else None
            if isinstance(marker, ImageMarker):
                if self._marker_opened is not None:
                    self._marker_opened(marker)
                event.accept()
                return
        super().mouseDoubleClickEvent(event)

    def _mark_user_interacted(self, reason: str) -> None:
        if not self._has_user_interacted or self._pending_initial_fit:
            LOGGER.debug(
                "viewer user interaction logical_key=%s reason=%s pending_initial_fit=%s",
                self._current_image_key,
                reason,
                self._pending_initial_fit,
            )
        self._has_user_interacted = True
        self._pending_initial_fit = False

    def _redraw_markers(self) -> None:
        for item in self._marker_items:
            if item.scene() is self._scene:
                self._scene.removeItem(item)
        self._marker_items = []
        if self._pixmap_item is None:
            return
        for marker in self._display_markers():
            if marker.marker_type == MarkerType.BATCH_LABEL:
                self._add_batch_label(marker)
                continue
            radius = marker.radius or 8.0
            pen_color, fill_color = _marker_colors(marker)
            pen = QPen(pen_color, 2.0 if marker.selected else 1.4)
            # Markers approximated from MRC stage metadata get a
            # dashed stroke so the user can tell them apart from
            # recorded acquisitions. The PDF render does the same.
            if marker.metadata and marker.metadata.get("inferred_from_stage"):
                pen.setStyle(Qt.PenStyle.DashLine)
            brush = QBrush(fill_color)
            if marker.bbox is not None:
                x, y, width, height = marker.bbox
                if marker.marker_type == MarkerType.CAMERA_FOV:
                    pen.setCosmetic(True)
                    fallback_fill = bool(marker.metadata.get("filled_fallback")) if marker.metadata else False
                    fill = QBrush(fill_color if fallback_fill else Qt.BrushStyle.NoBrush)
                else:
                    fill = QBrush(QColor(fill_color.red(), fill_color.green(), fill_color.blue(), 35))
                shape = self._scene.addRect(x, y, width, height, pen, fill)
            elif marker.polygon and len(marker.polygon) == 2:
                start, end = marker.polygon
                shape = self._scene.addLine(start[0], start[1], end[0], end[1], pen)
            elif marker.polygon:
                shape = self._scene.addPolygon(QPolygonF([QPointF(x, y) for x, y in marker.polygon]), pen, QBrush(QColor(fill_color.red(), fill_color.green(), fill_color.blue(), 35)))
            else:
                assert marker.x is not None and marker.y is not None
                shape = self._scene.addEllipse(marker.x - radius, marker.y - radius, radius * 2, radius * 2, pen, brush)
            if marker.selected and marker.marker_type == MarkerType.EXPOSURE_AREA:
                self._add_exposure_highlight_edge(marker, radius)
            if marker.marker_type == MarkerType.CAMERA_FOV:
                shape.setZValue(14)
            else:
                shape.setZValue(20 if marker.selected else 15)
            shape.setData(0, marker)
            shape.setToolTip(marker.tooltip or marker.label or marker.marker_type)
            self._marker_items.append(shape)
            if marker.marker_type in {MarkerType.BATCH_POSITION, MarkerType.TILT_SERIES} and marker.x is not None and marker.y is not None:
                self._add_cross_marker(marker, radius, pen_color)
            if marker.marker_type == MarkerType.STAGE_CROSSHAIR and marker.x is not None and marker.y is not None:
                # A larger axis-aligned crosshair so the stage origin reads
                # differently from a batch / tilt marker.
                self._add_axis_crosshair(marker, radius * 2.4, pen_color)
            if marker.label:
                from tomography_session_browser.ui.theme import SANS_FONT_NAME, current_palette

                theme = current_palette()
                label = self._scene.addText(marker.label, QFont(SANS_FONT_NAME, 9))
                label.setDefaultTextColor(QColor(theme.text_strong))
                anchor = _marker_label_anchor(marker)
                label.setPos(anchor.x() + radius + 4, anchor.y() - radius - 2)
                label.setZValue(21 if marker.selected else 16)
                label.setData(0, marker)
                label.setData(1, "label")
                label.setToolTip(marker.tooltip or marker.label)
                self._marker_items.append(label)
        self._update_marker_detail()

    def _add_batch_label(self, marker: ImageMarker) -> None:
        if not marker.label or marker.x is None or marker.y is None or self._current_image_shape is None:
            return
        from tomography_session_browser.ui.theme import SANS_FONT_NAME

        scale = self._zoom_scale()
        displayed_height, displayed_width = self._current_image_shape
        font = QFont(SANS_FONT_NAME)
        font.setPixelSize(round(batch_label_screen_font_size_px(scale)))
        font.setWeight(QFont.Weight.Black)

        path = _batch_label_text_path(marker.label, font)
        text_rect = path.boundingRect()
        if text_rect.isEmpty():
            return
        scene_w = text_rect.width() / scale
        scene_h = text_rect.height() / scale
        x, y = _batch_label_scene_origin(
            marker,
            label_size=(scene_w, scene_h),
            image_size=(displayed_width, displayed_height),
        )

        from tomography_session_browser.ui.theme import current_palette

        theme = current_palette()
        shadow_color = QColor(theme.overlay_label_shadow)
        shadow_color.setAlpha(105)
        halo_color = QColor(theme.overlay_label_halo)
        halo_color.setAlpha(178)
        text_color = _batch_label_color(self._visible_marker_types)

        shadow = self._scene.addPath(path, QPen(Qt.PenStyle.NoPen), QBrush(shadow_color))
        shadow.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIgnoresTransformations, True)
        shadow.setPos(x + 1.4 / scale, y + 1.4 / scale)
        shadow.setZValue(30)
        shadow.setData(0, marker)
        shadow.setData(1, "batch_label")
        shadow.setToolTip(marker.tooltip or marker.label)
        self._marker_items.append(shadow)

        halo_pen = QPen(halo_color, LABEL_STROKE_WIDTH_PX)
        halo_pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        halo_pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        halo = self._scene.addPath(path, halo_pen, QBrush(Qt.BrushStyle.NoBrush))
        halo.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIgnoresTransformations, True)
        halo.setPos(x, y)
        halo.setZValue(31)
        halo.setData(0, marker)
        halo.setData(1, "batch_label")
        halo.setToolTip(marker.tooltip or marker.label)
        self._marker_items.append(halo)

        text = self._scene.addPath(path, QPen(Qt.PenStyle.NoPen), QBrush(text_color))
        text.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIgnoresTransformations, True)
        text.setPos(x, y)
        text.setZValue(32)
        text.setData(0, marker)
        text.setData(1, "batch_label")
        text.setToolTip(marker.tooltip or marker.label)
        self._marker_items.append(text)

    def _add_exposure_highlight_edge(self, marker: ImageMarker, radius: float) -> None:
        pen = QPen(_exposure_highlight_color(), EXPOSURE_MARKER_HIGHLIGHT_WIDTH)
        pen.setCosmetic(True)
        brush = QBrush(Qt.BrushStyle.NoBrush)
        if marker.bbox is not None:
            x, y, width, height = marker.bbox
            edge = self._scene.addRect(x, y, width, height, pen, brush)
        elif marker.polygon and len(marker.polygon) == 2:
            start, end = marker.polygon
            edge = self._scene.addLine(start[0], start[1], end[0], end[1], pen)
        elif marker.polygon:
            edge = self._scene.addPolygon(QPolygonF([QPointF(x, y) for x, y in marker.polygon]), pen, brush)
        else:
            assert marker.x is not None and marker.y is not None
            edge = self._scene.addEllipse(marker.x - radius, marker.y - radius, radius * 2, radius * 2, pen, brush)
        # Draw just below the normal green exposure marker so the blue reads as
        # a highlight edge while the exposure's own green fill/stroke remains.
        edge.setZValue(19)
        edge.setData(0, marker)
        edge.setToolTip(marker.tooltip or marker.label or marker.marker_type)
        self._marker_items.append(edge)

    def _update_marker_detail(self) -> None:
        scale = self._zoom_scale()
        for item in self._marker_items:
            marker = item.data(0)
            if isinstance(marker, ImageMarker) and item.data(1) == "batch_label":
                item.setVisible(True)
            elif isinstance(marker, ImageMarker) and item.data(1) == "label":
                show_medium_label = marker.marker_type in {MarkerType.BATCH_POSITION, MarkerType.SEARCH_MAP, MarkerType.OVERVIEW}
                item.setVisible(marker.selected or scale >= HIGH_DETAIL_SCALE or (scale >= MEDIUM_DETAIL_SCALE and show_medium_label))

    def _display_markers(self) -> list[ImageMarker]:
        markers = [
            self._project_marker(marker)
            for marker in self._markers
            if marker.visible
            and not marker.unresolved
            and marker.marker_type in self._visible_marker_types
            and (marker.marker_type != MarkerType.LINK_LINE or MarkerType.EXPOSURE_AREA in self._visible_marker_types)
            and (marker.x is not None or marker.bbox is not None or marker.polygon)
        ]
        return markers

    def _zoom_scale(self) -> float:
        return max(abs(self.transform().m11()), 0.01)

    def _add_cross_marker(self, marker: ImageMarker, radius: float, color: QColor) -> None:
        assert marker.x is not None and marker.y is not None
        pen = QPen(color, 2.0 if marker.selected else 1.5)
        lines = [
            self._scene.addLine(marker.x - radius, marker.y - radius, marker.x + radius, marker.y + radius, pen),
            self._scene.addLine(marker.x - radius, marker.y + radius, marker.x + radius, marker.y - radius, pen),
        ]
        for line in lines:
            line.setZValue(22 if marker.selected else 17)
            line.setData(0, marker)
            line.setToolTip(marker.tooltip or marker.label or marker.marker_type)
            self._marker_items.append(line)

    def _add_axis_crosshair(self, marker: ImageMarker, length: float, color: QColor) -> None:
        """Render an axis-aligned crosshair (vertical + horizontal) for a STAGE_CROSSHAIR marker."""

        assert marker.x is not None and marker.y is not None
        pen = QPen(color, 1.6)
        pen.setStyle(Qt.PenStyle.DashLine)
        lines = [
            self._scene.addLine(marker.x - length, marker.y, marker.x + length, marker.y, pen),
            self._scene.addLine(marker.x, marker.y - length, marker.x, marker.y + length, pen),
        ]
        for line in lines:
            line.setZValue(18)
            line.setData(0, marker)
            line.setToolTip(marker.tooltip or marker.label or "Stage origin")
            self._marker_items.append(line)

    def _project_marker(self, marker: ImageMarker) -> ImageMarker:
        image_size = marker.metadata.get("image_size")
        if not (
            isinstance(image_size, tuple)
            and len(image_size) == 2
            and isinstance(image_size[0], int | float)
            and isinstance(image_size[1], int | float)
            and self._current_image_shape is not None
        ):
            return marker
        source_width, source_height = float(image_size[0]), float(image_size[1])
        if source_width <= 0 or source_height <= 0:
            return marker
        displayed_height, displayed_width = self._current_image_shape
        scale_x = displayed_width / source_width
        scale_y = displayed_height / source_height
        bbox = None
        if marker.bbox is not None:
            x, y, width, height = marker.bbox
            bbox = (x * scale_x, y * scale_y, width * scale_x, height * scale_y)
        polygon = [(x * scale_x, y * scale_y) for x, y in marker.polygon] if marker.polygon else None
        metadata = dict(marker.metadata)
        if marker.marker_type == MarkerType.BATCH_LABEL:
            geometry = _metadata_box(metadata.get("label_source_geometry"))
            if geometry is not None:
                gx, gy, gw, gh = geometry
                metadata["label_source_geometry"] = (gx * scale_x, gy * scale_y, gw * scale_x, gh * scale_y)
            anchor = _metadata_point(metadata.get("label_anchor"))
            if anchor is not None:
                metadata["label_anchor"] = (anchor[0] * scale_x, anchor[1] * scale_y)

        return ImageMarker(
            id=marker.id,
            marker_type=marker.marker_type,
            linked_object_id=marker.linked_object_id,
            source_object_id=marker.source_object_id,
            x=marker.x * scale_x if marker.x is not None else None,
            y=marker.y * scale_y if marker.y is not None else None,
            radius=marker.radius * ((scale_x + scale_y) / 2) if marker.radius is not None else None,
            bbox=bbox,
            polygon=polygon,
            label=marker.label,
            tooltip=marker.tooltip,
            status=marker.status,
            visible=marker.visible,
            selected=marker.selected,
            unresolved=marker.unresolved,
            metadata=metadata,
        )


def _marker_colors(marker: ImageMarker) -> tuple[QColor, QColor]:
    from tomography_session_browser.ui.theme import current_palette

    theme = current_palette()

    def fill(hex_color: str, alpha: int) -> QColor:
        color = QColor(hex_color)
        color.setAlpha(alpha)
        return color

    if marker.selected and marker.marker_type == MarkerType.EXPOSURE_AREA:
        return QColor(theme.marker_exposure), fill(theme.marker_exposure, 105)
    if marker.selected:
        return QColor(theme.overlay_selected), fill(theme.overlay_selected, 90)
    if marker.unresolved:
        return QColor(theme.overlay_unresolved), fill(theme.overlay_unresolved, 60)
    status_color = _status_color(marker.status)
    # Failed batches now propagate their status to every marker they
    # spawn (template area, exposure / tracking / focus, batch dot).
    # Honouring it on the area-marker types as well makes failed
    # acquisitions read in red on the Search Map / Overview tabs,
    # matching the PDF.
    failure_marker_types = {
        MarkerType.BATCH_POSITION,
        MarkerType.TILT_SERIES,
        MarkerType.TEMPLATE_AREA,
        MarkerType.EXPOSURE_AREA,
        MarkerType.TRACKING_AREA,
        MarkerType.FOCUS_AREA,
    }
    if (
        status_color is not None
        and marker.marker_type in failure_marker_types
        and (marker.status or "").lower().find("fail") >= 0
    ):
        return status_color, QColor(status_color.red(), status_color.green(), status_color.blue(), 70)
    if marker.marker_type in {MarkerType.BATCH_POSITION, MarkerType.TILT_SERIES} and status_color is not None:
        return status_color, QColor(status_color.red(), status_color.green(), status_color.blue(), 70)
    colors = {
        MarkerType.SEARCH_MAP: (theme.marker_search, fill(theme.marker_search, 28)),
        MarkerType.OVERVIEW: (theme.marker_overview, fill(theme.marker_overview, 60)),
        MarkerType.BATCH_POSITION: (theme.marker_batch, fill(theme.marker_batch, 70)),
        MarkerType.TEMPLATE_AREA: (theme.marker_template, fill(theme.marker_template, 35)),
        MarkerType.EXPOSURE_AREA: (theme.marker_exposure, fill(theme.marker_exposure, 65)),
        MarkerType.CAMERA_FOV: (theme.marker_camera, fill(theme.overlay_camera_fill, 46)),
        MarkerType.BATCH_LABEL: (theme.marker_exposure, fill(theme.marker_exposure, 0)),
        MarkerType.TRACKING_AREA: (theme.marker_tracking, fill(theme.marker_tracking, 70)),
        MarkerType.FOCUS_AREA: (theme.marker_focus, fill(theme.marker_focus, 70)),
        MarkerType.CONDITION_AREA: (theme.marker_condition, fill(theme.marker_condition, 70)),
        MarkerType.TILT_SERIES: (theme.marker_tilt, fill(theme.marker_tilt, 70)),
        MarkerType.LINK_LINE: (theme.marker_link, fill(theme.marker_link, 0)),
    }
    pen, brush = colors.get(marker.marker_type, (theme.text, fill(theme.text, 60)))
    return QColor(pen), brush


def _batch_label_text_path(text: str, font: QFont) -> QPainterPath:
    path = QPainterPath()
    path.addText(0.0, 0.0, font, text)
    bounds = path.boundingRect()
    if bounds.isEmpty():
        return path
    normalised = QPainterPath()
    normalised.addText(-bounds.left(), -bounds.top(), font, text)
    return normalised


def _batch_label_scene_origin(
    marker: ImageMarker,
    *,
    label_size: tuple[float, float],
    image_size: tuple[float, float],
) -> tuple[float, float]:
    label_w, label_h = label_size
    image_w, image_h = image_size
    x = float(marker.x or 0.0)
    y = float(marker.y or 0.0)

    return (
        min(max(0.0, x), max(0.0, image_w - label_w)),
        min(max(0.0, y), max(0.0, image_h - label_h)),
    )


def _metadata_box(value: Any) -> tuple[float, float, float, float] | None:
    if not (
        isinstance(value, tuple | list)
        and len(value) == 4
        and all(isinstance(part, int | float) for part in value)
    ):
        return None
    x, y, width, height = value
    if width <= 0 or height <= 0:
        return None
    return float(x), float(y), float(width), float(height)


def _metadata_point(value: Any) -> tuple[float, float] | None:
    if not (
        isinstance(value, tuple | list)
        and len(value) == 2
        and all(isinstance(part, int | float) for part in value)
    ):
        return None
    return float(value[0]), float(value[1])


def _batch_label_color(visible_marker_types: set[str]) -> QColor:
    from tomography_session_browser.ui.theme import current_palette

    theme = current_palette()
    if MarkerType.EXPOSURE_AREA in visible_marker_types:
        return QColor(theme.overlay_batch_label_exposure)
    if MarkerType.CAMERA_FOV in visible_marker_types:
        return QColor(theme.overlay_batch_label_camera)
    return QColor(theme.overlay_batch_label_default)


def _exposure_highlight_color() -> QColor:
    from tomography_session_browser.ui.theme import current_palette

    return QColor(current_palette().marker_focus)


def _status_color(status: str | None) -> QColor | None:
    from tomography_session_browser.ui.theme import current_palette

    if not status:
        return None
    theme = current_palette()
    normalized = status.lower()
    if any(value in normalized for value in ("fail", "error", "abort")):
        return QColor(theme.overlay_status_failed)
    if "missing" in normalized:
        return QColor(theme.overlay_status_failed)
    if any(value in normalized for value in ("partial", "incomplete", "warn")):
        return QColor(theme.overlay_status_warning)
    if any(value in normalized for value in ("queue", "pending", "selected")):
        return QColor(theme.overlay_status_pending)
    if any(value in normalized for value in ("acquired", "done", "complete", "ok", "available")):
        return QColor(theme.overlay_status_complete)
    if any(value in normalized for value in ("refined", "initial")):
        return QColor(theme.overlay_status_complete)
    return None


def _pixel_size_meters_for_item(value: Any, displayed_path: Path | None = None) -> float | None:
    if isinstance(value, TiltSeries) and value.pixel_size is not None:
        return value.pixel_size * 1e-10
    if isinstance(value, BatchPosition):
        metadata = _batch_mrc_metadata_for_path(value, displayed_path)
        pixel = _mrc_pixel_size_meters(metadata)
        if pixel is not None:
            return pixel
        return _xml_pixel_size_meters(value.metadata)
    if isinstance(value, SearchTile):
        return _xml_pixel_size_meters(value.metadata.get("Tile.xml")) or _mrc_pixel_size_meters(value.mrc_metadata)
    if isinstance(value, SearchMap):
        return _xml_pixel_size_meters(value.metadata.get("SearchMap.xml")) or _xml_pixel_size_meters(value.metadata) or _mrc_pixel_size_meters(value.mrc_metadata)
    if isinstance(value, Atlas):
        mosaic = value.metadata.get("AtlasMosaicPixelSize")
        if isinstance(mosaic, dict):
            pixel = _as_float(mosaic.get("x")) or _as_float(mosaic.get("y"))
            if pixel is not None and pixel > 0:
                return pixel
        return _xml_pixel_size_meters(value.metadata) or _mrc_pixel_size_meters(value.mrc_metadata)
    if isinstance(value, Overview):
        return _xml_pixel_size_meters(value.metadata) or _mrc_pixel_size_meters(value.mrc_metadata)
    metadata = getattr(value, "mrc_metadata", None)
    return _mrc_pixel_size_meters(metadata if isinstance(metadata, MrcMetadata) else None)


def _source_image_size_for_item(value: Any, displayed_path: Path | None = None) -> tuple[int, int] | None:
    if isinstance(value, BatchPosition):
        metadata = _batch_mrc_metadata_for_path(value, displayed_path)
        return _mrc_image_size(metadata) or _metadata_image_size(value.metadata)
    if isinstance(value, SearchTile):
        return _metadata_image_size(value.metadata.get("Tile.xml")) or _mrc_image_size(value.mrc_metadata)
    if isinstance(value, SearchMap):
        return _mrc_image_size(value.mrc_metadata) or _metadata_image_size(value.metadata.get("SearchMap.xml")) or _metadata_image_size(value.metadata)
    if isinstance(value, Atlas):
        return _mrc_image_size(value.mrc_metadata) or _metadata_image_size(value.metadata)
    if isinstance(value, Overview):
        return _mrc_image_size(value.mrc_metadata) or _metadata_image_size(value.metadata)
    if isinstance(value, TiltSeries):
        return _mrc_image_size(value.mrc_metadata)
    metadata = getattr(value, "mrc_metadata", None)
    return _mrc_image_size(metadata if isinstance(metadata, MrcMetadata) else None)


def _batch_mrc_metadata_for_path(batch: BatchPosition, displayed_path: Path | None) -> MrcMetadata | None:
    if displayed_path is not None:
        if batch.search_image_path == displayed_path:
            return batch.search_mrc_metadata
        if batch.tracking_image_path == displayed_path:
            return batch.tracking_mrc_metadata
        if batch.exposure_image_path == displayed_path or displayed_path in batch.exposure_image_paths:
            return batch.exposure_mrc_metadata
    return batch.search_mrc_metadata or batch.tracking_mrc_metadata or batch.exposure_mrc_metadata


def _mrc_pixel_size_meters(metadata: MrcMetadata | None) -> float | None:
    if metadata is None:
        return None
    pixel = next((value for value in metadata.voxel_size if isinstance(value, int | float) and value > 0), None)
    if pixel is None:
        return None
    # MRC header voxel sizes are conventionally Å/px in this codebase. A tiny
    # value is treated as metres/px for safety when a parser has already
    # converted instrument metadata into SI units.
    return float(pixel) if pixel < 1e-6 else float(pixel) * 1e-10


def _mrc_image_size(metadata: MrcMetadata | None) -> tuple[int, int] | None:
    if metadata is None or metadata.nx is None or metadata.ny is None:
        return None
    return metadata.nx, metadata.ny


def _xml_pixel_size_meters(metadata: Any) -> float | None:
    pixel_size = find_first(metadata, "pixelSize")
    if not isinstance(pixel_size, dict):
        return None
    x_value = pixel_size.get("x")
    y_value = pixel_size.get("y")
    for node in (x_value, y_value):
        if isinstance(node, dict):
            numeric = _as_float(node.get("numericValue"))
            if numeric is not None and numeric > 0:
                return numeric
    return None


def _metadata_image_size(metadata: Any) -> tuple[int, int] | None:
    for key in ("ImageSize", "ReadoutArea"):
        value = find_first(metadata, key)
        if isinstance(value, dict):
            width = _as_int(value.get("width"))
            height = _as_int(value.get("height"))
            if width is not None and height is not None:
                return width, height
    return None


def _nice_scale_length_meters(target_meters: float) -> float | None:
    if target_meters <= 0 or not math.isfinite(target_meters):
        return None
    exponent = math.floor(math.log10(target_meters))
    candidates = [
        multiplier * (10**power)
        for power in range(exponent - 1, exponent + 2)
        for multiplier in (1, 2, 5)
    ]
    candidates = [value for value in candidates if value > 0]
    return min(candidates, key=lambda value: abs(math.log10(value / target_meters)))


def _format_pixel_size_label(value_meters: float | None) -> str:
    return _format_scale_length_label(value_meters) if value_meters is not None else "unknown"


def _format_scale_length_label(value_meters: float) -> str:
    angstrom = value_meters * 1e10
    if angstrom < 100:
        return f"{_format_scale_number(angstrom)} Å"
    nm = value_meters * 1e9
    if nm < 1000:
        return f"{_format_scale_number(nm)} nm"
    um = value_meters * 1e6
    if um < 1000:
        return f"{_format_scale_number(um)} µm"
    return f"{_format_scale_number(value_meters * 1e3)} mm"


def _format_scale_number(value: float) -> str:
    if value >= 100:
        return f"{value:.0f}"
    if value >= 10:
        return f"{value:.1f}".rstrip("0").rstrip(".")
    return f"{value:.2f}".rstrip("0").rstrip(".")


def _as_float(value: Any) -> float | None:
    return float(value) if isinstance(value, int | float) and math.isfinite(float(value)) else None


def _as_int(value: Any) -> int | None:
    return int(value) if isinstance(value, int) else None


def _marker_label_anchor(marker: ImageMarker) -> QPointF:
    if marker.x is not None and marker.y is not None:
        return QPointF(marker.x, marker.y)
    if marker.bbox is not None:
        x, y, width, height = marker.bbox
        return QPointF(x + width, y + height / 2)
    if marker.polygon:
        x = sum(point[0] for point in marker.polygon) / len(marker.polygon)
        y = sum(point[1] for point in marker.polygon) / len(marker.polygon)
        return QPointF(x, y)
    return QPointF(0, 0)


def _relative_point(rect, point: QPointF) -> QPointF:
    width = rect.width() or 1.0
    height = rect.height() or 1.0
    return QPointF((point.x() - rect.left()) / width, (point.y() - rect.top()) / height)


def _absolute_point(rect, relative: QPointF | None) -> QPointF:
    if relative is None:
        return rect.center()
    return QPointF(rect.left() + rect.width() * relative.x(), rect.top() + rect.height() * relative.y())


class FrameSelectionSlider(QSlider):
    """Slider with frame ticks drawn above the groove for tilt-series browsing."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(Qt.Orientation.Horizontal, parent)
        self.setObjectName("frameScrubber")
        self.setMinimum(0)
        self.setMinimumHeight(42)
        self.setTickPosition(QSlider.TickPosition.TicksAbove)
        self.setTickInterval(1)

    def paintEvent(self, event) -> None:
        frame_count = self.maximum() - self.minimum() + 1
        if frame_count <= 1:
            super().paintEvent(event)
            return

        super().paintEvent(event)

        option = QStyleOptionSlider()
        self.initStyleOption(option)
        groove = self.style().subControlRect(
            QStyle.ComplexControl.CC_Slider,
            option,
            QStyle.SubControl.SC_SliderGroove,
            self,
        )
        if groove.width() <= 0:
            return

        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        from tomography_session_browser.ui.theme import current_palette

        painter.setPen(QPen(QColor(current_palette().text_muted), 1))
        y_long = max(2, groove.top() - 5)
        y_short = max(2, y_long - 6)
        for x in self.frame_tick_positions():
            painter.drawLine(x, y_short, x, y_long)
        painter.end()

    def frame_tick_positions(self) -> list[int]:
        return [self._handle_center_x_for_value(value) for value in range(self.minimum(), self.maximum() + 1)]

    def _handle_center_x_for_value(self, value: int) -> int:
        option = QStyleOptionSlider()
        self.initStyleOption(option)
        option.sliderPosition = value
        option.sliderValue = value
        handle = self.style().subControlRect(
            QStyle.ComplexControl.CC_Slider,
            option,
            QStyle.SubControl.SC_SliderHandle,
            self,
        )
        return handle.center().x()


class _ViewerListDelegate(QStyledItemDelegate):
    def sizeHint(self, option: QStyleOptionViewItem, index) -> QSize:  # noqa: N802 - Qt API
        return QSize(super().sizeHint(option, index).width(), 58)

    def paint(self, painter: QPainter, option: QStyleOptionViewItem, index) -> None:  # noqa: N802 - Qt API
        from tomography_session_browser.ui.theme import current_palette

        theme = current_palette()
        primary = str(index.data(LIST_PRIMARY_ROLE) or index.data(Qt.ItemDataRole.DisplayRole) or "")
        summary = str(index.data(LIST_SUMMARY_ROLE) or "")
        status = str(index.data(LIST_STATUS_ROLE) or "")

        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        rect = option.rect.adjusted(0, 1, 0, -1)
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        hovered = bool(option.state & QStyle.StateFlag.State_MouseOver)
        fill = QColor(theme.selection if selected else theme.surface_alt if hovered else theme.panel)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(fill)
        painter.drawRoundedRect(rect.adjusted(2, 1, -2, -1), 7, 7)

        painter.setPen(QPen(QColor(theme.border), 1))
        painter.drawLine(rect.bottomLeft(), rect.bottomRight())

        left = rect.left() + 12
        right = rect.right() - 12
        top = rect.top() + 7

        badge_font = QFont(option.font)
        badge_font.setPointSizeF(max(option.font.pointSizeF() - 0.5, 8.0))
        badge_metrics = painter.fontMetrics()
        painter.setFont(badge_font)
        badge_metrics = painter.fontMetrics()
        badge_w = max(54, badge_metrics.horizontalAdvance(status) + 22) if status else 0
        badge_h = 26
        badge_rect = QRectF(right - badge_w, rect.center().y() - badge_h / 2, badge_w, badge_h)

        text_right = badge_rect.left() - 12 if status else right
        text_width = max(int(text_right - left), 16)

        name_font = QFont(option.font)
        name_font.setBold(True)
        name_font.setPointSizeF(max(option.font.pointSizeF(), 10.0))
        painter.setFont(name_font)
        name_metrics = painter.fontMetrics()
        painter.setPen(QColor(theme.selection_text if selected else theme.text_strong))
        painter.drawText(
            QRectF(left, top, text_width, 20),
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            name_metrics.elidedText(primary, Qt.TextElideMode.ElideRight, text_width),
        )

        summary_font = QFont(option.font)
        summary_font.setPointSizeF(max(option.font.pointSizeF() - 1.0, 8.0))
        painter.setFont(summary_font)
        summary_metrics = painter.fontMetrics()
        painter.setPen(QColor(theme.text_muted))
        painter.drawText(
            QRectF(left, top + 23, text_width, 18),
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            summary_metrics.elidedText(summary, Qt.TextElideMode.ElideRight, text_width),
        )

        if status:
            fg, bg = _status_badge_colors(status)
            painter.setPen(QPen(fg, 1))
            painter.setBrush(bg)
            painter.drawRoundedRect(badge_rect, badge_h / 2, badge_h / 2)
            painter.setPen(fg)
            painter.setFont(badge_font)
            _draw_centered_badge_text(painter, badge_rect, status)

        painter.restore()


def _draw_centered_badge_text(painter: QPainter, rect: QRectF, text: str) -> None:
    """Draw badge text with an explicit baseline so it is visually centered.

    Qt's rectangle-based AlignCenter can sit a touch high or low depending on
    the active font and platform metrics. These status pills are small enough
    that the drift is noticeable, so centre the actual text bounds inside the
    badge background.
    """

    metrics = painter.fontMetrics()
    available_width = max(1, int(rect.width() - 14))
    elided = metrics.elidedText(text, Qt.TextElideMode.ElideRight, available_width)
    text_width = metrics.horizontalAdvance(elided)
    x = rect.center().x() - (text_width / 2)
    baseline = rect.center().y() + ((metrics.ascent() - metrics.descent()) / 2)
    painter.drawText(QPointF(x, baseline), elided)


def _status_badge_colors(status: str) -> tuple[QColor, QColor]:
    from tomography_session_browser.ui.theme import current_palette

    theme = current_palette()
    raw = status.strip().lower()
    if raw in {"done", "ok", "acquired", "complete", "completed", "available"}:
        fg = QColor(theme.chart_green)
    elif raw in {"partial", "incomplete", "warning", "warn"}:
        fg = QColor(theme.chart_amber)
    elif raw in {"failed", "fail", "error", "missing"}:
        fg = QColor(theme.chart_red)
    elif raw in {"queued", "pending", "selected"}:
        fg = QColor(theme.chart_blue)
    else:
        fg = QColor(theme.chart_grey)
    bg = QColor(fg)
    bg.setAlpha(40)
    return fg, bg


def _is_status_chip(value: str) -> bool:
    normalized = value.strip().lower()
    return normalized in {
        "acquired",
        "done",
        "complete",
        "completed",
        "partial",
        "incomplete",
        "warning",
        "warn",
        "failed",
        "fail",
        "error",
        "missing",
        "empty",
        "unknown",
    }


def _warning_count_text(warnings: list[str]) -> str:
    if not warnings:
        return ""
    if len(warnings) == 1:
        return " · 1 warning"
    return f" · {len(warnings)} warnings"


def _warning_tooltip(warnings: list[str]) -> str:
    if not warnings:
        return ""
    return "\nWarnings:\n" + "\n".join(f"- {warning}" for warning in warnings)


def _path_size_bytes(path: Path) -> int:
    try:
        return path.stat().st_size
    except OSError:
        return 0


class ScaleBarWidget(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._length_px = 0.0
        self._label = ""
        self.setObjectName("viewerScaleBar")
        self.setFixedSize(230, 46)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)

    def set_scale(self, length_px: float, label: str) -> None:
        self._length_px = max(0.0, min(length_px, self.width() - 42))
        self._label = label
        self.setVisible(self._length_px >= MIN_SCALE_BAR_SCREEN_PX and bool(label))
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt signature
        super().paintEvent(event)
        if self._length_px <= 0 or not self._label:
            return
        from tomography_session_browser.ui.theme import current_palette

        theme = current_palette()
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        try:
            bg = QColor(theme.panel)
            bg.setAlpha(210)
            border = QColor(theme.border)
            painter.setPen(QPen(border, 1))
            painter.setBrush(bg)
            painter.drawRoundedRect(QRectF(0.5, 0.5, self.width() - 1, self.height() - 1), 8, 8)

            x0 = 16.0
            y = 27.0
            x1 = x0 + self._length_px
            pen = QPen(QColor(theme.text_strong), 3)
            pen.setCapStyle(Qt.PenCapStyle.SquareCap)
            painter.setPen(pen)
            painter.drawLine(QPointF(x0, y), QPointF(x1, y))
            tick_pen = QPen(QColor(theme.text_strong), 2)
            painter.setPen(tick_pen)
            painter.drawLine(QPointF(x0, y - 6), QPointF(x0, y + 6))
            painter.drawLine(QPointF(x1, y - 6), QPointF(x1, y + 6))

            from tomography_session_browser.ui.theme import MONO_FONT_NAME

            font = QFont(MONO_FONT_NAME, 9, QFont.Weight.DemiBold)
            painter.setFont(font)
            painter.setPen(QColor(theme.text))
            painter.drawText(QRectF(12, 4, self.width() - 24, 18), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, self._label)
        finally:
            painter.end()


class ViewerTab(QWidget):
    def __init__(
        self,
        empty_text: str,
        *,
        show_list: bool = True,
        show_tilt_controls: bool = False,
        on_item_selected: Callable[[Any], None] | None = None,
        on_frame_changed: Callable[[Any, int, int], None] | None = None,
        tilt_angle_for: Callable[[Any, int], str | None] | None = None,
        markers_for: Callable[[Any], list[ImageMarker]] | None = None,
        on_marker_selected: Callable[[ImageMarker], None] | None = None,
        on_marker_opened: Callable[[ImageMarker], None] | None = None,
        show_marker_controls: bool = True,
        navigation_actions_for: Callable[[Any], list[ViewerNavigationAction]] | None = None,
        on_navigation_requested: Callable[[Any, str], None] | None = None,
    ) -> None:
        super().__init__()
        self._items: list[Any] = []
        self._sources_for: Callable[[Any], PreviewSources] = lambda _value: PreviewSources(None)
        self._prepared_sources: dict[str, PreviewSources] = {}
        self._on_item_selected = on_item_selected
        self._on_frame_changed = on_frame_changed
        self._tilt_angle_for = tilt_angle_for or (lambda _value, _index: None)
        self._markers_for = markers_for or (lambda _value: [])
        self._on_marker_selected = on_marker_selected
        self._on_marker_opened = on_marker_opened
        self._show_marker_controls = show_marker_controls
        self._navigation_actions_for = navigation_actions_for
        self._on_navigation_requested = on_navigation_requested
        self._item_status_for: Callable[[Any], ItemListStatus] | None = None
        self._selected_marker_id_override: str | None = None
        self._show_tilt_controls = show_tilt_controls
        self._scale_bar_enabled = True
        self._current_value: Any | None = None
        self._current_path: Path | None = None
        self._current_fallback: Path | None = None
        self._displayed_path: Path | None = None
        self._displayed_slice_index: int | None = None
        self._current_mrc_max_size = MAX_PREVIEW_DIMENSION
        self._request_id = 0
        self._pending_fallback_request: tuple[int, Path, int, Path, tuple[str, int, int | None]] | None = None
        self._pending_slice_index = 0
        self._mrc_signals = _MrcLoadSignals()
        self._mrc_signals.loaded.connect(self._mrc_loaded)
        self._mrc_signals.failed.connect(self._mrc_failed)
        self._thread_pool = QThreadPool.globalInstance()
        self._slider_debounce = QTimer(self)
        self._slider_debounce.setSingleShot(True)
        self._slider_debounce.setInterval(75)
        self._slider_debounce.timeout.connect(self._load_pending_slice)
        self._fallback_debounce = QTimer(self)
        self._fallback_debounce.setSingleShot(True)
        self._fallback_debounce.setInterval(MRC_JPEG_FALLBACK_GRACE_MS)
        self._fallback_debounce.timeout.connect(self._show_delayed_mrc_fallback)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(8)
        self.viewer = ImagePreviewView(self)
        self.viewer.set_zoom_changed_callback(self._zoom_changed)
        self.viewer.set_marker_selected_callback(self._marker_selected)
        self.viewer.set_marker_opened_callback(self._marker_opened)

        header = QHBoxLayout()
        header.setSpacing(8)
        self.header_title = ElidedLabel("No item selected", parent=self)
        self.header_title.setObjectName("viewerTitle")
        self.header_title.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        header.addWidget(self.header_title, stretch=1)
        self.header_chips: list[QLabel] = []

        controls = QHBoxLayout()
        controls.setSpacing(6)
        self.zoom_in_button = QPushButton("", self)
        self.zoom_out_button = QPushButton("", self)
        self.fit_button = QPushButton("Fit", self)
        self.overlay_button = QPushButton("Overlays", self)
        self.overlay_button.setCheckable(True)
        self.overlay_button.setToolTip("Show overlay controls")
        self.zoom_in_button.setIcon(themed_icon("zoom-in", size=16))
        self.zoom_out_button.setIcon(themed_icon("zoom-out", size=16))
        self.overlay_button.setIcon(themed_icon("layers", size=16))
        self.fit_button.setIcon(themed_icon("maximize", size=16))
        self.zoom_label = QLabel("100%", self)
        self.zoom_label.setObjectName("viewerZoomLabel")
        self.zoom_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.zoom_label.setFixedWidth(30)
        self.image_badge = QLabel("", self)
        self.image_badge.setObjectName("viewerImageBadge")
        self.image_badge.setVisible(False)
        self.scale_bar = ScaleBarWidget(self)
        self.zoom_in_button.setToolTip("Zoom in")
        self.zoom_out_button.setToolTip("Zoom out")
        self.fit_button.setToolTip("Fit preview to the available space")
        for button in (self.zoom_in_button, self.zoom_out_button, self.fit_button, self.overlay_button):
            button.setObjectName("viewerToolButton")
            button.setIconSize(QSize(16, 16))
        for button in (self.zoom_in_button, self.zoom_out_button):
            button.setObjectName("viewerZoomButton")
            button.setFixedSize(30, 28)
        self.status = QLabel(empty_text, self)
        self.status.setObjectName("viewerStatus")
        self.status.setWordWrap(True)
        self.status.setToolTip(empty_text)
        self.frame_label = QLabel("Frame", self)
        self.frame_label.setObjectName("frameLabel")
        self.frame_label.setToolTip("Current tilt series frame")
        self.slice_slider = FrameSelectionSlider(self)
        self.slice_slider.setVisible(show_tilt_controls)
        self.frame_label.setVisible(False)
        self.slice_slider.setEnabled(False)
        self.slice_slider.setVisible(False)
        self.slice_slider.valueChanged.connect(self._slider_changed)
        if show_tilt_controls:
            self._install_frame_shortcuts()
        self._marker_type_checks: dict[str, QCheckBox] = {}
        self._user_marker_type_visible: dict[str, bool] = {
            marker_type: True for marker_type in MARKER_TYPES_WITH_CONTROLS
        }
        self._unavailable_marker_types: set[str] = set()
        self.overlay_panel = QWidget(self)
        self.overlay_panel.setObjectName("viewerOverlayPanel")
        overlay_panel_layout = QVBoxLayout(self.overlay_panel)
        overlay_panel_layout.setContentsMargins(10, 10, 10, 10)
        overlay_panel_layout.setSpacing(6)
        if show_marker_controls:
            for marker_type in MARKER_TYPES_WITH_CONTROLS:
                checkbox = QCheckBox(MARKER_TYPE_LABELS.get(marker_type, marker_type), self.overlay_panel)
                checkbox.setChecked(True)
                checkbox.setToolTip(f"Show {MARKER_TYPE_LABELS.get(marker_type, marker_type).lower()} markers")
                checkbox.stateChanged.connect(lambda _state, marker_type=marker_type: self._marker_type_toggled(marker_type))
                self._marker_type_checks[marker_type] = checkbox
                overlay_panel_layout.addWidget(checkbox)
        self.scale_bar_checkbox = QCheckBox("Scale bar", self.overlay_panel)
        self.scale_bar_checkbox.setChecked(True)
        self.scale_bar_checkbox.setToolTip("Show or hide the dynamic scale bar")
        self.scale_bar_checkbox.stateChanged.connect(self._scale_bar_toggled)
        overlay_panel_layout.addWidget(self.scale_bar_checkbox)
        self.overlay_panel.setVisible(False)
        self.overlay_button.toggled.connect(self._overlay_panel_toggled)

        self._navigation_buttons: dict[str, QPushButton] = {}
        if self._navigation_actions_for is not None:
            controls.addSpacing(8)
            for key, label in (
                ("batch", "\u2197 Batch"),
                ("search", "\u2197 Search"),
                ("search_map", "\u2197 Search map"),
                ("overview", "\u2197 Overview"),
                ("tilt_series", "\u2197 Tilt series"),
            ):
                button = QPushButton(label, self)
                button.setObjectName("viewerNavButton")
                button.setVisible(False)
                button.clicked.connect(lambda _checked=False, key=key: self._navigation_button_clicked(key))
                self._navigation_buttons[key] = button
                controls.addWidget(button)

        self.zoom_in_button.clicked.connect(self.viewer.zoom_in)
        self.zoom_out_button.clicked.connect(self.viewer.zoom_out)
        self.fit_button.clicked.connect(self.viewer.fit_image)

        self.list = QTreeWidget(self)
        self.list.setObjectName("viewerList")
        self.list.setColumnCount(1)
        self.list.setHeaderHidden(True)
        self.list.setIndentation(12)
        self.list.setTextElideMode(Qt.TextElideMode.ElideRight)
        self.list.header().setStretchLastSection(False)
        self.list.header().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.list.setItemDelegate(_ViewerListDelegate(self.list))
        self.list.setAlternatingRowColors(False)
        self.list.setMouseTracking(True)
        self.list.currentItemChanged.connect(self._item_changed)
        self.list.setVisible(show_list)

        self.list_filter = QLineEdit(self)
        self.list_filter.setObjectName("viewerFilter")
        self.list_filter.setPlaceholderText("Filter items...")
        self.list_filter.setToolTip("Filter this list by name, status, or summary")
        self.list_filter.setVisible(show_list)
        self.list_filter.textChanged.connect(self._filter_list)
        self.list_header = QLabel("Items", self)
        self.list_header.setObjectName("viewerPanelHeader")
        self.list_header.setVisible(show_list)
        self.list_count = QLabel("0", self)
        self.list_count.setObjectName("panelCount")
        self.list_count.setVisible(show_list)

        viewer_panel = QWidget(self)
        viewer_layout = QVBoxLayout(viewer_panel)
        viewer_layout.setContentsMargins(0, 0, 0, 0)
        viewer_layout.setSpacing(6)
        viewer_shell = QWidget(viewer_panel)
        self.viewer_shell = viewer_shell
        viewer_shell.setObjectName("viewerCanvasShell")
        viewer_shell_layout = QGridLayout(viewer_shell)
        viewer_shell_layout.setContentsMargins(0, 0, 0, 0)
        viewer_shell_layout.setSpacing(0)
        viewer_shell_layout.addWidget(self.viewer, 0, 0)

        top_right = QWidget(viewer_shell)
        top_right.setObjectName("viewerTopControls")
        top_right_layout = QVBoxLayout(top_right)
        top_right_layout.setContentsMargins(0, 0, 0, 0)
        top_right_layout.setSpacing(6)
        top_right_row = QHBoxLayout()
        top_right_row.setContentsMargins(0, 0, 0, 0)
        top_right_row.setSpacing(6)
        top_right_row.addWidget(self.overlay_button)
        top_right_row.addWidget(self.fit_button)
        top_right_layout.addLayout(top_right_row)
        top_right_layout.addWidget(self.overlay_panel, alignment=Qt.AlignmentFlag.AlignRight)
        top_right_anchor = QWidget(viewer_shell)
        top_right_anchor.setObjectName("viewerFloatingTopRightAnchor")
        top_right_anchor_layout = QVBoxLayout(top_right_anchor)
        top_right_anchor_layout.setContentsMargins(
            0,
            FLOATING_CONTROL_INSET_PX,
            FLOATING_CONTROL_INSET_PX,
            0,
        )
        top_right_anchor_layout.addWidget(top_right)
        viewer_shell_layout.addWidget(
            top_right_anchor,
            0,
            0,
            alignment=Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignRight,
        )

        zoom_panel = QWidget(viewer_shell)
        zoom_panel.setObjectName("viewerZoomPanel")
        zoom_panel.setFixedWidth(36)
        zoom_layout = QVBoxLayout(zoom_panel)
        zoom_layout.setContentsMargins(3, 5, 3, 5)
        zoom_layout.setSpacing(3)
        zoom_layout.addWidget(self.zoom_in_button, alignment=Qt.AlignmentFlag.AlignHCenter)
        zoom_layout.addWidget(self.zoom_label, alignment=Qt.AlignmentFlag.AlignHCenter)
        zoom_layout.addWidget(self.zoom_out_button, alignment=Qt.AlignmentFlag.AlignHCenter)
        zoom_anchor = QWidget(viewer_shell)
        zoom_anchor.setObjectName("viewerFloatingBottomRightAnchor")
        zoom_anchor_layout = QVBoxLayout(zoom_anchor)
        zoom_anchor_layout.setContentsMargins(
            0,
            0,
            FLOATING_CONTROL_INSET_PX,
            FLOATING_CONTROL_BOTTOM_INSET_PX,
        )
        zoom_anchor_layout.addWidget(zoom_panel)
        viewer_shell_layout.addWidget(
            zoom_anchor,
            0,
            0,
            alignment=Qt.AlignmentFlag.AlignBottom | Qt.AlignmentFlag.AlignRight,
        )
        scale_bar_anchor = QWidget(viewer_shell)
        scale_bar_anchor.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        scale_bar_anchor_layout = QVBoxLayout(scale_bar_anchor)
        scale_bar_anchor_layout.setContentsMargins(
            FLOATING_CONTROL_INSET_PX,
            0,
            0,
            FLOATING_CONTROL_BOTTOM_INSET_PX,
        )
        scale_bar_anchor_layout.addWidget(self.scale_bar)
        viewer_shell_layout.addWidget(scale_bar_anchor, 0, 0, alignment=Qt.AlignmentFlag.AlignBottom | Qt.AlignmentFlag.AlignLeft)
        image_badge_anchor = QWidget(viewer_shell)
        image_badge_anchor.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        image_badge_anchor_layout = QVBoxLayout(image_badge_anchor)
        image_badge_anchor_layout.setContentsMargins(
            FLOATING_CONTROL_INSET_PX,
            FLOATING_CONTROL_INSET_PX,
            0,
            0,
        )
        image_badge_anchor_layout.addWidget(self.image_badge)
        viewer_shell_layout.addWidget(
            image_badge_anchor,
            0,
            0,
            alignment=Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft,
        )
        viewer_layout.addWidget(viewer_shell, stretch=1)
        if show_tilt_controls:
            viewer_layout.addWidget(self.frame_label)
            viewer_layout.addWidget(self.slice_slider)

        splitter = QSplitter(Qt.Orientation.Horizontal, self)
        splitter.setChildrenCollapsible(True)
        splitter.addWidget(viewer_panel)
        if show_list:
            list_panel = QWidget(splitter)
            list_panel.setObjectName("viewerListPanel")
            list_layout = QVBoxLayout(list_panel)
            list_layout.setContentsMargins(0, 0, 0, 0)
            list_layout.setSpacing(0)
            list_top = QWidget(list_panel)
            list_top_layout = QHBoxLayout(list_top)
            list_top_layout.setContentsMargins(12, 10, 12, 8)
            list_top_layout.addWidget(self.list_header)
            list_top_layout.addStretch(1)
            list_top_layout.addWidget(self.list_count)
            list_layout.addWidget(list_top)
            filter_wrap = QWidget(list_panel)
            filter_layout = QVBoxLayout(filter_wrap)
            filter_layout.setContentsMargins(10, 0, 10, 8)
            filter_layout.addWidget(self.list_filter)
            list_layout.addWidget(filter_wrap)
            list_layout.addWidget(self.list, stretch=1)
            splitter.addWidget(list_panel)
            self.list.setMinimumWidth(220)
            # Keep the right-side list wide enough for status summaries
            # while leaving the image viewer with most of the horizontal
            # space. The user can still drag the splitter handle.
            splitter.setSizes([1180, 300])
            splitter.setStretchFactor(0, 6)
            splitter.setStretchFactor(1, 1)
            splitter.setCollapsible(0, False)
            splitter.setCollapsible(1, True)

        header.addLayout(controls)
        layout.addLayout(header)
        layout.addWidget(splitter, stretch=1)
        layout.addWidget(self.status)

    def _set_header_chips(self, values: list[str]) -> None:
        layout = self.layout().itemAt(0).layout() if self.layout() and self.layout().count() else None
        if layout is None:
            return
        for chip in self.header_chips:
            for index in range(layout.count()):
                item = layout.itemAt(index)
                if item is not None and item.widget() is chip:
                    layout.takeAt(index)
                    break
            chip.deleteLater()
        self.header_chips = []
        for index, value in enumerate(values):
            chip = QLabel(value, self)
            chip.setObjectName("viewerChipStatus" if _is_status_chip(value) else "viewerChip")
            chip.setAlignment(Qt.AlignmentFlag.AlignCenter)
            chip.setToolTip(value)
            layout.insertWidget(1 + index, chip)
            self.header_chips.append(chip)

    def refresh_theme(self) -> None:
        """Refresh custom-painted and pixmap-based viewer controls after theme changes."""

        self.overlay_button.setIcon(themed_icon("layers", size=16))
        self.fit_button.setIcon(themed_icon("maximize", size=16))
        self.zoom_in_button.setIcon(themed_icon("zoom-in", size=16))
        self.zoom_out_button.setIcon(themed_icon("zoom-out", size=16))
        if self._current_value is not None:
            self._refresh_marker_selection()
        self.viewer.viewport().update()
        self.viewer.scene().update()
        self.image_badge.update()
        self.scale_bar.update()
        self.overlay_panel.update()
        self.update()

    def _update_header(self, value: Any) -> None:
        self.header_title.setText(_title_for_item(value))
        chips = _chips_for_item(value)
        if isinstance(value, TiltSeries) and self._item_status_for is not None:
            status = self._item_status_for(value).status
            if status:
                chips.insert(0, "Acquired" if status == "done" else status)
        self._set_header_chips(chips)
        self._update_navigation_actions(value)

    def _update_navigation_actions(self, value: Any | None) -> None:
        if not self._navigation_buttons:
            return
        actions = (
            self._navigation_actions_for(value)
            if value is not None and self._navigation_actions_for is not None
            else []
        )
        by_key = {action.key: action for action in actions}
        any_visible = False
        for key, button in self._navigation_buttons.items():
            action = by_key.get(key)
            if action is None:
                button.setVisible(False)
                continue
            button.setText(action.label)
            button.setEnabled(action.enabled)
            button.setToolTip(action.tooltip)
            button.setVisible(True)
            any_visible = True
        if not any_visible:
            for button in self._navigation_buttons.values():
                button.setVisible(False)

    def _navigation_button_clicked(self, key: str) -> None:
        if self._current_value is None or self._on_navigation_requested is None:
            return
        self._on_navigation_requested(self._current_value, key)

    def selected_marker_id(self) -> str | None:
        return self._selected_marker_id_override

    def _filter_list(self, text: str) -> None:
        query = text.strip().lower()
        visible_count = 0
        for index in range(self.list.topLevelItemCount()):
            item = self.list.topLevelItem(index)
            status = str(item.data(0, LIST_STATUS_ROLE) or "")
            summary = str(item.data(0, LIST_SUMMARY_ROLE) or "")
            matches = (
                not query
                or query in item.text(0).lower()
                or query in status.lower()
                or query in summary.lower()
                or query in item.toolTip(0).lower()
            )
            item.setHidden(not matches)
            if matches:
                visible_count += 1
        self.list_count.setText(str(visible_count if query else self.list.topLevelItemCount()))

    def _install_frame_shortcuts(self) -> None:
        for key, step in ((Qt.Key.Key_Left, -1), (Qt.Key.Key_Right, 1)):
            shortcut = QShortcut(QKeySequence(key), self)
            shortcut.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
            shortcut.activated.connect(lambda step=step: self._step_frame(step))

    def _step_frame(self, step: int) -> None:
        if self.slice_slider.isHidden() or not self.slice_slider.isEnabled():
            return
        next_value = max(self.slice_slider.minimum(), min(self.slice_slider.maximum(), self.slice_slider.value() + step))
        if next_value != self.slice_slider.value():
            self.slice_slider.setValue(next_value)

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
    ) -> None:
        self._items = items
        self._sources_for = sources_for
        self._prepared_sources = prepared_sources or {}
        self._item_status_for = item_status_for
        if markers_for is not None:
            self._markers_for = markers_for
        self._current_path = None
        self._current_fallback = None
        self._displayed_path = None
        self._displayed_slice_index = None
        self._current_value = None
        self._selected_marker_id_override = None
        self._current_mrc_max_size = MAX_PREVIEW_DIMENSION
        self._request_id += 1
        self._slider_debounce.stop()
        self.list.blockSignals(True)
        self.list.setUpdatesEnabled(False)
        try:
            self.list.clear()
            empty_text = "Select an item" if items else "No items for the current selection."
            self.viewer.clear(empty_text)
            self.image_badge.setVisible(False)
            self.scale_bar.setVisible(False)
            self._set_status_text(empty_text)
            self.header_title.setText(empty_text)
            self._set_header_chips([])
            self._update_navigation_actions(None)
            self.list_count.setText(str(len(items)))
            self.list_header.setText(_panel_title_for_items(items))
            self._set_slice_state(1, 0, valid_stack=False)
            tree_items: list[QTreeWidgetItem] = []
            for index, item in enumerate(items):
                label = label_for(item)
                list_status = item_status_for(item) if item_status_for is not None else None
                status = list_status.status if list_status is not None else _status_label_for_item(item)
                summary = list_status.summary if list_status is not None else _summary_for_item(item)
                tooltip = list_status.tooltip if list_status is not None else ""
                tree_item = QTreeWidgetItem([label])
                tooltip_text = tooltip or (f"{label}\n{summary}" if summary else label)
                tree_item.setToolTip(0, tooltip_text)
                tree_item.setData(0, VIEWER_OBJECT_ROLE, item)
                tree_item.setData(0, LIST_PRIMARY_ROLE, label)
                tree_item.setData(0, LIST_SUMMARY_ROLE, summary)
                tree_item.setData(0, LIST_STATUS_ROLE, status)
                tree_item.setData(0, LIST_TOOLTIP_ROLE, tooltip_text)
                tree_item.setSizeHint(0, QSize(0, 58))
                tree_items.append(tree_item)
            self.list.addTopLevelItems(tree_items)
            if items and auto_load_preview:
                self._load_initial_item()
        finally:
            self.list.setUpdatesEnabled(True)
            self.list.blockSignals(False)
        self._filter_list(self.list_filter.text())
        if items:
            QTimer.singleShot(0, lambda: fade_in(self.viewer_shell, duration_ms=140, start_opacity=0.82))
            if self.list.isVisible():
                QTimer.singleShot(30, lambda: fade_in(self.list.viewport(), duration_ms=140, start_opacity=0.82))

    def ensure_initial_preview_loaded(self, *, notify_selection: bool = True) -> None:
        if self._current_value is None and self._items:
            self._load_initial_item(notify_selection=notify_selection)
        elif notify_selection and self._current_value is not None and self._on_item_selected is not None:
            self._on_item_selected(self._current_value)

    def _load_initial_item(self, *, notify_selection: bool = True) -> None:
        if not self._items:
            return
        initial_index = 0
        for index, item in enumerate(self._items):
            source = self._source_for_value(item).primary
            if source is not None:
                initial_index = index
                break
        if self.list.isVisible():
            previous = self.list.blockSignals(True)
            try:
                self.list.setCurrentItem(self.list.topLevelItem(initial_index))
            finally:
                self.list.blockSignals(previous)
            self._load_value(self._items[initial_index], 0)
        else:
            self._load_value(self._items[initial_index], 0)
        if notify_selection and self._on_item_selected is not None:
            self._on_item_selected(self._items[initial_index])

    def select_object(self, value: Any) -> None:
        for index in range(self.list.topLevelItemCount()):
            item = self.list.topLevelItem(index)
            if item.data(0, VIEWER_OBJECT_ROLE) is value:
                if item.isHidden():
                    item.setHidden(False)
                self.list.setCurrentItem(item)
                self.list.scrollToItem(item, QAbstractItemView.ScrollHint.PositionAtCenter)
                return
        self._load_value(value, 0)

    def select_frame(self, frame_index: int) -> None:
        """Select a zero-based frame in the currently loaded stack, if any."""

        if self._current_path is None:
            return
        frame_count = self._known_frame_count_for_path(self._current_path)
        if frame_count <= 0:
            return
        clamped = max(0, min(int(frame_index), frame_count - 1))
        if self.slice_slider.isVisible() and self.slice_slider.isEnabled():
            if self.slice_slider.value() != clamped:
                self.slice_slider.setValue(clamped)
            return
        self._load_path(self._current_path, self._current_fallback, clamped)

    def _item_changed(self, current: QTreeWidgetItem | None, previous: QTreeWidgetItem | None) -> None:
        if current is None:
            return
        value = current.data(0, VIEWER_OBJECT_ROLE)
        self._load_value(value, 0)
        if self._on_item_selected is not None:
            self._on_item_selected(value)

    def _slider_changed(self, value: int) -> None:
        self._pending_slice_index = value
        self._update_frame_label(self.slice_slider.maximum() + 1)
        self._notify_frame_changed(self.slice_slider.maximum() + 1)
        if self._current_path is not None:
            self._slider_debounce.start()

    def _load_pending_slice(self) -> None:
        if self._current_path is not None:
            self._load_path(self._current_path, self._current_fallback, self._pending_slice_index)

    def _load_value(self, value: Any, slice_index: int) -> None:
        sources = self._source_for_value(value)
        path = sources.primary
        self._current_value = value
        self._selected_marker_id_override = None
        self._update_header(value)
        markers = self._markers_for(value)
        self._update_marker_type_availability(markers)
        self.viewer.set_markers(markers, selected_marker_id=self._active_selected_marker_id(value, markers))
        if path is None:
            self._current_path = None
            self._current_fallback = None
            self._displayed_path = None
            self._displayed_slice_index = None
            self.viewer.clear("Preview unavailable")
            self._set_status_text("No preview image found for this item.")
            self._set_slice_state(1, 0, valid_stack=False)
            return
        self._current_path = path
        self._current_fallback = sources.fallback
        self._current_mrc_max_size = self._initial_mrc_preview_size(path)
        slice_index = self._default_frame_index(value, path, slice_index)
        self._load_path(path, sources.fallback, slice_index)

    def _source_for_value(self, value: Any) -> PreviewSources:
        key = getattr(value, "id", None)
        if key is not None:
            prepared = self._prepared_sources.get(str(key))
            if prepared is not None:
                return prepared
        return self._sources_for(value)

    def _load_path(self, path: Path, fallback: Path | None, slice_index: int) -> None:
        if path.suffix.lower() == ".mrc":
            self._load_mrc_path(path, fallback, slice_index)
            return
        warnings = self.viewer.load_raster_path(path, logical_image_key=self._logical_image_key(path, 0))
        self._refresh_marker_selection()
        if not warnings:
            self._displayed_path = path
            self._displayed_slice_index = 0
        self._update_image_badge()
        self._update_scale_bar()
        self._set_slice_state(1, 0, valid_stack=False)
        self._set_status_text(
            self._compact_path_status("Image", path, warnings=warnings),
            tooltip=self._path_status_tooltip("Image", path, warnings=warnings),
        )

    def _load_mrc_path(self, path: Path, fallback: Path | None, slice_index: int) -> None:
        self._fallback_debounce.stop()
        self._pending_fallback_request = None
        frame_count = self._known_frame_count_for_path(path)
        clamped_index = max(0, min(slice_index, frame_count - 1)) if frame_count else max(0, slice_index)
        self._set_slice_state(frame_count or 1, clamped_index, valid_stack=(frame_count or 0) > 1)
        logical_key = self._logical_image_key(path, clamped_index)
        cached = MRC_PREVIEW_CACHE.get(_preview_cache_key(path, clamped_index, self._current_mrc_max_size))
        if cached is not None:
            self.viewer.set_image(
                cached.image,
                logical_image_key=logical_key,
                pyramid_level=self._current_mrc_max_size,
            )
            self._refresh_marker_selection()
            self._displayed_path = path
            self._displayed_slice_index = clamped_index
            self._update_image_badge()
            self._update_scale_bar()
            self._set_slice_state(cached.slice_count, clamped_index, valid_stack=cached.slice_count > 1)
            self._set_status(path, cached.slice_count, cached.warnings, "MRC cache")
            self._prefetch_neighbors(path, clamped_index, cached.slice_count)
            self._prefetch_current_high_detail(path, clamped_index)
            return

        self._request_id += 1
        request_id = self._request_id
        if fallback is not None:
            self._pending_fallback_request = (request_id, path, clamped_index, fallback, logical_key)
            self._set_status_text(
                f"Loading MRC preview: {path.name}",
                tooltip=f"MRC: {path}\nJPEG fallback will display if the MRC preview is not ready within {MRC_JPEG_FALLBACK_GRACE_MS} ms.",
            )
            self._fallback_debounce.start()
        elif self.viewer.has_image():
            self._set_status_text(
                f"Loading frame {clamped_index + 1} of {frame_count}: {path.name}",
                tooltip=str(path),
            )
        else:
            self.viewer.clear("Loading MRC preview...")
            self._set_status_text(f"Loading MRC preview: {path.name}", tooltip=str(path))
        self._thread_pool.start(_MrcLoadTask(request_id, path, clamped_index, self._current_mrc_max_size, self._mrc_signals))

    def _show_delayed_mrc_fallback(self) -> None:
        pending = self._pending_fallback_request
        if pending is None:
            return
        request_id, path, slice_index, fallback, logical_key = pending
        if request_id != self._request_id or path != self._current_path or slice_index != self.slice_slider.value():
            return
        if self._displayed_path == path:
            return
        warnings = self.viewer.load_raster_path(fallback, logical_image_key=logical_key)
        self._refresh_marker_selection()
        if not warnings:
            self._displayed_path = fallback
            self._displayed_slice_index = 0
        self._update_image_badge()
        self._update_scale_bar()
        self._set_status_text(
            self._compact_path_status("Loading MRC preview · JPEG fallback", fallback, warnings=warnings),
            tooltip=f"MRC: {path}\nFallback: {fallback}{_warning_tooltip(warnings)}",
        )
        LOGGER.debug(
            "Delayed JPEG placeholder path=%s mrc_path=%s grace_ms=%s",
            fallback,
            path,
            MRC_JPEG_FALLBACK_GRACE_MS,
        )

    def _known_frame_count_for_path(self, path: Path) -> int:
        value = self._current_value
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

    def _initial_mrc_preview_size(self, path: Path | None) -> int:
        if path is None or path.suffix.lower() != ".mrc":
            return MAX_PREVIEW_DIMENSION
        source_max = self._known_mrc_source_max_for_path(path)
        if source_max > 0:
            return max(1, min(FIRST_ZOOM_PREVIEW_DIMENSION, source_max))
        return FIRST_ZOOM_PREVIEW_DIMENSION

    def _set_slice_state(self, slice_count: int, slice_index: int, *, valid_stack: bool) -> None:
        show_slider = self._show_tilt_controls and valid_stack and slice_count > 1
        self.frame_label.setVisible(show_slider)
        self.slice_slider.setVisible(show_slider)
        self.slice_slider.blockSignals(True)
        self.slice_slider.setEnabled(show_slider)
        self.slice_slider.setMaximum(max(0, slice_count - 1))
        self.slice_slider.setValue(min(slice_index, max(0, slice_count - 1)))
        self.slice_slider.blockSignals(False)
        self._update_frame_label(slice_count)
        self._notify_frame_changed(slice_count)

    def _set_status(self, path: Path, slice_count: int, warnings: list[str], source: str) -> None:
        self._set_status_text(
            self._compact_path_status(source, path, slice_count=slice_count, warnings=warnings),
            tooltip=self._path_status_tooltip(source, path, slice_count=slice_count, warnings=warnings),
        )

    def _set_status_text(self, text: str, *, tooltip: str | None = None) -> None:
        self.status.setText(text)
        self.status.setToolTip(tooltip or text)

    def _compact_path_status(
        self,
        source: str,
        path: Path,
        *,
        slice_count: int = 1,
        warnings: list[str] | None = None,
    ) -> str:
        warning_text = _warning_count_text(warnings or [])
        return f"{source}: {path.name}{self._frame_text(slice_count)}{warning_text}"

    def _path_status_tooltip(
        self,
        source: str,
        path: Path,
        *,
        slice_count: int = 1,
        warnings: list[str] | None = None,
    ) -> str:
        return f"{source}: {path}{self._frame_text(slice_count)}{_warning_tooltip(warnings or [])}"

    def _frame_text(self, slice_count: int) -> str:
        if self.slice_slider.isHidden() or slice_count <= 1:
            return ""
        angle = self._tilt_angle_for(self._current_value, self.slice_slider.value()) if self._current_value is not None else None
        angle_text = f"    Tilt angle: {angle}" if angle else ""
        return f" Frame {self.slice_slider.value() + 1} of {slice_count}{angle_text}"

    def _update_frame_label(self, slice_count: int) -> None:
        text = self._frame_text(slice_count)
        self.frame_label.setText(text.strip() if text else "Frame")

    def _mrc_loaded(
        self,
        request_id: int,
        path: Path,
        slice_index: int,
        image: QImage,
        slice_count: int,
        warnings: list[str],
        estimated_bytes: int,
    ) -> None:
        if request_id != self._request_id or path != self._current_path or slice_index != self.slice_slider.value():
            LOGGER.debug("Ignored stale MRC preview path=%s slice=%s request=%s", path, slice_index, request_id)
            return
        self._fallback_debounce.stop()
        self._pending_fallback_request = None
        self.viewer.set_image(
            image,
            logical_image_key=self._logical_image_key(path, slice_index),
            pyramid_level=self._current_mrc_max_size,
        )
        self._refresh_marker_selection()
        self._displayed_path = path
        self._displayed_slice_index = slice_index
        self._update_image_badge()
        self._update_scale_bar()
        self._set_slice_state(slice_count, slice_index, valid_stack=slice_count > 1)
        self._set_status(path, slice_count, warnings, "MRC")
        self._prefetch_neighbors(path, slice_index, slice_count)
        self._prefetch_current_high_detail(path, slice_index)
        LOGGER.debug("Displayed MRC preview path=%s slice=%s estimated_bytes=%s", path, slice_index, estimated_bytes)

    def _mrc_failed(self, request_id: int, path: Path, slice_index: int, slice_count: int, warnings: list[str]) -> None:
        if request_id != self._request_id or path != self._current_path:
            return
        self._fallback_debounce.stop()
        self._pending_fallback_request = None
        if self._current_fallback is not None:
            if self._displayed_path == self._current_fallback:
                fallback_warnings = []
            else:
                fallback_warnings = self.viewer.load_raster_path(
                    self._current_fallback,
                    logical_image_key=self._logical_image_key(path, slice_index),
                )
                self._refresh_marker_selection()
                if not fallback_warnings:
                    self._displayed_path = self._current_fallback
                    self._displayed_slice_index = 0
                self._update_image_badge()
                self._update_scale_bar()
            self._set_slice_state(1, 0, valid_stack=False)
            all_warnings = warnings + fallback_warnings
            self._set_status_text(
                self._compact_path_status("JPEG fallback · MRC unavailable", self._current_fallback, warnings=all_warnings),
                tooltip=f"MRC unavailable: {path}\nFallback: {self._current_fallback}{_warning_tooltip(all_warnings)}",
            )
            return
        self.viewer.clear("Preview unavailable")
        self.image_badge.setVisible(False)
        self.scale_bar.setVisible(False)
        self._set_slice_state(slice_count, slice_index, valid_stack=False)
        self._set_status_text(
            self._compact_path_status("MRC unavailable", path, slice_count=slice_count, warnings=warnings),
            tooltip=self._path_status_tooltip("MRC unavailable", path, slice_count=slice_count, warnings=warnings),
        )

    def _logical_image_key(self, path: Path, slice_index: int) -> tuple[str, int, int | None]:
        return (str(path), slice_index, id(self._current_value) if self._current_value is not None else None)

    def _active_selected_marker_id(self, value: Any, markers: list[ImageMarker] | None = None) -> str | None:
        markers = markers if markers is not None else self._markers_for(value)
        if self._selected_marker_id_override is not None:
            if MarkerType.EXPOSURE_AREA in self._unavailable_marker_types:
                selected = next((marker for marker in markers if marker.id == self._selected_marker_id_override), None)
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
            return self._selected_marker_id_override
        if isinstance(value, SearchTile):
            area_name = str(value.metadata.get("ExposureAreaName") or "").strip().casefold()
            if area_name:
                if MarkerType.EXPOSURE_AREA not in self._unavailable_marker_types:
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

    def _refresh_marker_selection(self) -> None:
        if self._current_value is None:
            return
        markers = self._markers_for(self._current_value)
        self._update_marker_type_availability(markers)
        self.viewer.set_markers(markers, selected_marker_id=self._active_selected_marker_id(self._current_value, markers))

    def select_marker(self, marker_id: str | None) -> None:
        self._selected_marker_id_override = marker_id
        self._refresh_marker_selection()
        self._update_navigation_actions(self._current_value)

    def _marker_selected(self, marker: ImageMarker) -> None:
        self.select_marker(marker.id)
        if self._on_marker_selected is not None:
            self._on_marker_selected(marker)

    def _marker_opened(self, marker: ImageMarker) -> None:
        self.select_marker(marker.id)
        if self._on_marker_opened is not None:
            self._on_marker_opened(marker)

    def _marker_type_toggled(self, marker_type: str) -> None:
        checkbox = self._marker_type_checks[marker_type]
        self._user_marker_type_visible[marker_type] = checkbox.isChecked()
        self._apply_marker_type_visibility(marker_type)

    def _update_marker_type_availability(self, markers: list[ImageMarker]) -> None:
        has_camera_fov = any(marker.marker_type == MarkerType.CAMERA_FOV for marker in markers)
        has_batch_labels = any(marker.marker_type == MarkerType.BATCH_LABEL for marker in markers)
        has_camera_fallback = any(
            marker.marker_type == MarkerType.CAMERA_FOV
            and bool(marker.metadata.get("filled_fallback"))
            for marker in markers
        )
        unavailable = (
            {MarkerType.EXPOSURE_AREA, MarkerType.FOCUS_AREA, MarkerType.TRACKING_AREA}
            if has_camera_fallback
            else set()
        )
        if not has_camera_fov:
            unavailable.add(MarkerType.CAMERA_FOV)
        if not has_batch_labels:
            unavailable.add(MarkerType.BATCH_LABEL)
        self._unavailable_marker_types = unavailable
        for marker_type, checkbox in self._marker_type_checks.items():
            unavailable_reason = self._marker_unavailable_reason(marker_type)
            effective_visible = self._effective_marker_type_visible(marker_type)
            checkbox.blockSignals(True)
            checkbox.setEnabled(unavailable_reason is None)
            checkbox.setChecked(effective_visible)
            checkbox.setToolTip(
                unavailable_reason
                or f"Show {MARKER_TYPE_LABELS.get(marker_type, marker_type).lower()} markers"
            )
            checkbox.blockSignals(False)
            self.viewer.set_marker_type_visible(marker_type, effective_visible)

    def _marker_unavailable_reason(self, marker_type: str) -> str | None:
        if marker_type not in self._unavailable_marker_types:
            return None
        if marker_type == MarkerType.CAMERA_FOV:
            return "Camera dimensions are unavailable for this item"
        if marker_type == MarkerType.BATCH_LABEL:
            return "Batch labels are unavailable for this item"
        return "Unavailable because beam diameter metadata is missing"

    def _effective_marker_type_visible(self, marker_type: str) -> bool:
        return (
            self._user_marker_type_visible.get(marker_type, True)
            and marker_type not in self._unavailable_marker_types
        )

    def _apply_marker_type_visibility(self, marker_type: str) -> None:
        effective_visible = self._effective_marker_type_visible(marker_type)
        checkbox = self._marker_type_checks.get(marker_type)
        if checkbox is not None:
            checkbox.blockSignals(True)
            checkbox.setChecked(effective_visible)
            checkbox.blockSignals(False)
        self.viewer.set_marker_type_visible(marker_type, effective_visible)

    def _overlay_panel_toggled(self, checked: bool) -> None:
        self.overlay_panel.setVisible(checked)

    def _scale_bar_toggled(self) -> None:
        self._scale_bar_enabled = self.scale_bar_checkbox.isChecked()
        self._update_scale_bar()

    def _zoom_changed(self, zoom: float) -> None:
        self.zoom_label.setText(f"{max(1, round(zoom * 100))}%")
        self._update_scale_bar()
        if self._current_path is None or self._current_path.suffix.lower() != ".mrc":
            return
        source = MRC_SOURCE_CACHE.get(self._current_path)
        if source is not None:
            source_max = max(source.inspection.nx, source.inspection.ny)
        else:
            source_max = self._known_mrc_source_max_for_path(self._current_path)
        if source_max <= 0:
            LOGGER.debug("Skipping MRC zoom refresh until preview metadata is available path=%s", self._current_path)
            return
        target = self._current_mrc_max_size
        if zoom > 1.5:
            for level in PYRAMID_LEVELS:
                if level > self._current_mrc_max_size:
                    target = min(level, source_max)
                    break
        if target < self._current_mrc_max_size:
            target = self._current_mrc_max_size
        if target == self._current_mrc_max_size:
            return
        self._current_mrc_max_size = target
        self._load_path(self._current_path, self._current_fallback, self.slice_slider.value())

    def _known_mrc_source_max_for_path(self, path: Path) -> int:
        value = self._current_value
        metadata = getattr(value, "mrc_metadata", None)
        if metadata is None or getattr(metadata, "path", None) != path:
            return 0
        nx = int(metadata.nx or 0)
        ny = int(metadata.ny or 0)
        return max(nx, ny)

    def _update_image_badge(self) -> None:
        if self._displayed_path is None or self._current_value is None or not self.viewer.has_image():
            self.image_badge.setVisible(False)
            return
        kind = "MRC" if self._displayed_path.suffix.lower() == ".mrc" else self._displayed_path.suffix.lstrip(".").upper() or "Image"
        pixel_size = _pixel_size_meters_for_item(self._current_value, self._displayed_path)
        pixel_text = f" · {_format_pixel_size_label(pixel_size)}/px" if pixel_size is not None else ""
        self.image_badge.setText(f"{kind}{pixel_text}")
        self.image_badge.setToolTip(str(self._displayed_path))
        self.image_badge.setVisible(True)

    def _update_scale_bar(self) -> None:
        if not self._scale_bar_enabled or self._current_value is None or not self.viewer.has_image():
            self.scale_bar.setVisible(False)
            return
        pixel_size_m = _pixel_size_meters_for_item(self._current_value, self._displayed_path)
        if pixel_size_m is None or pixel_size_m <= 0:
            self.scale_bar.setVisible(False)
            return
        shape = self.viewer.displayed_image_shape()
        if shape is None:
            self.scale_bar.setVisible(False)
            return
        displayed_height, displayed_width = shape
        if displayed_width <= 0 or displayed_height <= 0:
            self.scale_bar.setVisible(False)
            return
        source_size = _source_image_size_for_item(self._current_value, self._displayed_path)
        source_width = float(source_size[0]) if source_size is not None and source_size[0] else float(displayed_width)
        meters_per_scene_px = pixel_size_m * (source_width / float(displayed_width))
        zoom = self.viewer.zoom_scale()
        target_screen_px = min(
            MAX_SCALE_BAR_SCREEN_PX,
            max(MIN_SCALE_BAR_SCREEN_PX, self.viewer.viewport().width() * 0.15),
        )
        target_meters = (target_screen_px / zoom) * meters_per_scene_px
        length_meters = _nice_scale_length_meters(target_meters)
        if length_meters is None:
            self.scale_bar.setVisible(False)
            return
        screen_px = (length_meters / meters_per_scene_px) * zoom
        if screen_px > MAX_SCALE_BAR_SCREEN_PX:
            smaller = _nice_scale_length_meters(target_meters * 0.5)
            if smaller is not None:
                length_meters = smaller
                screen_px = (length_meters / meters_per_scene_px) * zoom
        if screen_px < MIN_SCALE_BAR_SCREEN_PX:
            larger = _nice_scale_length_meters(target_meters * 2.0)
            if larger is not None:
                larger_px = (larger / meters_per_scene_px) * zoom
                if larger_px <= MAX_SCALE_BAR_SCREEN_PX:
                    length_meters = larger
                    screen_px = larger_px
        self.scale_bar.set_scale(screen_px, _format_scale_length_label(length_meters))

    def _default_frame_index(self, value: Any, path: Path, requested_index: int) -> int:
        if not self._show_tilt_controls or not isinstance(value, TiltSeries) or path.suffix.lower() != ".mrc":
            return requested_index
        metadata = value.mrc_metadata
        frame_count = int(metadata.nz) if metadata is not None and metadata.nz else value.number_of_frames or len(value.sections)
        if frame_count <= 0:
            return requested_index
        if frame_count <= 1:
            return 0
        angles = self._reliable_stack_order_angles_for(value, frame_count)
        if angles:
            frame_index = min(range(len(angles)), key=lambda index: abs(angles[index]))
            LOGGER.debug(
                "Tilt default frame from explicit tilt metadata path=%s frame=%s angle=%s",
                path,
                frame_index,
                angles[frame_index],
            )
            return frame_index
        LOGGER.debug("Tilt default frame fallback path=%s frame=%s reason=%s", path, frame_count // 2, "explicit tilt metadata unavailable")
        return frame_count // 2

    def _reliable_stack_order_angles_for(self, value: Any, frame_count: int) -> list[float] | None:
        if not isinstance(value, TiltSeries):
            return None
        angle_info = explicit_stack_order_tilt_angles(value, frame_count)
        if angle_info is not None:
            angles, _source = angle_info
            return angles
        return None

    def _stack_order_angles_for(self, value: Any, frame_count: int) -> tuple[list[float], str] | None:
        if not isinstance(value, TiltSeries):
            return None
        return stack_order_tilt_angles(value, frame_count)

    def _notify_frame_changed(self, slice_count: int) -> None:
        if (
            self._on_frame_changed is None
            or self._current_value is None
            or self.slice_slider.isHidden()
            or slice_count <= 1
        ):
            return
        self._on_frame_changed(self._current_value, self.slice_slider.value(), slice_count)

    def _prefetch_neighbors(self, path: Path, slice_index: int, slice_count: int) -> None:
        if slice_count <= 1:
            return
        max_size = min(max(self._current_mrc_max_size, MAX_PREVIEW_DIMENSION), FIRST_ZOOM_PREVIEW_DIMENSION)
        source_max = self._known_mrc_source_max_for_path(path)
        if source_max > 0:
            max_size = min(max_size, source_max)
        for offset in (1, -1, 2, -2):
            neighbor = slice_index + offset
            if 0 <= neighbor < slice_count:
                self._prefetch_mrc_frame(path, neighbor, max_size)

    def _prefetch_current_high_detail(self, path: Path, slice_index: int) -> None:
        if self._current_mrc_max_size >= FIRST_ZOOM_PREVIEW_DIMENSION:
            return
        target = self._next_mrc_detail_level(path, self._current_mrc_max_size)
        if target is None:
            return
        target = min(target, FIRST_ZOOM_PREVIEW_DIMENSION)
        if target <= self._current_mrc_max_size:
            return
        self._prefetch_mrc_frame(path, slice_index, target)

    def _next_mrc_detail_level(self, path: Path, current_size: int) -> int | None:
        source = MRC_SOURCE_CACHE.get(path)
        if source is not None:
            source_max = max(source.inspection.nx, source.inspection.ny)
        else:
            source_max = self._known_mrc_source_max_for_path(path)
        if source_max <= current_size:
            return None
        for level in PYRAMID_LEVELS:
            if level > current_size:
                return min(level, source_max)
        return source_max

    def _prefetch_mrc_frame(self, path: Path, slice_index: int, max_size: int = MAX_PREVIEW_DIMENSION) -> None:
        key = _preview_cache_key(path, slice_index, max_size)
        if MRC_PREVIEW_CACHE.get(key) is not None:
            return
        if key in MRC_PREFETCH_KEYS:
            return
        MRC_PREFETCH_KEYS.add(key)
        self._thread_pool.start(_MrcLoadTask(-1, path, slice_index, max_size, self._mrc_signals))


def _panel_title_for_items(items: list[Any]) -> str:
    if not items:
        return "Items"
    first = items[0]
    if isinstance(first, Overview):
        return "OVERVIEWS"
    if isinstance(first, SearchMap):
        return "SEARCH MAPS"
    if isinstance(first, SearchTile):
        return "SEARCH"
    if isinstance(first, BatchPosition):
        return "BATCH POSITIONS"
    if isinstance(first, TiltSeries):
        return "TILT SERIES"
    if isinstance(first, Atlas):
        return "ATLASES"
    return "ITEMS"


def _title_for_item(value: Any) -> str:
    if isinstance(value, Atlas):
        return "Atlas"
    if isinstance(value, Overview):
        return format_overview_display_name(value.name)
    if isinstance(value, SearchMap):
        return value.name
    if isinstance(value, SearchTile):
        return value.name
    if isinstance(value, BatchPosition):
        return value.name or value.id
    if isinstance(value, TiltSeries):
        return value.name
    return str(value)


def _chips_for_item(value: Any) -> list[str]:
    if isinstance(value, Atlas):
        chips: list[str] = []
        if value.tile_paths:
            chips.append(f"{len(value.tile_paths)} tiles")
        if value.mrc_metadata and value.mrc_metadata.nx and value.mrc_metadata.ny:
            chips.append(f"{value.mrc_metadata.nx} × {value.mrc_metadata.ny}")
        return chips
    if isinstance(value, Overview):
        chips = []
        if value.linked_search_map_ids:
            chips.append(f"{len(value.linked_search_map_ids)} search maps")
        return chips
    if isinstance(value, SearchMap):
        chips = []
        if value.tile_paths:
            chips.append(f"{len({path.stem for path in value.tile_paths})} tiles")
        if value.linked_batch_position_ids:
            chips.append(f"{len(value.linked_batch_position_ids)} batch positions")
        return chips
    if isinstance(value, SearchTile):
        chips = []
        if value.tile_index is not None:
            chips.append(f"tile {value.tile_index}")
        if value.batch_position_name:
            chips.append(value.batch_position_name)
        if value.linked_tilt_series_ids:
            chips.append(f"{len(value.linked_tilt_series_ids)} tilt series")
        return chips
    if isinstance(value, BatchPosition):
        chips = []
        if value.status:
            chips.append(value.status)
        if value.linked_tilt_series_ids:
            chips.append(f"{len(value.linked_tilt_series_ids)} tilt series")
        return chips
    if isinstance(value, TiltSeries):
        chips = []
        if value.tilt_count:
            chips.append(f"{value.tilt_count} frames")
        step = _tilt_step_chip(value)
        if step:
            chips.append(step)
        if value.tilt_range:
            chips.append(_range_chip(value.tilt_range, "°"))
        duration = _tilt_duration_chip(value)
        if duration:
            chips.append(duration)
        if value.pixel_size:
            chips.append(f"{value.pixel_size:g} A/px")
        return chips
    return []


def _summary_for_item(value: Any) -> str:
    if isinstance(value, Overview):
        return f"{len(value.linked_search_map_ids)} linked search maps"
    if isinstance(value, SearchMap):
        tile_count = len({path.stem for path in value.tile_paths})
        return f"{tile_count} tiles · {len(value.linked_batch_position_ids)} batch positions"
    if isinstance(value, SearchTile):
        parts = []
        if value.search_map_name:
            parts.append(value.search_map_name)
        if value.batch_position_name:
            parts.append(value.batch_position_name)
        if value.linked_tilt_series_ids:
            parts.append(f"{len(value.linked_tilt_series_ids)} tilt series")
        return " · ".join(parts)
    if isinstance(value, BatchPosition):
        status = value.status or "unknown"
        return f"{status} · {len(value.linked_tilt_series_ids)} linked tilt series"
    if isinstance(value, TiltSeries):
        tilt_count = value.tilt_count or len(value.sections)
        return f"{tilt_count or 'unknown'} frames · {_range_chip(value.tilt_range, '°') if value.tilt_range else 'range unknown'}"
    if isinstance(value, Atlas):
        return f"{len(value.tile_paths)} tiles"
    return ""


def _status_label_for_item(value: Any) -> str:
    if isinstance(value, BatchPosition):
        raw = (value.status or "").strip().lower()
        if raw in {"acquired", "done", "complete", "completed"}:
            return "done"
        if raw in {"failed", "error"}:
            return "fail"
        if raw in {"pending", "queued", "scheduled"}:
            return "queued"
        return raw or ""
    if isinstance(value, SearchMap):
        return "done" if value.tile_paths else "empty"
    if isinstance(value, SearchTile):
        return "done" if value.image_path is not None else "missing"
    if isinstance(value, TiltSeries):
        if value.mrc_path is None or not value.mrc_path.exists():
            return "missing"
        return "available"
    return ""


def _range_chip(value: tuple[float, float] | None, suffix: str) -> str:
    if value is None:
        return "unknown"
    lo, hi = value
    return f"{lo:g}{suffix} → {hi:g}{suffix}"


def _tilt_step_chip(value: TiltSeries) -> str | None:
    if value.tilt_range is None:
        return None
    count = value.tilt_count or len(value.sections)
    if count <= 1:
        return None
    low, high = value.tilt_range
    step = abs((high - low) / (count - 1))
    if step <= 0:
        return None
    return f"{step:g}° step"


def _tilt_duration_chip(value: TiltSeries) -> str | None:
    segment = build_acquisition_timeline(value)
    if segment is None:
        return None
    seconds = max((segment.end - segment.start).total_seconds(), 0)
    return _compact_duration(seconds)


def _compact_duration(seconds: float) -> str:
    total_seconds = int(round(seconds))
    minutes, secs = divmod(total_seconds, 60)
    hours, mins = divmod(minutes, 60)
    if hours:
        return f"{hours}h {mins:02d}m"
    if mins:
        return f"{mins}m {secs:02d}s"
    return f"{secs}s"


def overview_preview_path(overview: Overview) -> PreviewSources:
    return _prefer_matching_mrc(overview.image_path)


def search_map_preview_path(search_map: SearchMap) -> PreviewSources:
    return PreviewSources(search_map.mrc_path or search_map.image_path, search_map.image_path if search_map.mrc_path else None)


def search_tile_preview_path(search_tile: SearchTile) -> PreviewSources:
    return _prefer_matching_mrc(search_tile.image_path)


def batch_position_preview_path(batch_position: BatchPosition) -> PreviewSources:
    preferred = (
        batch_position.search_image_path
        or batch_position.tracking_image_path
        or batch_position.exposure_image_path
        or next(iter(batch_position.exposure_image_paths), None)
        or batch_position.overview_image_path
    )
    return _prefer_matching_mrc(preferred)


def tilt_series_preview_path(tilt_series: TiltSeries) -> PreviewSources:
    return PreviewSources(tilt_series.mrc_path)


def atlas_preview_path(atlas: Atlas) -> PreviewSources:
    return _prefer_matching_mrc(atlas.image_path)


def _prefer_matching_mrc(path: Path | None) -> PreviewSources:
    if path is None:
        return PreviewSources(None)
    if path.suffix.lower() == ".mrc":
        return PreviewSources(path)
    mrc_path = path.with_suffix(".mrc")
    if mrc_path.exists():
        return PreviewSources(mrc_path, path)
    return PreviewSources(path)


def _mrc_source(path: Path) -> MrcPreviewSource:
    source = MRC_SOURCE_CACHE.get(path)
    if source is None:
        source = MrcPreviewSource(path)
        MRC_SOURCE_CACHE[path] = source
    return source


def _preview_cache_key(path: Path, slice_index: int, max_size: int) -> PreviewCacheKey:
    try:
        mtime = path.stat().st_mtime
    except OSError:
        mtime = 0.0
    return (path, slice_index, max_size, mtime, NORMALISATION)


def _pil_to_qimage(image: Image.Image) -> QImage:
    grayscale = image.convert("L")
    data = grayscale.tobytes()
    return QImage(data, grayscale.width, grayscale.height, grayscale.width, QImage.Format.Format_Grayscale8).copy()
