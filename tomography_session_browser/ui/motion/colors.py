"""Colors components for the motion."""
from __future__ import annotations
from PySide6.QtGui import QColor


def blend(start: QColor, end: QColor, amount: float) -> QColor:
    """Linear blend of two colours, ``amount`` clamped to 0..1."""

    t = min(1.0, max(0.0, amount))
    return QColor(
        round(start.red() + (end.red() - start.red()) * t),
        round(start.green() + (end.green() - start.green()) * t),
        round(start.blue() + (end.blue() - start.blue()) * t),
        round(start.alpha() + (end.alpha() - start.alpha()) * t),
    )

