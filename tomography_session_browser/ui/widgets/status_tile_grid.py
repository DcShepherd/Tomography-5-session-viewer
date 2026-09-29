"""Status band for the dashboard's count cards.

Every card shows one full-width band of the same height (plan C4). While each
item can have a segment at least ``MIN_SEGMENT_PX`` wide, the band is one
segment per item, colour-coded by status, stretched to fill the card;
otherwise it is one proportional bar with the same breakdown. Hover shows an
item's tooltip; clicking a segment emits ``tile_clicked(item_id, destination)``.

It used to draw squares of at most 18 px, so 12 atlases filled a third of
the card at 4K, and a card that fell back to a thinner bar sat beside one
showing squares in the same row.

Used by :class:`tomography_session_browser.ui.widgets.stat_card.StatCard`.
The PDF report draws its own tile grid (``reports.graphics``), unchanged.
"""

from __future__ import annotations

from PySide6.QtCore import QEvent, QPoint, QRect, QSize, Qt, Signal
from PySide6.QtGui import QColor, QHelpEvent, QKeyEvent, QMouseEvent, QPainter, QPen
from PySide6.QtWidgets import QSizePolicy, QToolTip, QWidget

from tomography_session_browser.ui.session_presenter import (
    TILE_STATUS_COMPLETE,
    TILE_STATUS_FAILED,
    TILE_STATUS_MISSING,
    TILE_STATUS_NEUTRAL,
    TILE_STATUS_WARNING,
    StatusTileModel,
)
from tomography_session_browser.ui.theme import current_palette


# Height of the band, segmented or proportional, on every card.
BAND_HEIGHT_PX = 12

# Narrowest segment still drawn per item. Below this the band becomes one
# proportional bar, so the user sees a breakdown instead of specks.
MIN_SEGMENT_PX = 6

# Spacing between segments in pixels.
TILE_GAP = 3


class StatusTileGrid(QWidget):
    """One status band: a segment per item, or a proportional bar.

    ``has_visible_grid()`` is True while the band is segmented, that is
    while every item has a segment at least ``MIN_SEGMENT_PX`` wide.
    """

    tile_clicked = Signal(str, str)  # (item_id, destination_tab_label)

    def __init__(
        self,
        items: list[StatusTileModel],
        *,
        accent: QColor | str | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._items = list(items)
        self._tile_rects: list[tuple[QRect, StatusTileModel]] = []
        self._tile_size = 0
        self._has_visible_grid = bool(self._items)
        self._columns = 1
        self._keyboard_index = 0

        self._fixed_accent = accent
        self._load_colours()

        # The band is fixed-height and top-aligned; the parent card decides
        # how much vertical space it gets. Width expands to fill the card.
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setMinimumHeight(BAND_HEIGHT_PX + 2)
        self.setMouseTracking(True)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setObjectName("statusTileGrid")
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAccessibleName("Status items")
        self.setAccessibleDescription(
            f"{len(self._items)} items. Use the arrow keys to move and Enter to open an item."
        )

    def _load_colours(self) -> None:
        theme = current_palette()
        self._palette_text = QColor(theme.text)
        self._accent = QColor(self._fixed_accent) if self._fixed_accent is not None else QColor(theme.chart_blue)
        self._color_complete = QColor(theme.chart_green)
        self._color_warning = QColor(theme.chart_amber)
        self._color_failed = QColor(theme.chart_red)
        self._color_missing = QColor(theme.chart_red)
        self._color_neutral = QColor(theme.chart_grey)

    def refresh_theme(self, *, accent: QColor | str | None = None) -> None:
        """Recolour for the current theme; ``accent`` replaces a fixed accent."""

        if accent is not None:
            self._fixed_accent = accent
        self._load_colours()
        self.update()

    # -------------------------------------------------------------------- API

    def items(self) -> list[StatusTileModel]:
        return list(self._items)

    def has_visible_grid(self) -> bool:
        """Whether the grid currently has room to render individual tiles.

        ``False`` when there are too many items to render legibly at the
        current width — the parent card should fall back to the grouped
        status summary in that case.
        """

        return self._has_visible_grid

    def tile_size(self) -> int:
        """Width of each segment in pixels at the last paint (0 when proportional)."""

        return self._tile_size

    def tile_rects(self) -> list[tuple[QRect, StatusTileModel]]:
        """Read-only access to the painted segment rectangles. Used by tests."""

        return list(self._tile_rects)

    # ----------------------------------------------------------------- layout

    def segment_width(self, width: int) -> float:
        """Width each item's segment gets across ``width``, gaps included."""

        count = len(self._items)
        if count == 0 or width <= 0:
            return 0.0
        return (width - TILE_GAP * (count - 1)) / count

    def segment_rects(self, width: int) -> list[QRect]:
        """One rectangle per item, together spanning exactly ``width``."""

        segment = self.segment_width(width)
        step = segment + TILE_GAP
        rects = []
        for index in range(len(self._items)):
            left = round(index * step)
            right = round(index * step + segment)
            rects.append(QRect(left, 0, max(1, right - left), BAND_HEIGHT_PX))
        return rects

    # ------------------------------------------------------------------ paint

    def paintEvent(self, event) -> None:  # noqa: N802 — Qt signature
        self._tile_rects.clear()
        if not self._items:
            self._has_visible_grid = False
            return

        if self.segment_width(self.width()) < MIN_SEGMENT_PX:
            # Too many items for a segment each. Previously the widget simply
            # drew nothing, so a card with (say) 85 tilt series showed a
            # conspicuous blank where its neighbours showed a tile grid.
            # A single proportional bar carries the same breakdown.
            self._has_visible_grid = False
            self._tile_size = 0
            self._paint_proportion_bar()
            return

        self._has_visible_grid = True
        self._tile_size = int(self.segment_width(self.width()))
        # Arrow keys move along the one row.
        self._columns = len(self._items)
        radius = min(3, BAND_HEIGHT_PX // 4)

        painter = QPainter(self)
        try:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            painter.setPen(Qt.PenStyle.NoPen)
            for index, (rect, tile) in enumerate(zip(self.segment_rects(self.width()), self._items)):
                self._tile_rects.append((rect, tile))
                painter.setBrush(self._color_for(tile.status))
                painter.drawRoundedRect(rect, radius, radius)
                if self.hasFocus() and index == self._keyboard_index:
                    painter.setBrush(Qt.BrushStyle.NoBrush)
                    painter.setPen(QPen(QColor(current_palette().text_strong), 2))
                    painter.drawRoundedRect(rect.adjusted(1, 1, -1, -1), radius, radius)
                    painter.setPen(Qt.PenStyle.NoPen)
        finally:
            painter.end()

    def _paint_proportion_bar(self) -> None:
        """Draw one stacked bar summarising the same statuses as the tiles.

        Segments keep the tile order so the bar reads left-to-right as
        "mostly complete, then these failures", and every item is
        represented even when there are hundreds of them.
        """

        if not self._items:
            return
        # The same height and position as a segmented band, so the two read
        # as one kind of mark when cards in a row fall on either side.
        height = BAND_HEIGHT_PX
        top = 0
        full_width = self.width()
        if full_width <= 0:
            return

        counts: dict[str, int] = {}
        for tile in self._items:
            counts[tile.status] = counts.get(tile.status, 0) + 1
        ordered = [
            status
            for status in (
                TILE_STATUS_COMPLETE,
                TILE_STATUS_NEUTRAL,
                TILE_STATUS_WARNING,
                TILE_STATUS_FAILED,
                TILE_STATUS_MISSING,
            )
            if counts.get(status)
        ]
        ordered.extend(status for status in counts if status not in ordered)

        painter = QPainter(self)
        try:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            painter.setPen(Qt.PenStyle.NoPen)
            total = len(self._items)
            x = 0
            for index, status in enumerate(ordered):
                remaining = full_width - x
                if index == len(ordered) - 1:
                    segment = remaining
                else:
                    # Never let a non-zero group vanish to nothing.
                    segment = max(2, round(full_width * counts[status] / total))
                    segment = min(segment, max(2, remaining - 2 * (len(ordered) - index - 1)))
                if segment <= 0:
                    continue
                painter.setBrush(self._color_for(status))
                painter.drawRoundedRect(QRect(x, top, segment, height), 3, 3)
                x += segment
        finally:
            painter.end()

    def _color_for(self, status: str) -> QColor:
        if status == TILE_STATUS_COMPLETE:
            return self._color_complete
        if status == TILE_STATUS_WARNING:
            return self._color_warning
        if status == TILE_STATUS_FAILED:
            return self._color_failed
        if status == TILE_STATUS_MISSING:
            return self._color_missing
        if status == TILE_STATUS_NEUTRAL:
            # Neutral items (e.g. "loaded" with no further status) use the
            # card's accent colour so a uniform set of tiles reads as
            # "everything is in its expected state".
            return self._accent
        return self._color_neutral

    # ------------------------------------------------------------- mouse / hover

    def event(self, event: QEvent) -> bool:  # noqa: N802 — Qt signature
        # Qt sends a ToolTip event once the pointer rests. The band has no
        # tooltip of its own, so the event used to travel on to the parent
        # StatCard, whose help text replaced the item's tooltip that
        # mouseMoveEvent had just shown. Answering it here keeps the item's.
        # Off the band it still travels on, so the card's help shows there.
        if event.type() == QEvent.Type.ToolTip and isinstance(event, QHelpEvent):
            hit = self._hit_at(event.pos())
            if hit is not None:
                zone, tile = hit
                QToolTip.showText(event.globalPos(), tile.tooltip, self, zone)
                return True
        return super().event(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:  # noqa: N802 — Qt signature
        hit = self._hit_at(event.position().toPoint())
        if hit is not None:
            zone, tile = hit
            QToolTip.showText(event.globalPosition().toPoint(), tile.tooltip, self, zone)
        else:
            QToolTip.hideText()
        super().mouseMoveEvent(event)

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802 — Qt signature
        if event.button() == Qt.MouseButton.LeftButton:
            tile = self._tile_at(event.position().toPoint())
            if tile is not None:
                self.tile_clicked.emit(tile.id, tile.destination)
                event.accept()
                return
        super().mousePressEvent(event)

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802 — Qt signature
        if not self._items:
            super().keyPressEvent(event)
            return
        key = event.key()
        if key in {Qt.Key.Key_Left, Qt.Key.Key_Right, Qt.Key.Key_Up, Qt.Key.Key_Down}:
            delta = {
                Qt.Key.Key_Left: -1,
                Qt.Key.Key_Right: 1,
                Qt.Key.Key_Up: -max(1, self._columns),
                Qt.Key.Key_Down: max(1, self._columns),
            }[key]
            self._keyboard_index = max(
                0,
                min(len(self._items) - 1, self._keyboard_index + delta),
            )
            tile = self._items[self._keyboard_index]
            self.setAccessibleDescription(
                f"{tile.tooltip}. Press Enter to open this item."
            )
            self.update()
            event.accept()
            return
        if key in {Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Space}:
            tile = self._items[self._keyboard_index]
            self.tile_clicked.emit(tile.id, tile.destination)
            event.accept()
            return
        super().keyPressEvent(event)

    def _tile_at(self, point: QPoint) -> StatusTileModel | None:
        hit = self._hit_at(point)
        return hit[1] if hit is not None else None

    def _hit_at(self, point: QPoint) -> tuple[QRect, StatusTileModel] | None:
        for zone, tile in self.hit_zones():
            if zone.contains(point):
                return zone, tile
        return None

    def hit_zones(self) -> list[tuple[QRect, StatusTileModel]]:
        """Each segment's hover and click area: its column of the band.

        The gaps between segments are split between their neighbours, so the
        pointer is always over some item while it is on the band. Hovering
        used to drop the tooltip at every 3 px gap. Below the band belongs to
        no item, and the card's own help shows there.
        """

        zones: list[tuple[QRect, StatusTileModel]] = []
        left = 0
        for index, (rect, tile) in enumerate(self._tile_rects):
            if index + 1 < len(self._tile_rects):
                right = (rect.right() + self._tile_rects[index + 1][0].left()) // 2
            else:
                right = max(rect.right(), self.width() - 1)
            zones.append((QRect(QPoint(left, 0), QPoint(right, BAND_HEIGHT_PX - 1)), tile))
            left = right + 1
        return zones

    # --------------------------------------------------------------- size hint

    def sizeHint(self) -> QSize:  # noqa: N802 — Qt signature
        # A reasonable default; the parent layout always overrides this with
        # its own width via the setMinimumHeight + Expanding × Fixed policy.
        return QSize(120, BAND_HEIGHT_PX + 2)
