"""Applied-defocus scatter plot for the Session dashboard.

The widget is deliberately presentational: it renders
``AppliedDefocusPlotModel`` records prepared by the presenter and never reads
MDOC files itself.
"""

from __future__ import annotations

import logging
from datetime import datetime
from hashlib import sha1
from math import ceil, floor
from typing import Any

from PySide6.QtCore import QElapsedTimer, QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPen
from PySide6.QtWidgets import QSizePolicy, QToolTip, QWidget

from tomography_session_browser.ui.session_presenter import (
    AppliedDefocusPlotModel,
    applied_defocus_point_tooltip,
)
from tomography_session_browser.ui.widgets.time_chart import (
    TIME_CHART_AXIS_LABEL_GAP,
    TIME_CHART_BOTTOM_AXIS_SPACE,
    TIME_CHART_DEFOCUS_MIN_HEIGHT,
    TIME_CHART_ELAPSED_AXIS_LABEL,
    TIME_CHART_LEFT_GUTTER,
    TIME_CHART_RIGHT_GUTTER,
    TIME_CHART_TOP,
    format_time_axis_tick,
    time_axis_tick_count,
)

LOGGER = logging.getLogger(__name__)


class AppliedDefocusScatterPlot(QWidget):
    """Theme-aware scatter plot for microscope-applied MDOC defocus values."""

    pointClicked = Signal(str, object)  # tilt_series_id, 1-based frame_index | None
    pointDoubleClicked = Signal(str, object)  # tilt_series_id, 1-based frame_index | None

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._model: Any | None = None
        self._point_rects: list[tuple[QRectF, Any]] = []
        self._point_bins: dict[tuple[int, int], list[tuple[QRectF, Any]]] = {}
        self._last_plot_rect = QRectF()
        self._last_legend_rect = QRectF()
        self._last_y_axis_label_rect = QRectF()
        self._last_x_axis_label_font = QFont(self.font())
        self._last_y_axis_label_font = QFont(self.font())
        self._highlighted_tilt_series_id: str | None = None
        self.setMinimumHeight(TIME_CHART_DEFOCUS_MIN_HEIGHT)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setMouseTracking(True)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAccessibleName("Applied defocus plot")
        self.setAccessibleDescription(
            "Scatter plot of per-image microscope-applied defocus values. "
            "Double-click a point to open the corresponding tilt series frame."
        )

    # ------------------------------------------------------------------ API

    def set_model(self, model: AppliedDefocusPlotModel | None) -> None:
        self._model = model
        self.update()

    def set_highlighted_tilt_series_id(self, tilt_series_id: str | None) -> None:
        self._highlighted_tilt_series_id = tilt_series_id
        self.update()

    def highlighted_tilt_series_id(self) -> str | None:
        return self._highlighted_tilt_series_id

    def point_style(self) -> tuple[float, int]:
        """Return ``(radius, alpha)`` for the current model size."""

        count = len(self._model.points) if self._model is not None else 0
        return _point_style(count)

    def color_for_label(self, label: str) -> QColor:
        return _color_for_label(label, self._series_palette())

    def _point_value(self, point: Any) -> float:
        return point.applied_defocus_um

    def _y_axis_label_text(self) -> str:
        return "Applied defocus (µm)"

    def _format_y_tick(self, value: float) -> str:
        return f"{value:+.1f}"

    def _empty_title(self) -> str:
        return "No applied-defocus metadata available"

    def _empty_detail(self) -> str:
        return "MDOC defocus values were not found for this selection."

    def _point_tooltip(self, point: Any) -> str:
        return applied_defocus_point_tooltip(point)

    def _axis_label_font(self) -> QFont:
        """Font shared by the horizontal and vertical axis titles."""

        return QFont(self.font())

    # ---------------------------------------------------------------- paint

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt signature
        paint_timer = QElapsedTimer()
        if LOGGER.isEnabledFor(logging.DEBUG):
            paint_timer.start()
        point_count = len(self._model.points) if self._model is not None else 0
        painter = QPainter(self)
        try:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            self._point_rects = []
            self._point_bins = {}

            model = self._model
            if model is None or not model.points:
                self._draw_empty(painter)
                return

            colors = self._colors()
            plot = self._plot_rect(painter, model)
            if plot.width() <= 20 or plot.height() <= 20:
                self._draw_empty(painter)
                return

            x_min, x_max = self._x_range(model)
            y_min, y_max = self._y_range(model.points)
            self._last_plot_rect = QRectF(plot)
            self._draw_axes(painter, plot, model, x_min, x_max, y_min, y_max, colors)
            self._draw_points(painter, plot, model, x_min, x_max, y_min, y_max)
            self._draw_legend(painter, self._legend_rect(painter, plot, model), model, colors)
        finally:
            painter.end()
            if paint_timer.isValid():
                LOGGER.debug(
                    "dashboard plot painted type=%s points=%d paint_ms=%d",
                    self.__class__.__name__,
                    point_count,
                    paint_timer.elapsed(),
                )

    def _draw_axes(
        self,
        painter: QPainter,
        plot: QRectF,
        model: AppliedDefocusPlotModel,
        x_min: float,
        x_max: float,
        y_min: float,
        y_max: float,
        colors: dict[str, QColor],
    ) -> None:
        metrics = painter.fontMetrics()
        painter.setPen(QPen(colors["grid"], 1))
        painter.setBrush(Qt.BrushStyle.NoBrush)

        tick_count = time_axis_tick_count(plot.width())
        for index in range(tick_count):
            frac = index / (tick_count - 1)
            y = plot.bottom() - plot.height() * frac
            painter.drawLine(QPointF(plot.left(), y), QPointF(plot.right(), y))
            value = y_min + (y_max - y_min) * frac
            label = self._format_y_tick(value)
            painter.setPen(colors["label"])
            painter.drawText(
                QRectF(26, y - metrics.height() / 2, plot.left() - 34, metrics.height()),
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                label,
            )
            painter.setPen(QPen(colors["grid"], 1))

        for index in range(tick_count):
            frac = index / (tick_count - 1)
            x = plot.left() + plot.width() * frac
            painter.drawLine(QPointF(x, plot.top()), QPointF(x, plot.bottom()))
            label = self._x_tick_label(model, x_min + (x_max - x_min) * frac, x_min, x_max)
            label_width = metrics.horizontalAdvance(label)
            text_x = x - label_width / 2
            if index == 0:
                text_x = plot.left()
            elif index == tick_count - 1:
                text_x = plot.right() - label_width
            painter.setPen(colors["label"])
            painter.drawText(QPointF(text_x, plot.bottom() + metrics.ascent() + TIME_CHART_AXIS_LABEL_GAP), label)
            painter.setPen(QPen(colors["grid"], 1))

        self._draw_y_axis_label(painter, plot, colors["label"])
        axis_font = self._axis_label_font()
        axis_metrics = QFontMetrics(axis_font)
        self._last_x_axis_label_font = QFont(axis_font)
        painter.setFont(axis_font)
        painter.setPen(colors["label"])
        painter.drawText(
            QRectF(plot.left(), self.height() - axis_metrics.height() - 1, plot.width(), axis_metrics.height()),
            Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter,
            self._x_axis_label(model),
        )

    def _draw_y_axis_label(self, painter: QPainter, plot: QRectF, color: QColor) -> None:
        label = self._y_axis_label_text()
        font = self._axis_label_font()
        metrics = QFontMetrics(font)
        self._last_y_axis_label_font = QFont(font)
        painter.save()
        try:
            painter.setPen(color)
            painter.setFont(font)
            # Keep the rotated title in the left gutter. Tick labels occupy
            # the space immediately beside the plot, so the axis title is
            # anchored near the card edge instead of above the chart.
            x = max(8, (plot.left() - metrics.height()) * 0.18)
            self._last_y_axis_label_rect = QRectF(
                x,
                plot.center().y() - metrics.horizontalAdvance(label) / 2,
                metrics.height(),
                metrics.horizontalAdvance(label),
            )
            painter.translate(x + metrics.height() / 2, plot.center().y())
            painter.rotate(-90)
            painter.drawText(
                QRectF(-plot.height() / 2, -metrics.height() / 2, plot.height(), metrics.height()),
                Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter,
                label,
            )
        finally:
            painter.restore()

    def _draw_points(
        self,
        painter: QPainter,
        plot: QRectF,
        model: AppliedDefocusPlotModel,
        x_min: float,
        x_max: float,
        y_min: float,
        y_max: float,
    ) -> None:
        radius, alpha = _point_style(len(model.points))
        palette = self._series_palette()
        painter.setPen(Qt.PenStyle.NoPen)
        for point in model.points:
            x_value = self._point_x(point, model)
            x_frac = 0.5 if x_max <= x_min else (x_value - x_min) / (x_max - x_min)
            y_value = self._point_value(point)
            y_frac = 0.5 if y_max <= y_min else (y_value - y_min) / (y_max - y_min)
            x = plot.left() + plot.width() * max(0.0, min(x_frac, 1.0))
            y = plot.bottom() - plot.height() * max(0.0, min(y_frac, 1.0))
            color = _color_for_label(point.sample_name, palette)
            highlighted = self._point_is_highlighted(point)
            color.setAlpha(self._point_alpha(point, alpha))
            painter.setBrush(color)
            rect = QRectF(x - radius, y - radius, radius * 2, radius * 2)
            painter.drawEllipse(rect)
            if highlighted:
                from tomography_session_browser.ui.theme import current_palette

                ring = QColor(current_palette().overlay_selected)
                ring.setAlpha(118)
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.setPen(QPen(ring, 1.0))
                painter.drawEllipse(rect.adjusted(-2, -2, 2, 2))
                painter.setPen(Qt.PenStyle.NoPen)
            hit_rect = rect.adjusted(-3, -3, 3, 3)
            self._point_rects.append((hit_rect, point))
            self._add_point_bin(hit_rect, point)

    def _point_is_highlighted(self, point: Any) -> bool:
        return bool(
            self._highlighted_tilt_series_id
            and getattr(point, "tilt_series_id", None) == self._highlighted_tilt_series_id
        )

    def _point_alpha(self, point: Any, base_alpha: int) -> int:
        if not self._highlighted_tilt_series_id:
            return base_alpha
        if self._point_is_highlighted(point):
            return max(base_alpha, 235)
        return max(45, int(base_alpha * 0.38))

    def _draw_legend(
        self,
        painter: QPainter,
        legend_rect: QRectF,
        model: AppliedDefocusPlotModel,
        colors: dict[str, QColor],
    ) -> None:
        self._last_legend_rect = QRectF(legend_rect)
        labels = self._legend_labels(model)
        if not labels or legend_rect.height() <= 0:
            return
        metrics = painter.fontMetrics()
        palette = self._series_palette()
        x = legend_rect.left()
        y = legend_rect.top()
        max_chip_width = max(70, min(128, int(legend_rect.width() / min(len(labels), 6)) - 8))
        row_height = metrics.height() + 3
        for label in labels:
            text = metrics.elidedText(label, Qt.TextElideMode.ElideRight, max_chip_width - 17)
            chip_width = min(max_chip_width, metrics.horizontalAdvance(text) + 18)
            if x + chip_width > legend_rect.right() and x > legend_rect.left():
                x = legend_rect.left()
                y += row_height
            if y + row_height > legend_rect.bottom():
                return
            dot = _color_for_label(label, palette)
            dot.setAlpha(230)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(dot)
            painter.drawEllipse(QRectF(x, y + 4, 7, 7))
            painter.setPen(colors["label"])
            painter.drawText(QPointF(x + 13, y + metrics.ascent() + 1), text)
            x += chip_width + 8

    def _draw_empty(self, painter: QPainter) -> None:
        colors = self._colors()
        painter.setPen(colors["text"])
        font = QFont(self.font())
        font.setWeight(QFont.Weight.DemiBold)
        painter.setFont(font)
        rect = self.rect().adjusted(12, 20, -12, -20)
        painter.drawText(
            rect,
            Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter,
            self._empty_title(),
        )
        font.setWeight(QFont.Weight.Normal)
        painter.setFont(font)
        painter.setPen(colors["label"])
        painter.drawText(
            rect.adjusted(0, 34, 0, 34),
            Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter,
            self._empty_detail(),
        )

    # --------------------------------------------------------------- tooltip

    def event(self, event):  # noqa: N802 - Qt signature
        if event.type() == event.Type.ToolTip and self._model is not None:
            point = self._point_at(event.pos())
            if point is not None:
                QToolTip.showText(event.globalPos(), self._point_tooltip(point), self)
            else:
                QToolTip.hideText()
            event.accept()
            return True
        return super().event(event)

    def mousePressEvent(self, event) -> None:  # noqa: N802 - Qt signature
        if event.button() != Qt.MouseButton.LeftButton:
            super().mousePressEvent(event)
            return

        point = self._point_at(event.pos())
        tilt_series_id = getattr(point, "tilt_series_id", None)
        if isinstance(tilt_series_id, str) and tilt_series_id:
            self.pointClicked.emit(tilt_series_id, getattr(point, "frame_index", None))
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseDoubleClickEvent(self, event) -> None:  # noqa: N802 - Qt signature
        if event.button() != Qt.MouseButton.LeftButton:
            super().mouseDoubleClickEvent(event)
            return

        point = self._point_at(event.pos())
        tilt_series_id = getattr(point, "tilt_series_id", None)
        if isinstance(tilt_series_id, str) and tilt_series_id:
            self.pointDoubleClicked.emit(tilt_series_id, getattr(point, "frame_index", None))
        else:
            QToolTip.showText(event.globalPos(), "No linked tilt series", self)
        event.accept()

    def _point_at(self, pos) -> Any | None:
        key = _bin_key(pos.x(), pos.y())
        candidates: list[tuple[QRectF, Any]] = []
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                candidates.extend(self._point_bins.get((key[0] + dx, key[1] + dy), ()))
        if not candidates:
            candidates = self._point_rects
        best: tuple[float, Any] | None = None
        for rect, point in candidates:
            if not rect.contains(pos):
                continue
            center = rect.center()
            distance = (center.x() - pos.x()) ** 2 + (center.y() - pos.y()) ** 2
            if best is None or distance < best[0]:
                best = (distance, point)
        return best[1] if best is not None else None

    def _add_point_bin(self, rect: QRectF, point: Any) -> None:
        key = _bin_key(rect.center().x(), rect.center().y())
        self._point_bins.setdefault(key, []).append((rect, point))

    # --------------------------------------------------------------- geometry

    def _plot_rect(self, painter: QPainter, model: AppliedDefocusPlotModel) -> QRectF:
        metrics = painter.fontMetrics()
        left = max(TIME_CHART_LEFT_GUTTER, metrics.horizontalAdvance("-99.9") + 38)
        legend_height = self._legend_height(painter, model)
        top = TIME_CHART_TOP + legend_height
        return QRectF(
            left,
            top,
            self.width() - left - TIME_CHART_RIGHT_GUTTER,
            self.height() - top - TIME_CHART_BOTTOM_AXIS_SPACE,
        )

    def _legend_rect(
        self,
        painter: QPainter,
        plot: QRectF,
        model: AppliedDefocusPlotModel,
    ) -> QRectF:
        height = self._legend_height(painter, model)
        if height <= 0:
            return QRectF()
        return QRectF(plot.left(), TIME_CHART_TOP, plot.width(), height - 4)

    def _legend_height(self, painter: QPainter, model: AppliedDefocusPlotModel) -> int:
        labels = self._legend_labels(model)
        if not labels:
            return 0
        metrics = painter.fontMetrics()
        rows = 1 if len(labels) <= 6 else 2
        return rows * (metrics.height() + 3) + 5

    @staticmethod
    def _legend_labels(model: AppliedDefocusPlotModel) -> list[str]:
        labels = sorted({point.sample_name for point in model.points})
        if len(labels) < 2 or len(labels) > 12:
            return []
        return labels

    def _x_range(self, model: AppliedDefocusPlotModel) -> tuple[float, float]:
        values = [self._point_x(point, model) for point in model.points]
        if not values:
            return 0.0, 1.0
        lo = min(values)
        hi = max(values)
        if hi <= lo:
            return lo - 0.5, hi + 0.5
        return lo, hi

    def _y_range(self, points: list[Any]) -> tuple[float, float]:
        values = [self._point_value(point) for point in points]
        lo = min(values)
        hi = max(values)
        if hi <= lo:
            pad = 0.5 if abs(lo) < 0.5 else abs(lo) * 0.15
            return lo - pad, hi + pad
        pad = max((hi - lo) * 0.08, 0.1)
        lo -= pad
        hi += pad
        return floor(lo * 10) / 10, ceil(hi * 10) / 10

    def _point_x(self, point: Any, model: AppliedDefocusPlotModel) -> float:
        if model.x_mode == "absolute_time" and point.acquisition_time is not None:
            return point.acquisition_time.timestamp()
        return float(point.frame_order)

    def _x_tick_label(self, model: AppliedDefocusPlotModel, value: float, start: float, end: float) -> str:
        if model.x_mode != "absolute_time":
            return str(int(round(value)))
        dt = datetime.fromtimestamp(value)
        start_dt = datetime.fromtimestamp(start)
        end_dt = datetime.fromtimestamp(end)
        return format_time_axis_tick(dt, start_dt, end_dt)

    @staticmethod
    def _x_axis_label(model: AppliedDefocusPlotModel) -> str:
        if model.x_mode == "absolute_time":
            return TIME_CHART_ELAPSED_AXIS_LABEL
        return model.x_axis_label

    # --------------------------------------------------------------- colours

    def _colors(self) -> dict[str, QColor]:
        from tomography_session_browser.ui.theme import current_palette

        theme = current_palette()
        grid = QColor(theme.border)
        grid.setAlpha(130)
        return {
            "text": QColor(theme.text_strong),
            "label": QColor(theme.text_muted),
            "grid": grid,
        }

    def _series_palette(self) -> list[QColor]:
        from tomography_session_browser.ui.theme import current_palette

        theme = current_palette()
        return [
            QColor(theme.accent),
            QColor(theme.chart_blue),
            QColor(theme.chart_green),
            QColor(theme.chart_amber),
            QColor(theme.chart_violet),
            QColor(theme.chart_red),
        ]


def _point_style(count: int) -> tuple[float, int]:
    if count <= 500:
        return 3.2, 210
    if count <= 3000:
        return 2.2, 145
    return 1.4, 88


def _color_for_label(label: str, palette: list[QColor]) -> QColor:
    if not palette:
        from tomography_session_browser.ui.theme import current_palette

        return QColor(current_palette().accent)
    digest = sha1(label.encode("utf-8", errors="replace")).digest()
    index = int.from_bytes(digest[:2], "big") % len(palette)
    return QColor(palette[index])


def _bin_key(x: float, y: float) -> tuple[int, int]:
    cell = 18
    return int(x // cell), int(y // cell)
