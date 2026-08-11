"""Per-image Defocus scatter plot for the Session dashboard.

The widget is deliberately presentational: it renders
``DefocusPlotModel`` records prepared by the presenter and never reads
MDOC files itself.
"""

from __future__ import annotations

import logging
from datetime import datetime
from hashlib import sha1
from math import ceil, floor
from typing import Any

from PySide6.QtCore import QElapsedTimer, QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPen, QPolygonF
from PySide6.QtWidgets import QSizePolicy, QToolTip, QWidget

from tomography_session_browser.ui.session_presenter import DefocusPlotModel, defocus_point_tooltip
from tomography_session_browser.ui.widgets.time_chart import (
    TIME_CHART_AXIS_LABEL_GAP,
    TIME_CHART_BOTTOM_AXIS_SPACE,
    TIME_CHART_DEFOCUS_MIN_HEIGHT,
    TIME_CHART_ELAPSED_AXIS_LABEL,
    TIME_CHART_LEFT_GUTTER,
    TIME_CHART_RIGHT_GUTTER,
    TIME_CHART_TOP,
    format_time_axis_tick,
    nice_axis_ticks,
    time_axis_tick_count,
    value_axis_tick_count,
)

LOGGER = logging.getLogger(__name__)

# Above this count, individual colour-and-shape painter calls are visibly
# expensive but the 2–4 px symbols are too small for their silhouettes to be
# useful. Dense plots therefore use the earlier, simple-circle visual language
# and submit one point batch per series.
_DENSE_POINT_BATCH_THRESHOLD = 500


class DefocusScatterPlot(QWidget):
    """Theme-aware scatter plot for per-image MRC/MDOC Defocus values."""

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
        self._keyboard_point_index = -1
        self._dense_geometry_cache: dict[str, Any] | None = None
        self._dense_geometry_builds = 0
        self.setMinimumHeight(TIME_CHART_DEFOCUS_MIN_HEIGHT)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setMouseTracking(True)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAccessibleName("Defocus readout plot")
        self.setAccessibleDescription(
            "Scatter plot of per-image Defocus metadata values. "
            "Double-click a point to open its linked tilt series, or use Left or Right "
            "to inspect points and Enter to open one."
        )

    # ------------------------------------------------------------------ API

    def set_model(self, model: DefocusPlotModel | None) -> None:
        self._model = model
        self._keyboard_point_index = -1
        self._dense_geometry_cache = None
        self.update()

    def set_highlighted_tilt_series_id(self, tilt_series_id: str | None) -> None:
        if self._highlighted_tilt_series_id == tilt_series_id:
            return
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
        return point.defocus_um

    def _y_axis_label_text(self) -> str:
        return "Defocus (µm)"

    def _format_y_tick(self, value: float) -> str:
        # Defocus is signed, so the +/- prefix is meaningful — except on the
        # zero line, where "+0.0" is just wrong.
        if abs(value) < 1e-9:
            return "0.0"
        return f"{value:+.1f}"

    def _empty_title(self) -> str:
        return "No Defocus metadata available"

    def _empty_detail(self) -> str:
        return "Per-image MRC or MDOC Defocus values were not found for this selection."

    def _point_tooltip(self, point: Any) -> str:
        return defocus_point_tooltip(point)

    def _axis_label_font(self) -> QFont:
        """Font shared by the horizontal and vertical axis titles."""

        return QFont(self.font())

    # ---------------------------------------------------------------- paint

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt signature
        del event
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
            # Widen the padded data range onto a round-number grid so the
            # axis labels are readable values rather than arbitrary
            # fractions of the data extent.
            y_min, y_max, y_ticks = nice_axis_ticks(
                y_min, y_max, max_ticks=value_axis_tick_count(plot.height())
            )
            self._last_plot_rect = QRectF(plot)
            self._draw_axes(painter, plot, model, x_min, x_max, y_min, y_max, y_ticks, colors)
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
        model: DefocusPlotModel,
        x_min: float,
        x_max: float,
        y_min: float,
        y_max: float,
        y_ticks: list[float],
        colors: dict[str, QColor],
    ) -> None:
        metrics = painter.fontMetrics()
        painter.setPen(QPen(colors["grid"], 1))
        painter.setBrush(Qt.BrushStyle.NoBrush)

        y_span = y_max - y_min
        for value in y_ticks:
            frac = 0.0 if y_span <= 0 else (value - y_min) / y_span
            y = plot.bottom() - plot.height() * frac
            painter.drawLine(QPointF(plot.left(), y), QPointF(plot.right(), y))
            label = self._format_y_tick(value)
            painter.setPen(colors["label"])
            painter.drawText(
                QRectF(26, y - metrics.height() / 2, plot.left() - 34, metrics.height()),
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                label,
            )
            painter.setPen(QPen(colors["grid"], 1))

        tick_count = time_axis_tick_count(plot.width())
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
        model: DefocusPlotModel,
        x_min: float,
        x_max: float,
        y_min: float,
        y_max: float,
    ) -> None:
        if len(model.points) > _DENSE_POINT_BATCH_THRESHOLD:
            self._draw_dense_points(
                painter,
                plot,
                model,
                x_min,
                x_max,
                y_min,
                y_max,
            )
            return

        radius, alpha = _point_style(len(model.points))
        palette = self._series_palette()
        series_styles = {
            label: (_color_for_label(label, palette), _series_index(label, 4))
            for label in {point.sample_name for point in model.points}
        }
        painter.setPen(Qt.PenStyle.NoPen)
        for point_index, point in enumerate(model.points):
            x_value = self._point_x(point, model)
            x_frac = 0.5 if x_max <= x_min else (x_value - x_min) / (x_max - x_min)
            y_value = self._point_value(point)
            y_frac = 0.5 if y_max <= y_min else (y_value - y_min) / (y_max - y_min)
            x = plot.left() + plot.width() * max(0.0, min(x_frac, 1.0))
            y = plot.bottom() - plot.height() * max(0.0, min(y_frac, 1.0))
            base_color, shape_index = series_styles[point.sample_name]
            color = QColor(base_color)
            highlighted = self._point_is_highlighted(point)
            color.setAlpha(self._point_alpha(point, alpha))
            painter.setBrush(color)
            rect = QRectF(x - radius, y - radius, radius * 2, radius * 2)
            _draw_point_symbol(
                painter,
                rect,
                shape_index,
            )
            keyboard_highlighted = point_index == self._keyboard_point_index and self.hasFocus()
            if highlighted or keyboard_highlighted:
                from tomography_session_browser.ui.theme import current_palette

                ring = QColor(current_palette().overlay_selected)
                ring.setAlpha(205 if keyboard_highlighted else 118)
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.setPen(QPen(ring, 1.5 if keyboard_highlighted else 1.0))
                painter.drawEllipse(rect.adjusted(-2, -2, 2, 2))
                painter.setPen(Qt.PenStyle.NoPen)
            hit_rect = rect.adjusted(-3, -3, 3, 3)
            self._point_rects.append((hit_rect, point))
            self._add_point_bin(hit_rect, point)

    def _draw_dense_points(
        self,
        painter: QPainter,
        plot: QRectF,
        model: DefocusPlotModel,
        x_min: float,
        x_max: float,
        y_min: float,
        y_max: float,
    ) -> None:
        """Draw a dense plot in a few vector batches.

        Historical versions used simple circles. Submitting those circles one
        at a time made every Session-tab reveal scale with the number of
        images, while a whole-plot pixmap cache became stale whenever a viewer
        selection changed the highlight. Geometry is stable across highlight
        and focus changes, so cache only that geometry and keep painting live.
        """

        radius, alpha = _point_style(len(model.points))
        geometry = self._dense_point_geometry(
            plot,
            model,
            x_min,
            x_max,
            y_min,
            y_max,
            radius,
        )
        self._point_rects = geometry["point_rects"]
        self._point_bins = geometry["point_bins"]

        palette = self._series_palette()
        base_alpha = (
            alpha
            if not self._highlighted_tilt_series_id
            else max(45, int(alpha * 0.38))
        )
        painter.setBrush(Qt.BrushStyle.NoBrush)
        for label, positions in geometry["series_points"].items():
            color = _color_for_label(label, palette)
            color.setAlpha(base_alpha)
            painter.setPen(
                QPen(
                    color,
                    radius * 2.0,
                    Qt.PenStyle.SolidLine,
                    Qt.PenCapStyle.RoundCap,
                )
            )
            painter.drawPoints(positions)

        highlighted_entries = geometry["by_tilt"].get(
            self._highlighted_tilt_series_id,
            (),
        )
        for entry in highlighted_entries:
            self._draw_dense_point_emphasis(
                painter,
                entry,
                palette,
                radius,
                max(alpha, 235),
                keyboard=False,
            )

        if (
            self.hasFocus()
            and 0 <= self._keyboard_point_index < len(geometry["entries"])
        ):
            entry = geometry["entries"][self._keyboard_point_index]
            self._draw_dense_point_emphasis(
                painter,
                entry,
                palette,
                radius,
                self._point_alpha(entry[2], alpha),
                keyboard=True,
            )

    def _dense_point_geometry(
        self,
        plot: QRectF,
        model: DefocusPlotModel,
        x_min: float,
        x_max: float,
        y_min: float,
        y_max: float,
        radius: float,
    ) -> dict[str, Any]:
        key = (
            id(model),
            len(model.points),
            plot.x(),
            plot.y(),
            plot.width(),
            plot.height(),
            x_min,
            x_max,
            y_min,
            y_max,
            radius,
        )
        cached = self._dense_geometry_cache
        if cached is not None and cached["key"] == key:
            return cached

        series_points: dict[str, list[QPointF]] = {}
        point_rects: list[tuple[QRectF, Any]] = []
        point_bins: dict[tuple[int, int], list[tuple[QRectF, Any]]] = {}
        entries: list[tuple[QPointF, QRectF, Any]] = []
        by_tilt: dict[str, list[tuple[QPointF, QRectF, Any]]] = {}
        x_span = x_max - x_min
        y_span = y_max - y_min
        for point in model.points:
            x_value = self._point_x(point, model)
            x_frac = 0.5 if x_span <= 0 else (x_value - x_min) / x_span
            y_value = self._point_value(point)
            y_frac = 0.5 if y_span <= 0 else (y_value - y_min) / y_span
            x = plot.left() + plot.width() * max(0.0, min(x_frac, 1.0))
            y = plot.bottom() - plot.height() * max(0.0, min(y_frac, 1.0))
            position = QPointF(x, y)
            series_points.setdefault(point.sample_name, []).append(position)
            rect = QRectF(x - radius, y - radius, radius * 2, radius * 2)
            hit_rect = rect.adjusted(-3, -3, 3, 3)
            point_rects.append((hit_rect, point))
            point_bins.setdefault(
                _bin_key(hit_rect.center().x(), hit_rect.center().y()),
                [],
            ).append((hit_rect, point))
            entry = (position, rect, point)
            entries.append(entry)
            tilt_series_id = getattr(point, "tilt_series_id", None)
            if isinstance(tilt_series_id, str) and tilt_series_id:
                by_tilt.setdefault(tilt_series_id, []).append(entry)

        geometry: dict[str, Any] = {
            "key": key,
            "series_points": {
                label: QPolygonF(positions)
                for label, positions in series_points.items()
            },
            "point_rects": point_rects,
            "point_bins": point_bins,
            "entries": tuple(entries),
            "by_tilt": {
                tilt_series_id: tuple(tilt_entries)
                for tilt_series_id, tilt_entries in by_tilt.items()
            },
        }
        self._dense_geometry_cache = geometry
        self._dense_geometry_builds += 1
        return geometry

    @staticmethod
    def _draw_dense_point_emphasis(
        painter: QPainter,
        entry: tuple[QPointF, QRectF, Any],
        palette: list[QColor],
        radius: float,
        alpha: int,
        *,
        keyboard: bool,
    ) -> None:
        from tomography_session_browser.ui.theme import current_palette

        position, rect, point = entry
        color = _color_for_label(point.sample_name, palette)
        color.setAlpha(alpha)
        painter.setPen(
            QPen(
                color,
                radius * 2.0,
                Qt.PenStyle.SolidLine,
                Qt.PenCapStyle.RoundCap,
            )
        )
        painter.drawPoint(position)
        ring = QColor(current_palette().overlay_selected)
        ring.setAlpha(205 if keyboard else 118)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(ring, 1.5 if keyboard else 1.0))
        painter.drawEllipse(rect.adjusted(-2, -2, 2, 2))

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
        model: DefocusPlotModel,
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
            symbol_rect = QRectF(x, y + 4, 7, 7)
            if len(model.points) > _DENSE_POINT_BATCH_THRESHOLD:
                painter.drawEllipse(symbol_rect)
            else:
                _draw_point_symbol(
                    painter,
                    symbol_rect,
                    _series_index(label, 4),
                )
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
            if self._model is not None:
                try:
                    self._keyboard_point_index = self._model.points.index(point)
                except ValueError:
                    self._keyboard_point_index = -1
            self.setFocus(Qt.FocusReason.MouseFocusReason)
            self.pointClicked.emit(tilt_series_id, getattr(point, "frame_index", None))
            self.update()
            event.accept()
            return
        super().mousePressEvent(event)

    def keyPressEvent(self, event) -> None:  # noqa: N802 - Qt signature
        points = list(self._model.points) if self._model is not None else []
        if not points:
            super().keyPressEvent(event)
            return
        key = event.key()
        if key in {Qt.Key.Key_Left, Qt.Key.Key_Up, Qt.Key.Key_Right, Qt.Key.Key_Down}:
            direction = -1 if key in {Qt.Key.Key_Left, Qt.Key.Key_Up} else 1
            if self._keyboard_point_index < 0:
                self._keyboard_point_index = 0 if direction > 0 else len(points) - 1
            else:
                self._keyboard_point_index = (
                    self._keyboard_point_index + direction
                ) % len(points)
            point = points[self._keyboard_point_index]
            QToolTip.showText(
                self.mapToGlobal(self.rect().center()),
                self._point_tooltip(point),
                self,
            )
            self.update()
            event.accept()
            return
        # Space selects, Enter opens — mirroring single click and double click.
        #
        # Before this, Enter, Return and Space all emitted ``pointClicked``, so
        # a keyboard user could highlight a point but had **no way to open it
        # at all**: nothing reached ``pointDoubleClicked``. Worse, that emission
        # was consumed by the receiver's mouse double-click timer, so keyboard
        # activation was delayed and could be cancelled by a following press.
        #
        # This widget has a distinct highlight state, which is why Space and
        # Enter differ here while a plain StatCard treats them alike.
        if key in {Qt.Key.Key_Space, Qt.Key.Key_Return, Qt.Key.Key_Enter}:
            index = self._keyboard_point_index if self._keyboard_point_index >= 0 else 0
            point = points[index]
            tilt_series_id = getattr(point, "tilt_series_id", None)
            if isinstance(tilt_series_id, str) and tilt_series_id:
                frame_index = getattr(point, "frame_index", None)
                if key == Qt.Key.Key_Space:
                    self.pointClicked.emit(tilt_series_id, frame_index)
                else:
                    self.pointDoubleClicked.emit(tilt_series_id, frame_index)
            self._keyboard_point_index = index
            self.update()
            event.accept()
            return
        super().keyPressEvent(event)

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

    def _plot_rect(self, painter: QPainter, model: DefocusPlotModel) -> QRectF:
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
        model: DefocusPlotModel,
    ) -> QRectF:
        height = self._legend_height(painter, model)
        if height <= 0:
            return QRectF()
        return QRectF(plot.left(), TIME_CHART_TOP, plot.width(), height - 4)

    def _legend_height(self, painter: QPainter, model: DefocusPlotModel) -> int:
        labels = self._legend_labels(model)
        if not labels:
            return 0
        metrics = painter.fontMetrics()
        rows = 1 if len(labels) <= 6 else 2
        return rows * (metrics.height() + 3) + 5

    @staticmethod
    def _legend_labels(model: DefocusPlotModel) -> list[str]:
        labels = sorted({point.sample_name for point in model.points})
        if len(labels) < 2 or len(labels) > 12:
            return []
        return labels

    def _x_range(self, model: DefocusPlotModel) -> tuple[float, float]:
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

    def _point_x(self, point: Any, model: DefocusPlotModel) -> float:
        if model.x_mode == "absolute_time" and point.acquisition_time is not None:
            return point.acquisition_time.timestamp()
        return float(point.frame_order)

    def _x_tick_label(self, model: DefocusPlotModel, value: float, start: float, end: float) -> str:
        if model.x_mode != "absolute_time":
            return str(int(round(value)))
        dt = datetime.fromtimestamp(value)
        start_dt = datetime.fromtimestamp(start)
        end_dt = datetime.fromtimestamp(end)
        return format_time_axis_tick(dt, start_dt, end_dt)

    @staticmethod
    def _x_axis_label(model: DefocusPlotModel) -> str:
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
        """Per-sample series colours.

        ``_color_for_label`` picks from this list by hashing the sample name,
        so every entry has to stand on its own — list order confers nothing.
        The requirement is therefore that no two entries are equal (they used
        to be: ``chart_blue`` and ``chart_violet`` were the same hex, so two
        samples could render identically) and that all six stay far enough
        apart to be told apart in a dense scatter.
        """

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
    index = _series_index(label, len(palette))
    return QColor(palette[index])


def _series_index(label: str, count: int) -> int:
    if count <= 0:
        return 0
    digest = sha1(label.encode("utf-8", errors="replace")).digest()
    return int.from_bytes(digest[:2], "big") % count


def _draw_point_symbol(painter: QPainter, rect: QRectF, shape_index: int) -> None:
    """Draw a compact colour-independent series symbol."""

    shape_index %= 4
    if shape_index == 0:
        painter.drawEllipse(rect)
        return
    if shape_index == 1:
        painter.drawRect(rect)
        return
    center = rect.center()
    if shape_index == 2:
        painter.drawPolygon(
            QPolygonF(
                [
                    QPointF(center.x(), rect.top()),
                    QPointF(rect.right(), center.y()),
                    QPointF(center.x(), rect.bottom()),
                    QPointF(rect.left(), center.y()),
                ]
            )
        )
        return
    painter.drawPolygon(
        QPolygonF(
            [
                QPointF(center.x(), rect.top()),
                QPointF(rect.right(), rect.bottom()),
                QPointF(rect.left(), rect.bottom()),
            ]
        )
    )


def _bin_key(x: float, y: float) -> tuple[int, int]:
    cell = 18
    return int(x // cell), int(y // cell)
