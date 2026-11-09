"""A passive film mark just left of the capture column: a movie was saved for this tomogram (B5).

Only a mark: no click, no cursor change, no number. Its place is kept only
while some listed row has a movie, as the camera's place is kept only while
some row has captures. Kept apart from ``CaptureColumn``, which is a control
that opens capture history.
"""
from __future__ import annotations

from collections.abc import Mapping

from PySide6.QtCore import QEvent, QObject, QRect, Qt
from PySide6.QtWidgets import QToolTip

from tomography_session_browser.presentation.design_tokens import SPACE_M, SPACE_S
from tomography_session_browser.presentation.tomogram_movie_history import MovieMark, movie_tooltip
from tomography_session_browser.ui.icons import themed_icon

ICON = 14
SLOT = 18


class MovieMarks(QObject):
    def __init__(self, listing) -> None:
        super().__init__(listing)
        self.listing = listing
        self.marks: Mapping[str, MovieMark] = {}
        self.width = 0
        self.icon = themed_icon("film", size=ICON)
        listing.tree.viewport().installEventFilter(self)

    def set_marks(self, marks: Mapping[str, MovieMark]) -> None:
        if marks == self.marks:
            return
        self.marks = marks
        self.reflow()

    def reflow(self) -> None:
        self.width = SLOT if any(key in self.marks for key in self.listing.ordered_keys) else 0
        self.listing.tree.viewport().update()

    def rect(self, row_rect: QRect) -> QRect:
        """The slot, flush left of the capture column, or of the row's right margin when no row has captures."""
        captures = self.listing.capture_column
        right = captures.rect(row_rect).left() - SPACE_S if captures.width else row_rect.right() - SPACE_M
        return QRect(right - self.width, row_rect.center().y() - ICON // 2, self.width, ICON)

    def paint(self, painter, row_rect: QRect, key: str) -> None:
        if self.width and key in self.marks:
            slot = self.rect(row_rect)
            self.icon.paint(painter, QRect(slot.left() + (SLOT - ICON) // 2, slot.top(), ICON, ICON))

    def key_at(self, point) -> str | None:
        item = self.listing.tree.itemAt(point)
        key = item.data(0, Qt.ItemDataRole.UserRole) if item is not None else None
        if isinstance(key, str) and key in self.marks and self.width:
            if self.rect(self.listing.tree.visualItemRect(item)).adjusted(0, -3, 0, 3).contains(point):
                return key
        return None

    def eventFilter(self, watched, event) -> bool:  # noqa: N802
        if event.type() == QEvent.Type.ToolTip:
            key = self.key_at(event.pos())
            if key is not None:
                QToolTip.showText(event.globalPos(), movie_tooltip(self.marks[key]), watched)
                return True
        return super().eventFilter(watched, event)
