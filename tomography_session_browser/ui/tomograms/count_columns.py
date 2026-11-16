"""The count slots at the right of each review row: film (saved movies) and camera (captures).

One owner does the geometry, the hit test, hover, press, the cursor and the
tooltip for both slots, so the hand cursor never fights a second event filter.
Both slots have one width, so their numbers line up. A slot keeps its place only
while some listed row has something to count in it.
"""
from __future__ import annotations

from collections.abc import Callable, Iterable

from PySide6.QtCore import QEvent, QObject, QRect, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QStyle, QToolTip

from tomography_session_browser.presentation.design_tokens import RADIUS_CONTROL, SPACE_S, TYPE_CAPTION
from tomography_session_browser.presentation.tomogram_movie_history import movie_count_tooltip
from tomography_session_browser.ui.icons import themed_icon
from tomography_session_browser.ui.theme import current_palette

ICON = 14


def contains(rect, point) -> bool:
    """Inside the slot's rounded shape, not only its bounding box."""
    shape = QPainterPath()
    shape.addRoundedRect(QRectF(rect), RADIUS_CONTROL, RADIUS_CONTROL)
    return shape.contains(point)


class CountSlot(QObject):
    """One kind of count: its icon, counts, tooltip and ``requested`` signal."""
    requested = Signal(str)

    def __init__(self, columns: CountColumns, icon: str, tooltip: Callable[[CountSlot, str], str]) -> None:
        super().__init__(columns)
        self.columns = columns
        self.icon_name = icon
        self.icon = themed_icon(icon, size=ICON)
        self.tooltip = tooltip
        self.counts = {}
        self.failures = frozenset()
        self.refreshing = False
        self.available = True
        self.error = ""
        self.open_key: str | None = None

    def set_counts(self, counts, failures=(), *, available=True, refreshing=False, error="") -> None:
        failures = frozenset(failures)
        if counts is self.counts and (failures, available, refreshing, error) == (self.failures, self.available, self.refreshing, self.error):
            return
        self.counts, self.failures, self.refreshing, self.error = counts, failures, refreshing, error
        self.available = available
        self.columns.reflow()

    def marked(self, key: str) -> bool:
        return bool(self.counts.get(key, 0)) or key in self.failures

    def text(self, key: str) -> str:
        return str(self.counts.get(key, 0)) if self.available else "…"

    def shown(self) -> bool:
        return any(self.marked(key) for key in self.columns.listing.ordered_keys)

    @property
    def width(self) -> int:
        return self.columns.width if self in self.columns.visible else 0

    def rect(self, row_rect) -> QRect:
        return self.columns.slot_rect(self, row_rect)

    @property
    def hover_key(self) -> str | None:
        slot, key = self.columns.hover
        return key if slot is self else None

    @hover_key.setter
    def hover_key(self, key: str | None) -> None:
        self.columns.hover = (self if key is not None else None, key)

    @property
    def pressed(self) -> bool:
        return self.columns.pressed

    @pressed.setter
    def pressed(self, value: bool) -> None:
        self.columns.pressed = value

    @staticmethod
    def contains(rect, point) -> bool:
        return contains(rect, point)

    def set_open(self, key: str | None) -> None:
        self.open_key = key
        if key is None:
            self.columns.pressed = False
        self.columns.listing.tree.viewport().update()

    def key_at(self, point) -> str | None:
        hit = self.columns.hit(point)
        return hit[1] if hit is not None and hit[0] is self else None

    def refresh_theme(self) -> None:
        self.icon = themed_icon(self.icon_name, size=ICON)

    def paint(self, painter, option, key: str) -> None:
        if not self.width or not self.marked(key):
            return
        rect = self.rect(option.rect)
        palette = current_palette()
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        hovered = key == self.hover_key
        active = key == self.open_key or (self.columns.pressed and hovered)
        if active or hovered:
            fill = QColor(palette.text_strong)
            fill.setAlphaF(.16 if active else .09)
            painter.setBrush(fill)
            painter.setPen(QPen(QColor(palette.accent), 1) if active else Qt.PenStyle.NoPen)
            painter.drawRoundedRect(rect.adjusted(0, 0, -1, -1), RADIUS_CONTROL, RADIUS_CONTROL)
        self.icon.paint(painter, QRect(rect.left() + 6, rect.center().y() - 7, ICON, ICON))
        painter.setPen(QColor(palette.text_strong if option.state & QStyle.StateFlag.State_Selected else palette.text_muted))
        font = QFont(option.font)
        font.setPointSizeF(TYPE_CAPTION)
        font.setWeight(QFont.Weight.Normal)
        painter.setFont(font)
        painter.drawText(rect.adjusted(24, 0, -6, 0), Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight, self.text(key))
        if key in self.failures:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(palette.chart_amber))
            painter.drawEllipse(rect.left() + 16, rect.center().y() - 8, 5, 5)
        painter.restore()


class CountColumns(QObject):
    """Owns the slots, right to left: the camera at the row's right end, the film just left of it."""

    def __init__(self, listing, slots: Iterable[tuple[str, str, Callable[[CountSlot, str], str]]]) -> None:
        super().__init__(listing)
        self.listing = listing
        #: Right to left.
        self.slots = tuple(CountSlot(self, icon, tooltip) for _name, icon, tooltip in slots)
        self.by_name = dict(zip((name for name, _icon, _tooltip in slots), self.slots))
        self.visible: tuple[CountSlot, ...] = ()
        self.width = 0
        self.hover: tuple[CountSlot | None, str | None] = (None, None)
        self.pressed = False
        listing.tree.viewport().installEventFilter(self)

    def reflow(self) -> None:
        self.visible = tuple(slot for slot in self.slots if slot.shown())
        font = QFont(self.listing.tree.font())
        font.setPointSizeF(TYPE_CAPTION)
        metrics = QFontMetrics(font)
        texts = [slot.text(key) for slot in self.visible for key in self.listing.ordered_keys if slot.marked(key)]
        self.width = 36 + max(metrics.horizontalAdvance(text) for text in texts) if texts else 0
        self.listing.tree.viewport().update()

    def slot_rect(self, slot: CountSlot, row_rect) -> QRect:
        height = min(20, row_rect.height())
        right = row_rect.right() + 1 - 8
        for each in self.visible:
            if each is slot:
                return QRect(right - self.width, row_rect.center().y() - height // 2, self.width, height)
            right -= self.width + SPACE_S
        return QRect(right, row_rect.center().y() - height // 2, 0, height)

    def left(self, row_rect) -> int:
        """The left edge of the leftmost shown slot, for the row text beside it."""
        return self.slot_rect(self.visible[-1], row_rect).left() if self.visible else row_rect.right() + 1

    def paint(self, painter, option, key: str) -> None:
        for slot in self.visible:
            slot.paint(painter, option, key)

    def hit(self, point) -> tuple[CountSlot, str] | None:
        item = self.listing.tree.itemAt(point)
        key = item.data(0, Qt.ItemDataRole.UserRole) if item is not None else None
        if not isinstance(key, str) or not self.width:
            return None
        row = self.listing.tree.visualItemRect(item)
        for slot in self.visible:
            if slot.marked(key) and contains(slot.rect(row), point):
                return slot, key
        return None

    def eventFilter(self, watched, event) -> bool:  # noqa: N802
        kind = event.type()
        if kind == QEvent.Type.FontChange:
            self.reflow()
        elif kind in (QEvent.Type.MouseMove, QEvent.Type.MouseButtonPress):
            hit = self.hit(event.position().toPoint())
            if kind == QEvent.Type.MouseButtonPress:
                self.pressed = False
                if event.button() == Qt.MouseButton.LeftButton and hit is not None:
                    self.hover, self.pressed = hit, True
                    watched.update()
                    hit[0].requested.emit(hit[1])
                    return True
            hover = hit or (None, None)
            if hover != self.hover:
                self.hover = hover
                watched.setCursor(Qt.CursorShape.PointingHandCursor if hit else Qt.CursorShape.ArrowCursor)
                watched.update()
        elif kind == QEvent.Type.MouseButtonRelease and self.pressed:
            self.pressed = False
            watched.update()
            return True
        elif kind == QEvent.Type.ToolTip:
            hit = self.hit(event.pos())
            if hit is not None:
                QToolTip.showText(event.globalPos(), hit[0].tooltip(*hit), watched)
                return True
        elif kind == QEvent.Type.Leave:
            self.hover = (None, None)
            watched.unsetCursor()
            watched.update()
        return super().eventFilter(watched, event)


def capture_tooltip(slot: CountSlot, key: str) -> str:
    message = (f"Show the {slot.counts.get(key, 0)} captures of this tomogram." if slot.available
               else "Capture count unavailable. Open capture history.")
    if key in slot.failures:
        message += " Snapshots need recovery."
    if slot.refreshing:
        message += " Refreshing; this is the last known count." if slot.available else " Loading capture history."
    if slot.error:
        message += " Refresh unavailable. Open All captures and use Refresh."
    return message


def film_tooltip(slot: CountSlot, key: str) -> str:
    return movie_count_tooltip(slot.counts.get(key, 0), available=slot.available, refreshing=slot.refreshing, error=slot.error)
