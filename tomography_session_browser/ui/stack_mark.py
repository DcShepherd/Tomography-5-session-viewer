"""The Stack mark's drawing, kept free of heavy imports.

The start mark paints this before the application modules load, so the module
imports only QtCore, QtGui and the design tokens: no QtSvg and no ``ui.theme``.
``ui.branding`` and the loading overlay draw the mark through it too.
"""

from __future__ import annotations

from typing import Final

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPen

from tomography_session_browser.presentation.design_tokens import palette_for


_MARK_GEOMETRY: Final[tuple[tuple[float, float, float, bool], ...]] = (
    (3.0, 0.42, 0.55, False),
    (6.0, 0.65, 0.75, False),
    (9.0, 0.84, 0.90, False),
    (11.6, 1.00, 1.00, True),
    (14.2, 0.84, 0.90, False),
    (17.2, 0.65, 0.70, False),
    (20.0, 0.42, 0.50, False),
)
_MARK_CENTER: Final[float] = 11.0
_MARK_MAX_WIDTH: Final[float] = 18.0
# The asset-sheet geometry is intentionally preserved, but its visible stroke
# envelope is centred at y=11.5 inside the 22-unit viewBox. UI containers need
# the visible mark, not the nominal viewBox, centred in their local frame.
_MARK_VISUAL_OFFSET_Y: Final[float] = -0.5


def draw_stack_mark(painter: QPainter, rect: QRectF, theme_name: str) -> None:
    """Paint the Stack mark centred in ``rect`` using asset-sheet geometry."""

    palette = palette_for(theme_name)
    accent = palette.accent
    mid = palette.text_strong
    scale = min(rect.width(), rect.height()) / 22.0
    painter.save()
    painter.translate(
        rect.left() + (rect.width() - 22 * scale) / 2,
        rect.top() + (rect.height() - 22 * scale) / 2 + _MARK_VISUAL_OFFSET_Y * scale,
    )
    painter.scale(scale, scale)
    try:
        for y, half_width, opacity, is_mid in _MARK_GEOMETRY:
            length = _MARK_MAX_WIDTH * half_width
            pen = QPen(QColor(mid if is_mid else accent), 1.6 if is_mid else 1.4)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            painter.setPen(pen)
            painter.setOpacity(1.0 if is_mid else opacity)
            painter.drawLine(
                QPointF(_MARK_CENTER - length / 2, y),
                QPointF(_MARK_CENTER + length / 2, y),
            )
    finally:
        painter.restore()
