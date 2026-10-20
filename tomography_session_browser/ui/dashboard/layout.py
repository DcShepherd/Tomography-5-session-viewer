"""Layout components for the dashboard."""
from __future__ import annotations
import math
from PySide6.QtCore import QPoint, QRect, QSize, Qt
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import QBoxLayout, QGridLayout, QLayout, QLayoutItem, QWidget
from tomography_session_browser.presentation.design_tokens import SPACE_L, SPACE_M
from tomography_session_browser.ui.fit_first import balanced_rows


class DashboardPage(QWidget):
    """The dashboard's scrolling page, opaque (plan F5.5).

    Opaque, Qt scrolls the page by moving pixels it already has and paints
    only the strip coming into view; otherwise every step of an eased wheel
    scroll repainted every card and chart (39 ms frames natively, against
    16.5 ms). It paints its own background, because the style sheet's pass
    over a scroll area's page clears ``autoFillBackground``.
    """

    def __init__(self) -> None:
        super().__init__()
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent)

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt signature
        from tomography_session_browser.ui.theme import current_palette

        QPainter(self).fillRect(event.rect(), QColor(current_palette().background))


class SideBySideRow(QWidget):
    """Two cards in a row when the page is very wide, stacked otherwise (D3).

    Switches the layout's direction rather than rebuilding it, like the
    context header, so the cards (and their plots) are never recreated.
    """

    def __init__(self, cards: list[QWidget], *, min_width: int, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._min_width = min_width
        self._layout = QBoxLayout(QBoxLayout.Direction.TopToBottom, self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        # Stacked, they are sections on a page; side by side, cards in a row.
        self._layout.setSpacing(SPACE_L)
        for card in cards:
            self._layout.addWidget(card, stretch=1)
        self._side_by_side = False

    def is_side_by_side(self) -> bool:
        return self._side_by_side

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt signature
        super().resizeEvent(event)
        side_by_side = event.size().width() >= self._min_width
        if side_by_side != self._side_by_side:
            self._side_by_side = side_by_side
            self._layout.setDirection(
                QBoxLayout.Direction.LeftToRight if side_by_side else QBoxLayout.Direction.TopToBottom
            )
            self._layout.setSpacing(SPACE_M if side_by_side else SPACE_L)


class ResponsiveCardGrid(QWidget):
    """Reflow dashboard cards into balanced rows without horizontal scrolling.

    Rows are split as evenly as the column count allows, and a shorter row
    stretches to the full width (plan C4): five cards in three columns used
    to leave an empty sixth slot. Each row spans the same grid, so every row
    fills the width: with rows of 3 and 2 the grid has 6 columns, each card
    of the first row spans 2 and each of the second spans 3.
    """

    #: ``(width, columns)``: the columns from the widest width reached down.
    BREAKPOINTS: tuple[tuple[int, int], ...] = ((1120, 5), (680, 3), (440, 2))

    def __init__(
        self,
        cards: list[QWidget],
        parent: QWidget | None = None,
        *,
        breakpoints: tuple[tuple[int, int], ...] | None = None,
    ) -> None:
        super().__init__(parent)
        self._cards = list(cards)
        self._breakpoints = breakpoints or self.BREAKPOINTS
        self._columns = 0
        self._layout: QGridLayout | None = None
        self._reflow(self._column_count_for_width(self.width()))

    def _column_count_for_width(self, width: int) -> int:
        return next((columns for reached, columns in self._breakpoints if width >= reached), 1)

    def _reflow(self, columns: int) -> None:
        rows = balanced_rows(len(self._cards), columns) or [0]
        if rows[0] == self._columns and self._layout is not None:
            return
        # A fresh grid each time: stale columns from a wider arrangement
        # would otherwise keep their spacing. Empty the old grid first, so
        # moving it onto a throwaway widget (which detaches it from this one)
        # does not take the cards with it.
        if self._layout is not None:
            while self._layout.count():
                self._layout.takeAt(0)
            QWidget().setLayout(self._layout)
        layout = QGridLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setHorizontalSpacing(SPACE_M)
        layout.setVerticalSpacing(SPACE_M)
        span_total = math.lcm(*rows) if rows[0] else 1
        cards = iter(self._cards)
        for row, size in enumerate(rows):
            span = span_total // max(size, 1)
            for position in range(size):
                layout.addWidget(next(cards), row, position * span, 1, span)
        for column in range(span_total):
            layout.setColumnStretch(column, 1)
        self._layout = layout
        self._columns = rows[0]

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt signature
        super().resizeEvent(event)
        self._reflow(self._column_count_for_width(event.size().width()))

    def column_count(self) -> int:
        """Return the active column count for regression tests."""

        return self._columns


class FlowLayout(QLayout):
    """Left-to-right layout that wraps onto a new line when it runs out of width.

    Qt ships no flow layout. A ``QHBoxLayout`` of fixed-size chips reports
    the sum of their widths as its minimum, so one row of five status chips
    was enough to push the whole dashboard wider than its scroll viewport.

    An item wider than a whole line is given the line's width rather than
    overflowing it, so an eliding label inside it (a long path) elides.
    ``line_spacing`` separates wrapped lines; it defaults to ``spacing``.
    """

    def __init__(self, *, spacing: int = 8, line_spacing: int | None = None) -> None:
        super().__init__()
        self._items: list[QLayoutItem] = []
        self.setContentsMargins(0, 0, 0, 0)
        self._gap = spacing
        self._line_gap = spacing if line_spacing is None else line_spacing

    def addItem(self, item: QLayoutItem) -> None:  # noqa: N802 - Qt API
        self._items.append(item)

    def count(self) -> int:
        return len(self._items)

    def itemAt(self, index: int) -> QLayoutItem | None:  # noqa: N802 - Qt API
        return self._items[index] if 0 <= index < len(self._items) else None

    def takeAt(self, index: int) -> QLayoutItem | None:  # noqa: N802 - Qt API
        if 0 <= index < len(self._items):
            return self._items.pop(index)
        return None

    def expandingDirections(self) -> Qt.Orientations:  # noqa: N802 - Qt API
        return Qt.Orientations(Qt.Orientation(0))

    def hasHeightForWidth(self) -> bool:  # noqa: N802 - Qt API
        return True

    def heightForWidth(self, width: int) -> int:  # noqa: N802 - Qt API
        return self._layout(QRect(0, 0, width, 0), apply=False)

    def setGeometry(self, rect: QRect) -> None:  # noqa: N802 - Qt API
        super().setGeometry(rect)
        self._layout(rect, apply=True)

    def sizeHint(self) -> QSize:  # noqa: N802 - Qt API
        return self.minimumSize()

    def minimumSize(self) -> QSize:  # noqa: N802 - Qt API
        size = QSize()
        for item in self._items:
            size = size.expandedTo(item.minimumSize())
        margins = self.contentsMargins()
        return size + QSize(margins.left() + margins.right(), margins.top() + margins.bottom())

    def _layout(self, rect: QRect, *, apply: bool) -> int:
        margins = self.contentsMargins()
        line_left = rect.x() + margins.left()
        x = line_left
        y = rect.y() + margins.top()
        right = rect.right() - margins.right()
        line_height = 0
        for item in self._items:
            hint = item.sizeHint()
            width = max(0, min(hint.width(), right - line_left + 1))
            if line_height and x + width - 1 > right:
                x = line_left
                y += line_height + self._line_gap
                line_height = 0
            if apply:
                item.setGeometry(QRect(QPoint(x, y), QSize(width, hint.height())))
            x += width + self._gap
            line_height = max(line_height, hint.height())
        return y + line_height - rect.y() + margins.bottom()

