"""The one card that leads both summary pages (plan P6, D3, D4).

A grid: the main column holds the heading row, the headline row and the
page's body rows. An optional aside (Tomograms) takes a column of its own and
puts its heading and its number in the same grid rows as the main heading and
headline, so the two numbers share one baseline because they are the same
style in the same row, not because of an offset.

On a narrow card the aside becomes a strip under the body. The aside's link
area is laid behind its pieces from the grid's cells, so a click anywhere on
the aside opens it and its border meets the card's edges.
"""
from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol

from PySide6.QtCore import QEvent, QRect, Qt
from PySide6.QtWidgets import QGridLayout, QHBoxLayout, QWidget

from tomography_session_browser.presentation.design_tokens import SPACE_L, SPACE_M, SPACE_XL
from tomography_session_browser.ui.dashboard.common import CardBox
from tomography_session_browser.ui.dashboard.route_link import RouteLink
from tomography_session_browser.ui.widgets.dashboard_card import DASHBOARD_CARD_MARGINS

#: The aside's column, as on the Main board.
ASIDE_WIDTH_PX = 360
#: The aside is beside the body only when the card has room for both: the
#: body's own minimum width and the aside, and never below this width.
#: Otherwise it is a strip under the body. It comes back beside the body only
#: past that width plus the margin, so a width near the edge does not flip it
#: on every resize.
SUMMARY_ASIDE_MIN_PX = 1000
_HYSTERESIS_PX = 40

_MAIN, _GAP, _ASIDE = 0, 1, 2


class CardAside(Protocol):
    """What a summary card needs from its aside: its area and its pieces."""

    link: RouteLink
    heading: QWidget
    number: QWidget
    foot: QWidget

    def set_strip(self, strip: bool) -> None: ...


class SummaryCard(CardBox):
    """Heading, headline and body rows, with an optional aside beside or under them."""

    def __init__(self, heading: QWidget, headline: QWidget | None, body: Sequence[QWidget],
                 aside: CardAside | None = None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("dashboardCard")
        self.heading = heading
        self.headline = headline
        self.aside = aside
        self._main = [widget for widget in (heading, headline, *body) if widget is not None]
        self._strip: bool | None = None
        self._rows = 2 + max(1, len(body))
        grid = self._grid = QGridLayout(self)
        grid.setContentsMargins(*DASHBOARD_CARD_MARGINS)
        grid.setHorizontalSpacing(0)
        grid.setVerticalSpacing(SPACE_M)
        grid.setColumnStretch(_MAIN, 1)
        grid.addWidget(heading, 0, _MAIN)
        if headline is not None:
            grid.addWidget(headline, 1, _MAIN)
        for row, widget in enumerate(body, start=2):
            grid.addWidget(widget, row, _MAIN)
        if aside is not None:
            aside.link.setParent(self)
            aside.link.lower()  # Behind the pieces, which let the pointer through.
            for piece in (aside.heading, aside.number, aside.foot):
                piece.setParent(self)
                piece.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
            self._strip_row = QHBoxLayout()
            self._strip_row.setContentsMargins(0, SPACE_L, 0, 0)
            self._strip_row.setSpacing(SPACE_M)
            self._place_aside(strip=False)

    @property
    def strip(self) -> bool:
        return bool(self._strip)

    def _place_aside(self, *, strip: bool) -> None:
        aside = self.aside
        if aside is None or strip == self._strip:
            return
        self._strip = strip
        grid = self._grid
        pieces = (aside.heading, aside.number, aside.foot)
        for piece in pieces:
            grid.removeWidget(piece)
            self._strip_row.removeWidget(piece)
        grid.removeItem(self._strip_row)
        aside.set_strip(strip)
        if strip:
            grid.setColumnMinimumWidth(_GAP, 0)
            grid.setColumnMinimumWidth(_ASIDE, 0)
            for piece in pieces:
                self._strip_row.addWidget(piece, 1 if piece is aside.foot else 0)
            grid.addLayout(self._strip_row, self._rows, _MAIN, 1, 3)
        else:
            grid.setColumnMinimumWidth(_GAP, SPACE_XL + SPACE_L)
            grid.setColumnMinimumWidth(_ASIDE, ASIDE_WIDTH_PX - SPACE_L - DASHBOARD_CARD_MARGINS[2])
            grid.addWidget(aside.heading, 0, _ASIDE, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
            grid.addWidget(aside.number, 1, _ASIDE)
            grid.addWidget(aside.foot, 2, _ASIDE, self._rows - 2, 1)
        grid.invalidate()

    def event(self, event: QEvent) -> bool:  # noqa: D102 - Qt signature
        handled = super().event(event)
        if self.aside is not None and event.type() in (QEvent.Type.Resize, QEvent.Type.LayoutRequest):
            width, needed = self.width(), self.side_width()
            if not self._strip and width < needed:
                self._place_aside(strip=True)
                self._grid.activate()
            elif self._strip and width >= needed + _HYSTERESIS_PX:
                self._place_aside(strip=False)
                self._grid.activate()
            self._lay_link()
        return handled

    def side_width(self) -> int:
        """The card width that holds the body at its minimum and the aside beside it."""

        body = max(widget.minimumSizeHint().width() for widget in self._main)
        return max(SUMMARY_ASIDE_MIN_PX, DASHBOARD_CARD_MARGINS[0] + body + SPACE_XL + ASIDE_WIDTH_PX)

    def _lay_link(self) -> None:
        """The aside's area: from its border to the card's edges, behind the pieces."""

        assert self.aside is not None
        inner = self.rect().adjusted(1, 1, -1, -1)  # Inside the card's own border.
        if self._strip:
            top = self._strip_row.geometry().top()
            area = QRect(inner.left(), top, inner.width(), inner.bottom() - top + 1)
        else:
            left = self._grid.cellRect(0, _ASIDE).left() - SPACE_L
            area = QRect(left, inner.top(), inner.right() - left + 1, inner.height())
        if area != self.aside.link.geometry():
            self.aside.link.setGeometry(area)


def heading_row(title: QWidget, note: QWidget | None = None, *, note_stretch: int = 0) -> QWidget:
    """The card's heading row: its title on the left and its note on the right."""

    wrap = QWidget()
    row = QHBoxLayout(wrap)
    row.setContentsMargins(0, 0, 0, 0)
    row.setSpacing(SPACE_M)
    row.addWidget(title)
    row.addStretch(1)
    if note is not None:
        row.addWidget(note, note_stretch)
    return wrap
