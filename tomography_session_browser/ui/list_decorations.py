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

from PySide6.QtCore import QRect
from PySide6.QtGui import QColor, QPainter

from tomography_session_browser.ui.theme import ThemePalette


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
