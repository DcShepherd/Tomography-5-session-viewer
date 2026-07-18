"""Warp-style donut chart used by the Session dashboard.

Draws a ring chart with a centre label. Segments take colours from the
caller — typically the active theme's chart palette so the chart stays
readable in both dark and light modes.
"""

from __future__ import annotations

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import QSizePolicy, QWidget


class DonutChart(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._segments: list[tuple[str, int, str]] = []
        self._center_label = "0"
        self._center_caption = ""
        self._track_color: QColor | None = None
        # Text colours default to ``None`` so the painter falls back to the
        # widget's palette (which Qt updates when the app stylesheet changes).
        # Hard-coded dark-on-light label colours left the centre label invisible on
        # the dark theme.
        self._label_color: QColor | None = None
        self._caption_color: QColor | None = None
        # Fixed minimum *square* keeps the chart from collapsing into a
        # different size between rebuilds — the parent dashboard expects a
        # stable footprint per card.
        self.setMinimumSize(160, 160)
        self.setSizePolicy(QSizePolicy.Policy.MinimumExpanding, QSizePolicy.Policy.MinimumExpanding)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)

    # ----- API ---------------------------------------------------------------

    def set_segments(self, segments: list[tuple[str, int, str]]) -> None:
        """``segments`` is a list of ``(label, value, color)`` triples."""

        self._segments = list(segments)
        self.update()

    def set_center(self, label: str, caption: str = "") -> None:
        self._center_label = label
        self._center_caption = caption
        self.update()

    def set_track_color(self, color: str | QColor | None) -> None:
        self._track_color = QColor(color) if color is not None else None
        self.update()

    def set_text_colors(self, label: str | QColor | None, caption: str | QColor | None = None) -> None:
        """Override the centre label / caption colours.

        Pass ``None`` to fall back to the widget palette. The dashboard sets
        these from the active theme palette so the chart stays readable in
        both dark and light modes.
        """

        self._label_color = QColor(label) if label is not None else None
        self._caption_color = QColor(caption) if caption is not None else None
        self.update()

    # ----- paint -------------------------------------------------------------

    def paintEvent(self, event) -> None:  # noqa: N802 — Qt signature
        painter = QPainter(self)
        try:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            size = min(self.width(), self.height())
            margin = 8
            rect = QRectF(
                (self.width() - size) / 2 + margin,
                (self.height() - size) / 2 + margin,
                size - margin * 2,
                size - margin * 2,
            )
            ring_thickness = max(rect.width() * 0.18, 12)
            inner_rect = rect.adjusted(ring_thickness, ring_thickness, -ring_thickness, -ring_thickness)

            # Stylesheet-based theme changes do not reliably propagate into
            # already-rendered custom painters. Pull from the active theme at
            # paint time so the chart updates immediately after a theme switch.
            from tomography_session_browser.ui.theme import current_palette

            theme = current_palette()
            total = sum(value for _, value, _ in self._segments)
            # Background track ring
            track_pen = QPen(self._track_color or QColor(theme.surface_hi), ring_thickness)
            track_pen.setCapStyle(Qt.PenCapStyle.FlatCap)
            painter.setPen(track_pen)
            painter.drawArc(rect.adjusted(ring_thickness / 2, ring_thickness / 2, -ring_thickness / 2, -ring_thickness / 2), 0, 360 * 16)

            # Segments
            if total > 0:
                start_angle = 90 * 16  # 12 o'clock
                for _, value, color in self._segments:
                    if value <= 0:
                        continue
                    span = -int(360 * 16 * (value / total))
                    pen = QPen(QColor(color), ring_thickness)
                    pen.setCapStyle(Qt.PenCapStyle.FlatCap)
                    painter.setPen(pen)
                    painter.drawArc(rect.adjusted(ring_thickness / 2, ring_thickness / 2, -ring_thickness / 2, -ring_thickness / 2), start_angle, span)
                    start_angle += span

            label_color = self._label_color or QColor(theme.text_strong)
            painter.setPen(label_color)
            font = self.font()
            big = QFont(font)
            base_size = font.pointSizeF() if font.pointSizeF() > 0 else 10.0
            big.setPointSizeF(max(base_size + 4.0, min(base_size + 9.0, inner_rect.height() * 0.26)))
            big.setWeight(QFont.Weight.DemiBold)
            painter.setFont(big)
            label_metrics = painter.fontMetrics()
            label_height = label_metrics.height()
            caption_height = 0
            caption_gap = 0
            small = QFont(font)
            if self._center_caption:
                small.setPointSizeF(max(7.0, min(base_size - 1.0, inner_rect.height() * 0.14)))
                painter.setFont(small)
                caption_height = painter.fontMetrics().height()
                caption_gap = 4
            total_text_height = label_height + caption_gap + caption_height
            text_top = inner_rect.center().y() - (total_text_height / 2.0)
            painter.setFont(big)
            painter.drawText(
                QRectF(inner_rect.left(), text_top, inner_rect.width(), label_height),
                Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter,
                self._center_label,
            )
            if self._center_caption:
                painter.setFont(small)
                caption_color = self._caption_color or QColor(theme.text_muted)
                painter.setPen(caption_color)
                painter.drawText(
                    QRectF(
                        inner_rect.left(),
                        text_top + label_height + caption_gap,
                        inner_rect.width(),
                        caption_height,
                    ),
                    Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter,
                    self._center_caption,
                )
        finally:
            painter.end()
