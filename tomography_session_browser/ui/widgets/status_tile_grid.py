"""Adaptive status-tile grid for the dashboard's count cards.

Renders one rounded square per item, colour-coded by status. The widget
chooses its own tile size based on the available width and the item count;
when individual tiles would shrink below ``MIN_TILE_PX`` the grid hides
itself and a grouped status summary is surfaced instead. Hover shows the
item tooltip; clicking emits ``tile_clicked(item_id, destination)``.

Used by :class:`tomography_session_browser.ui.widgets.stat_card.StatCard`.
"""

from __future__ import annotations

from PySide6.QtCore import QPoint, QRect, QSize, Qt, Signal
from PySide6.QtGui import QColor, QMouseEvent, QPainter
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


# Smallest tile size (in CSS-pixel units) we will still render. Below this
# the grid switches to the grouped-summary fallback so the user sees a
# meaningful breakdown instead of unreadable specks.
MIN_TILE_PX = 8

# Largest tile we render even with very few items — keeps the grid visually
# proportional to the rest of the card.
MAX_TILE_PX = 18

# Spacing between tiles in pixels.
TILE_GAP = 3


class StatusTileGrid(QWidget):
    """Waffle-style grid of status-coloured tiles.

    Layout strategy: pick the largest tile size that allows ``items`` to fit
    within the widget's current width using either one row (if they all fit)
    or as many rows as needed. If the resulting size drops below
    ``MIN_TILE_PX``, the widget reports ``has_visible_grid() == False`` so
    the parent card can swap in a grouped summary instead.
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
        self._tile_size = MAX_TILE_PX
        self._has_visible_grid = bool(self._items)

        theme = current_palette()
        self._palette_text = QColor(theme.text)
        self._accent = QColor(accent) if accent is not None else QColor(theme.chart_blue)
        self._color_complete = QColor(theme.chart_green)
        self._color_warning = QColor(theme.chart_amber)
        self._color_failed = QColor(theme.chart_red)
        self._color_missing = QColor(theme.chart_red)
        self._color_neutral = QColor(theme.chart_grey)

        # The grid is fixed-height; the parent card decides how much vertical
        # space it gets. Width expands to fill the card.
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setMinimumHeight(MAX_TILE_PX + 2)
        self.setMouseTracking(True)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)

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
        """Last-computed tile edge length in pixels (after the most recent paint)."""

        return self._tile_size

    def tile_rects(self) -> list[tuple[QRect, StatusTileModel]]:
        """Read-only access to the painted tile rectangles. Used by tests."""

        return list(self._tile_rects)

    # ----------------------------------------------------------------- layout

    def _compute_layout(self, width: int, height: int) -> tuple[int, int, int]:
        """Return ``(tile_size, columns, rows)`` for the current geometry.

        Honors ``MAX_TILE_PX`` as a ceiling so a single item doesn't render
        as a giant square, and clamps to ``MIN_TILE_PX`` for the
        readability check.
        """

        count = len(self._items)
        if count == 0 or width <= 0 or height <= 0:
            return 0, 0, 0

        # Try larger sizes first; pick the largest that fits both axes.
        for size in range(MAX_TILE_PX, MIN_TILE_PX - 1, -1):
            cols = max(1, (width + TILE_GAP) // (size + TILE_GAP))
            rows = (count + cols - 1) // cols
            needed_height = rows * (size + TILE_GAP) - TILE_GAP
            if needed_height <= height:
                return size, int(cols), int(rows)

        # Even at MIN_TILE_PX we can't fit — caller will hide the grid.
        return 0, 0, 0

    # ------------------------------------------------------------------ paint

    def paintEvent(self, event) -> None:  # noqa: N802 — Qt signature
        self._tile_rects.clear()
        if not self._items:
            self._has_visible_grid = False
            return

        size, cols, rows = self._compute_layout(self.width(), self.height())
        if size < MIN_TILE_PX or cols == 0:
            # Not enough room — parent renders the grouped summary instead.
            self._has_visible_grid = False
            self._tile_size = 0
            return

        self._has_visible_grid = True
        self._tile_size = size

        painter = QPainter(self)
        try:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            painter.setPen(Qt.PenStyle.NoPen)
            for index, tile in enumerate(self._items):
                col = index % cols
                row = index // cols
                x = col * (size + TILE_GAP)
                y = row * (size + TILE_GAP)
                rect = QRect(x, y, size, size)
                self._tile_rects.append((rect, tile))
                painter.setBrush(self._color_for(tile.status))
                painter.drawRoundedRect(rect, max(2, size // 4), max(2, size // 4))
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

    def mouseMoveEvent(self, event: QMouseEvent) -> None:  # noqa: N802 — Qt signature
        tile = self._tile_at(event.position().toPoint())
        if tile is not None:
            QToolTip.showText(event.globalPosition().toPoint(), tile.tooltip, self)
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

    def _tile_at(self, point: QPoint) -> StatusTileModel | None:
        for rect, tile in self._tile_rects:
            if rect.contains(point):
                return tile
        return None

    # --------------------------------------------------------------- size hint

    def sizeHint(self) -> QSize:  # noqa: N802 — Qt signature
        # A reasonable default; the parent layout always overrides this with
        # its own width via the setMinimumHeight + Expanding × Fixed policy.
        return QSize(120, MAX_TILE_PX + 2)
