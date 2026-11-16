"""The Atlas colour key: what the marker colours mean in a value mode (batch 4).

It sits at the bottom centre of the canvas (``CanvasControls.place_bottom_anchors``)
on the canvas chrome, while the Positions card shows and a value mode is set;
Clean image hides it, and an image export draws it where it is. It shows:

- the measure's name and the range of the markers shown (decision 20);
- a bar per bin on the ramp, with four values or more;
- the ramp strip, and ticks with the unit inside the observed range;
- "No value" with its count.

Tomogram and Tags modes show their categories and No value, with counts. With
little room a value key drops the bars first, then the ticks
(``set_available_width``).

Each entry (a bin, a category, No value) points at its markers (batch 5). The
pointer on an entry, or the keyboard, emits ``highlighted`` with the entry's
marker ids, and ``ColourBy`` asks the marker layer to dim the other glyphs. The
key is one Tab stop: Left and Right step through the entries and Esc clears.
Moving off the key, leaving it by the keyboard or hiding it clears. Nothing is
pinned; the colouring does not change.
"""
from __future__ import annotations

from collections.abc import Hashable
from dataclasses import dataclass

from PySide6.QtCore import QEvent, QPointF, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QAccessible, QAccessibleEvent, QColor, QFont, QFontMetricsF, QLinearGradient, QPainter, QPen
from PySide6.QtWidgets import QToolTip, QWidget

from tomography_session_browser.presentation.atlas_colouring import (
    MODE_CATEGORIES,
    NO_VALUE,
    PRESENCE_LABELS,
    AtlasColouring,
    TomogramPresence,
    mode_name,
)
from tomography_session_browser.presentation.dashboard.index_chart import value_axis
from tomography_session_browser.presentation.design_tokens import CANVAS_CHROME, RADIUS_CARD, SANS_FONT_NAME
from tomography_session_browser.presentation.help_content import InteractionKind, LocalBinding
from tomography_session_browser.presentation.marker_palette import COLOUR_RAMP, ramp_colour
from tomography_session_browser.presentation.recorded_metrics import value_text
from tomography_session_browser.services.atlas_marker_style import MARKER_INK
from tomography_session_browser.ui.viewer.marker_paint import key_colour

#: Fewer values than this draw no bars: a bar per value says nothing a count does not.
HISTOGRAM_MIN_VALUES = 4

WIDTH = 300
#: Below these widths the bars, then the ticks, are left out.
BARS_MIN_WIDTH = 260
TICKS_MIN_WIDTH = 200
MIN_WIDTH = 150

PAD = 8.0
TITLE_H = 18.0
BARS_H = 26.0
STRIP_H = 8.0
TICKS_H = 15.0
ROW_H = 18.0


#: The hit band round the strip when no bars are drawn, so a thin strip is easy to point at.
STRIP_HIT_PAD = 4.0
#: Room between an entry's right-hand text and the outline of the entry the pointer is on.
NO_VALUE_OUTLINE_ROOM = 4.0


@dataclass(frozen=True, slots=True)
class KeyEntry:
    """One entry of the key: where it is, the markers it points at, and what it says."""
    rect: QRectF
    ids: frozenset[str]
    text: str


def _positions(count: int) -> str:
    return f"{count} position" if count == 1 else f"{count} positions"


def _font(pixels: int, weight: QFont.Weight = QFont.Weight.Normal) -> QFont:
    font = QFont(SANS_FONT_NAME)
    font.setPixelSize(pixels)
    font.setWeight(weight)
    return font


class ColourKey(QWidget):
    #: The marker ids of the entry the pointer or the keyboard is on; ``None`` when none.
    highlighted = Signal(object)

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setObjectName("atlasColourKey")
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        self.setAccessibleName("Colour key")
        self.colouring: AtlasColouring | None = None
        self.show_bars = True
        self.show_ticks = True
        self._width = WIDTH
        #: The index in ``entries()`` the pointer or the keyboard is on.
        self.current: int | None = None
        self._entries: list[KeyEntry] | None = None
        self.setVisible(False)

    # Content ------------------------------------------------------------
    def set_colouring(self, colouring: AtlasColouring | None) -> None:
        if colouring is self.colouring:
            return
        self.set_current(None)
        self.colouring = colouring
        self._entries = None
        self._refit()
        self._describe()
        self.update()

    def set_available_width(self, width: int) -> None:
        """Fit into ``width``: drop the bars first, then the ticks."""

        target = max(MIN_WIDTH, min(WIDTH, width))
        bars, ticks = target >= BARS_MIN_WIDTH, target >= TICKS_MIN_WIDTH
        if (target, bars, ticks) != (self._width, self.show_bars, self.show_ticks):
            self.set_current(None)
            self._width, self.show_bars, self.show_ticks = target, bars, ticks
            self._entries = None
            self._refit()
            self.update()

    @property
    def is_value_mode(self) -> bool:
        return self.colouring is not None and self.colouring.range is not None

    def draws_bars(self) -> bool:
        colouring = self.colouring
        return (self.show_bars and colouring is not None and colouring.range is not None
                and sum(len(chart_bin.row_keys) for chart_bin in colouring.bins) >= HISTOGRAM_MIN_VALUES)

    def _content_height(self) -> float:
        colouring = self.colouring
        if colouring is None:
            return 0.0
        if colouring.range is None:
            return PAD * 2 + TITLE_H + ROW_H * len(self._presence_rows())
        height = PAD * 2 + TITLE_H + STRIP_H
        if self.draws_bars():
            height += BARS_H
        if self.show_ticks:
            height += TICKS_H
        return height

    def _refit(self) -> None:
        self.setFixedSize(QSize(self._width, round(self._content_height())))

    def _presence_rows(self) -> list[tuple[Hashable, str, int]]:
        colouring = self.colouring
        if colouring is None:
            return []
        rows = []
        for presence in MODE_CATEGORIES.get(colouring.mode, ()):
            key = TomogramPresence(presence)
            rows.append((key, PRESENCE_LABELS[presence], colouring.counts.get(key, 0)))
        rows.append((NO_VALUE, "No value", colouring.no_value_count))
        return [row for row in rows if row[2] > 0]

    def title_text(self) -> str:
        colouring = self.colouring
        if colouring is None:
            return ""
        name = mode_name(colouring.mode)
        if colouring.range is None:
            return name
        low, high = colouring.range
        definition = getattr(colouring.mode, "definition", "")
        if low == high:
            return f"{name}: {value_text(definition, low)} {colouring.unit}"
        return f"{name}: {value_text(definition, low)}–{value_text(definition, high)} {colouring.unit}"

    # Entries ------------------------------------------------------------
    def entries(self) -> list[KeyEntry]:
        """The key's entries in reading order: bins low to high, then No value; or the categories."""

        if self._entries is None:
            self._entries = self._build_entries()
        return self._entries

    def _build_entries(self) -> list[KeyEntry]:
        colouring = self.colouring
        if colouring is None:
            return []
        if colouring.range is None:
            entries = []
            for index, (key, label, count) in enumerate(self._presence_rows()):
                rect = QRectF(PAD, PAD + TITLE_H + index * ROW_H, self.width() - PAD * 2, ROW_H)
                entries.append(KeyEntry(rect, colouring.members_of(key), f"{label}: {_positions(count)}"))
            return entries
        low, high = colouring.range
        left, width = PAD, self.width() - PAD * 2
        top = PAD + TITLE_H
        if self.draws_bars():
            band_top, band_h = top, BARS_H + STRIP_H
        else:
            band_top, band_h = top - STRIP_HIT_PAD, STRIP_H + STRIP_HIT_PAD * 2
        definition = getattr(colouring.mode, "definition", "")
        entries = []
        for chart_bin in colouring.bins:
            if high == low:
                x0, x1 = left, left + width
            else:
                x0 = left + (chart_bin.lower - low) / (high - low) * width
                x1 = left + (chart_bin.upper - low) / (high - low) * width
            lower, upper = value_text(definition, chart_bin.lower), value_text(definition, chart_bin.upper)
            span = lower if lower == upper else f"{lower}–{upper}"
            ids = frozenset(chart_bin.row_keys)
            entries.append(KeyEntry(QRectF(x0, band_top, max(1.0, x1 - x0), band_h), ids,
                                    f"{mode_name(colouring.mode)} {span} {colouring.unit}: {_positions(len(ids))}"))
        no_value = colouring.no_value_count
        if no_value:
            entries.append(KeyEntry(self._no_value_rect(f"No value {no_value}"), colouring.members_of(NO_VALUE),
                                    f"No value: {_positions(no_value)}"))
        return entries

    def _no_value_rect(self, text: str) -> QRectF:
        """The title's No value text with its swatch, and room for the outline past the text."""

        text_w = QFontMetricsF(_font(11)).horizontalAdvance(text)
        right = self.width() - PAD
        return QRectF(right - text_w - 16, PAD, text_w + 16 + NO_VALUE_OUTLINE_ROOM, TITLE_H)

    def entry_at(self, point: QPointF) -> int | None:
        for index, entry in enumerate(self.entries()):
            if entry.rect.contains(point):
                return index
        return None

    def set_current(self, index: int | None) -> None:
        """Point at entry ``index`` (or none) and emit its markers."""

        if index == self.current:
            return
        self.current = index
        self.highlighted.emit(self.entries()[index].ids if index is not None else None)
        self._describe()
        self.update()

    def current_text(self) -> str:
        return "" if self.current is None else self.entries()[self.current].text

    def _describe(self) -> None:
        """What a screen reader says: the key's title and the entry the keyboard is on."""

        parts = [self.title_text()]
        if self.current is not None:
            parts.append(self.current_text())
        elif self.entries():
            parts.append("Use Left and Right to highlight the markers of each entry")
        description = ". ".join(part for part in parts if part)
        if description != self.accessibleDescription():
            self.setAccessibleDescription(description)
            if self.hasFocus():
                QAccessible.updateAccessibility(QAccessibleEvent(self, QAccessible.Event.DescriptionChanged))

    # Pointer and keyboard -------------------------------------------------
    def mouseMoveEvent(self, event) -> None:  # noqa: N802 - Qt signature
        self.set_current(self.entry_at(event.position()))
        super().mouseMoveEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802 - Qt signature
        self.set_current(None)
        super().leaveEvent(event)

    def focusInEvent(self, event) -> None:  # noqa: N802 - Qt signature
        self._describe()
        self.update()
        super().focusInEvent(event)

    def focusOutEvent(self, event) -> None:  # noqa: N802 - Qt signature
        self.set_current(None)
        self.update()
        super().focusOutEvent(event)

    def hideEvent(self, event) -> None:  # noqa: N802 - Qt signature
        self.set_current(None)
        super().hideEvent(event)

    def keyPressEvent(self, event) -> None:  # noqa: N802 - Qt signature
        count = len(self.entries())
        key = event.key()
        if key in (Qt.Key.Key_Right, Qt.Key.Key_Left) and count:
            step = 1 if key == Qt.Key.Key_Right else -1
            if self.current is None:
                self.set_current(0 if step > 0 else count - 1)
            else:
                self.set_current(min(max(self.current + step, 0), count - 1))
            event.accept()
            return
        if key == Qt.Key.Key_Escape and self.current is not None:
            self.set_current(None)
            event.accept()
            return
        super().keyPressEvent(event)

    def event(self, event) -> bool:
        if event.type() == QEvent.Type.ToolTip:
            index = self.entry_at(QPointF(event.pos()))
            if index is None:
                QToolTip.hideText()
                event.ignore()
            else:
                QToolTip.showText(event.globalPos(), self.entries()[index].text, self)
            return True
        return super().event(event)

    # Paint --------------------------------------------------------------
    def paintEvent(self, _event) -> None:  # noqa: N802 - Qt signature
        colouring = self.colouring
        if colouring is None:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        try:
            self._paint_plate(painter)
            if colouring.range is None:
                self._paint_presence(painter)
            else:
                self._paint_values(painter, colouring)
            self._paint_current(painter)
        finally:
            painter.end()

    def _paint_current(self, painter: QPainter) -> None:
        if self.current is None:
            return
        rect = self.entries()[self.current].rect.adjusted(-1.5, -1.5, 1.5, 1.5)
        painter.setPen(QPen(QColor(CANVAS_CHROME.text_strong), 1.5))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRoundedRect(rect, 3.0, 3.0)

    def _paint_plate(self, painter: QPainter) -> None:
        border = CANVAS_CHROME.accent if self.hasFocus() else CANVAS_CHROME.border_strong
        painter.setPen(QPen(QColor(border), 1.0))
        painter.setBrush(QColor(CANVAS_CHROME.surface))
        painter.drawRoundedRect(QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5), RADIUS_CARD, RADIUS_CARD)

    def _paint_title(self, painter: QPainter, right_text: str = "") -> None:
        inner = QRectF(PAD, PAD, self.width() - PAD * 2, TITLE_H)
        painter.setFont(_font(11, QFont.Weight.DemiBold))
        painter.setPen(QColor(CANVAS_CHROME.text_strong))
        reserve = QFontMetricsF(_font(11)).horizontalAdvance(right_text) + 24 if right_text else 0
        title = QFontMetricsF(painter.font()).elidedText(self.title_text(), Qt.TextElideMode.ElideRight, inner.width() - reserve)
        painter.drawText(inner, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, title)
        if right_text:
            painter.setFont(_font(11))
            painter.setPen(QColor(CANVAS_CHROME.text_muted))
            text_rect = self._no_value_rect(right_text).adjusted(16, 0, -NO_VALUE_OUTLINE_ROOM, 0)
            painter.drawText(text_rect, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, right_text)
            self._paint_swatch(painter, QPointF(text_rect.left() - 9, inner.center().y()), key_colour(NO_VALUE))

    def _paint_swatch(self, painter: QPainter, centre: QPointF, colour: QColor) -> None:
        painter.setPen(QPen(QColor(MARKER_INK), 2.0))
        painter.setBrush(colour)
        painter.drawEllipse(centre, 4.5, 4.5)

    def _paint_values(self, painter: QPainter, colouring: AtlasColouring) -> None:
        no_value = colouring.no_value_count
        self._paint_title(painter, f"No value {no_value}" if no_value else "")
        assert colouring.range is not None
        low, high = colouring.range
        left, width = PAD, self.width() - PAD * 2
        top = PAD + TITLE_H

        def x_of(value: float) -> float:
            return left + (0.5 if high == low else (value - low) / (high - low)) * width

        if self.draws_bars():
            tallest = max(len(chart_bin.row_keys) for chart_bin in colouring.bins)
            painter.setPen(Qt.PenStyle.NoPen)
            for chart_bin in colouring.bins:
                if high == low:
                    x0, x1 = left, left + width
                else:
                    x0, x1 = x_of(chart_bin.lower), x_of(chart_bin.upper)
                bar_h = max(2.0, (BARS_H - 4) * len(chart_bin.row_keys) / tallest)
                fraction = 0.5 if high == low else ((chart_bin.lower + chart_bin.upper) / 2 - low) / (high - low)
                painter.setBrush(QColor(ramp_colour(fraction)))
                painter.drawRect(QRectF(x0 + 0.5, top + BARS_H - 2 - bar_h, max(1.0, x1 - x0 - 1.0), bar_h))
            top += BARS_H

        strip = QRectF(left, top, width, STRIP_H)
        if high == low:
            painter.setBrush(QColor(ramp_colour(0.5)))
        else:
            gradient = QLinearGradient(strip.topLeft(), strip.topRight())
            for at, colour in COLOUR_RAMP:
                gradient.setColorAt(at, QColor(colour))
            painter.setBrush(gradient)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawRoundedRect(strip, 2.0, 2.0)
        top += STRIP_H

        if not self.show_ticks or high == low:
            return
        definition = getattr(colouring.mode, "definition", "")
        _, ticks = value_axis([low, high])
        shown = [tick for tick in ticks if low - 1e-9 <= tick <= high + 1e-9] or [low, high]
        painter.setFont(_font(10))
        metrics = QFontMetricsF(painter.font())
        last_right = -1e9
        for tick in shown:
            x = x_of(tick)
            painter.setPen(QPen(QColor(CANVAS_CHROME.text_muted), 1.0))
            painter.drawLine(QPointF(x, top), QPointF(x, top + 3))
            text = value_text(definition, tick)
            text_w = metrics.horizontalAdvance(text)
            text_x = min(max(x - text_w / 2, left), left + width - text_w)
            if text_x < last_right + 4:
                continue
            painter.drawText(QRectF(text_x, top + 2, text_w + 1, TICKS_H - 2),
                             Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, text)
            last_right = text_x + text_w

    def _paint_presence(self, painter: QPainter) -> None:
        self._paint_title(painter)
        top = PAD + TITLE_H
        painter.setFont(_font(11))
        for key, label, count in self._presence_rows():
            row = QRectF(PAD, top, self.width() - PAD * 2, ROW_H)
            self._paint_swatch(painter, QPointF(row.left() + 6, row.center().y()), key_colour(key))
            painter.setPen(QColor(CANVAS_CHROME.text))
            painter.drawText(row.adjusted(18, 0, 0, 0), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, label)
            painter.drawText(row.adjusted(0, 0, -NO_VALUE_OUTLINE_ROOM, 0),
                             Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, str(count))
            top += ROW_H



# Contextual keys handled by this module; consumed by the shared Help sheet.
LOCAL_KEYS = (
    LocalBinding('viewer.colour_key.colour-key-left', 'Colour key', InteractionKind.KEYBOARD, ('Left', 'Right'), 'Highlight the markers of the previous or next entry'),
    LocalBinding('viewer.colour_key.colour-key-esc', 'Colour key', InteractionKind.KEYBOARD, ('Esc',), 'Show all the markers in full'),
)
