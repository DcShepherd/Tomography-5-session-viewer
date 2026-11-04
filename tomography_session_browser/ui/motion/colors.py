"""Colors components for the motion."""
from __future__ import annotations
import re
from PySide6.QtGui import QColor


_CSS_RGBA = re.compile(r"rgba\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*,\s*([\d.]+)\s*\)")


def css_colour(value: str) -> QColor:
    """A palette token as a QColor, including QSS ``rgba(r, g, b, a)``."""

    match = _CSS_RGBA.fullmatch(value.strip())
    if match is None:
        return QColor(value)
    red, green, blue, alpha = match.groups()
    colour = QColor(int(red), int(green), int(blue))
    colour.setAlphaF(float(alpha))
    return colour


def blend(start: QColor, end: QColor, amount: float) -> QColor:
    """Linear blend of two colours, ``amount`` clamped to 0..1."""

    t = min(1.0, max(0.0, amount))
    return QColor(
        round(start.red() + (end.red() - start.red()) * t),
        round(start.green() + (end.green() - start.green()) * t),
        round(start.blue() + (end.blue() - start.blue()) * t),
        round(start.alpha() + (end.alpha() - start.alpha()) * t),
    )

