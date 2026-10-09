"""Shared painting for tree/list row decorations.

The selected-row fill is deliberately soft in both palettes — around 1.2:1
against the panel — so on its own it is a very weak cue, and in the light
theme it was effectively invisible. An accent bar at the leading edge of the
row raises the signal above the 3:1 non-text contrast floor without turning
the selection into a heavy block that fights the rest of the chrome.

It has to be painted rather than styled: Qt stylesheet ``::item`` rules apply
to every column of a row independently, so a ``border-left`` renders one
(radius-curved) arc per column. Item delegates can see ``index.column()``,
so they can draw exactly one bar per row.
"""

from __future__ import annotations

from PySide6.QtCore import QRect, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPen

from tomography_session_browser.presentation.design_tokens import MONO_FONT_NAME, RADIUS_CONTROL, ThemePalette


#: Width of the marker in device-independent pixels.
SELECTION_MARKER_WIDTH = 3

#: Vertical inset so the bar reads as a rounded cap inside the row fill
#: rather than butting against the rows above and below.
SELECTION_MARKER_INSET = 3


def selection_marker_rect(row_rect: QRect) -> QRect:
    """Return the bar geometry for a row occupying ``row_rect``."""

    bar = QRect(row_rect).adjusted(0, SELECTION_MARKER_INSET, 0, -SELECTION_MARKER_INSET)
    bar.setWidth(SELECTION_MARKER_WIDTH)
    return bar


def paint_selection_marker(
    painter: QPainter,
    row_rect: QRect,
    palette: ThemePalette,
    *,
    strong: bool = False,
) -> None:
    """Draw the leading accent bar for a selected (or highlighted) row.

    ``strong`` uses the deeper accent reserved for the cross-view highlight,
    so a highlighted row stays distinguishable from the selected one.
    """

    colour = QColor(palette.accent_strong if strong else palette.selection_marker)
    painter.fillRect(selection_marker_rect(row_rect), colour)


#: The palette slot each project badge is drawn in (harmonisation D3). The
#: letters and what they mean are ``project_tree.PROJECT_BADGE_MEANINGS``;
#: this only draws them, as a fixed-width tag rather than bare text.
PROJECT_BADGE_TONES: dict[str, str] = {
    "LS": "accent",
    "AT": "chart_blue",
    "DC": "chart_grey",
    "!": "chart_amber",
}
#: One size for every badge, so a column of them lines up.
PROJECT_BADGE_SIZE = (26, 18)
#: Alpha (of 255) of a badge's fill behind its letters.
PROJECT_BADGE_FILL_ALPHA = 40


def is_project_badge(text: str) -> bool:
    return text in PROJECT_BADGE_TONES


def project_badge_rect(cell: QRectF | QRect, *, align_right: bool = True, right_inset: float = 2.0) -> QRectF:
    """The badge's rectangle inside ``cell``: vertically centred, ``right_inset``
    in from its right edge (a tree's badge column) or at its left (the legend)."""

    cell = QRectF(cell)
    width, height = PROJECT_BADGE_SIZE
    left = cell.right() - width - right_inset if align_right else cell.left()
    return QRectF(left, cell.center().y() - height / 2, width, height)


def paint_project_badge(painter: QPainter, rect: QRectF, badge: str, palette: ThemePalette) -> None:
    """Draw ``badge`` (LS, AT, DC, !) as a toned tag filling ``rect``."""

    tone = QColor(getattr(palette, PROJECT_BADGE_TONES[badge]))
    fill = QColor(tone)
    fill.setAlpha(PROJECT_BADGE_FILL_ALPHA)
    painter.save()
    try:
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(QPen(tone, 1.0))
        painter.setBrush(fill)
        painter.drawRoundedRect(rect.adjusted(0.5, 0.5, -0.5, -0.5), RADIUS_CONTROL, RADIUS_CONTROL)
        font = QFont(MONO_FONT_NAME)
        font.setPixelSize(11)
        font.setWeight(QFont.Weight.DemiBold)
        painter.setFont(font)
        painter.setPen(QColor(palette.text_strong))
        painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, badge)
    finally:
        painter.restore()
