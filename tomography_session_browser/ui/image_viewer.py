from __future__ import annotations

from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass, replace
import logging
import math
from pathlib import Path
import time
from typing import Any

from PIL import Image
from PySide6.QtCore import QObject, QPoint, QPointF, QRect, QRectF, QRunnable, QSize, QStandardPaths, Qt, QThreadPool, QTimer, Signal, Slot
from PySide6.QtGui import QBrush, QColor, QFont, QFontMetricsF, QIcon, QImage, QKeyEvent, QKeySequence, QMouseEvent, QPainter, QPainterPath, QPen, QPixmap, QPolygonF, QRegion, QShortcut, QWheelEvent
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFrame,
    QGraphicsItem,
    QGraphicsPixmapItem,
    QGraphicsScene,
    QGraphicsView,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QHeaderView,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSlider,
    QSplitter,
    QStyle,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QStyleOptionSlider,
    QToolButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from tomography_session_browser.domain.display_names import count_phrase, format_overview_display_name
from tomography_session_browser.domain.markers import ImageMarker, MarkerType
from tomography_session_browser.domain.models import Atlas, BatchPosition, MrcMetadata, Overview, SearchMap, SearchTile, TiltSeries
from tomography_session_browser.domain.units import ANGSTROM, ANGSTROM_PER_PIXEL, DEGREE, MICROMETRE, NANOMETRE
from tomography_session_browser.ui.context_stack import open_action_label
from tomography_session_browser.ui.empty_states import (
    KIND_LOAD_FAILED,
    EmptyState,
    missing_preview_state,
    viewer_empty_state,
)
from tomography_session_browser.ui.widgets.empty_state_panel import EmptyStatePanel
from tomography_session_browser.ui.list_decorations import paint_selection_marker
from tomography_session_browser.parsers.mrc_parser import MrcPreviewSource, NORMALISATION
from tomography_session_browser.parsers.xml_parser import find_first
from tomography_session_browser.services.item_status import ItemListStatus, display_status_label
from tomography_session_browser.services.status_taxonomy import acquisition_state
from tomography_session_browser.services.batch_label_service import (
    LABEL_STROKE_WIDTH_PX,
    AtlasLabelPlacement,
    atlas_batch_label_placements,
    batch_label_screen_font_size_px,
)
from tomography_session_browser.services.atlas_marker_style import (
    CLUSTER_HALO_WIDTH_DP,
    CLUSTER_INNER_GAP_DP,
    CLUSTER_SEGMENT_WIDTH_DP,
    IMAGE_STATUS_COLOURS,
    LABEL_CORNER_RADIUS_DP,
    LABEL_FONT_SIZE_DP,
    LABEL_HEIGHT_DP,
    LEAF_DIAMETER_DP,
    LEAF_FAILED_GLYPH_WIDTH_DP,
    LEAF_FILL_RADIUS_DP,
    LEAF_HALO_WIDTH_DP,
    LEAF_HIT_RADIUS_DP,
    LEAF_RING_RADIUS_DP,
    LEAF_STATUS_WIDTH_DP,
    MARKER_INK,
    MARKER_LABEL_TEXT,
    MARKER_SELECTED,
    STATE_RING_OFFSET_DP,
    cluster_ring_segments,
)
from tomography_session_browser.services.marker_clustering import (
    CLUSTER_RADIUS_PX,
    MAX_ATLAS_ZOOM,
    NO_CLUSTER_ABOVE_ZOOM,
    PER_EXPOSURE_MIN_SCREEN_PX,
    SEARCH_MAP_FOOTPRINT_MIN_SCREEN_PX,
    SEARCH_MAP_TILE_MIN_SCREEN_PX,
    ZOOM_DEBOUNCE_MS,
    ClusterInput,
    MarkerCluster,
    ScreenTransform,
    cluster_markers,
    marker_set_fingerprint,
    screen_extent_visible,
    zoom_bucket,
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
# Verb-plus-destination names for the viewer's jump buttons. The button faces
# stay short because the strip is dense; these carry the full phrasing to
# assistive technology and tooltips.
NAVIGATION_ACTION_NAMES: dict[str, str] = {
    "batch": open_action_label("Batch position"),
    "search": open_action_label("Search"),
    "search_map": open_action_label("Search map"),
    "overview": open_action_label("Overview"),
    "tilt_series": open_action_label("Tilt series"),
}
# How many logical images keep a remembered crop. Four proportional floats
# each, so the cap is about memory discipline rather than size.
VIEWPORT_MEMORY_LIMIT = 32
# The overlay panel is bounded to its canvas so it cannot grow off-screen when
# a linked root contributes many collections; the dynamic list scrolls instead.
OVERLAY_PANEL_MARGIN_PX = 24
ATLAS_COLLECTION_LIST_MAX_PX = 180
FIRST_ZOOM_PREVIEW_DIMENSION = 4096
MRC_JPEG_FALLBACK_GRACE_MS = 220
PYRAMID_LEVELS = (2048, 4096, 8192)
LOGGER = logging.getLogger(__name__)
MEDIUM_DETAIL_SCALE = 0.9
HIGH_DETAIL_SCALE = 1.6
MIN_SCALE_BAR_SCREEN_PX = 58.0
MAX_SCALE_BAR_SCREEN_PX = 190.0
ZOOM_LABEL_MIN_WIDTH_PX = 48
ZOOM_PANEL_HORIZONTAL_PADDING_PX = 6
FLOATING_CONTROL_INSET_PX = 18
VIEWER_LIST_MIN_WIDTH_PX = 268
# Keep every bottom-floating viewer control on the same baseline so the scale
# bar and zoom strip feel anchored to one frame edge instead of separate panes.
FLOATING_CONTROL_BOTTOM_INSET_PX = 24
EXPORT_RENDER_SCALE = 2.0
MAX_EXPORT_DIMENSION_PX = 8192
MAX_EXPORT_PIXELS = 50_000_000
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
    MarkerType.BATCH_CLUSTER: "Batch-position cluster",
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

ATLAS_LOD_CLUSTER = "cluster_batch_positions"
ATLAS_LOD_LABELS = "batch_position_labels"
ATLAS_LOD_TILE_GRIDS = "search_map_tile_grids"
ATLAS_LOD_EXPOSURES = "per_exposure_markers"
ATLAS_LOD_CONTROL_LABELS = (
    (ATLAS_LOD_CLUSTER, "Cluster batch positions", "cluster-positions", True),
    (ATLAS_LOD_LABELS, "Batch position labels", "position-labels", True),
    (ATLAS_LOD_TILE_GRIDS, "Search-map tile grids", "tile-grid", True),
    (ATLAS_LOD_EXPOSURES, "Per-exposure markers", "exposure-markers", False),
)


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


@dataclass(frozen=True, slots=True)
class AtlasCollectionOverlayOption:
    key: str
    label: str
    visible: bool = True


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

    def _emit(self, signal, *args: object) -> None:
        """Emit unless the receiving widget has already been torn down.

        Closing the window while a preview is still decoding destroys the
        C++ side of ``_MrcLoadSignals`` under this worker, and the bare emit
        surfaced as an uncaught ``RuntimeError`` in the crash log on exit.
        """

        try:
            signal.emit(*args)
        except RuntimeError:
            LOGGER.debug("Dropped preview result for %s; viewer was closed", self._path)

    @Slot()
    def run(self) -> None:
        cache_key = _preview_cache_key(self._path, max(0, self._slice_index), self._max_size)
        try:
            source = _mrc_source(self._path)
            preview = source.get_frame_preview(self._slice_index, max_size=self._max_size)
        except Exception as exc:  # pragma: no cover - exercised through UI thread safety
            LOGGER.warning("MRC preview load failed path=%s slice=%s: %s", self._path, self._slice_index, exc)
            MRC_PREFETCH_KEYS.discard(cache_key)
            self._emit(self._signals.failed, self._request_id, self._path, max(0, self._slice_index), 0, [str(exc)])
            return
        if preview.image is None:
            MRC_PREFETCH_KEYS.discard(cache_key)
            self._emit(
                self._signals.failed,
                self._request_id,
                self._path,
                preview.slice_index,
                preview.slice_count,
                preview.warnings,
            )
            return
        image = _pil_to_qimage(preview.image)
        estimated_bytes = image.sizeInBytes()
        cache_key = _preview_cache_key(self._path, preview.slice_index, self._max_size)
        MRC_PREVIEW_CACHE.put(
            cache_key,
            CachedPreview(image, preview.slice_count, preview.warnings, estimated_bytes),
        )
        MRC_PREFETCH_KEYS.discard(cache_key)
        self._emit(
            self._signals.loaded,
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


class _AtlasLodMarkerItem(QGraphicsItem):
    """Fixed-screen-size Atlas glyph with an 11 dp interaction target."""

    def __init__(self, marker: ImageMarker) -> None:
        super().__init__()
        self._marker = marker
        self._hovered = False
        diameter = float(marker.metadata.get("diameter_px") or LEAF_DIAMETER_DP)
        self._radius = diameter / 2.0
        self._hit_radius = max(
            LEAF_HIT_RADIUS_DP,
            self._radius + STATE_RING_OFFSET_DP
            if marker.marker_type == MarkerType.BATCH_CLUSTER
            else LEAF_HIT_RADIUS_DP,
        )
        margin = max(
            self._hit_radius - self._radius,
            STATE_RING_OFFSET_DP + 4.0,
        )
        self._bounds = QRectF(
            -self._radius - margin,
            -self._radius - margin,
            diameter + margin * 2,
            diameter + margin * 2,
        )
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIgnoresTransformations, True)
        self.setAcceptHoverEvents(bool(marker.metadata.get("navigation_enabled", True)))
        self._update_z_value()

    def boundingRect(self) -> QRectF:  # noqa: N802 - Qt signature
        return self._bounds

    def shape(self) -> QPainterPath:
        path = QPainterPath()
        path.addEllipse(
            QPointF(0.0, 0.0),
            self._hit_radius,
            self._hit_radius,
        )
        return path

    def hoverEnterEvent(self, event) -> None:  # noqa: N802 - Qt signature
        if bool(self._marker.metadata.get("navigation_enabled", True)):
            self._hovered = True
            self.setCursor(Qt.CursorShape.PointingHandCursor)
            self._update_z_value()
            self.update()
        super().hoverEnterEvent(event)

    def hoverLeaveEvent(self, event) -> None:  # noqa: N802 - Qt signature
        self._hovered = False
        self.unsetCursor()
        self._update_z_value()
        self.update()
        super().hoverLeaveEvent(event)

    def _update_z_value(self) -> None:
        status = (self._marker.status or "").lower()
        if self._marker.selected:
            z_value = 50.0
        elif self._hovered:
            z_value = 40.0
        elif status == "failed":
            z_value = 30.0
        elif self._marker.marker_type == MarkerType.BATCH_CLUSTER:
            z_value = 20.0
        else:
            z_value = 10.0
        self.setZValue(z_value)

    def paint(self, painter: QPainter, _option, _widget=None) -> None:
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        status = (self._marker.status or "queued").lower()
        if self._marker.marker_type == MarkerType.BATCH_CLUSTER:
            self._paint_cluster(painter)
        else:
            self._paint_leaf(painter, status)

    def _paint_leaf(self, painter: QPainter, status: str) -> None:
        navigable = bool(self._marker.metadata.get("navigation_enabled", True))
        colour = _atlas_status_color(status)
        if not navigable and status != "unattributed":
            colour = _atlas_status_color("unattributed")
        rect = QRectF(
            -LEAF_RING_RADIUS_DP,
            -LEAF_RING_RADIUS_DP,
            LEAF_RING_RADIUS_DP * 2,
            LEAF_RING_RADIUS_DP * 2,
        )

        if navigable and status in {"collected", "failed"}:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(colour)
            painter.drawEllipse(
                QPointF(0.0, 0.0),
                LEAF_FILL_RADIUS_DP,
                LEAF_FILL_RADIUS_DP,
            )
        elif navigable and status == "partial":
            fill_rect = QRectF(
                -LEAF_FILL_RADIUS_DP,
                -LEAF_FILL_RADIUS_DP,
                LEAF_FILL_RADIUS_DP * 2,
                LEAF_FILL_RADIUS_DP * 2,
            )
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(colour)
            painter.drawPie(fill_rect, 90 * 16, 180 * 16)

        halo = QPen(QColor(MARKER_INK), LEAF_HALO_WIDTH_DP)
        halo.setCapStyle(Qt.PenCapStyle.FlatCap)
        halo.setJoinStyle(Qt.PenJoinStyle.MiterJoin)
        painter.setPen(halo)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawEllipse(rect)

        status_pen = QPen(colour, LEAF_STATUS_WIDTH_DP)
        status_pen.setCapStyle(Qt.PenCapStyle.FlatCap)
        status_pen.setJoinStyle(Qt.PenJoinStyle.MiterJoin)
        if status == "unattributed":
            status_pen.setDashPattern(
                [3.9 / LEAF_STATUS_WIDTH_DP, 2.4 / LEAF_STATUS_WIDTH_DP]
            )
            circumference = 2.0 * math.pi * LEAF_RING_RADIUS_DP
            status_pen.setDashOffset((circumference / 4.0) / LEAF_STATUS_WIDTH_DP)
        painter.setPen(status_pen)
        painter.drawEllipse(rect)

        if navigable and status == "failed":
            glyph = LEAF_FILL_RADIUS_DP * 0.62
            cross_pen = QPen(QColor(MARKER_INK), LEAF_FAILED_GLYPH_WIDTH_DP)
            cross_pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            painter.setPen(cross_pen)
            painter.drawLine(QPointF(-glyph, -glyph), QPointF(glyph, glyph))
            painter.drawLine(QPointF(glyph, -glyph), QPointF(-glyph, glyph))

        self._paint_interaction_state(painter, colour, navigable=navigable)

    def _paint_cluster(self, painter: QPainter) -> None:
        diameter = self._radius * 2.0
        ring_radius = diameter / 2.0 - 2.3
        colour = _atlas_status_color(self._marker.status or "queued")
        inner_radius = (
            ring_radius - CLUSTER_SEGMENT_WIDTH_DP / 2.0 - CLUSTER_INNER_GAP_DP
        )
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(colour)
        painter.drawEllipse(QPointF(0.0, 0.0), inner_radius, inner_radius)

        ring_rect = QRectF(
            -ring_radius,
            -ring_radius,
            ring_radius * 2.0,
            ring_radius * 2.0,
        )
        halo = QPen(QColor(MARKER_INK), CLUSTER_HALO_WIDTH_DP)
        halo.setCapStyle(Qt.PenCapStyle.FlatCap)
        painter.setPen(halo)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawEllipse(ring_rect)

        counts = self._marker.metadata.get("status_counts")
        if isinstance(counts, dict):
            for segment in cluster_ring_segments(counts):
                pen = QPen(
                    _atlas_status_color(segment.status),
                    CLUSTER_SEGMENT_WIDTH_DP,
                )
                pen.setCapStyle(Qt.PenCapStyle.FlatCap)
                painter.setPen(pen)
                # Qt uses positive counter-clockwise sweeps.  Negating both
                # authored screen-space angles keeps 12 o'clock as the start
                # and proceeds clockwise.
                painter.drawArc(
                    ring_rect,
                    round(-segment.start_degrees * 16),
                    round(-segment.sweep_degrees * 16),
                )

        count = len(self._marker.metadata.get("member_ids", ()))
        from tomography_session_browser.ui.theme import MONO_FONT_NAME

        font = QFont(MONO_FONT_NAME)
        font.setPixelSize(
            max(1, round(diameter * (0.30 if count >= 100 else 0.36)))
        )
        font.setWeight(QFont.Weight.DemiBold)
        painter.setFont(font)
        painter.setPen(QColor(MARKER_INK))
        painter.drawText(
            QRectF(-self._radius, -self._radius + 0.5, diameter, diameter),
            Qt.AlignmentFlag.AlignCenter,
            str(count),
        )
        self._paint_interaction_state(
            painter,
            colour,
            navigable=True,
            ring_radius=ring_radius,
        )

    def _paint_interaction_state(
        self,
        painter: QPainter,
        colour: QColor,
        *,
        navigable: bool,
        ring_radius: float = LEAF_RING_RADIUS_DP,
    ) -> None:
        if not navigable:
            return
        state_radius = ring_radius + STATE_RING_OFFSET_DP
        state_rect = QRectF(
            -state_radius,
            -state_radius,
            state_radius * 2,
            state_radius * 2,
        )
        if self._marker.selected:
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(QPen(QColor(MARKER_INK), 3.0))
            painter.drawEllipse(state_rect)
            selected_pen = QPen(QColor(MARKER_SELECTED), 1.4)
            selected_pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            painter.setPen(selected_pen)
            painter.drawEllipse(state_rect)
            for degrees in (45.0, 135.0, 225.0, 315.0):
                radians = math.radians(degrees)
                start = QPointF(
                    math.cos(radians) * state_radius,
                    math.sin(radians) * state_radius,
                )
                end = QPointF(
                    math.cos(radians) * (state_radius + 3.0),
                    math.sin(radians) * (state_radius + 3.0),
                )
                painter.drawLine(start, end)
        elif self._hovered:
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(QPen(QColor(MARKER_INK), 2.2))
            painter.drawEllipse(state_rect)
            painter.setPen(QPen(colour, 1.0))
            painter.drawEllipse(state_rect)


class _AtlasLodLabelItem(QGraphicsItem):
    """Short numeric Atlas label on an 88% ink plate."""

    def __init__(self, marker: ImageMarker, placement: AtlasLabelPlacement) -> None:
        super().__init__()
        self._marker = marker
        self._placement = placement
        self._plate = QRectF(
            placement.x_offset_px,
            placement.y_offset_px,
            placement.width_px,
            placement.height_px,
        )
        self._bounds = self._plate.adjusted(-3.0, -3.0, 3.0, 3.0)
        if placement.leader:
            self._bounds = self._bounds.united(
                QRectF(
                    LEAF_RING_RADIUS_DP + 3.0,
                    min(0.0, self._plate.center().y()),
                    max(1.0, self._plate.left() - LEAF_RING_RADIUS_DP),
                    abs(self._plate.center().y()) + 1.0,
                ).normalized()
            )
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIgnoresTransformations, True)

    def boundingRect(self) -> QRectF:  # noqa: N802 - Qt signature
        return self._bounds

    def paint(self, painter: QPainter, _option, _widget=None) -> None:
        from tomography_session_browser.ui.theme import MONO_FONT_NAME

        text = self._marker.label or ""
        if not text:
            return
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        if self._placement.leader:
            start = QPointF(LEAF_RING_RADIUS_DP + 3.0, 0.0)
            end = QPointF(self._plate.left(), self._plate.center().y())
            ink_pen = QPen(QColor(MARKER_INK), 2.4)
            ink_pen.setCapStyle(Qt.PenCapStyle.FlatCap)
            painter.setPen(ink_pen)
            painter.drawLine(start, end)
            text_pen = QPen(QColor(MARKER_LABEL_TEXT), 1.0)
            text_pen.setCapStyle(Qt.PenCapStyle.FlatCap)
            painter.setPen(text_pen)
            painter.drawLine(start, end)

        painter.setPen(Qt.PenStyle.NoPen)
        plate = QColor(MARKER_INK)
        plate.setAlphaF(0.88)
        painter.setBrush(plate)
        painter.drawRoundedRect(
            self._plate,
            LABEL_CORNER_RADIUS_DP,
            LABEL_CORNER_RADIUS_DP,
        )
        font = QFont(MONO_FONT_NAME)
        font.setPixelSize(round(LABEL_FONT_SIZE_DP))
        font.setWeight(QFont.Weight.Medium)
        painter.setFont(font)
        painter.setPen(QColor(MARKER_LABEL_TEXT))
        painter.drawText(self._plate, Qt.AlignmentFlag.AlignCenter, text)


class AtlasMarkerLegend(QWidget):
    """Collapsed-by-default status legend anchored above the scale bar."""

    _ROW_ORDER = (
        ("collected", "Collected"),
        ("partial", "Partial"),
        ("queued", "Queued"),
        ("failed", "Failed"),
        ("unattributed", "Unattributed"),
    )

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._expanded = False
        self._counts = {status: 0 for status, _label in self._ROW_ORDER}
        self.setObjectName("atlasMarkerLegend")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip("Show or hide the Atlas batch-position marker legend")
        self._sync_size()

    @property
    def expanded(self) -> bool:
        return self._expanded

    def set_markers(self, markers: list[ImageMarker]) -> None:
        counts = {status: 0 for status, _label in self._ROW_ORDER}
        for marker in markers:
            if marker.metadata.get("atlas_lod_role") not in {
                "batch_position",
                "unattributed",
            }:
                continue
            status = (
                (marker.status or "queued").lower()
                if bool(marker.metadata.get("navigation_enabled", True))
                else "unattributed"
            )
            if status in counts:
                counts[status] += 1
        if counts != self._counts:
            self._counts = counts
            self.update()

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self._expanded = not self._expanded
            self._sync_size()
            self.update()
            event.accept()
            return
        super().mousePressEvent(event)

    def _sync_size(self) -> None:
        self.setFixedSize(228, 184 if self._expanded else 34)

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt signature
        super().paintEvent(event)
        from tomography_session_browser.ui.theme import DARK_PALETTE, MONO_FONT_NAME, SANS_FONT_NAME

        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        try:
            painter.setPen(QPen(QColor(DARK_PALETTE.border_strong), 1.0))
            painter.setBrush(QColor(DARK_PALETTE.surface))
            painter.drawRoundedRect(
                QRectF(0.5, 0.5, self.width() - 1.0, self.height() - 1.0),
                8.0,
                8.0,
            )
            painter.setPen(QColor(DARK_PALETTE.safe_text_light))
            title_font = QFont(SANS_FONT_NAME)
            title_font.setPixelSize(12)
            painter.setFont(title_font)
            painter.drawText(
                QRectF(34.0, 0.0, 160.0, 34.0),
                Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                "Marker legend",
            )
            _paint_legend_cluster_glyph(painter, QPointF(17.0, 17.0))
            painter.setPen(QColor(DARK_PALETTE.text_muted))
            painter.drawText(
                QRectF(196.0, 0.0, 18.0, 34.0),
                Qt.AlignmentFlag.AlignCenter,
                "▴" if self._expanded else "▾",
            )
            if not self._expanded:
                return

            y = 34.0
            label_font = QFont(SANS_FONT_NAME)
            label_font.setPixelSize(12)
            count_font = QFont(MONO_FONT_NAME)
            count_font.setPixelSize(11)
            for status, label in self._ROW_ORDER:
                _paint_chrome_leaf_glyph(
                    painter,
                    QPointF(17.0, y + 12.0),
                    status,
                    diameter=12.0,
                    halo=True,
                )
                painter.setFont(label_font)
                painter.setPen(QColor(MARKER_LABEL_TEXT))
                painter.drawText(
                    QRectF(34.0, y, 140.0, 24.0),
                    Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                    label,
                )
                painter.setFont(count_font)
                painter.setPen(QColor(DARK_PALETTE.text_muted))
                painter.drawText(
                    QRectF(174.0, y, 38.0, 24.0),
                    Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight,
                    str(self._counts[status]),
                )
                y += 24.0

            painter.setPen(QPen(QColor(DARK_PALETTE.border_strong), 1.0))
            painter.drawLine(QPointF(12.0, y + 1.0), QPointF(216.0, y + 1.0))
            _paint_legend_cluster_glyph(painter, QPointF(17.0, y + 15.0))
            painter.setFont(label_font)
            painter.setPen(QColor(DARK_PALETTE.safe_text_light))
            painter.drawText(
                QRectF(34.0, y + 3.0, 178.0, 24.0),
                Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                "n = members, ring = mix",
            )
        finally:
            painter.end()


class _AtlasClusterRow(QPushButton):
    def __init__(
        self,
        marker: ImageMarker,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._marker = marker
        self.setFixedHeight(24)
        self.setFlat(True)
        self.setCursor(
            Qt.CursorShape.PointingHandCursor
            if bool(marker.metadata.get("navigation_enabled", True))
            else Qt.CursorShape.ArrowCursor
        )
        self.setToolTip(marker.tooltip or "")
        # The label is painted, not set, so this button has no text for a
        # screen reader to announce. Spell out the batch and its status, and
        # say when it cannot be opened rather than leaving it silently inert.
        navigable = bool(marker.metadata.get("navigation_enabled", True))
        name = marker.label or marker.linked_object_id or marker.id
        self.setAccessibleName(f"Batch {name}, {acquisition_state(marker.status)}")
        self.setAccessibleDescription(
            marker.tooltip
            if navigable
            else f"Unavailable. {marker.tooltip or 'No unique destination.'}"
        )

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt signature
        from tomography_session_browser.ui.theme import DARK_PALETTE, MONO_FONT_NAME

        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        try:
            if self.underMouse():
                painter.fillRect(self.rect(), QColor(DARK_PALETTE.surface_hi))
            _paint_chrome_leaf_glyph(
                painter,
                QPointF(14.0, 12.0),
                (self._marker.status or "queued").lower(),
                diameter=10.0,
                halo=False,
            )
            font = QFont(MONO_FONT_NAME)
            font.setPixelSize(11)
            painter.setFont(font)
            painter.setPen(QColor(MARKER_LABEL_TEXT))
            label = self._marker.label or self._marker.id
            painter.drawText(
                QRectF(28.0, 0.0, 120.0, 24.0),
                Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                f"Position {label}",
            )
            planned = int(self._marker.metadata.get("planned_count") or 0)
            acquired = int(self._marker.metadata.get("acquired_count") or 0)
            ratio = f"{acquired}/{planned}" if planned > 0 else "—"
            ratio_font = QFont(MONO_FONT_NAME)
            ratio_font.setPixelSize(10)
            painter.setFont(ratio_font)
            painter.setPen(QColor(DARK_PALETTE.text_muted))
            painter.drawText(
                QRectF(150.0, 0.0, 48.0, 24.0),
                Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight,
                ratio,
            )
        finally:
            painter.end()


class AtlasClusterPopup(QFrame):
    """Fixed-width cluster member list positioned inside the viewer viewport."""

    def __init__(
        self,
        members: list[ImageMarker],
        *,
        on_member_selected: Callable[[ImageMarker], None] | None,
        parent: QWidget,
    ) -> None:
        super().__init__(parent)
        from tomography_session_browser.ui.theme import DARK_PALETTE

        self.setObjectName("atlasClusterPopup")
        self.setFixedWidth(216)
        self.setStyleSheet(
            f"#atlasClusterPopup {{ background: {DARK_PALETTE.surface}; "
            f"border: 1px solid {DARK_PALETTE.border_strong}; border-radius: 8px; }}"
        )
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        header = QLabel(f"{len(members)} batch positions", self)
        header.setFixedHeight(26)
        header.setContentsMargins(10, 0, 8, 0)
        header.setStyleSheet(
            f"color: {MARKER_LABEL_TEXT}; font-weight: 600; border: none;"
        )
        layout.addWidget(header)

        scroll = QScrollArea(self)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setVerticalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAsNeeded
            if len(members) > 8
            else Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        rows = QWidget(scroll)
        rows.setStyleSheet(f"background: {DARK_PALETTE.surface};")
        rows_layout = QVBoxLayout(rows)
        rows_layout.setContentsMargins(0, 0, 0, 0)
        rows_layout.setSpacing(0)
        for member in members:
            row = _AtlasClusterRow(member, rows)
            if on_member_selected is not None:
                row.clicked.connect(
                    lambda _checked=False, value=member: on_member_selected(value)
                )
            rows_layout.addWidget(row)
        rows.setFixedHeight(len(members) * 24)
        scroll.setWidget(rows)
        scroll.setWidgetResizable(True)
        scroll.setFixedHeight(min(len(members), 8) * 24)
        layout.addWidget(scroll)

        footer = QLabel("Esc to close", self)
        footer.setFixedHeight(26)
        footer.setContentsMargins(10, 0, 8, 0)
        footer.setStyleSheet(
            f"color: {DARK_PALETTE.text_muted}; border: none;"
        )
        layout.addWidget(footer)
        self.setFixedHeight(52 + min(len(members), 8) * 24)


def _paint_chrome_leaf_glyph(
    painter: QPainter,
    center: QPointF,
    status: str,
    *,
    diameter: float,
    halo: bool,
) -> None:
    """Paint a compact status glyph on known dark chrome."""

    from tomography_session_browser.ui.theme import current_palette

    theme = current_palette()
    colour = QColor(
        {
            "collected": theme.atlas_marker_collected,
            "partial": theme.atlas_marker_partial,
            "queued": theme.atlas_marker_queued,
            "failed": theme.atlas_marker_failed,
            "unattributed": theme.atlas_marker_unattributed,
        }.get(status, theme.atlas_marker_queued)
    )
    ring_radius = max(2.5, diameter / 2.0 - 1.0)
    fill_radius = max(1.5, ring_radius - 1.4)
    painter.save()
    painter.translate(center)
    if status in {"collected", "failed"}:
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(colour)
        painter.drawEllipse(QPointF(0.0, 0.0), fill_radius, fill_radius)
    elif status == "partial":
        rect = QRectF(-fill_radius, -fill_radius, fill_radius * 2, fill_radius * 2)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(colour)
        painter.drawPie(rect, 90 * 16, 180 * 16)
    ring_rect = QRectF(-ring_radius, -ring_radius, ring_radius * 2, ring_radius * 2)
    if halo:
        painter.setPen(QPen(QColor(MARKER_INK), 3.0))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawEllipse(ring_rect)
    pen = QPen(colour, 1.4)
    if status == "unattributed":
        pen.setStyle(Qt.PenStyle.DashLine)
    painter.setPen(pen)
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.drawEllipse(ring_rect)
    if status == "failed":
        glyph = fill_radius * 0.62
        cross = QPen(QColor(MARKER_INK), 1.2)
        cross.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(cross)
        painter.drawLine(QPointF(-glyph, -glyph), QPointF(glyph, glyph))
        painter.drawLine(QPointF(glyph, -glyph), QPointF(-glyph, glyph))
    painter.restore()


def _paint_legend_cluster_glyph(painter: QPainter, center: QPointF) -> None:
    painter.save()
    painter.translate(center)
    painter.setPen(QPen(QColor(MARKER_INK), 3.0))
    painter.setBrush(QColor(IMAGE_STATUS_COLOURS["failed"]))
    painter.drawEllipse(QPointF(0.0, 0.0), 6.0, 6.0)
    painter.setPen(QPen(QColor(IMAGE_STATUS_COLOURS["failed"]), 2.0))
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.drawArc(QRectF(-6.0, -6.0, 12.0, 12.0), 90 * 16, -90 * 16)
    painter.setPen(QPen(QColor(IMAGE_STATUS_COLOURS["collected"]), 2.0))
    painter.drawArc(QRectF(-6.0, -6.0, 12.0, 12.0), 0, -270 * 16)
    painter.restore()


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
        # Viewport memory, keyed by *logical image* rather than by tab, so a
        # different image can never inherit another's crop. Stores only the
        # scene-space rectangle the user was looking at.
        self._view_memory: OrderedDict[Any, tuple[float, float, float, float]] = OrderedDict()
        self._current_image_shape: tuple[int, int] | None = None
        self._markers: list[ImageMarker] = []
        self._marker_items: list[QGraphicsItem] = []
        self._marker_selected: Callable[[ImageMarker], None] | None = None
        self._marker_opened: Callable[[ImageMarker], None] | None = None
        self._cluster_member_activated: Callable[[ImageMarker], None] | None = None
        self._visible_marker_types: set[str] = set(DEFAULT_VISIBLE_MARKER_TYPES)
        self._atlas_lod_enabled = False
        self._atlas_lod_options = {
            ATLAS_LOD_CLUSTER: True,
            ATLAS_LOD_LABELS: True,
            ATLAS_LOD_TILE_GRIDS: True,
            ATLAS_LOD_EXPOSURES: False,
        }
        self._cluster_cache: dict[tuple[object, ...], tuple[MarkerCluster, ...]] = {}
        self._cluster_compute_count = 0
        self._last_display_markers: list[ImageMarker] = []
        self._keyboard_marker_id: str | None = None
        self._cluster_member_popup: AtlasClusterPopup | None = None
        self._cluster_popup_connector: QFrame | None = None
        self._selection_cleared: Callable[[], None] | None = None
        self._highlighted_exposure_marker_ids: set[str] = set()
        self._marker_redraw_debounce = QTimer(self)
        self._marker_redraw_debounce.setSingleShot(True)
        self._marker_redraw_debounce.setInterval(ZOOM_DEBOUNCE_MS)
        self._marker_redraw_debounce.timeout.connect(self._redraw_markers)
        self.setScene(self._scene)
        self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setResizeAnchor(QGraphicsView.ViewportAnchor.AnchorViewCenter)
        self.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        self.setBackgroundBrush(Qt.GlobalColor.black)

    def clear(self, message: str = "") -> None:
        # Bank the crop before the key is dropped, so a rebuild that lands the
        # user back on the same image returns them to where they were.
        self.remember_current_view()
        self._close_cluster_popup()
        self._scene.clear()
        self._pixmap_item = None
        self._marker_items = []
        self._last_display_markers = []
        self._cluster_cache.clear()
        self._keyboard_marker_id = None
        self._highlighted_exposure_marker_ids.clear()
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

    def set_cluster_member_activated_callback(
        self,
        callback: Callable[[ImageMarker], None] | None,
    ) -> None:
        self._cluster_member_activated = callback

    def set_selection_cleared_callback(self, callback: Callable[[], None]) -> None:
        self._selection_cleared = callback

    def set_atlas_lod_enabled(self, enabled: bool) -> None:
        self._atlas_lod_enabled = bool(enabled)
        self._cluster_cache.clear()
        self._redraw_markers()

    def set_atlas_lod_option(self, key: str, enabled: bool) -> None:
        if key not in self._atlas_lod_options:
            raise KeyError(f"Unknown Atlas LOD option: {key}")
        self._atlas_lod_options[key] = bool(enabled)
        self._cluster_cache.clear()
        self._redraw_markers()

    def set_marker_type_visible(self, marker_type: str, visible: bool) -> None:
        if visible:
            self._visible_marker_types.add(marker_type)
        else:
            self._visible_marker_types.discard(marker_type)
        self._redraw_markers()

    def set_markers(self, markers: list[ImageMarker], selected_marker_id: str | None = None) -> None:
        self._close_cluster_popup()
        self._cluster_cache.clear()
        marker_ids = {marker.id for marker in markers}
        # Keep the keyboard cursor if its marker survived this refresh.
        # Clearing unconditionally meant a keyboard selection wiped its own
        # cursor: arrowing to a marker notifies the window, which refreshes the
        # overlay, which landed back here — so the next arrow press started
        # over from the edge.
        if self._keyboard_marker_id not in marker_ids:
            self._keyboard_marker_id = None
        self._highlighted_exposure_marker_ids.intersection_update(marker_ids)
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

    def set_atlas_zoom(self, zoom: float) -> None:
        if not self._atlas_lod_enabled or self._pixmap_item is None:
            return
        target = min(MAX_ATLAS_ZOOM, max(1.0, float(zoom)))
        if target <= 1.0 + 1e-9:
            self.fit_image()
            return
        self._scale_by_center(target / max(self._zoom, 0.01))

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

    def remember_current_view(self) -> None:
        """Store the current crop against the image being displayed.

        Only a view the user actually changed is worth restoring; a fitted
        view is reproduced exactly by fitting again, and remembering it would
        pin an image to a stale window size.
        """

        if self._current_image_key is None or self._pixmap_item is None:
            return
        if not self._has_user_interacted:
            self._view_memory.pop(self._current_image_key, None)
            return
        rect = self.mapToScene(self.viewport().rect()).boundingRect()
        item_rect = self._pixmap_item.boundingRect()
        if item_rect.width() <= 0 or item_rect.height() <= 0:
            return
        # Stored proportionally so a resized window, or a different pyramid
        # level of the same image, restores the same region rather than the
        # same pixel coordinates.
        self._view_memory[self._current_image_key] = (
            rect.left() / item_rect.width(),
            rect.top() / item_rect.height(),
            rect.width() / item_rect.width(),
            rect.height() / item_rect.height(),
        )
        self._view_memory.move_to_end(self._current_image_key)
        while len(self._view_memory) > VIEWPORT_MEMORY_LIMIT:
            self._view_memory.popitem(last=False)

    def _restore_remembered_view(self, logical_image_key: Any) -> bool:
        """Re-apply a remembered crop for ``logical_image_key``."""

        remembered = self._view_memory.get(logical_image_key)
        if remembered is None or self._pixmap_item is None:
            return False
        item_rect = self._pixmap_item.boundingRect()
        if item_rect.width() <= 0 or item_rect.height() <= 0:
            return False
        left, top, width, height = remembered
        if width <= 0 or height <= 0:
            return False
        target = QRectF(
            left * item_rect.width(),
            top * item_rect.height(),
            width * item_rect.width(),
            height * item_rect.height(),
        )
        viewport_rect = self.viewport().rect()
        if viewport_rect.width() <= 0 or viewport_rect.height() <= 0:
            return False
        # Set the transform directly rather than calling fitInView: that
        # reserves room for the frame, so a round trip drifts a few percent
        # wider each time instead of landing back on the same crop.
        scale = min(
            viewport_rect.width() / target.width(),
            viewport_rect.height() / target.height(),
        )
        if scale <= 0:
            return False
        self.resetTransform()
        self.scale(scale, scale)
        self.centerOn(target.center())
        self._pending_initial_fit = False
        self._has_user_interacted = True
        visible = self.mapToScene(viewport_rect).boundingRect()
        self._zoom = item_rect.width() / visible.width() if visible.width() > 0 else 1.0
        return True

    def forget_remembered_views(self) -> None:
        """Drop all viewport memory (session replaced or removed)."""

        self._view_memory.clear()

    def view_range(self) -> tuple[tuple[float, float], tuple[float, float]] | None:
        if self._pixmap_item is None:
            return None
        rect = self.mapToScene(self.viewport().rect()).boundingRect()
        return ((rect.left(), rect.right()), (rect.top(), rect.bottom()))

    def rendered_image_rect(self) -> QRect | None:
        """Where the image is actually drawn, in viewport coordinates.

        A fitted image rarely fills the viewport — an image wider than it is
        tall leaves letterbox bands above and below. Overlaid chrome that
        belongs to the image (the scale bar) needs this rather than the
        widget rect, or it floats in the empty band.
        """

        if self._pixmap_item is None:
            return None
        mapped = self.mapFromScene(self._pixmap_item.sceneBoundingRect()).boundingRect()
        visible = mapped.intersected(self.viewport().rect())
        return visible if not visible.isEmpty() else None

    def render_current_view(self) -> tuple[QImage, float] | None:
        """Render the visible image and scene overlays without viewer chrome.

        ``QGraphicsView.render`` replays the scene rather than copying pixels
        from the desktop. Rendering at a larger target size preserves the
        vector marker edges and text antialiasing in the exported PNG.
        """

        source_rect = self.rendered_image_rect()
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
            self.render(
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
        self._close_cluster_popup()
        LOGGER.debug("fit_to_view logical_key=%s", self._current_image_key)
        self.fitInView(self._pixmap_item, Qt.AspectRatioMode.KeepAspectRatio)
        self._zoom = 1.0
        self._last_view_range = self.view_range()
        self._schedule_marker_redraw()
        if self._zoom_changed is not None:
            self._zoom_changed(self._zoom)

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt signature
        super().resizeEvent(event)
        if self._zoom_changed is not None:
            QTimer.singleShot(0, lambda: self._zoom_changed(self._zoom))

    def wheelEvent(self, event: QWheelEvent) -> None:
        delta = event.angleDelta().y()
        factor = (
            math.pow(1.0015, delta)
            if self._atlas_lod_enabled
            else (1.25 if delta > 0 else 0.8)
        )
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
        self._close_cluster_popup()
        old_shape = self._current_image_shape
        old_range = self.view_range()
        old_rect = self._pixmap_item.boundingRect() if self._pixmap_item is not None else None
        old_center = self.mapToScene(self.viewport().rect().center()) if self._pixmap_item is not None else None
        old_transform = self.transform()
        old_relative_center = _relative_point(old_rect, old_center) if old_rect is not None and old_center is not None else None
        same_logical_image = logical_image_key is not None and logical_image_key == self._current_image_key
        is_new_logical_image = logical_image_key is None or not same_logical_image
        if is_new_logical_image:
            # Bank the outgoing image's crop before its key is replaced.
            self.remember_current_view()
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
        elif not (
            is_new_logical_image
            and logical_image_key is not None
            and self._restore_remembered_view(logical_image_key)
        ):
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
        self._close_cluster_popup()
        self._mark_user_interacted("wheel_zoom")
        if self._atlas_lod_enabled:
            target_zoom = min(MAX_ATLAS_ZOOM, max(1.0, self._zoom * factor))
            factor = target_zoom / max(self._zoom, 0.01)
            if abs(factor - 1.0) < 1e-6:
                return
            self._zoom = target_zoom
        else:
            self._zoom *= factor
        anchor_view = self.mapFromScene(anchor_scene)
        self.scale(factor, factor)
        shifted_anchor = self.mapToScene(anchor_view)
        delta = shifted_anchor - anchor_scene
        current_center = self.mapToScene(self.viewport().rect().center())
        self.centerOn(current_center - delta)
        if self._zoom_changed is not None:
            self._zoom_changed(self._zoom)
        self._schedule_marker_redraw()

    def _scale_by_center(self, factor: float) -> None:
        if self._pixmap_item is None:
            return
        self._close_cluster_popup()
        self._mark_user_interacted("button_zoom")
        if self._atlas_lod_enabled:
            target_zoom = min(MAX_ATLAS_ZOOM, max(1.0, self._zoom * factor))
            factor = target_zoom / max(self._zoom, 0.01)
            if abs(factor - 1.0) < 1e-6:
                return
            self._zoom = target_zoom
        else:
            self._zoom *= factor
        anchor_scene = self.mapToScene(self.viewport().rect().center())
        self.scale(factor, factor)
        self.centerOn(anchor_scene)
        if self._zoom_changed is not None:
            self._zoom_changed(self._zoom)
        self._schedule_marker_redraw()

    def _schedule_marker_redraw(self) -> None:
        if self._atlas_lod_enabled:
            self._marker_redraw_debounce.start()
        else:
            self._redraw_markers()

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if self._pixmap_item is None:
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
                self._cluster_member_popup is not None
                and not self._cluster_member_popup.geometry().contains(
                    event.position().toPoint()
                )
            ):
                self._close_cluster_popup()
            if isinstance(marker, ImageMarker):
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
        super().mousePressEvent(event)

    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:
        if self._pixmap_item is not None and event.button() == Qt.MouseButton.LeftButton:
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
        if event.key() == Qt.Key.Key_Escape and self._keyboard_marker_id is not None:
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
                    self._keyboard_marker_id = selected.id
                    self._redraw_markers()
                    if (
                        self._marker_selected is not None
                        and selected.marker_type != MarkerType.BATCH_CLUSTER
                    ):
                        self._marker_selected(selected)
                    event.accept()
                    return
        if (
            event.key() in {Qt.Key.Key_Return, Qt.Key.Key_Enter}
            and self._keyboard_marker_id is not None
        ):
            marker = next(
                (
                    value
                    for value in self._interactive_markers()
                    if value.id == self._keyboard_marker_id
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
        """Markers the keyboard can reach, on Atlas *and* every other tab.

        Arrow-key marker traversal used to exist only when Atlas LOD was on, so
        Overview, Search map, Search and Batch position overlays were reachable
        by pointer alone.
        """

        if self._atlas_lod_enabled:
            return sorted(
                [
                    marker
                    for marker in self._last_display_markers
                    if marker.marker_type == MarkerType.BATCH_CLUSTER
                    or marker.metadata.get("atlas_lod_role")
                    in {"batch_position", "unattributed"}
                ],
                key=lambda marker: marker.id,
            )
        return sorted(
            [
                marker
                for marker in self._last_display_markers
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

    def _marker_in_direction(
        self,
        markers: list[ImageMarker],
        key: Qt.Key,
    ) -> ImageMarker | None:
        """Nearest marker in the pressed direction, geometrically.

        Traversal used to step through markers sorted by ID, which bears no
        relation to where they are on the image — pressing Right could jump
        across the micrograph. Movement now follows the overlay's geometry, so
        the keyboard order matches what the reviewer sees.
        """

        current = next(
            (m for m in markers if m.id == self._keyboard_marker_id),
            None,
        )
        if current is None:
            # No cursor yet: enter from the edge the key implies.
            reverse = key in {Qt.Key.Key_Left, Qt.Key.Key_Up}
            vertical = key in {Qt.Key.Key_Up, Qt.Key.Key_Down}
            return sorted(
                markers,
                key=lambda m: (m.y, m.x) if vertical else (m.x, m.y),
                reverse=reverse,
            )[0]

        dx_sign = {Qt.Key.Key_Left: -1, Qt.Key.Key_Right: 1}.get(key, 0)
        dy_sign = {Qt.Key.Key_Up: -1, Qt.Key.Key_Down: 1}.get(key, 0)

        candidates: list[tuple[float, ImageMarker]] = []
        for marker in markers:
            if marker.id == current.id:
                continue
            dx = marker.x - current.x
            dy = marker.y - current.y
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
        return current

    def _activate_cluster(self, marker: ImageMarker) -> None:
        bounds = marker.metadata.get("member_bounds_scene")
        if not (
            isinstance(bounds, tuple | list)
            and len(bounds) == 4
            and all(isinstance(value, int | float) for value in bounds)
        ):
            return
        x, y, width, height = (float(value) for value in bounds)
        current_scale = self._zoom_scale()
        spread_now = max(width, height) * current_scale
        if self._zoom >= MAX_ATLAS_ZOOM - 1e-6 or spread_now <= 3.0:
            self._show_cluster_member_popup(marker)
            return

        center = QPointF(x + width / 2, y + height / 2)
        target_zoom = min(
            MAX_ATLAS_ZOOM,
            max(2.2, self._zoom * 2.2),
        )
        factor = target_zoom / max(self._zoom, 0.01)
        self._zoom = target_zoom
        self.scale(factor, factor)
        self.centerOn(center)
        self._keyboard_marker_id = marker.id
        if self._zoom_changed is not None:
            self._zoom_changed(self._zoom)
        self._redraw_markers()

    def _show_cluster_member_popup(self, marker: ImageMarker) -> None:
        self._close_cluster_popup()
        member_ids = tuple(marker.metadata.get("member_ids", ()))
        by_id = {value.id: value for value in self._markers}
        members = [by_id[member_id] for member_id in member_ids if member_id in by_id]
        if not members:
            return
        self._keyboard_marker_id = marker.id
        marker.selected = True
        self._redraw_markers()
        popup = AtlasClusterPopup(
            members,
            on_member_selected=self._select_popup_member,
            parent=self.viewport(),
        )
        popup.adjustSize()
        center = self.mapFromScene(QPointF(float(marker.x or 0.0), float(marker.y or 0.0)))
        diameter = float(marker.metadata.get("diameter_px") or 22.0)
        right_space = self.viewport().width() - center.x()
        left_space = center.x()
        right_x = center.x() + diameter / 2.0 + 16.0
        left_x = center.x() - diameter / 2.0 - 16.0 - popup.width()
        right_fits = right_x + popup.width() <= self.viewport().width() - 4
        left_fits = left_x >= 4
        use_right = (
            (right_space >= left_space and right_fits)
            or (not left_fits and right_fits)
            or (not left_fits and not right_fits and right_space >= left_space)
        )
        x_pos = right_x if use_right else left_x
        x_pos = max(4, min(round(x_pos), self.viewport().width() - popup.width() - 4))
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
                self.viewport().height() - popup.height() - 4,
            ),
        )
        popup.move(x_pos, y_pos)
        popup.show()
        popup.raise_()

        connector = QFrame(self.viewport())
        from tomography_session_browser.ui.theme import DARK_PALETTE

        connector.setStyleSheet(
            f"background: {DARK_PALETTE.border_strong}; border: none;"
        )
        connector_x = round(min(connector_left, connector_right))
        connector_width = max(1, round(abs(connector_right - connector_left)))
        connector.setGeometry(connector_x, round(center.y()), connector_width, 1)
        connector.show()
        connector.raise_()
        popup.raise_()
        self._cluster_member_popup = popup
        self._cluster_popup_connector = connector

    def _select_popup_member(self, marker: ImageMarker) -> None:
        self._close_cluster_popup()
        self._keyboard_marker_id = marker.id
        if self._cluster_member_activated is not None:
            self._cluster_member_activated(marker)
        elif self._marker_selected is not None:
            self._marker_selected(marker)

    def highlighted_exposure_marker_ids(self) -> frozenset[str]:
        return frozenset(self._highlighted_exposure_marker_ids)

    def clear_exposure_highlights(self, *, redraw: bool = True) -> None:
        if not self._highlighted_exposure_marker_ids:
            return
        self._highlighted_exposure_marker_ids.clear()
        if redraw:
            self._redraw_markers()

    def _highlight_linked_exposures(self, marker: ImageMarker) -> None:
        batch_id = str(
            marker.metadata.get("batch_id")
            or marker.linked_object_id
            or ""
        )
        if not batch_id:
            self._highlighted_exposure_marker_ids = {marker.id}
        else:
            self._highlighted_exposure_marker_ids = {
                candidate.id
                for candidate in self._markers
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
        self._redraw_markers()

    def _image_contains_view_position(self, position: QPoint) -> bool:
        if self._pixmap_item is None:
            return False
        return self._pixmap_item.boundingRect().contains(self.mapToScene(position))

    def _clear_marker_interaction(self) -> None:
        self._close_cluster_popup()
        self._keyboard_marker_id = None
        self._highlighted_exposure_marker_ids.clear()
        for marker in self._markers:
            marker.selected = False
        if self._selection_cleared is not None:
            self._selection_cleared()
        else:
            self._redraw_markers()

    def _close_cluster_popup(self) -> None:
        if self._cluster_member_popup is not None:
            self._cluster_member_popup.deleteLater()
            self._cluster_member_popup = None
        if self._cluster_popup_connector is not None:
            self._cluster_popup_connector.deleteLater()
            self._cluster_popup_connector = None

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
        display_markers = self._display_markers()
        self._last_display_markers = display_markers
        for marker in display_markers:
            if (
                marker.marker_type == MarkerType.BATCH_CLUSTER
                or marker.metadata.get("atlas_lod_role") in {"batch_position", "unattributed"}
            ):
                shape = _AtlasLodMarkerItem(marker)
                shape.setPos(float(marker.x or 0.0), float(marker.y or 0.0))
                shape.setData(0, marker)
                shape.setToolTip(marker.tooltip or marker.label or marker.marker_type)
                self._scene.addItem(shape)
                self._marker_items.append(shape)
                if marker.label and marker.marker_type != MarkerType.BATCH_CLUSTER:
                    placement = marker.metadata.get("atlas_label_placement")
                    if not isinstance(placement, AtlasLabelPlacement):
                        continue
                    label = _AtlasLodLabelItem(marker, placement)
                    label.setPos(float(marker.x or 0.0), float(marker.y or 0.0))
                    label.setZValue(35)
                    label.setData(0, marker)
                    label.setData(1, "atlas_batch_label")
                    label.setToolTip(marker.tooltip or marker.label)
                    self._scene.addItem(label)
                    self._marker_items.append(label)
                continue
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
                atlas_outline = (
                    self._atlas_lod_enabled
                    and (
                        marker.marker_type == MarkerType.OVERVIEW
                        or marker.metadata.get("atlas_lod_role")
                        in {"search_map_footprint", "search_map_tile"}
                    )
                )
                if atlas_outline:
                    pen.setCosmetic(True)
                    pen.setWidthF(1.2)
                    fill = QBrush(Qt.BrushStyle.NoBrush)
                elif marker.marker_type == MarkerType.CAMERA_FOV:
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
            if (
                marker.marker_type
                in {MarkerType.EXPOSURE_AREA, MarkerType.CAMERA_FOV}
                and (
                    marker.selected
                    or marker.id in self._highlighted_exposure_marker_ids
                )
            ):
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
        if not self._atlas_lod_enabled:
            return markers
        return self._atlas_lod_display_markers(markers)

    def _atlas_lod_display_markers(self, markers: list[ImageMarker]) -> list[ImageMarker]:
        screen_scale = self._zoom_scale()
        static_markers: list[ImageMarker] = []
        batch_markers: list[ImageMarker] = []
        unattributed: list[ImageMarker] = []
        for marker in markers:
            role = marker.metadata.get("atlas_lod_role")
            if role == "batch_position":
                batch_markers.append(marker)
                continue
            if role == "unattributed":
                unattributed.append(marker)
                continue
            if role == "search_map_footprint":
                if marker.bbox is not None and screen_extent_visible(
                    marker.bbox[2],
                    marker.bbox[3],
                    screen_scale=screen_scale,
                    threshold_px=SEARCH_MAP_FOOTPRINT_MIN_SCREEN_PX,
                ):
                    static_markers.append(marker)
                continue
            if role == "search_map_tile":
                if (
                    self._atlas_lod_options[ATLAS_LOD_TILE_GRIDS]
                    and marker.bbox is not None
                    and screen_extent_visible(
                        marker.bbox[2],
                        marker.bbox[3],
                        screen_scale=screen_scale,
                        threshold_px=SEARCH_MAP_TILE_MIN_SCREEN_PX,
                        use_short_edge=True,
                    )
                ):
                    static_markers.append(marker)
                continue
            if role == "per_exposure":
                width, height = _marker_scene_extent(marker)
                if (
                    self._atlas_lod_options[ATLAS_LOD_EXPOSURES]
                    and screen_extent_visible(
                        width,
                        height,
                        screen_scale=screen_scale,
                        threshold_px=PER_EXPOSURE_MIN_SCREEN_PX,
                        use_short_edge=True,
                    )
                ):
                    static_markers.append(marker)
                continue
            static_markers.append(marker)

        cluster_inputs = [
            ClusterInput(
                id=marker.id,
                x=float(marker.x),
                y=float(marker.y),
                status=(
                    marker.status or "queued"
                    if bool(marker.metadata.get("navigation_enabled", True))
                    else "unattributed"
                ),
            )
            for marker in batch_markers
            if marker.x is not None and marker.y is not None
        ]
        scope_id = next(
            (marker.source_object_id for marker in batch_markers if marker.source_object_id),
            self._current_image_key,
        )
        fingerprint = marker_set_fingerprint(cluster_inputs)
        clustering_enabled = self._atlas_lod_options[ATLAS_LOD_CLUSTER]
        cache_key = (
            scope_id,
            zoom_bucket(screen_scale),
            fingerprint,
            clustering_enabled,
            self._zoom >= NO_CLUSTER_ABOVE_ZOOM,
        )
        clusters = self._cluster_cache.get(cache_key)
        if clusters is None:
            clusters = cluster_markers(
                cluster_inputs,
                transform=ScreenTransform(screen_scale, screen_scale),
                logical_zoom=(
                    self._zoom
                    if clustering_enabled
                    else NO_CLUSTER_ABOVE_ZOOM + 1.0
                ),
            )
            self._cluster_cache[cache_key] = clusters
            self._cluster_compute_count += 1

        marker_by_id = {marker.id: marker for marker in batch_markers}
        leaves: list[ImageMarker] = []
        cluster_markers_out: list[ImageMarker] = []
        for index, cluster in enumerate(clusters):
            if not cluster.is_cluster:
                leaf = marker_by_id.get(cluster.member_ids[0])
                if leaf is not None:
                    leaves.append(leaf)
                continue
            members = [marker_by_id[member_id] for member_id in cluster.member_ids]
            summaries = [
                f"{member.label or member.id}: {(member.status or 'unknown').title()}"
                for member in members
            ]
            status_counts = dict(cluster.status_counts)
            cluster_markers_out.append(
                ImageMarker(
                    id=f"{scope_id}:batch-cluster:{index}:{fingerprint[:10]}",
                    marker_type=MarkerType.BATCH_CLUSTER,
                    linked_object_id=None,
                    source_object_id=str(scope_id) if scope_id is not None else None,
                    x=cluster.centroid_scene[0],
                    y=cluster.centroid_scene[1],
                    radius=cluster.diameter_px / (2 * max(screen_scale, 0.01)),
                    tooltip="\n".join(
                        [f"{len(members)} batch positions", *summaries]
                    ),
                    status=cluster.status,
                    selected=self._keyboard_marker_id
                    == f"{scope_id}:batch-cluster:{index}:{fingerprint[:10]}",
                    metadata={
                        "diameter_px": cluster.diameter_px,
                        "member_ids": cluster.member_ids,
                        "member_bounds_scene": cluster.bounds_scene,
                        "status_counts": status_counts,
                        "navigation_enabled": True,
                    },
                )
            )

        leaves.extend(unattributed)
        label_placements = (
            atlas_batch_label_placements(leaves, view_scale=screen_scale)
            if self._atlas_lod_options[ATLAS_LOD_LABELS] and self._zoom >= 2.0
            else {}
        )
        for leaf in leaves:
            placement = label_placements.get(leaf.id)
            if placement is None:
                leaf.label = None
            else:
                leaf.metadata["atlas_label_placement"] = placement
            leaf.selected = leaf.selected or leaf.id == self._keyboard_marker_id
            leaf.metadata["diameter_px"] = LEAF_DIAMETER_DP
        return static_markers + cluster_markers_out + leaves

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
            return replace(marker, metadata=dict(marker.metadata))
        source_width, source_height = float(image_size[0]), float(image_size[1])
        if source_width <= 0 or source_height <= 0:
            return replace(marker, metadata=dict(marker.metadata))
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
    if (
        marker.metadata.get("queued_position")
        and marker.marker_type in {MarkerType.EXPOSURE_AREA, MarkerType.CAMERA_FOV}
    ):
        # Match the Atlas queued glyph: the blue hue carries pending status,
        # while the empty centre keeps it distinct from the filled blue Focus
        # area even when both are near the same target.
        return QColor(theme.atlas_marker_queued), fill(theme.atlas_marker_queued, 0)
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
        # Camera fields can span a large fraction of the image. Keep the
        # outline vivid but make the default fill deliberately restrained so
        # membrane/detail contrast remains visible underneath.
        MarkerType.CAMERA_FOV: (theme.marker_camera, fill(theme.overlay_camera_fill, 34)),
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


def _atlas_status_color(status: str) -> QColor:
    normalized = (status or "").strip().lower()
    return QColor(
        IMAGE_STATUS_COLOURS.get(normalized, IMAGE_STATUS_COLOURS["queued"])
    )


def _marker_scene_extent(marker: ImageMarker) -> tuple[float, float]:
    if marker.bbox is not None:
        return abs(float(marker.bbox[2])), abs(float(marker.bbox[3]))
    if marker.polygon:
        xs = [point[0] for point in marker.polygon]
        ys = [point[1] for point in marker.polygon]
        return max(xs) - min(xs), max(ys) - min(ys)
    radius = abs(float(marker.radius or 0.0))
    return radius * 2, radius * 2


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
        return f"{_format_scale_number(angstrom)} {ANGSTROM}"
    nm = value_meters * 1e9
    if nm < 1000:
        return f"{_format_scale_number(nm)} {NANOMETRE}"
    um = value_meters * 1e6
    if um < 1000:
        return f"{_format_scale_number(um)} {MICROMETRE}"
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
        self.setAccessibleName("Tilt-series frame")
        self.setAccessibleDescription(
            "Select a tilt-series frame. Use the Left and Right arrow keys to move one frame."
        )

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
        if selected:
            # The selection fill is ~1.2:1 against the panel; the accent bar
            # is what actually makes the current row findable.
            paint_selection_marker(painter, rect.adjusted(2, 0, 0, 0), theme)

        painter.setPen(QPen(QColor(theme.border), 1))
        painter.drawLine(rect.bottomLeft(), rect.bottomRight())

        left = rect.left() + 12
        decoration = index.data(Qt.ItemDataRole.DecorationRole)
        if isinstance(decoration, QIcon) and not decoration.isNull():
            icon_size = 16
            icon_rect = QRect(
                left,
                rect.center().y() - icon_size // 2,
                icon_size,
                icon_size,
            )
            painter.drawPixmap(
                icon_rect,
                decoration.pixmap(QSize(icon_size, icon_size)),
            )
            left += icon_size + 8
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
            # Middle-elide: Tomography 5 names share a long common prefix
            # ("SearchMap_2026...") and differ only in the tail, so eliding
            # the right made every row in the list read identically.
            name_metrics.elidedText(primary, Qt.TextElideMode.ElideMiddle, text_width),
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
            semantic_color, bg = _status_badge_colors(status)
            painter.setPen(QPen(semantic_color, 1))
            painter.setBrush(bg)
            painter.drawRoundedRect(badge_rect, badge_h / 2, badge_h / 2)
            painter.setPen(QColor(theme.text_strong))
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


#: What each status word means, so the header chip explains itself instead of
#: repeating its own text as a tooltip.
_STATUS_CHIP_TOOLTIPS = {
    "acquired": "Acquisition completed for this position.",
    "done": "Acquisition completed.",
    "complete": "Every planned image was acquired.",
    "completed": "Every planned image was acquired.",
    "available": "The source file is present and readable.",
    "partial": "Fewer images than expected were acquired.",
    "incomplete": "Fewer images than expected were acquired.",
    "warning": "Acquired, but the metadata reports a problem.",
    "warn": "Acquired, but the metadata reports a problem.",
    "failed": "Acquisition stopped before a usable series was produced.",
    "fail": "Acquisition stopped before a usable series was produced.",
    "error": "The source data could not be interpreted.",
    "missing": "The expected source file was not found.",
    "empty": "No images are associated with this item.",
    "queued": "Planned but not yet acquired.",
    "pending": "Planned but not yet acquired.",
    "unknown": "There is not enough metadata to classify this item.",
}


def _status_chip_tooltip(value: str) -> str:
    normalized = value.strip().lower()
    explanation = _STATUS_CHIP_TOOLTIPS.get(normalized)
    return f"{value} — {explanation}" if explanation else value


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
    return f" · {count_phrase(len(warnings), 'warning')}"


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
        self.setFixedSize(round(MAX_SCALE_BAR_SCREEN_PX + 8), 34)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setToolTip(
            "Scale bar with a snapped physical-distance label."
        )

    def set_scale(self, length_px: float, label: str) -> None:
        self._length_px = max(0.0, min(float(length_px), self.width() - 8.0))
        self._label = label
        self.setVisible(
            self._length_px >= MIN_SCALE_BAR_SCREEN_PX and bool(label)
        )
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt signature
        super().paintEvent(event)
        if self._length_px <= 0 or not self._label:
            return
        from tomography_session_browser.ui.theme import DARK_PALETTE, MONO_FONT_NAME

        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        try:
            x0 = 1.5
            y = 27.0
            x1 = x0 + self._length_px
            font = QFont(MONO_FONT_NAME)
            font.setPixelSize(11)
            font.setWeight(QFont.Weight.Medium)
            painter.setFont(font)
            metrics = QFontMetricsF(font)
            plate_width = min(
                self.width() - 2.0,
                max(20.0, metrics.horizontalAdvance(self._label) + 12.0),
            )
            plate = QColor(MARKER_INK)
            plate.setAlphaF(0.88)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(plate)
            painter.drawRoundedRect(
                QRectF(x0 - 1.0, 1.0, plate_width, 16.0),
                3.0,
                3.0,
            )
            painter.setPen(QColor(DARK_PALETTE.safe_text_light))
            painter.drawText(
                QRectF(x0 + 5.0, 1.0, plate_width - 10.0, 16.0),
                Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                self._label,
            )

            bar_path = QPainterPath()
            bar_path.moveTo(x0, y)
            bar_path.lineTo(x1, y)
            bar_path.moveTo(x0, y - 3.5)
            bar_path.lineTo(x0, y + 3.5)
            bar_path.moveTo(x1, y - 3.5)
            bar_path.lineTo(x1, y + 3.5)
            ink_pen = QPen(QColor(MARKER_INK), 3.4)
            ink_pen.setCapStyle(Qt.PenCapStyle.FlatCap)
            painter.setPen(ink_pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawPath(bar_path)
            light_pen = QPen(QColor(DARK_PALETTE.safe_text_light), 1.4)
            light_pen.setCapStyle(Qt.PenCapStyle.FlatCap)
            painter.setPen(light_pen)
            painter.drawPath(bar_path)
        finally:
            painter.end()


class ImageExportDialog(QDialog):
    """Preview an offscreen viewer render and collect its PNG destination."""

    def __init__(
        self,
        image: QImage,
        default_path: Path,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._selected_path: Path | None = None
        self.setWindowTitle("Export image")
        self.setModal(True)
        self.resize(820, 650)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)

        preview = QLabel(self)
        preview.setObjectName("imageExportPreview")
        preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        preview.setMinimumSize(480, 320)
        preview.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Expanding,
        )
        preview_image = image.scaled(
            QSize(760, 480),
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        preview.setPixmap(QPixmap.fromImage(preview_image))
        preview.setToolTip(
            "Preview of the exported image; the PNG is saved at full resolution."
        )
        layout.addWidget(preview, stretch=1)

        details = QLabel(
            f"PNG · {image.width():,} × {image.height():,} px",
            self,
        )
        details.setObjectName("viewerStatus")
        layout.addWidget(details)

        destination_row = QHBoxLayout()
        destination_row.setSpacing(8)
        destination_label = QLabel("Save to", self)
        self.path_edit = QLineEdit(str(default_path), self)
        self.path_edit.setAccessibleName("Export destination")
        self.path_edit.setToolTip("Destination for the full-resolution PNG")
        browse_button = QPushButton("Browse…", self)
        browse_button.clicked.connect(self._browse)
        destination_row.addWidget(destination_label)
        destination_row.addWidget(self.path_edit, stretch=1)
        destination_row.addWidget(browse_button)
        layout.addLayout(destination_row)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save
            | QDialogButtonBox.StandardButton.Cancel,
            parent=self,
        )
        buttons.accepted.connect(self._accept_path)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    @property
    def selected_path(self) -> Path | None:
        return self._selected_path

    def _browse(self) -> None:
        selected, _filter = QFileDialog.getSaveFileName(
            self,
            "Export image",
            self.path_edit.text().strip(),
            "PNG images (*.png)",
        )
        if selected:
            self.path_edit.setText(selected)

    def _accept_path(self) -> None:
        raw_path = self.path_edit.text().strip()
        if not raw_path:
            QMessageBox.warning(
                self,
                "Export image",
                "Choose where the PNG should be saved.",
            )
            return
        path = Path(raw_path)
        if path.suffix.lower() != ".png":
            path = path.with_suffix(".png")
            self.path_edit.setText(str(path))
        if path.exists():
            answer = QMessageBox.question(
                self,
                "Replace image?",
                f"{path.name} already exists. Replace it?",
                QMessageBox.StandardButton.Yes
                | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
        self._selected_path = path
        self.accept()


@dataclass(frozen=True, slots=True)
class ViewerViewState:
    """What a viewer tab must remember across a rebuild.

    Identifiers and scalars only — never entities, pixmaps or markers — so a
    cache of these cannot keep a removed session alive or grow with image data.
    """

    object_id: str | None = None
    marker_id: str | None = None
    frame_index: int | None = None
    filter_text: str = ""

    @property
    def is_empty(self) -> bool:
        return self.object_id is None and not self.filter_text


def _viewer_object_id(value: Any) -> str | None:
    identifier = getattr(value, "id", None)
    return str(identifier) if identifier else None


class ViewerTab(QWidget):
    #: A command from the empty-state panel, e.g. ``open_session`` or
    #: ``clear_filter``. The main window decides what each one does.
    empty_state_action_requested = Signal(str)

    def __init__(
        self,
        empty_text: str,
        *,
        entity_icon: str | None = None,
        show_list: bool = True,
        show_tilt_controls: bool = False,
        on_item_selected: Callable[[Any], None] | None = None,
        on_frame_changed: Callable[[Any, int, int], None] | None = None,
        tilt_angle_for: Callable[[Any, int], str | None] | None = None,
        markers_for: Callable[[Any], list[ImageMarker]] | None = None,
        on_marker_selected: Callable[[ImageMarker], None] | None = None,
        on_marker_opened: Callable[[ImageMarker], None] | None = None,
        on_cluster_member_activated: Callable[[ImageMarker], None] | None = None,
        on_marker_selection_cleared: Callable[[], None] | None = None,
        show_marker_controls: bool = True,
        atlas_lod: bool = False,
        navigation_actions_for: Callable[[Any], list[ViewerNavigationAction]] | None = None,
        on_navigation_requested: Callable[[Any, str], None] | None = None,
    ) -> None:
        super().__init__()
        self._items: list[Any] = []
        self._entity_icon = entity_icon
        self._sources_for: Callable[[Any], PreviewSources] = lambda _value: PreviewSources(None)
        self._prepared_sources: dict[str, PreviewSources] = {}
        self._on_item_selected = on_item_selected
        self._on_frame_changed = on_frame_changed
        self._tilt_angle_for = tilt_angle_for or (lambda _value, _index: None)
        self._markers_for = markers_for or (lambda _value: [])
        self._on_marker_selected = on_marker_selected
        self._on_marker_opened = on_marker_opened
        self._on_marker_selection_cleared = on_marker_selection_cleared
        self._show_marker_controls = show_marker_controls
        self._atlas_lod = atlas_lod
        self._navigation_actions_for = navigation_actions_for
        self._on_navigation_requested = on_navigation_requested
        self._item_status_for: Callable[[Any], ItemListStatus] | None = None
        self._selected_marker_id_override: str | None = None
        self._marker_selection_explicitly_cleared = False
        self._show_tilt_controls = show_tilt_controls
        self._scale_bar_enabled = True
        self._current_value: Any | None = None
        self._current_path: Path | None = None
        self._current_fallback: Path | None = None
        self._displayed_path: Path | None = None
        self._displayed_slice_index: int | None = None
        # Set when a hidden tab is rebuilt: applied on first show so lazy tabs
        # stay lazy without losing the selection they should return to.
        self._pending_view_state: ViewerViewState | None = None
        # Supplied by the main window so an empty list can name its scope and
        # tell "nothing loaded" apart from "this scope has none of these".
        self._has_sessions = False
        self._scope_name = ""
        self._entity_label = ""
        self._empty_state_override: EmptyState | None = None
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
        self.viewer.set_atlas_lod_enabled(atlas_lod)
        self.viewer.set_zoom_changed_callback(self._zoom_changed)
        self.viewer.set_marker_selected_callback(self._marker_selected)
        self.viewer.set_marker_opened_callback(self._marker_opened)
        self.viewer.set_cluster_member_activated_callback(
            on_cluster_member_activated
        )
        self.viewer.set_selection_cleared_callback(self._marker_selection_cleared)

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
        self.export_image_button = QPushButton("", self)
        self.overlay_button = QPushButton("Overlays", self)
        self.overlay_button.setCheckable(True)
        self.overlay_button.setToolTip("Show overlay controls")
        self.zoom_in_button.setIcon(themed_icon("zoom-in", size=16))
        self.zoom_out_button.setIcon(themed_icon("zoom-out", size=16))
        self.export_image_button.setIcon(themed_icon("download", size=16))
        self.overlay_button.setIcon(themed_icon("layers", size=16))
        self.fit_button.setIcon(themed_icon("maximize", size=16))
        self.zoom_label = QLabel("100%", self)
        self.zoom_label.setObjectName("viewerZoomLabel")
        self.zoom_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        # Reserve enough stable width for four-digit values such as 1000%.
        # Resizing this floating strip at each digit boundary is distracting
        # and can make the Fit button appear to jump horizontally.
        zoom_label_width = max(
            ZOOM_LABEL_MIN_WIDTH_PX,
            self.zoom_label.fontMetrics().horizontalAdvance("9999%") + 8,
        )
        self.zoom_label.setFixedWidth(zoom_label_width)
        self.image_badge = QLabel("", self)
        self.image_badge.setObjectName("viewerImageBadge")
        self.image_badge.setVisible(False)
        self.scale_bar = ScaleBarWidget(self)
        self.atlas_marker_legend = AtlasMarkerLegend(self)
        self.atlas_marker_legend.setVisible(atlas_lod)
        self.zoom_in_button.setToolTip("Zoom in")
        self.zoom_out_button.setToolTip("Zoom out")
        self.fit_button.setToolTip("Fit preview to the available space")
        self.export_image_button.setToolTip(
            "Export the current image view as a high-quality PNG (Ctrl+S)"
        )
        self.zoom_in_button.setAccessibleName("Zoom in")
        self.zoom_out_button.setAccessibleName("Zoom out")
        self.fit_button.setAccessibleName("Fit preview")
        self.export_image_button.setAccessibleName("Export current image view")
        self.overlay_button.setAccessibleName("Overlay controls")
        for button in (self.zoom_in_button, self.zoom_out_button, self.fit_button, self.overlay_button):
            button.setObjectName("viewerToolButton")
            button.setIconSize(QSize(16, 16))
        for button in (self.zoom_in_button, self.zoom_out_button):
            button.setObjectName("viewerZoomButton")
            button.setFixedSize(30, 28)
        self.export_image_button.setObjectName("viewerZoomButton")
        self.export_image_button.setFixedSize(30, 28)
        self.export_image_button.setEnabled(False)
        self.status = QLabel(empty_text, self)
        self.status.setObjectName("viewerStatus")
        self.status.setWordWrap(True)
        self.status.setToolTip(empty_text)
        self.frame_label = QLabel("Frame", self)
        self.frame_label.setObjectName("frameLabel")
        self.frame_label.setToolTip("Current tilt series frame")
        self.slice_slider = FrameSelectionSlider(self)
        self.slice_slider.setToolTip("Select a tilt-series frame; use Left or Right to move one frame")
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
        self._atlas_lod_checks: dict[str, QCheckBox] = {}
        self.marker_legend_checkbox: QCheckBox | None = None
        self._atlas_collection_overlay_options_for: (
            Callable[[Any], list[AtlasCollectionOverlayOption]] | None
        ) = None
        self._on_atlas_collection_visibility_changed: (
            Callable[[str, bool], None] | None
        ) = None
        self._atlas_collection_checks: dict[str, QCheckBox] = {}
        self._atlas_collection_section_button: QToolButton | None = None
        self._atlas_collection_section: QWidget | None = None
        self._atlas_collection_section_layout: QVBoxLayout | None = None
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
        if atlas_lod:
            for key, label, icon_name, default in ATLAS_LOD_CONTROL_LABELS:
                checkbox = QCheckBox(label, self.overlay_panel)
                checkbox.setChecked(default)
                checkbox.setToolTip(f"Show or hide {label.lower()}")
                checkbox.setIcon(themed_icon(icon_name, size=16))
                checkbox.setIconSize(QSize(16, 16))
                checkbox.stateChanged.connect(
                    lambda _state, option=key: self._atlas_lod_toggled(option)
                )
                self._atlas_lod_checks[key] = checkbox
                overlay_panel_layout.addWidget(checkbox)
            self._atlas_collection_section_button = QToolButton(
                self.overlay_panel
            )
            self._atlas_collection_section_button.setObjectName(
                "viewerOverlaySectionButton"
            )
            self._atlas_collection_section_button.setText(
                "Data collections"
            )
            self._atlas_collection_section_button.setCheckable(True)
            self._atlas_collection_section_button.setChecked(False)
            self._atlas_collection_section_button.setArrowType(
                Qt.ArrowType.RightArrow
            )
            self._atlas_collection_section_button.setToolButtonStyle(
                Qt.ToolButtonStyle.ToolButtonTextBesideIcon
            )
            self._atlas_collection_section_button.setToolTip(
                "Show batch-position visibility controls by data collection"
            )
            self._atlas_collection_section_button.toggled.connect(
                self._atlas_collection_section_toggled
            )
            self._atlas_collection_section_button.setVisible(False)
            overlay_panel_layout.addWidget(
                self._atlas_collection_section_button
            )

            self._atlas_collection_section = QWidget(self.overlay_panel)
            self._atlas_collection_section.setObjectName(
                "viewerAtlasCollectionSection"
            )
            self._atlas_collection_section_layout = QVBoxLayout(
                self._atlas_collection_section
            )
            self._atlas_collection_section_layout.setContentsMargins(
                18,
                0,
                0,
                2,
            )
            self._atlas_collection_section_layout.setSpacing(4)
            self._atlas_collection_section.setVisible(False)
            # The per-collection list is dynamic — a linked root can carry many
            # collections — so it scrolls inside a bounded area rather than
            # growing the panel until it runs off the canvas.
            self._atlas_collection_scroll = QScrollArea(self.overlay_panel)
            self._atlas_collection_scroll.setObjectName("viewerAtlasCollectionScroll")
            self._atlas_collection_scroll.setWidgetResizable(True)
            self._atlas_collection_scroll.setFrameShape(QFrame.Shape.NoFrame)
            self._atlas_collection_scroll.setHorizontalScrollBarPolicy(
                Qt.ScrollBarPolicy.ScrollBarAlwaysOff
            )
            self._atlas_collection_scroll.setMaximumHeight(ATLAS_COLLECTION_LIST_MAX_PX)
            self._atlas_collection_scroll.setWidget(self._atlas_collection_section)
            self._atlas_collection_scroll.setVisible(False)
            overlay_panel_layout.addWidget(self._atlas_collection_scroll)

            self.marker_legend_checkbox = QCheckBox(
                "Marker legend",
                self.overlay_panel,
            )
            self.marker_legend_checkbox.setChecked(True)
            self.marker_legend_checkbox.setToolTip(
                "Show or hide the Atlas marker legend"
            )
            self.marker_legend_checkbox.stateChanged.connect(
                self._marker_legend_toggled
            )
            overlay_panel_layout.addWidget(self.marker_legend_checkbox)
        self.scale_bar_checkbox = QCheckBox("Scale bar", self.overlay_panel)
        self.scale_bar_checkbox.setChecked(True)
        self.scale_bar_checkbox.setToolTip("Show or hide the scale bar")
        self.scale_bar_checkbox.stateChanged.connect(self._scale_bar_toggled)
        overlay_panel_layout.addWidget(self.scale_bar_checkbox)
        self.overlay_panel.setVisible(False)
        self.overlay_button.toggled.connect(self._overlay_panel_toggled)

        self._navigation_buttons: dict[str, QPushButton] = {}
        if self._navigation_actions_for is not None:
            controls.addSpacing(8)
            for key, label in (
                ("batch", "Batch"),
                ("search", "Search"),
                ("search_map", "Search map"),
                ("overview", "Overview"),
                ("tilt_series", "Tilt series"),
            ):
                button = QPushButton(label, self)
                button.setObjectName("viewerNavButton")
                button.setIcon(themed_icon("arrow-right", size=14))
                button.setAccessibleName(f"Open linked {label.lower()}")
                button.setVisible(False)
                button.clicked.connect(lambda _checked=False, key=key: self._navigation_button_clicked(key))
                self._navigation_buttons[key] = button
                controls.addWidget(button)

        self.zoom_in_button.clicked.connect(self.viewer.zoom_in)
        self.zoom_out_button.clicked.connect(self.viewer.zoom_out)
        self.fit_button.clicked.connect(self.viewer.fit_image)
        self.export_image_button.clicked.connect(self._export_current_view)
        self._export_shortcut = QShortcut(
            QKeySequence.StandardKey.Save,
            self,
        )
        self._export_shortcut.setContext(
            Qt.ShortcutContext.WindowShortcut
        )
        # Each viewer tab owns the same shortcut. Only the visible tab keeps
        # its shortcut enabled, avoiding ambiguity while still allowing
        # Ctrl+S when focus is in a dock or another part of the main window.
        self._export_shortcut.setEnabled(False)
        self._export_shortcut.activated.connect(self._export_current_view)

        self.list = QTreeWidget(self)
        self.list.setObjectName("viewerList")
        self.list.setColumnCount(1)
        self.list.setHeaderHidden(True)
        self.list.setIconSize(QSize(16, 16))
        self.list.setIndentation(12)
        self.list.setTextElideMode(Qt.TextElideMode.ElideMiddle)
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
        self.list_filter.setAccessibleName("Filter viewer items")
        self.list_filter.setClearButtonEnabled(True)
        self._clear_filter_shortcut = QShortcut(
            QKeySequence(Qt.Key.Key_Escape),
            self.list_filter,
        )
        self._clear_filter_shortcut.setContext(Qt.ShortcutContext.WidgetShortcut)
        self._clear_filter_shortcut.activated.connect(self.list_filter.clear)
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

        # Stacked over the canvas so an empty list explains itself with a real
        # panel rather than a bare line of text on a black rectangle. Hidden
        # whenever there is an image to show.
        self.empty_state_panel = EmptyStatePanel(viewer_shell)
        self.empty_state_panel.action_requested.connect(self.empty_state_action_requested)
        self.empty_state_panel.setVisible(False)
        viewer_shell_layout.addWidget(self.empty_state_panel, 0, 0)

        top_right = QWidget(viewer_shell)
        top_right.setObjectName("viewerTopControls")
        top_right_layout = QVBoxLayout(top_right)
        top_right_layout.setContentsMargins(0, 0, 0, 0)
        top_right_layout.setSpacing(6)
        top_right_row = QHBoxLayout()
        top_right_row.setContentsMargins(0, 0, 0, 0)
        top_right_row.setSpacing(6)
        top_right_row.addWidget(self.overlay_button)
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
        self.zoom_panel = zoom_panel
        zoom_panel.setObjectName("viewerZoomPanel")
        zoom_panel.setFixedWidth(
            zoom_label_width + ZOOM_PANEL_HORIZONTAL_PADDING_PX
        )
        zoom_layout = QVBoxLayout(zoom_panel)
        zoom_layout.setContentsMargins(3, 5, 3, 5)
        zoom_layout.setSpacing(3)
        zoom_layout.addWidget(self.zoom_in_button, alignment=Qt.AlignmentFlag.AlignHCenter)
        zoom_layout.addWidget(self.zoom_label, alignment=Qt.AlignmentFlag.AlignHCenter)
        zoom_layout.addWidget(self.zoom_out_button, alignment=Qt.AlignmentFlag.AlignHCenter)
        # "Fit" is a zoom action, but it used to live in the opposite corner
        # from the zoom controls. Icon-only here because the panel is narrow;
        # the tooltip carries the name.
        self.fit_button.setText("")
        self.fit_button.setObjectName("viewerZoomButton")
        zoom_layout.addWidget(self.fit_button, alignment=Qt.AlignmentFlag.AlignHCenter)
        zoom_anchor = QWidget(viewer_shell)
        zoom_anchor.setObjectName("viewerFloatingBottomRightAnchor")
        zoom_anchor_layout = QVBoxLayout(zoom_anchor)
        zoom_anchor_layout.setContentsMargins(
            0,
            0,
            FLOATING_CONTROL_INSET_PX,
            FLOATING_CONTROL_BOTTOM_INSET_PX,
        )
        export_panel = QWidget(viewer_shell)
        export_panel.setObjectName("viewerZoomPanel")
        export_panel.setFixedWidth(
            zoom_label_width + ZOOM_PANEL_HORIZONTAL_PADDING_PX
        )
        export_layout = QVBoxLayout(export_panel)
        export_layout.setContentsMargins(3, 5, 3, 5)
        export_layout.addWidget(
            self.export_image_button,
            alignment=Qt.AlignmentFlag.AlignHCenter,
        )
        zoom_anchor_layout.addWidget(export_panel)
        zoom_anchor_layout.addSpacing(6)
        zoom_anchor_layout.addWidget(zoom_panel)
        # Kept alongside the scale-bar anchor so both share one baseline —
        # see ``_reposition_floating_controls``.
        self._zoom_anchor_layout = zoom_anchor_layout
        viewer_shell_layout.addWidget(
            zoom_anchor,
            0,
            0,
            alignment=Qt.AlignmentFlag.AlignBottom | Qt.AlignmentFlag.AlignRight,
        )
        scale_bar_anchor = QWidget(viewer_shell)
        scale_bar_anchor.setAttribute(
            Qt.WidgetAttribute.WA_TransparentForMouseEvents,
            not atlas_lod,
        )
        scale_bar_anchor_layout = QVBoxLayout(scale_bar_anchor)
        scale_bar_anchor_layout.setContentsMargins(
            FLOATING_CONTROL_INSET_PX,
            0,
            0,
            FLOATING_CONTROL_BOTTOM_INSET_PX,
        )
        if atlas_lod:
            scale_bar_anchor_layout.addWidget(self.atlas_marker_legend)
            scale_bar_anchor_layout.addSpacing(12)
        scale_bar_anchor_layout.addWidget(self.scale_bar)
        # Kept so ``_reposition_scale_bar`` can pin the bar to the rendered
        # image rather than to the canvas corner.
        self._scale_bar_anchor = scale_bar_anchor
        self._scale_bar_anchor_layout = scale_bar_anchor_layout
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
            # The entity icon consumes 24 px including its text gap. Reserve
            # enough additional width for the icon and an ordinary entity
            # name such as ``SearchMap_1`` beside the longest common status
            # badge, rather than introducing immediate elision.
            self.list.setMinimumWidth(VIEWER_LIST_MIN_WIDTH_PX)
            # Keep the right-side list wide enough for status summaries while
            # leaving the image viewer with most of the horizontal space. The
            # user can still drag or collapse the splitter handle.
            splitter.setSizes([1180, 300])
            splitter.setStretchFactor(0, 6)
            splitter.setStretchFactor(1, 1)
            splitter.setCollapsible(0, False)
            splitter.setCollapsible(1, True)

        header.addLayout(controls)
        layout.addLayout(header)
        layout.addWidget(splitter, stretch=1)
        layout.addWidget(self.status)

    def showEvent(self, event) -> None:  # noqa: N802 - Qt signature
        super().showEvent(event)
        self._export_shortcut.setEnabled(True)

    def hideEvent(self, event) -> None:  # noqa: N802 - Qt signature
        self._export_shortcut.setEnabled(False)
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
        self._atlas_collection_overlay_options_for = provider
        self._on_atlas_collection_visibility_changed = on_visibility_changed
        self._update_atlas_collection_overlay_controls(self._current_value)

    def _atlas_collection_section_toggled(self, expanded: bool) -> None:
        if self._atlas_collection_section_button is not None:
            self._atlas_collection_section_button.setArrowType(
                Qt.ArrowType.DownArrow
                if expanded
                else Qt.ArrowType.RightArrow
            )
        if self._atlas_collection_section is not None:
            # The scroll area is what participates in the panel layout now, so
            # it is the thing that shows and hides.
            self._set_atlas_collection_list_visible(
                expanded and bool(self._atlas_collection_checks)
            )

    def _update_atlas_collection_overlay_controls(
        self,
        value: Any,
    ) -> None:
        if (
            not self._atlas_lod
            or self._atlas_collection_section_layout is None
            or self._atlas_collection_section_button is None
            or self._atlas_collection_section is None
        ):
            return
        while self._atlas_collection_section_layout.count():
            item = self._atlas_collection_section_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        self._atlas_collection_checks = {}
        options = (
            self._atlas_collection_overlay_options_for(value)
            if self._atlas_collection_overlay_options_for is not None
            and value is not None
            else []
        )
        self._atlas_collection_section_button.setText(
            f"Data collections ({len(options)})"
            if options
            else "Data collections"
        )
        self._atlas_collection_section_button.setVisible(bool(options))
        for option in options:
            checkbox = QCheckBox(
                option.label,
                self._atlas_collection_section,
            )
            checkbox.setObjectName("viewerAtlasCollectionCheck")
            checkbox.setChecked(option.visible)
            checkbox.setToolTip(
                f"Show batch positions from {option.label}"
            )
            checkbox.stateChanged.connect(
                lambda _state, key=option.key: self._atlas_collection_toggled(
                    key
                )
            )
            self._atlas_collection_checks[option.key] = checkbox
            self._atlas_collection_section_layout.addWidget(checkbox)
        self._set_atlas_collection_list_visible(
            bool(options)
            and self._atlas_collection_section_button.isChecked()
        )
        # The list just changed length. Without this, an Atlas with many more
        # collections selected while the panel is open keeps a scroll cap
        # computed for the previous content until the next resize.
        self._constrain_overlay_panel()

    def _atlas_collection_toggled(self, key: str) -> None:
        checkbox = self._atlas_collection_checks.get(key)
        if checkbox is None:
            return
        if self._on_atlas_collection_visibility_changed is not None:
            self._on_atlas_collection_visibility_changed(
                key,
                checkbox.isChecked(),
            )
        self._refresh_marker_selection()

    def _set_header_chips(self, values: list[str]) -> None:
        from tomography_session_browser.ui.theme import current_palette

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
            is_status = _is_status_chip(value)
            chip.setObjectName("viewerChipStatus" if is_status else "viewerChip")
            if is_status:
                # The status chip used to be painted with the accent colour
                # whatever the status was, so a failed tilt series showed a
                # calm sage chip in the header while the list row beside it
                # showed a red one. Reuse the list-row badge colours.
                semantic_color, background = _status_badge_colors(value)
                text_color = current_palette().text_strong
                chip.setStyleSheet(
                    f"color: {text_color};"
                    f" border-color: {semantic_color.name()};"
                    f" background: rgba({background.red()}, {background.green()},"
                    f" {background.blue()}, {background.alpha()});"
                )
            chip.setAlignment(Qt.AlignmentFlag.AlignCenter)
            chip.setToolTip(_status_chip_tooltip(value) if is_status else value)
            layout.insertWidget(1 + index, chip)
            self.header_chips.append(chip)

    def refresh_theme(self) -> None:
        """Refresh custom-painted and pixmap-based viewer controls after theme changes."""

        self.overlay_button.setIcon(themed_icon("layers", size=16))
        self.export_image_button.setIcon(themed_icon("download", size=16))
        self.fit_button.setIcon(themed_icon("maximize", size=16))
        self.zoom_in_button.setIcon(themed_icon("zoom-in", size=16))
        self.zoom_out_button.setIcon(themed_icon("zoom-out", size=16))
        if self._entity_icon is not None:
            for index in range(self.list.topLevelItemCount()):
                self.list.topLevelItem(index).setIcon(
                    0,
                    themed_icon(self._entity_icon, size=16),
                )
        for button in self._navigation_buttons.values():
            button.setIcon(themed_icon("arrow-right", size=14))
        if self._atlas_lod:
            icon_by_key = {
                key: icon_name
                for key, _label, icon_name, _default in ATLAS_LOD_CONTROL_LABELS
            }
            for key, checkbox in self._atlas_lod_checks.items():
                checkbox.setIcon(themed_icon(icon_by_key[key], size=16))
        if self._current_value is not None:
            self._refresh_marker_selection()
        self.viewer.viewport().update()
        self.viewer.scene().update()
        self.image_badge.update()
        self.scale_bar.update()
        self.overlay_panel.update()
        self.update()

    def _update_header(self, value: Any) -> None:
        self.header_title.setText(self._header_title_for(value))
        chips = _chips_for_item(value)
        if isinstance(value, TiltSeries) and self._item_status_for is not None:
            status = self._item_status_for(value).status
            if status:
                chips.insert(0, display_status_label(status))
        self._set_header_chips(chips)
        self._update_navigation_actions(value)

    def _header_title_for(self, value: Any) -> str:
        """Title the header with the same label the list row shows.

        ``Atlas`` carries no name of its own, so ``_title_for_item`` could
        only return the literal word "Atlas" while every other tab titled
        itself with the selected item. The owning tab already resolved a
        real label (e.g. "SSK_vellio") to populate the list; reuse it.
        """

        for index in range(self.list.topLevelItemCount()):
            item = self.list.topLevelItem(index)
            if item.data(0, VIEWER_OBJECT_ROLE) is value:
                label = str(item.data(0, LIST_PRIMARY_ROLE) or "")
                if label:
                    return label
                break
        return _title_for_item(value)

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
            button.setText(action.label.lstrip("↗ ").strip())
            button.setEnabled(action.enabled)
            button.setToolTip(action.tooltip)
            # The visible face stays terse — these sit in a dense strip. The
            # verb-plus-destination phrasing goes where it has room, and where
            # a screen reader will actually reach it. A disabled action states
            # its reason here too, not only on hover.
            button.setAccessibleName(NAVIGATION_ACTION_NAMES.get(action.key, action.label))
            button.setAccessibleDescription(
                action.tooltip
                if action.enabled
                else f"Unavailable. {action.tooltip}".strip()
            )
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
        total = self.list.topLevelItemCount()
        # Honest count under a filter: "0 of 20" rather than a bare "0", which
        # reads the same as a scope that genuinely holds nothing.
        if query:
            self.list_count.setText(f"{visible_count} of {total}")
            announced = f"{visible_count} of {total} shown, filtered by {query}"
        else:
            self.list_count.setText(str(total))
            announced = f"{total} shown"
        # The bare number is meaningless out of context to a screen reader.
        self.list_count.setAccessibleName("Visible item count")
        self.list_count.setAccessibleDescription(announced)
        self._refresh_empty_state_panel()

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

    def has_same_items(self, items: list[Any]) -> bool:
        """True when ``items`` is the identical list this tab already shows."""

        if len(items) != len(self._items):
            return False
        return all(new is old for new, old in zip(items, self._items, strict=True))

    def _decorate_row(self, tree_item: QTreeWidgetItem, value: Any, label: str) -> None:
        """Write a row's status chip, summary, tooltip and announced text.

        Shared by the full rebuild and by ``refresh_providers``, so a row's
        decoration can never be derived from one provider while its markers
        come from another.
        """

        list_status = self._item_status_for(value) if self._item_status_for is not None else None
        raw_status = list_status.status if list_status is not None else _status_label_for_item(value)
        status = display_status_label(raw_status) if raw_status else ""
        summary = list_status.summary if list_status is not None else _summary_for_item(value)
        tooltip = list_status.tooltip if list_status is not None else ""
        tooltip_text = tooltip or (f"{label}\n{summary}" if summary else label)
        tree_item.setToolTip(0, tooltip_text)
        tree_item.setData(0, LIST_PRIMARY_ROLE, label)
        tree_item.setData(0, LIST_SUMMARY_ROLE, summary)
        tree_item.setData(0, LIST_STATUS_ROLE, status)
        tree_item.setData(0, LIST_TOOLTIP_ROLE, tooltip_text)
        # A screen reader otherwise announces the label alone, dropping the
        # status chip and summary that are the point of the row.
        announced = ", ".join(part for part in (label, status, summary) if part)
        tree_item.setData(0, Qt.ItemDataRole.AccessibleTextRole, announced)

    def _refresh_row_decorations(self) -> None:
        """Re-read every row's status without rebuilding the list."""

        for index in range(self.list.topLevelItemCount()):
            tree_item = self.list.topLevelItem(index)
            value = tree_item.data(0, VIEWER_OBJECT_ROLE)
            if value is None:
                continue
            label = str(tree_item.data(0, LIST_PRIMARY_ROLE) or tree_item.text(0))
            self._decorate_row(tree_item, value, label)
        self.list.viewport().update()

    def refresh_providers(
        self,
        sources_for: Callable[[Any], PreviewSources],
        *,
        markers_for: Callable[[Any], list[ImageMarker]] | None = None,
        item_status_for: Callable[[Any], ItemListStatus] | None = None,
        prepared_sources: dict[str, PreviewSources] | None = None,
    ) -> None:
        """Re-point the callbacks without tearing the list down.

        ``set_items`` rebuilds the rows *and* installs the marker/status
        providers, so a scope-unchanged rebuild used to be the only way to get
        fresh markers. This is that work without the teardown: the provider
        closures are replaced, the current image's overlay is repainted and
        every row's decoration is re-read, while selection, filter, frame and
        scroll position are untouched.

        Refreshing the rows matters as much as the markers. This path runs
        whenever ``has_same_items()`` reports the list is unchanged, so a
        re-derived status — a newly failed tilt series, a link that has since
        resolved — would otherwise repaint the overlay while leaving the row
        chips stating the previous answer, with nothing on screen to signal
        that the two disagreed.
        """

        self._sources_for = sources_for
        self._prepared_sources = prepared_sources or {}
        if item_status_for is not None:
            self._item_status_for = item_status_for
        if markers_for is not None:
            self._markers_for = markers_for
        self._refresh_marker_selection()
        self._refresh_row_decorations()
        # The filter matches on status and summary text, so re-apply it: the
        # visible set and the count must agree with the decorations just
        # written, not with the ones they replaced.
        self._filter_list(self.list_filter.text())
        # Navigation buttons carry the resolver's verdict for the current row;
        # that verdict is derived from the same providers.
        self._update_navigation_actions(self._current_value)

    def set_scope_description(
        self,
        *,
        has_sessions: bool,
        scope_name: str,
        entity_label: str = "",
    ) -> None:
        """Tell this tab what scope and entity type it is showing.

        ``entity_label`` matters when the list is *empty*: the type cannot be
        inferred from zero items, and "No items in Reference collection B" is
        markedly less useful than "No search maps in Reference collection B".
        """

        self._has_sessions = has_sessions
        self._scope_name = scope_name or ""
        self._entity_label = entity_label or self._entity_label

    def _current_entity_label(self) -> str:
        if self._items:
            return _panel_title_for_items(self._items)
        return self._entity_label or _panel_title_for_items(self._items)

    def empty_state(self) -> EmptyState:
        """The state-specific empty experience for this tab right now."""

        return viewer_empty_state(
            has_sessions=self._has_sessions,
            total_items=self.list.topLevelItemCount(),
            filter_text=self.list_filter.text().strip(),
            scope_name=self._scope_name,
            entity_label=self._current_entity_label(),
        )

    def show_empty_state(self, state: EmptyState) -> None:
        """Show a specific recovery state over an empty canvas."""

        self._empty_state_override = state
        self._refresh_empty_state_panel()

    def clear_empty_state_override(self) -> None:
        self._empty_state_override = None
        self._refresh_empty_state_panel()

    def _refresh_empty_state_panel(self) -> None:
        """Show the panel only when there is genuinely nothing to look at.

        Deliberately does **not** cover a loaded image. Filtering the list to
        nothing while a valid preview is on screen is not an empty state — the
        image is real content, and hiding it behind a panel would destroy more
        information than the panel adds. That case is already carried by the
        honest ``0 of 20`` count and by the context header's filter note and
        Clear action.

        The panel is for when the canvas itself has nothing: no session loaded,
        a scope holding none of this entity, or a filter applied before any
        preview resolved.
        """

        panel = getattr(self, "empty_state_panel", None)
        if panel is None:
            return
        visible_rows = sum(
            1
            for index in range(self.list.topLevelItemCount())
            if not self.list.topLevelItem(index).isHidden()
        )
        if self.viewer.has_image():
            panel.setVisible(False)
            return
        # Precedence between a zero-result filter and an override depends on
        # what the override is *about*.
        #
        # A failed load is about the whole scope, so it outranks the filter:
        # otherwise an unrelated search box could hide the Retry button and the
        # reason for it. A missing preview is about one row, and when the
        # filter matches nothing that row is not even in the list — announcing
        # it would describe something the reviewer cannot see, so the honest
        # "0 of N" wins there.
        override = self._empty_state_override
        filtered_to_nothing = bool(self.list_filter.text().strip()) and not visible_rows
        if override is not None and override.kind == KIND_LOAD_FAILED:
            state = override
        elif filtered_to_nothing:
            state = self.empty_state()
        elif override is not None:
            state = override
        elif visible_rows:
            panel.setVisible(False)
            return
        else:
            state = self.empty_state()
        panel.set_state(state)
        panel.setVisible(True)

    def capture_view_state(self) -> ViewerViewState:
        """Snapshot what this tab should get back after a rebuild."""

        return ViewerViewState(
            object_id=_viewer_object_id(self._current_value),
            marker_id=self._selected_marker_id_override,
            frame_index=self._displayed_slice_index,
            filter_text=self.list_filter.text(),
        )

    def _index_of_object_id(self, object_id: str | None) -> int | None:
        if not object_id:
            return None
        for index, item in enumerate(self._items):
            if _viewer_object_id(item) == object_id:
                return index
        return None

    def _apply_restored_state(
        self,
        state: ViewerViewState,
        *,
        auto_load_preview: bool,
    ) -> bool:
        """Re-select a remembered object, if it is still in this scope.

        Returns False when the object has gone, so the caller falls back to
        the ordinary initial-preview path rather than showing nothing.
        """

        index = self._index_of_object_id(state.object_id)
        if index is None:
            return False

        if self.list.isVisible() or self.list.topLevelItemCount():
            previous = self.list.blockSignals(True)
            try:
                self.list.setCurrentItem(self.list.topLevelItem(index))
            finally:
                self.list.blockSignals(previous)

        if not auto_load_preview:
            # Hidden tabs stay lazy: remember the intent and apply it when the
            # tab is actually shown.
            self._pending_view_state = state
            return True

        self._load_value(self._items[index], max(state.frame_index or 0, 0))
        if state.marker_id:
            self.select_marker(state.marker_id)
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
        self._marker_selection_explicitly_cleared = False
        self._pending_view_state = None
        self._empty_state_override = None
        self._update_atlas_collection_overlay_controls(None)
        self._current_mrc_max_size = MAX_PREVIEW_DIMENSION
        self._request_id += 1
        self._slider_debounce.stop()
        self.list.blockSignals(True)
        self.list.setUpdatesEnabled(False)
        try:
            self.list.clear()
            # One generic message used to cover six different situations. The
            # variant is chosen from what is actually true here; the caller
            # supplies the scope so the message can name it.
            if items:
                empty_text = "Select an item"
            else:
                empty_text = viewer_empty_state(
                    has_sessions=self._has_sessions,
                    total_items=0,
                    filter_text=self.list_filter.text().strip(),
                    scope_name=self._scope_name,
                    entity_label=self._current_entity_label(),
                ).title
            self.viewer.clear(empty_text)
            self.export_image_button.setEnabled(False)
            self.image_badge.setVisible(False)
            self.scale_bar.setVisible(False)
            self._set_status_text(empty_text)
            self.header_title.setText(empty_text)
            self._set_header_chips([])
            self._update_navigation_actions(None)
            self.list_count.setText(str(len(items)))
            panel_title = _panel_title_for_items(items)
            self.list_header.setText(panel_title)
            self.list_filter.setPlaceholderText(f"Filter {panel_title.lower()}...")
            self.list_filter.setAccessibleName(f"Filter {panel_title.lower()}")
            self._set_slice_state(1, 0, valid_stack=False)
            tree_items: list[QTreeWidgetItem] = []
            for item in items:
                label = label_for(item)
                tree_item = QTreeWidgetItem([label])
                if self._entity_icon is not None:
                    tree_item.setIcon(
                        0,
                        themed_icon(self._entity_icon, size=16),
                    )
                tree_item.setData(0, VIEWER_OBJECT_ROLE, item)
                self._decorate_row(tree_item, item, label)
                tree_item.setSizeHint(0, QSize(0, 58))
                tree_items.append(tree_item)
            self.list.addTopLevelItems(tree_items)
            # The filter must be applied before an initial preview is chosen,
            # otherwise the tab opens on a row the user has filtered out.
            #
            # Signals are blocked around ``setText`` so the single explicit
            # pass below is the only one: the edit signal would otherwise run
            # ``_filter_list`` over every row a second time, and drive a
            # context-header refresh, in the middle of a rebuild.
            if restore_state is not None:
                blocked = self.list_filter.blockSignals(True)
                try:
                    self.list_filter.setText(restore_state.filter_text)
                finally:
                    self.list_filter.blockSignals(blocked)
            self._filter_list(self.list_filter.text())
            restored = (
                restore_state is not None
                and items
                and self._apply_restored_state(
                    restore_state,
                    auto_load_preview=auto_load_preview,
                )
            )
            if items and auto_load_preview and not restored:
                self._load_initial_item()
        finally:
            self.list.setUpdatesEnabled(True)
            self.list.blockSignals(False)
        self._refresh_empty_state_panel()
        if items:
            QTimer.singleShot(0, lambda: fade_in(self.viewer_shell, duration_ms=140, start_opacity=0.82))
            if self.list.isVisible():
                QTimer.singleShot(30, lambda: fade_in(self.list.viewport(), duration_ms=140, start_opacity=0.82))

    def ensure_initial_preview_loaded(self, *, notify_selection: bool = True) -> None:
        pending = self._pending_view_state
        if pending is not None and self._current_value is None and self._items:
            self._pending_view_state = None
            if self._apply_restored_state(pending, auto_load_preview=True):
                if notify_selection and self._on_item_selected is not None:
                    self._on_item_selected(self._current_value)
                return
        if self._current_value is None and self._items:
            self._load_initial_item(notify_selection=notify_selection)
        elif notify_selection and self._current_value is not None and self._on_item_selected is not None:
            self._on_item_selected(self._current_value)

    def _load_initial_item(self, *, notify_selection: bool = True) -> None:
        if not self._items:
            return
        # Honour the active filter: opening on a row the user filtered out
        # contradicts the visible count and hides the item they are reviewing.
        visible_indexes = [
            index
            for index in range(min(self.list.topLevelItemCount(), len(self._items)))
            if not self.list.topLevelItem(index).isHidden()
        ]
        if self.list_filter.text().strip() and not visible_indexes:
            self._refresh_empty_state_panel()
            return
        candidate_indexes = visible_indexes or list(range(len(self._items)))
        initial_index = candidate_indexes[0]
        for index in candidate_indexes:
            source = self._source_for_value(self._items[index]).primary
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

    def select_object(self, value: Any) -> bool:
        """Select ``value`` in the list.

        Returns True when an incompatible filter had to be cleared to reveal
        it. Unhiding the single row instead would leave the row visible while
        the count still read 0, so the whole filter is dropped and the caller
        announces why.
        """

        filter_cleared = False
        for index in range(self.list.topLevelItemCount()):
            item = self.list.topLevelItem(index)
            if item.data(0, VIEWER_OBJECT_ROLE) is value:
                if item.isHidden():
                    self.list_filter.clear()
                    self._filter_list("")
                    filter_cleared = True
                self.list.setCurrentItem(item)
                self.list.scrollToItem(item, QAbstractItemView.ScrollHint.PositionAtCenter)
                return filter_cleared
        self._load_value(value, 0)
        return filter_cleared

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
        self._empty_state_override = None
        self._current_value = value
        self._selected_marker_id_override = None
        self._marker_selection_explicitly_cleared = False
        self.viewer.clear_exposure_highlights(redraw=False)
        self._update_atlas_collection_overlay_controls(value)
        self._update_header(value)
        markers = self._markers_for(value)
        self._update_marker_type_availability(markers)
        self.viewer.set_markers(markers, selected_marker_id=self._active_selected_marker_id(value, markers))
        if self._atlas_lod:
            self.atlas_marker_legend.set_markers(markers)
        if path is None:
            self._current_path = None
            self._current_fallback = None
            self._displayed_path = None
            self._displayed_slice_index = None
            self.viewer.clear("Preview unavailable")
            self.export_image_button.setEnabled(False)
            self._set_status_text("No preview image found for this item.")
            self._set_slice_state(1, 0, valid_stack=False)
            self.show_empty_state(
                missing_preview_state(
                    name=str(getattr(value, "name", None) or getattr(value, "id", "this item")),
                    metadata_available=True,
                )
            )
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
            self._empty_state_override = None
        elif not self.viewer.has_image():
            self._empty_state_override = missing_preview_state(
                name=str(
                    getattr(self._current_value, "name", None)
                    or getattr(self._current_value, "id", "this item")
                ),
                expected_path=str(path),
                metadata_available=True,
                warnings=tuple(warnings),
            )
        self._update_image_badge()
        self._update_scale_bar()
        self._set_slice_state(1, 0, valid_stack=False)
        self._set_status_text(
            self._compact_path_status("Image", path, warnings=warnings),
            tooltip=self._path_status_tooltip("Image", path, warnings=warnings),
        )
        self._refresh_empty_state_panel()

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
            self.export_image_button.setEnabled(False)
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
        frame_number = min(slice_index, max(0, slice_count - 1)) + 1
        frame_tooltip = (
            f"Frame {frame_number} of {slice_count}; use Left or Right to move one frame"
            if show_slider
            else "Tilt-series frame control"
        )
        self.slice_slider.setToolTip(frame_tooltip)
        self.slice_slider.setAccessibleDescription(frame_tooltip)
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
        self._empty_state_override = None
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
            if fallback_warnings and not self.viewer.has_image():
                self._empty_state_override = missing_preview_state(
                    name=str(
                        getattr(self._current_value, "name", None)
                        or getattr(self._current_value, "id", "this item")
                    ),
                    expected_path=str(path),
                    metadata_available=True,
                    warnings=tuple(all_warnings),
                )
                self._refresh_empty_state_panel()
            return
        self.viewer.clear("Preview unavailable")
        self.export_image_button.setEnabled(False)
        self.image_badge.setVisible(False)
        self.scale_bar.setVisible(False)
        self._set_slice_state(slice_count, slice_index, valid_stack=False)
        self._set_status_text(
            self._compact_path_status("MRC unavailable", path, slice_count=slice_count, warnings=warnings),
            tooltip=self._path_status_tooltip("MRC unavailable", path, slice_count=slice_count, warnings=warnings),
        )
        self._empty_state_override = missing_preview_state(
            name=str(
                getattr(self._current_value, "name", None)
                or getattr(self._current_value, "id", "this item")
            ),
            expected_path=str(path),
            metadata_available=True,
            warnings=tuple(warnings),
        )
        self._refresh_empty_state_panel()

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
        if self._marker_selection_explicitly_cleared:
            return None
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
        if self._atlas_lod:
            self.atlas_marker_legend.set_markers(markers)

    def select_marker(self, marker_id: str | None) -> None:
        self._selected_marker_id_override = marker_id
        self._marker_selection_explicitly_cleared = marker_id is None
        self._refresh_marker_selection()
        self._update_navigation_actions(self._current_value)

    def _marker_selected(self, marker: ImageMarker) -> None:
        self.select_marker(marker.id)
        if self._on_marker_selected is not None:
            self._on_marker_selected(marker)

    def _marker_selection_cleared(self) -> None:
        self.select_marker(None)
        if self._on_marker_selection_cleared is not None:
            self._on_marker_selection_cleared()

    def _marker_opened(self, marker: ImageMarker) -> None:
        self.select_marker(marker.id)
        if self._on_marker_opened is not None:
            self._on_marker_opened(marker)

    def _marker_type_toggled(self, marker_type: str) -> None:
        checkbox = self._marker_type_checks[marker_type]
        self._user_marker_type_visible[marker_type] = checkbox.isChecked()
        self._apply_marker_type_visibility(marker_type)

    def _atlas_lod_toggled(self, key: str) -> None:
        checkbox = self._atlas_lod_checks[key]
        self.viewer.set_atlas_lod_option(key, checkbox.isChecked())

    def _marker_legend_toggled(self) -> None:
        if self.marker_legend_checkbox is not None:
            self.atlas_marker_legend.setVisible(
                self.marker_legend_checkbox.isChecked()
            )

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
        for marker_type in {
            MarkerType.EXPOSURE_AREA,
            MarkerType.FOCUS_AREA,
            MarkerType.TRACKING_AREA,
        }:
            candidates = [
                marker for marker in markers if marker.marker_type == marker_type
            ]
            if candidates and not any(marker.visible for marker in candidates):
                unavailable.add(marker_type)
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

    def _set_atlas_collection_list_visible(self, visible: bool) -> None:
        section = getattr(self, "_atlas_collection_section", None)
        scroll = getattr(self, "_atlas_collection_scroll", None)
        if section is not None:
            section.setVisible(visible)
        if scroll is not None:
            scroll.setVisible(visible)

    def _constrain_overlay_panel(self) -> None:
        """Keep the overlay panel inside the canvas it belongs to.

        With many collections the panel would otherwise grow past the bottom of
        the image and become partly unreachable. Bounding it to the viewport
        means the list scrolls instead of the panel escaping.
        """

        panel = getattr(self, "overlay_panel", None)
        # Intent, not ``isVisible()``: an unshown parent reports every child as
        # invisible, which skipped the bound entirely during construction.
        if panel is None or not self.overlay_button.isChecked():
            return
        available = max(self.viewer.viewport().height() - OVERLAY_PANEL_MARGIN_PX, 120)
        panel.setMaximumHeight(available)
        scroll = getattr(self, "_atlas_collection_scroll", None)
        if scroll is not None and scroll.isVisible():
            # Give the dynamic list whatever the fixed controls do not need,
            # never more than its own cap.
            fixed = panel.sizeHint().height() - scroll.height()
            scroll.setMaximumHeight(
                max(min(ATLAS_COLLECTION_LIST_MAX_PX, available - fixed), 60)
            )

    def close_overlay_panel(self) -> bool:
        """Close the overlay panel and return focus to the control that opened it.

        Returns True when a panel was actually closed, so a key handler knows
        whether it consumed the event.
        """

        if not getattr(self, "overlay_panel", None) or not self.overlay_panel.isVisible():
            return False
        self.overlay_button.setChecked(False)
        self.overlay_panel.setVisible(False)
        self.overlay_button.setFocus(Qt.FocusReason.ShortcutFocusReason)
        return True

    def _overlay_panel_toggled(self, checked: bool) -> None:
        self.overlay_panel.setVisible(checked)
        self.overlay_button.setToolTip(
            "Hide overlay controls" if checked else "Show overlay controls"
        )
        if checked:
            self._constrain_overlay_panel()

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt signature
        super().resizeEvent(event)
        self._constrain_overlay_panel()

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802 - Qt signature
        # Escape closes the overlay panel before anything else acts on it, and
        # puts focus back on the button that opened it so a keyboard user is
        # not stranded.
        if event.key() == Qt.Key.Key_Escape and self.close_overlay_panel():
            event.accept()
            return
        super().keyPressEvent(event)

    def _scale_bar_toggled(self) -> None:
        self._scale_bar_enabled = self.scale_bar_checkbox.isChecked()
        self._update_scale_bar()

    def _floating_control_insets(self) -> tuple[int, int, int]:
        """Return ``(left, right, bottom)`` margins for the overlaid controls.

        Both bottom-corner overlays are positioned from the *rendered image*
        rather than the canvas: on a letterboxed image the canvas corner sits
        in the empty band below the data, which made the scale bar read as
        detached from what it measures.

        Crucially the bottom inset is shared, so the scale bar and the zoom
        stack always sit on one baseline. Anchoring only the scale bar to the
        image (as the first version of this did) left the two overlays
        stepped apart whenever the image did not fill the canvas.
        """

        left = FLOATING_CONTROL_INSET_PX
        right = FLOATING_CONTROL_INSET_PX
        bottom = FLOATING_CONTROL_BOTTOM_INSET_PX
        image_rect = self.viewer.rendered_image_rect()
        if image_rect is None:
            return left, right, bottom

        # The anchors and the viewport all span the viewer shell, so the
        # image rect's viewport-local offsets are the margins we want.
        # (Mapping through ``mapTo`` here double-counted the viewport origin
        # and pushed the bar into the middle of the image.)
        viewport = self.viewer.viewport().rect()
        left = max(FLOATING_CONTROL_INSET_PX, image_rect.left() + FLOATING_CONTROL_INSET_PX)
        right = max(
            FLOATING_CONTROL_INSET_PX,
            (viewport.right() - image_rect.right()) + FLOATING_CONTROL_INSET_PX,
        )
        bottom = max(
            FLOATING_CONTROL_BOTTOM_INSET_PX,
            (viewport.bottom() - image_rect.bottom()) + FLOATING_CONTROL_BOTTOM_INSET_PX,
        )
        return left, right, bottom

    def _reposition_floating_controls(self) -> None:
        """Keep the scale bar and the zoom stack on a common baseline."""

        scale_layout = getattr(self, "_scale_bar_anchor_layout", None)
        zoom_layout = getattr(self, "_zoom_anchor_layout", None)
        if scale_layout is None and zoom_layout is None:
            return

        left, right, bottom = self._floating_control_insets()
        if scale_layout is not None:
            margins = scale_layout.contentsMargins()
            if (margins.left(), margins.bottom()) != (left, bottom):
                scale_layout.setContentsMargins(left, 0, 0, bottom)
        if zoom_layout is not None:
            margins = zoom_layout.contentsMargins()
            if (margins.right(), margins.bottom()) != (right, bottom):
                zoom_layout.setContentsMargins(0, 0, right, bottom)

    def _zoom_changed(self, zoom: float) -> None:
        self.zoom_label.setText(f"{max(1, round(zoom * 100))}%")
        self._update_scale_bar()
        self._reposition_floating_controls()
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
        self.export_image_button.setEnabled(self.viewer.has_image())
        if self._displayed_path is None or self._current_value is None or not self.viewer.has_image():
            self.image_badge.setVisible(False)
            return
        kind = "MRC" if self._displayed_path.suffix.lower() == ".mrc" else self._displayed_path.suffix.lstrip(".").upper() or "Image"
        pixel_size = _pixel_size_meters_for_item(self._current_value, self._displayed_path)
        pixel_text = f" · {_format_pixel_size_label(pixel_size)}/px" if pixel_size is not None else ""
        self.image_badge.setText(f"{kind}{pixel_text}")
        self.image_badge.setToolTip(str(self._displayed_path))
        self.image_badge.setVisible(True)

    def _export_current_view(self) -> None:
        rendered = self.viewer.render_current_view()
        if rendered is None:
            QMessageBox.information(
                self,
                "Export image",
                "Load an image before exporting.",
            )
            return
        image, render_scale = rendered
        self._composite_export_overlays(image, render_scale)
        dialog = ImageExportDialog(
            image,
            self._default_export_path(),
            self,
        )
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        destination = dialog.selected_path
        if destination is None:
            return
        if not image.save(str(destination), "PNG", 100):
            QMessageBox.critical(
                self,
                "Export failed",
                f"Could not save the PNG to:\n{destination}",
            )

    def _default_export_path(self) -> Path:
        base_name = (
            self._displayed_path.stem
            if self._displayed_path is not None
            else "tomography_image"
        )
        if (
            self._displayed_path is not None
            and self._displayed_path.suffix.lower() == ".mrc"
            and self._displayed_slice_index is not None
        ):
            base_name += f"_frame_{self._displayed_slice_index + 1:04d}"
        zoom_percent = max(1, round(self.viewer.zoom_scale() * 100))
        filename = f"{base_name}_view_{zoom_percent}pct.png"
        export_directory = QStandardPaths.writableLocation(
            QStandardPaths.StandardLocation.PicturesLocation
        )
        if not export_directory:
            export_directory = QStandardPaths.writableLocation(
                QStandardPaths.StandardLocation.DocumentsLocation
            )
        return Path(export_directory or Path.cwd()) / filename

    def _composite_export_overlays(
        self,
        image: QImage,
        render_scale: float,
    ) -> None:
        """Add visible image-bound widgets to the offscreen scene render."""

        overlays: list[tuple[QWidget, int]] = []
        if not self.scale_bar.isHidden():
            overlays.append((self.scale_bar, 0))
        if self._atlas_lod and not self.atlas_marker_legend.isHidden():
            overlays.append((self.atlas_marker_legend, 12))
        if not overlays:
            return

        left_inset = round(FLOATING_CONTROL_INSET_PX * render_scale)
        bottom = image.height() - round(
            FLOATING_CONTROL_BOTTOM_INSET_PX * render_scale
        )
        painter = QPainter(image)
        painter.setRenderHints(
            QPainter.RenderHint.Antialiasing
            | QPainter.RenderHint.TextAntialiasing
            | QPainter.RenderHint.SmoothPixmapTransform,
            True,
        )
        try:
            for widget, spacing_above in overlays:
                bottom -= round(spacing_above * render_scale)
                widget_image = self._render_widget_for_export(
                    widget,
                    render_scale,
                )
                bottom -= widget_image.height()
                painter.drawImage(
                    QPoint(left_inset, max(0, bottom)),
                    widget_image,
                )
        finally:
            painter.end()

    @staticmethod
    def _render_widget_for_export(
        widget: QWidget,
        render_scale: float,
    ) -> QImage:
        target = QImage(
            QSize(
                max(1, round(widget.width() * render_scale)),
                max(1, round(widget.height() * render_scale)),
            ),
            QImage.Format.Format_ARGB32_Premultiplied,
        )
        target.fill(Qt.GlobalColor.transparent)
        painter = QPainter(target)
        painter.setRenderHints(
            QPainter.RenderHint.Antialiasing
            | QPainter.RenderHint.TextAntialiasing
            | QPainter.RenderHint.SmoothPixmapTransform,
            True,
        )
        try:
            painter.scale(render_scale, render_scale)
            widget.render(
                painter,
                QPoint(),
                QRegion(),
                QWidget.RenderFlag.DrawChildren,
            )
        finally:
            painter.end()
        return target

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
        if zoom <= 0 or not math.isfinite(zoom):
            self.scale_bar.setVisible(False)
            return
        target_screen_px = min(
            MAX_SCALE_BAR_SCREEN_PX,
            max(
                MIN_SCALE_BAR_SCREEN_PX,
                self.viewer.viewport().width() * 0.15,
            ),
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
        self.scale_bar.set_scale(
            screen_px,
            _format_scale_length_label(length_meters),
        )

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
            chips.append(count_phrase(len(value.tile_paths), "tile"))
        if value.mrc_metadata and value.mrc_metadata.nx and value.mrc_metadata.ny:
            chips.append(f"{value.mrc_metadata.nx} × {value.mrc_metadata.ny}")
        return chips
    if isinstance(value, Overview):
        chips = []
        if value.linked_search_map_ids:
            chips.append(count_phrase(len(value.linked_search_map_ids), "search map"))
        return chips
    if isinstance(value, SearchMap):
        chips = []
        if value.tile_paths:
            chips.append(count_phrase(len({path.stem for path in value.tile_paths}), "tile"))
        if value.linked_batch_position_ids:
            chips.append(count_phrase(len(value.linked_batch_position_ids), "batch position"))
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
            chips.append(count_phrase(value.tilt_count, "frame"))
        step = _tilt_step_chip(value)
        if step:
            chips.append(step)
        if value.tilt_range:
            chips.append(_range_chip(value.tilt_range, DEGREE))
        duration = _tilt_duration_chip(value)
        if duration:
            chips.append(duration)
        if value.pixel_size:
            chips.append(f"{value.pixel_size:g} {ANGSTROM_PER_PIXEL}")
        return chips
    return []


def _summary_for_item(value: Any) -> str:
    if isinstance(value, Overview):
        return count_phrase(len(value.linked_search_map_ids), "linked search map")
    if isinstance(value, SearchMap):
        tile_count = len({path.stem for path in value.tile_paths})
        return f"{count_phrase(tile_count, 'tile')} · {count_phrase(len(value.linked_batch_position_ids), 'batch position')}"
    if isinstance(value, SearchTile):
        parts = []
        if value.search_map_name:
            parts.append(value.search_map_name)
        if value.batch_position_name:
            parts.append(value.batch_position_name)
        if value.linked_tilt_series_ids:
            parts.append(count_phrase(len(value.linked_tilt_series_ids), "tilt series", "tilt series"))
        return " · ".join(parts)
    if isinstance(value, BatchPosition):
        status = value.status or "unknown"
        return f"{status} · {count_phrase(len(value.linked_tilt_series_ids), 'linked tilt series', 'linked tilt series')}"
    if isinstance(value, TiltSeries):
        tilt_count = value.tilt_count or len(value.sections)
        frames = count_phrase(tilt_count, "frame") if tilt_count else "unknown frames"
        return f"{frames} · {_range_chip(value.tilt_range, DEGREE) if value.tilt_range else 'range unknown'}"
    if isinstance(value, Atlas):
        return count_phrase(len(value.tile_paths), "tile")
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
    return f"{step:g}{DEGREE} step"


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
