"""Theme-aware icon loader.

Loads monochrome SVGs from ``assets/icons/`` and recolours them on the fly to
match the active theme so the same set of files works on both light and dark
backgrounds. The SVGs use ``stroke="currentColor"``; we substitute the literal
colour string before handing the result to a ``QSvgRenderer`` and rasterise it
to a ``QPixmap`` at the requested size.

Kept deliberately small — there is no caching dance beyond a per-(name, color,
size) ``QPixmap`` cache, and no exotic icon engine.
"""

from __future__ import annotations

from pathlib import Path
from math import ceil
from typing import Final

from PySide6.QtCore import QByteArray, QRectF, QSize, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer

from tomography_session_browser.ui.theme import DARK_PALETTE

_ICON_DIR: Final[Path] = Path(__file__).resolve().parent.parent / "assets" / "icons"
_DEFAULT_SIZE: Final[int] = 18
_DEFAULT_COLOR: str = DARK_PALETTE.icon

_pixmap_cache: dict[tuple[str, str, int, float], QPixmap] = {}


def themed_icon(name: str, *, color: str | None = None, size: int = _DEFAULT_SIZE) -> QIcon:
    """Return a ``QIcon`` for ``assets/icons/<name>.svg`` recoloured to ``color``."""

    icon_color = color or _DEFAULT_COLOR
    icon = QIcon()
    for dpr in (1.0, 1.25, 1.5, 2.0, 3.0):
        pixmap = _themed_pixmap(name, icon_color, size, dpr)
        icon.addPixmap(pixmap, QIcon.Mode.Normal, QIcon.State.Off)
        icon.addPixmap(pixmap, QIcon.Mode.Active, QIcon.State.Off)
        icon.addPixmap(pixmap, QIcon.Mode.Selected, QIcon.State.Off)
    return icon


def set_default_icon_color(color: str) -> None:
    """Update the colour used when no explicit ``color`` is passed.

    Called when the active theme changes so freshly built icons match the new
    palette. Already-rendered ``QIcon`` instances are not re-coloured — call
    sites that need a refresh should rebuild their actions.
    """

    global _DEFAULT_COLOR  # noqa: PLW0603 — module-level palette state
    _DEFAULT_COLOR = color


def _themed_pixmap(name: str, color: str, size: int, dpr: float) -> QPixmap:
    cached = _pixmap_cache.get((name, color, size, dpr))
    if cached is not None:
        return cached

    svg_path = _ICON_DIR / f"{name}.svg"
    raw = svg_path.read_text(encoding="utf-8") if svg_path.exists() else _missing_svg()
    coloured = raw.replace("currentColor", color)
    renderer = QSvgRenderer(QByteArray(coloured.encode("utf-8")))

    physical_size = max(1, ceil(size * dpr))
    pixmap = QPixmap(QSize(physical_size, physical_size))
    pixmap.setDevicePixelRatio(dpr)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    try:
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        renderer.render(painter, QRectF(0, 0, size, size))
    finally:
        painter.end()

    _pixmap_cache[(name, color, size, dpr)] = pixmap
    return pixmap


def _missing_svg() -> str:
    # Diamond placeholder so a typo in an icon name is visible at a glance
    # rather than silently rendering nothing.
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" '
        'viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">'
        '<polygon points="12 2 22 12 12 22 2 12"/></svg>'
    )


# Importable QColor for callers that want to subclass / blend.
def parse_color(color: str) -> QColor:
    return QColor(color)
