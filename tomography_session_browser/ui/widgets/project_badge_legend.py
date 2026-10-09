"""The project panel's badge key, drawn with the tree's own badges.

It used to be one line of text, "LS linked · AT atlas-only · DC data ·
! unresolved", that took two lines of a narrow panel whatever was loaded
(harmonisation E1). It now shows the badges the tree actually holds, each as
the same tag the tree draws, beside its word. Display only: the letters and
their meanings are ``project_tree.PROJECT_BADGE_MEANINGS``.
"""

from __future__ import annotations

from collections.abc import Iterable

from PySide6.QtCore import QRectF, QSize, Qt
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import QSizePolicy, QWidget

from tomography_session_browser.ui.list_decorations import (
    PROJECT_BADGE_SIZE,
    PROJECT_BADGE_TONES,
    paint_project_badge,
    project_badge_rect,
)
from tomography_session_browser.presentation.design_tokens import SPACE_S, SPACE_XS
from tomography_session_browser.ui.theme import current_palette

#: The word beside each badge. Short, because a key needs only enough to
#: recall the meaning; the tooltip carries the full sentence.
LEGEND_WORDS: dict[str, str] = {
    "LS": "linked",
    "AT": "atlas",
    "DC": "data collection",
    "!": "unresolved",
}


class ProjectBadgeLegend(QWidget):
    """A wrapping row of ``[badge] word`` entries, in the tree's badge order."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("projectBadgeLegend")
        self._badges: tuple[str, ...] = ()
        policy = QSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        policy.setHeightForWidth(True)
        self.setSizePolicy(policy)

    def badges(self) -> tuple[str, ...]:
        return self._badges

    def set_badges(self, badges: Iterable[str]) -> None:
        present = set(badges)
        ordered = tuple(badge for badge in PROJECT_BADGE_TONES if badge in present)
        if ordered == self._badges:
            return
        self._badges = ordered
        self.setAccessibleName(
            "Project badges: " + ", ".join(f"{badge} {LEGEND_WORDS[badge]}" for badge in ordered)
            if ordered
            else "Project badges"
        )
        self.updateGeometry()
        self.update()

    # -- layout ---------------------------------------------------------------

    def _entries(self, width: int) -> list[tuple[str, QRectF, QRectF]]:
        """(badge, badge rect, word rect) per entry, wrapped to ``width``."""

        metrics = self.fontMetrics()
        badge_width, badge_height = PROJECT_BADGE_SIZE
        line_height = max(badge_height, metrics.height())
        x = y = 0.0
        entries = []
        for badge in self._badges:
            word_width = metrics.horizontalAdvance(LEGEND_WORDS[badge])
            entry_width = badge_width + SPACE_XS + word_width
            if x > 0 and x + entry_width > width:
                x = 0.0
                y += line_height + SPACE_XS
            cell = QRectF(x, y, badge_width, line_height)
            word = QRectF(x + badge_width + SPACE_XS, y, word_width + 1, line_height)
            entries.append((badge, project_badge_rect(cell, align_right=False), word))
            x += entry_width + SPACE_S + SPACE_XS
        return entries

    def hasHeightForWidth(self) -> bool:  # noqa: N802 - Qt API
        return True

    def heightForWidth(self, width: int) -> int:  # noqa: N802 - Qt API
        entries = self._entries(width)
        if not entries:
            return 0
        return int(max(word.bottom() for _badge, _rect, word in entries)) + 1

    def sizeHint(self) -> QSize:  # noqa: N802 - Qt API
        width = max(self.width(), 1)
        return QSize(width, self.heightForWidth(width))

    def minimumSizeHint(self) -> QSize:  # noqa: N802 - Qt API
        return QSize(0, self.heightForWidth(max(self.width(), 1)))

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt API
        super().resizeEvent(event)
        self.updateGeometry()

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt API
        palette = current_palette()
        painter = QPainter(self)
        try:
            painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)
            for badge, rect, word in self._entries(self.width()):
                paint_project_badge(painter, rect, badge, palette)
                painter.setPen(QColor(palette.text_muted))
                painter.setFont(self.font())
                painter.drawText(word, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, LEGEND_WORDS[badge])
        finally:
            painter.end()
