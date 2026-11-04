"""Indexed metric plots; the immutable model owns values, bins and row identity."""
from __future__ import annotations
from tomography_session_browser.presentation.help_content import LocalBinding, InteractionKind

import math
from PySide6.QtCore import QPointF, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPen, QPolygonF
from PySide6.QtWidgets import QWidget, QSizePolicy

from tomography_session_browser.presentation.dashboard.index_chart import IndexChartModel, IndexSeries
from tomography_session_browser.presentation.recorded_metrics import value_text
from tomography_session_browser.ui.theme import current_palette
from tomography_session_browser.ui.widgets.chart_selection import BinPick, ChartPick, RowPick
from tomography_session_browser.ui.widgets.keyboard_focus import keyboard_focus_reason


class IndexChart(QWidget):
    """Draws the pick it is shown and reports intents; ``ChartSelection`` owns the pick.

    Click picks, double-click opens, right-click clears, as on the Summary's plots.
    """

    hovered = Signal(str)
    picked = Signal(object)
    opened = Signal(object)
    clear_requested = Signal()

    def __init__(self, model: IndexChartModel, parent: QWidget | None = None, *, focus_frame: QWidget | None = None) -> None:
        super().__init__(parent)
        self.model = model
        self.highlighted_key = ""
        self.pinned: ChartPick | None = None
        self.keyboard_focus = False
        self.focus_frame = focus_frame
        self.setMinimumHeight(300)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setMouseTracking(True)
        self.setAccessibleName(f"{model.title}, {model.unit}, {len(model.row_keys)} tomograms")
        self.setAccessibleDescription("Left/Right: tomogram. Up/Down: higher or lower count bin. Shift+Left/Right: next bin lane. "
                                      "Enter: open. Escape: clear.")

    def sizeHint(self) -> QSize:  # noqa: N802
        return QSize(620, 300)

    def legend_lines(self) -> tuple[str, ...]:
        result = []
        for series in self.model.series:
            count = sum(value is not None for value in series.values)
            text = f"{count} of {len(self.model.row_keys)} · {series.label}"
            if series.median is not None:
                text += f" · median {value_text(series.key, series.median)} {series.unit}"
            result.append(text)
        return tuple(result)

    def plot_rect(self) -> QRectF:
        metrics = self.fontMetrics()
        # Six-significant-digit finite labels fit this shared gutter. Slot geometry
        # must not change with a metric's magnitude or numeric coverage.
        left = max(54, metrics.horizontalAdvance("-0.00000e+000") + 24)
        side = min(120., max(64., self.width() * .15)) if len(self.model.row_keys) > 24 else 0.
        available = max(1., self.width() - left - 16 - (side + 14 if side else 0))
        width = min(available, max(1, len(self.model.row_keys)) * 80.)
        # Leave a text row between legends and the top y-axis tick at native DPR.
        top = 8 + len(self.model.series) * (metrics.height() + 4) + metrics.height()
        tick_gap = width / max(1, len(self.model.row_keys)) if len(self.model.row_keys) <= 24 else width / max(1, len(self.model.x_ticks) - 1)
        tick_width = max((metrics.horizontalAdvance(str(value)) for value in self.model.x_ticks), default=1)
        rows = min(4, max(1, math.ceil((tick_width + 2) / max(1., tick_gap))))
        lane = 22 if any(s.missing_lane for s in self.model.series) else 0
        return QRectF(left, top, width, max(20., self.height() - top - 32 - rows * metrics.height() - lane))

    def missing_point(self, index: int) -> QPointF:
        point = self.point(index, self.model.y_range[0])
        return QPointF(point.x(), self.plot_rect().bottom() + 12)

    def point(self, index: int, value: float, rect: QRectF | None = None) -> QPointF:
        rect = self.plot_rect() if rect is None else rect
        low, high = self.model.y_range
        scale = max(abs(low), abs(high)) or 1.
        return QPointF(rect.left() + (index + .5) * rect.width() / max(1, len(self.model.row_keys)),
                       rect.bottom() - (value / scale - low / scale) / (high / scale - low / scale) * rect.height())

    def bin_rects(self) -> tuple[tuple[int, int, QRectF], ...]:
        rect = self.plot_rect()
        active = [(index, item) for index, item in enumerate(self.model.series) if item.bins]
        width = min(120., max(64., self.width() * .15))
        lane = width / max(1, len(active))
        result = []
        for column, (series_index, series) in enumerate(active):
            maximum = max(len(value.row_keys) for value in series.bins)
            for index, value in enumerate(series.bins):
                y0, y1 = self.point(0, value.upper).y(), self.point(0, value.lower).y()
                height = max(4., y1 - y0 - 1)
                result.append((series_index, index, QRectF(rect.right() + 12 + column * lane, (y0 + y1 - height) / 2,
                              max(2., (lane - 4) * len(value.row_keys) / maximum), height)))
        return tuple(result)

    def show_pick(self, pinned: ChartPick | None, hovered: str = "") -> None:
        """Ring the hovered tomogram, else the pinned one; outline a pinned bin of this chart."""
        key = hovered or (pinned.key if isinstance(pinned, RowPick) else "")
        if (self.pinned, self.highlighted_key) != (pinned, key):
            self.pinned, self.highlighted_key = pinned, key
            self.update()

    def rows(self, pick: ChartPick) -> frozenset[str]:
        if isinstance(pick, BinPick):
            return frozenset(self.model.series[pick.series].bins[pick.index].row_keys)
        return frozenset((pick.key,))

    def describe(self, pick: ChartPick | None) -> str:
        """The pick in words: a tomogram's values, or a bin's lane, size and range."""
        if isinstance(pick, RowPick):
            return self.detail(self.model.row_keys.index(pick.key)) if pick.key in self.model.row_keys else ""
        if isinstance(pick, BinPick) and pick.chart is self:
            series = self.model.series[pick.series]
            value = series.bins[pick.index]
            return (f"{series.label} · {len(value.row_keys)} tomograms · "
                    f"{value_text(series.key, value.lower)}–{value_text(series.key, value.upper)} {self.model.unit}")
        return ""

    def detail(self, index: int) -> str:
        if not 0 <= index < len(self.model.row_keys):
            return "No tomograms"
        lines = [f"{index + 1} · {self.model.names[index]}"]
        for series in self.model.series:
            value = series.values[index]
            lines.append(f"{series.label}: {value_text(series.key, value)} {series.unit}" if value is not None else f"{series.label}: {series.missing[index]}")
        if self.model.slabs:
            slab = self.model.slabs[index]
            lines.append(f"Reconstructed slab: {slab.value:g} nm" if slab.value is not None else slab.note)
            lines.extend(slab.sources)
        return "\n".join(lines)

    def colour(self, series: IndexSeries) -> QColor:
        palette = current_palette()
        return QColor({"blue": palette.chart_blue, "green": palette.chart_green,
                       "violet": palette.chart_violet, "amber": palette.chart_amber}.get(series.tone, palette.chart_blue))

    def symbol(self, painter: QPainter, point: QPointF, series: IndexSeries, radius: float) -> None:
        if series.shape == "diamond":
            painter.drawPolygon(QPolygonF([point + QPointF(0, -radius), point + QPointF(radius, 0),
                                           point + QPointF(0, radius), point + QPointF(-radius, 0)]))
        else:
            painter.drawEllipse(point, radius, radius)

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        palette, rect, metrics = current_palette(), self.plot_rect(), self.fontMetrics()
        for index, (series, text) in enumerate(zip(self.model.series, self.legend_lines(), strict=True)):
            painter.setPen(self.colour(series))
            painter.setBrush(self.colour(series))
            y = 8 + (index + .5) * (metrics.height() + 4)
            self.symbol(painter, QPointF(7, y), series, 3.5)
            painter.setPen(QColor(palette.text_muted))
            painter.drawText(QRectF(18, y - metrics.height()/2, self.width() - 20, metrics.height()),
                             metrics.elidedText(text, Qt.TextElideMode.ElideRight, max(1, self.width() - 20)))
        painter.setPen(QPen(QColor(palette.border), 1))
        for value in self.model.y_ticks:
            y = self.point(0, value, rect).y()
            painter.drawLine(QPointF(rect.left(), y), QPointF(rect.right(), y))
            painter.setPen(QColor(palette.text_muted))
            painter.drawText(QRectF(16, y - metrics.height()/2, rect.left() - 22, metrics.height()),
                             Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, f"{value:g}")
            painter.setPen(QPen(QColor(palette.border), 1))
        painter.save()
        painter.setClipRect(rect.adjusted(-5, -5, 5, 5))
        if self.model.slabs:
            slabs = self.model.slabs
            values = {slab.value for slab in slabs}
            painter.setPen(QPen(QColor(palette.text_muted), 1, Qt.PenStyle.DashLine))
            if len(values) == 1 and None not in values:
                y = self.point(0, slabs[0].value or 0., rect).y()
                painter.drawLine(QPointF(rect.left(), y), QPointF(rect.right(), y))
                text = f"Slab {slabs[0].value:g} nm"
                if metrics.horizontalAdvance(text) > rect.width() - 8:
                    text = "Slab"
                painter.drawText(QRectF(rect.left() + 4, y - metrics.height() - 2, rect.width() - 8, metrics.height()),
                                 Qt.AlignmentFlag.AlignRight, text)
            else:
                for index, slab in enumerate(slabs):
                    if slab.value is not None:
                        point = self.point(index, slab.value, rect)
                        painter.drawLine(point + QPointF(-4, 0), point + QPointF(4, 0))
        if len(self.model.series) == 2:
            painter.setPen(QPen(QColor(palette.text_muted), 1))
            for index, (first, second) in enumerate(zip(self.model.series[0].values, self.model.series[1].values, strict=True)):
                if first is not None and second is not None:
                    painter.drawLine(self.point(index, first, rect), self.point(index, second, rect))
        for series in self.model.series:
            colour = self.colour(series)
            painter.setPen(QPen(colour, 1))
            painter.setBrush(colour)
            if len(self.model.series) == 1 and series.median is not None:
                y = self.point(0, series.median, rect).y()
                painter.setPen(QPen(colour, 1, Qt.PenStyle.DotLine))
                painter.drawLine(QPointF(rect.left(), y), QPointF(rect.right(), y))
                painter.setPen(QPen(colour, 1))
            for index, value in enumerate(series.values):
                if value is not None:
                    self.symbol(painter, self.point(index, value, rect), series, 3.5 if len(series.values) <= 24 else 1.8)
                    if self.model.row_keys[index] == self.highlighted_key:
                        painter.save()
                        painter.setBrush(Qt.BrushStyle.NoBrush)
                        painter.setPen(QPen(colour, 1.5))
                        painter.drawEllipse(self.point(index, value, rect), 6., 6.)
                        painter.restore()
        painter.restore()
        for series in self.model.series:
            if series.missing_lane:
                painter.setPen(QPen(self.colour(series), 1))
                painter.setBrush(Qt.BrushStyle.NoBrush)
                for index, (value, reason) in enumerate(zip(series.values, series.missing, strict=True)):
                    if value is None and reason == series.missing_lane:
                        painter.drawEllipse(self.missing_point(index), 3., 3.)
                painter.drawText(QRectF(16, rect.bottom() + 3, rect.left() - 22, 20), Qt.AlignmentFlag.AlignRight, "–")
        chosen = (self.pinned.series, self.pinned.index) if isinstance(self.pinned, BinPick) and self.pinned.chart is self else None
        for series_index, bin_index, bounds in self.bin_rects():
            colour = self.colour(self.model.series[series_index])
            colour.setAlphaF(.9 if (series_index, bin_index) == chosen else .65)
            painter.fillRect(bounds, colour)
            if (series_index, bin_index) == chosen:
                painter.setPen(QPen(QColor(palette.text_strong), 1))
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.drawRect(bounds)
        painter.setPen(QColor(palette.text_muted))
        tick_gap = rect.width() / max(1, len(self.model.row_keys)) if len(self.model.row_keys) <= 24 else rect.width() / max(1, len(self.model.x_ticks) - 1)
        rows = min(4, max(1, math.ceil((max((metrics.horizontalAdvance(str(x)) for x in self.model.x_ticks), default=1) + 2) / max(1., tick_gap))))
        for index, tick in enumerate(self.model.x_ticks):
            x = self.point(tick - 1, self.model.y_range[0], rect).x()
            lane = 22 if any(s.missing_lane for s in self.model.series) else 0
            painter.drawText(QRectF(x - 18, rect.bottom() + 3 + lane + index % rows * metrics.height(), 36, metrics.height()),
                             Qt.AlignmentFlag.AlignHCenter, str(tick))
        painter.drawText(QRectF(rect.left(), self.height() - metrics.height() - 2, max(rect.width(), 150), metrics.height()),
                         Qt.AlignmentFlag.AlignHCenter, "Tomogram, in list order")
        if self.bin_rects():
            painter.drawText(QRectF(rect.right() + 8, rect.bottom() + 4, self.width() - rect.right() - 8, metrics.height()),
                             Qt.AlignmentFlag.AlignHCenter, "Count")
        painter.save()
        painter.translate(12, rect.center().y())
        painter.rotate(-90)
        painter.drawText(QRectF(-rect.height()/2, -metrics.height()/2, rect.height(), metrics.height()),
                         Qt.AlignmentFlag.AlignCenter, f"{self.model.title} ({self.model.unit})")
        painter.restore()
        if self.focus_frame is None and self.keyboard_focus and self.hasFocus():
            painter.setPen(QPen(QColor(palette.accent), 1, Qt.PenStyle.DotLine))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRect(self.rect().adjusted(1, 1, -2, -2))
        painter.end()

    def focusInEvent(self, event) -> None:  # noqa: N802
        self.keyboard_focus = keyboard_focus_reason(event.reason())
        self.refresh_focus_frame()
        self.update()
        super().focusInEvent(event)

    def focusOutEvent(self, event) -> None:  # noqa: N802
        self.keyboard_focus = False
        self.refresh_focus_frame()
        self.update()
        super().focusOutEvent(event)

    def refresh_focus_frame(self) -> None:
        if self.focus_frame is not None:
            frame = self.focus_frame
            frame.setProperty('keyboardFocus', self.keyboard_focus)
            frame.style().unpolish(frame)
            frame.style().polish(frame)
            frame.update()

    def pick_at(self, point: QPointF) -> ChartPick | None:
        selected = next(((series, index) for series, index, rect in self.bin_rects() if rect.contains(point)), None)
        if selected is not None:
            return BinPick(self, *selected)
        rect = self.plot_rect()
        if any(s.missing_lane for s in self.model.series):
            rect = rect.adjusted(0, 0, 0, 22)
        if rect.contains(point) and self.model.row_keys:
            index = min(len(self.model.row_keys) - 1, int((point.x() - rect.left()) / rect.width() * len(self.model.row_keys)))
            return RowPick(self.model.row_keys[index])
        return None

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        pick = self.pick_at(event.position())
        self.setToolTip(self.describe(pick) if pick is not None else self.model.description + "\n" + "\n".join(self.legend_lines()))
        self.hovered.emit(pick.key if isinstance(pick, RowPick) else "")
        super().mouseMoveEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802
        self.hovered.emit("")
        super().leaveEvent(event)

    def mousePressEvent(self, event) -> None:  # noqa: N802
        pick = self.pick_at(event.position())
        if event.button() == Qt.MouseButton.RightButton:
            self.clear_requested.emit()
        elif event.button() == Qt.MouseButton.LeftButton and pick is not None:
            self.picked.emit(pick)
        else:
            super().mousePressEvent(event)
            return
        event.accept()

    def mouseDoubleClickEvent(self, event) -> None:  # noqa: N802
        pick = self.pick_at(event.position())
        if event.button() == Qt.MouseButton.LeftButton and pick is not None:
            self.opened.emit(self.rows(pick))
            event.accept()
            return
        super().mouseDoubleClickEvent(event)

    def keyboard_pick(self, key: int, shift: bool) -> ChartPick | None:
        """The pick an arrow key moves to: tomograms in list order, bins by value within a lane."""
        keys, lanes = self.model.row_keys, [index for index, series in enumerate(self.model.series) if series.bins]
        pinned = self.pinned
        own_bin = pinned if isinstance(pinned, BinPick) and pinned.chart is self else None
        row = keys.index(pinned.key) if isinstance(pinned, RowPick) and pinned.key in keys else None
        if key in (Qt.Key.Key_Left, Qt.Key.Key_Right) and shift and own_bin is not None and len(lanes) > 1:
            lane = lanes[max(0, min(len(lanes) - 1, lanes.index(own_bin.series) + (1 if key == Qt.Key.Key_Right else -1)))]
            current = self.model.series[own_bin.series].bins[own_bin.index]
            middle = (current.lower + current.upper) / 2
            bins = self.model.series[lane].bins
            return BinPick(self, lane, min(range(len(bins)), key=lambda i: abs((bins[i].lower + bins[i].upper) / 2 - middle)))
        if key in (Qt.Key.Key_Left, Qt.Key.Key_Right) and keys:
            step = 1 if key == Qt.Key.Key_Right else -1
            return RowPick(keys[0 if row is None else max(0, min(len(keys) - 1, row + step))])
        if key in (Qt.Key.Key_Up, Qt.Key.Key_Down) and lanes:
            up = key == Qt.Key.Key_Up
            if own_bin is not None:
                last = len(self.model.series[own_bin.series].bins) - 1
                return BinPick(self, own_bin.series, max(0, min(last, own_bin.index + (1 if up else -1))))
            if row is not None:
                for lane in lanes:
                    holder = next((i for i, value in enumerate(self.model.series[lane].bins) if keys[row] in value.row_keys), None)
                    if holder is not None:
                        return BinPick(self, lane, holder)
            # Entering a lane: Up starts at the lowest bin and climbs; Down starts at the highest.
            return BinPick(self, lanes[0], 0 if up else len(self.model.series[lanes[0]].bins) - 1)
        return None

    def keyPressEvent(self, event) -> None:  # noqa: N802
        key = event.key()
        shift = bool(event.modifiers() & Qt.KeyboardModifier.ShiftModifier)
        moved = self.keyboard_pick(key, shift)
        if moved is not None:
            self.setAccessibleDescription(self.describe(moved))
            self.picked.emit(moved)
        elif key == Qt.Key.Key_Space and self.model.row_keys:
            self.picked.emit(self.pinned or RowPick(self.model.row_keys[0]))
        elif key in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and self.describe(self.pinned):
            self.opened.emit(self.rows(self.pinned))
        elif key == Qt.Key.Key_Escape:
            self.clear_requested.emit()
        else:
            super().keyPressEvent(event)
            return
        event.accept()


# Contextual keys handled by this module; consumed by the shared Help sheet.
LOCAL_KEYS = (
    LocalBinding('widgets.index_chart.index-chart-left', 'Index chart', InteractionKind.KEYBOARD, ('Left', 'Right'), 'Highlight the previous or next tomogram'),
    LocalBinding('widgets.index_chart.index-chart-up', 'Index chart', InteractionKind.KEYBOARD, ('Up', 'Down'), 'Highlight the higher or lower count bin'),
    LocalBinding('widgets.index_chart.index-chart-lane', 'Index chart', InteractionKind.KEYBOARD, ('Shift+Left', 'Shift+Right'), 'Highlight the same bin in the next lane'),
    LocalBinding('widgets.index_chart.index-chart-space', 'Index chart', InteractionKind.KEYBOARD, ('Space',), 'Highlight a tomogram'),
    LocalBinding('widgets.index_chart.index-chart-enter', 'Index chart', InteractionKind.KEYBOARD, ('Enter', 'Return'), 'Open the highlighted tomogram or bin'),
    LocalBinding('widgets.index_chart.index-chart-esc', 'Index chart', InteractionKind.KEYBOARD, ('Esc',), 'Clear the highlight'),
    LocalBinding('widgets.index_chart.index-chart-pointer', 'Index chart', InteractionKind.MOUSE, ('Click', 'Double-click', 'Right-click'), 'Highlight a tomogram or bin, open it, or clear the highlight'),
)
