"""Atlas lod items for the image viewer."""
from __future__ import annotations
from collections.abc import Callable, Hashable, Sequence
import math
from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QFrame, QGraphicsItem, QLabel, QPushButton, QScrollArea, QVBoxLayout, QWidget
from tomography_session_browser.domain.markers import ImageMarker, MarkerType
from tomography_session_browser.domain.status import AtlasMarkerStatus
from tomography_session_browser.services.status_taxonomy import acquisition_state
from tomography_session_browser.services.batch_label_service import AtlasLabelPlacement
from tomography_session_browser.services.atlas_marker_style import CLUSTER_HALO_WIDTH_DP, CLUSTER_INNER_GAP_DP, CLUSTER_SEGMENT_WIDTH_DP, IMAGE_STATUS_COLOURS, LABEL_CORNER_RADIUS_DP, LABEL_FONT_SIZE_DP, LEAF_DIAMETER_DP, LEAF_FAILED_GLYPH_WIDTH_DP, LEAF_FILL_RADIUS_DP, LEAF_HALO_WIDTH_DP, LEAF_HIT_RADIUS_DP, LEAF_RING_RADIUS_DP, LEAF_STATUS_WIDTH_DP, MARKER_INK, MARKER_LABEL_TEXT, MARKER_SELECTED, STATE_RING_OFFSET_DP, RingSegment, cluster_ring_segments, ring_segments
from tomography_session_browser.ui.theme import current_palette
from tomography_session_browser.presentation.design_tokens import CANVAS_CHROME, MONO_FONT_NAME, RADIUS_CARD
from tomography_session_browser.services.atlas_lod import DASHED_GLYPH_KEYS, atlas_glyph_key, glyph_colour_status, is_unlinked_failure
from tomography_session_browser.presentation.atlas_colouring import AtlasColouring
from tomography_session_browser.ui.viewer.marker_paint import CLUSTER_NEUTRAL, atlas_status_color, key_colour


class AtlasLodMarkerItem(QGraphicsItem):
    """Fixed-screen-size Atlas glyph with an 11 dp interaction target.

    With a ``colouring`` (batch 4) the leaf takes its key's colour and keeps its
    status shape, halo, dash and cross; a cluster's ring shows the mix of its
    ``members``' keys around a neutral fill.
    """

    def __init__(self, marker: ImageMarker, colouring: AtlasColouring | None = None,
                 members: Sequence[ImageMarker] = ()) -> None:
        super().__init__()
        self._marker = marker
        self._colouring = colouring
        self._members = tuple(members)
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
            self._paint_leaf(painter)

    def _paint_leaf(self, painter: QPainter) -> None:
        navigable = bool(self._marker.metadata.get("navigation_enabled", True))
        # One glyph rule (``atlas_glyph_key``): a failed tilt series no batch
        # claims keeps its failed glyph with a dashed ring; a position with no
        # unique destination is a dashed grey ring.
        key = atlas_glyph_key(self._marker)
        status = glyph_colour_status(key)
        colouring = self._colouring
        colour = atlas_status_color(status) if colouring is None else key_colour(colouring.key_for(self._marker))
        rect = QRectF(
            -LEAF_RING_RADIUS_DP,
            -LEAF_RING_RADIUS_DP,
            LEAF_RING_RADIUS_DP * 2,
            LEAF_RING_RADIUS_DP * 2,
        )

        if status in {"collected", "failed"}:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(colour)
            painter.drawEllipse(
                QPointF(0.0, 0.0),
                LEAF_FILL_RADIUS_DP,
                LEAF_FILL_RADIUS_DP,
            )
        elif status == "partial":
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
        if key in DASHED_GLYPH_KEYS:
            status_pen.setDashPattern(
                [3.9 / LEAF_STATUS_WIDTH_DP, 2.4 / LEAF_STATUS_WIDTH_DP]
            )
            circumference = 2.0 * math.pi * LEAF_RING_RADIUS_DP
            status_pen.setDashOffset((circumference / 4.0) / LEAF_STATUS_WIDTH_DP)
        painter.setPen(status_pen)
        painter.drawEllipse(rect)

        if status == "failed":
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
        colouring = self._colouring
        colour = (atlas_status_color(self._marker.status or "queued") if colouring is None
                  else key_colour(CLUSTER_NEUTRAL))
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
        segments: tuple[RingSegment[Hashable], ...] = ()
        if colouring is not None:
            segments = ring_segments(colouring.ring_counts(self._members))
        elif isinstance(counts, dict):
            segments = cluster_ring_segments(counts)
        if segments:
            for segment in segments:
                pen = QPen(
                    key_colour(segment.key),
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


class AtlasLodLabelItem(QGraphicsItem):
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


#: The cluster popup's value column in a value mode (batch 4).
POPUP_VALUE_WIDTH = 72.0


class AtlasClusterRow(QPushButton):
    def __init__(
        self,
        marker: ImageMarker,
        parent: QWidget | None = None,
        colouring: AtlasColouring | None = None,
    ) -> None:
        super().__init__(parent)
        self._marker = marker
        #: The value in a few characters (batch 4); the glyph keeps its status.
        self._value = colouring.short_text(marker) if colouring is not None else ""
        explanation = colouring.explanation_for(marker) if colouring is not None else ""
        self.setFixedHeight(24)
        self.setFlat(True)
        self.setCursor(
            Qt.CursorShape.PointingHandCursor
            if bool(marker.metadata.get("navigation_enabled", True))
            else Qt.CursorShape.ArrowCursor
        )
        self.setToolTip("\n".join(text for text in (marker.tooltip or "", explanation) if text))
        # The label is painted, not set, so this button has no text for a
        # screen reader to announce. Spell out the batch and its status, and
        # say when it cannot be opened rather than leaving it silently inert.
        navigable = bool(marker.metadata.get("navigation_enabled", True))
        name = marker.label or marker.linked_object_id or marker.id
        if is_unlinked_failure(marker):
            self.setAccessibleName(f"Tilt series {name}, {acquisition_state(marker.status)}, no batch link")
        else:
            self.setAccessibleName(f"Batch {name}, {acquisition_state(marker.status)}")
        self.setAccessibleDescription(" ".join(text for text in (
            marker.tooltip
            if navigable
            else f"Unavailable. {marker.tooltip or 'No unique destination.'}",
            explanation,
        ) if text))

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt signature

        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        try:
            if self.underMouse():
                painter.fillRect(self.rect(), QColor(CANVAS_CHROME.surface_hi))
            paint_chrome_leaf_glyph(
                painter,
                QPointF(14.0, 12.0),
                atlas_glyph_key(self._marker),
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
            painter.setPen(QColor(CANVAS_CHROME.text_muted))
            painter.drawText(
                QRectF(150.0, 0.0, 48.0, 24.0),
                Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight,
                ratio,
            )
            if self._value:
                painter.setPen(QColor(MARKER_LABEL_TEXT))
                painter.drawText(
                    QRectF(204.0, 0.0, POPUP_VALUE_WIDTH - 12.0, 24.0),
                    Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight,
                    self._value,
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
        colouring: AtlasColouring | None = None,
    ) -> None:
        super().__init__(parent)

        self.setObjectName("atlasClusterPopup")
        self.setFixedWidth(216 + (round(POPUP_VALUE_WIDTH) if colouring is not None else 0))
        self.setStyleSheet(
            f"#atlasClusterPopup {{ background: {CANVAS_CHROME.surface}; "
            f"border: 1px solid {CANVAS_CHROME.border_strong}; border-radius: {RADIUS_CARD}px; }}"
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
        rows.setStyleSheet(f"background: {CANVAS_CHROME.surface};")
        rows_layout = QVBoxLayout(rows)
        rows_layout.setContentsMargins(0, 0, 0, 0)
        rows_layout.setSpacing(0)
        for member in members:
            row = AtlasClusterRow(member, rows, colouring)
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
            f"color: {CANVAS_CHROME.text_muted}; border: none;"
        )
        layout.addWidget(footer)
        self.setFixedHeight(52 + min(len(members), 8) * 24)


def paint_chrome_leaf_glyph(
    painter: QPainter,
    center: QPointF,
    key: str,
    *,
    diameter: float,
    halo: bool,
) -> None:
    """Paint a compact glyph for an ``atlas_glyph_key`` on known dark chrome."""

    theme = current_palette()
    dashed = key in DASHED_GLYPH_KEYS
    status = glyph_colour_status(key)
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
    if dashed:
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


def paint_legend_cluster_glyph(painter: QPainter, center: QPointF) -> None:
    painter.save()
    painter.translate(center)
    painter.setPen(QPen(QColor(MARKER_INK), 3.0))
    painter.setBrush(QColor(IMAGE_STATUS_COLOURS[AtlasMarkerStatus.FAILED]))
    painter.drawEllipse(QPointF(0.0, 0.0), 6.0, 6.0)
    painter.setPen(QPen(QColor(IMAGE_STATUS_COLOURS[AtlasMarkerStatus.FAILED]), 2.0))
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.drawArc(QRectF(-6.0, -6.0, 12.0, 12.0), 90 * 16, -90 * 16)
    painter.setPen(QPen(QColor(IMAGE_STATUS_COLOURS[AtlasMarkerStatus.COLLECTED]), 2.0))
    painter.drawArc(QRectF(-6.0, -6.0, 12.0, 12.0), 0, -270 * 16)
    painter.restore()

