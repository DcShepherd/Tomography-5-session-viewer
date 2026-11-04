"""Pay Windows' one-off font-fallback search off the GUI thread.

The first time Qt draws a character its font lacks ("▾" in the Atlas legend,
"✓" or "→" in the context panel), it searches the system's fonts for one that
has it. On Windows that takes about 400 ms, once per process, on whichever
thread asks first. Left to chance it was the GUI thread, during the first
visit to a tab, which then froze. Drawing one such character on a worker
thread when the window first shows fills the shared cache, so the GUI thread
finds the fallback ready (about 2 ms). The worker releases Python's lock, so
the GUI thread stays responsive meanwhile.

Display only: nothing here changes what any text says.
"""

from __future__ import annotations

import logging

from PySide6.QtCore import QRectF, Qt, QThreadPool
from PySide6.QtGui import QFont, QGuiApplication, QImage, QPainter

from tomography_session_browser.presentation.design_tokens import SANS_FONT_NAME

LOGGER = logging.getLogger(__name__)

#: A character Segoe UI does not have, so drawing it runs the search.
FALLBACK_PROBE = "✓"

_started = False


def warm_font_fallback() -> bool:
    """Start the search on a worker thread, once per process.

    Only on the native Windows platform, where the search is slow and text
    may be drawn off the GUI thread. Returns whether it started.
    """

    global _started  # noqa: PLW0603 - one search per process
    if _started or QGuiApplication.platformName() != "windows":
        return False
    _started = True
    QThreadPool.globalInstance().start(draw_fallback_probe)
    return True


def draw_fallback_probe() -> None:
    """Draw ``FALLBACK_PROBE`` in the interface font, into an image."""

    try:
        image = QImage(32, 32, QImage.Format.Format_ARGB32_Premultiplied)
        image.fill(Qt.GlobalColor.transparent)
        font = QFont(SANS_FONT_NAME)
        font.setPixelSize(12)
        painter = QPainter(image)
        try:
            painter.setFont(font)
            painter.drawText(QRectF(0, 0, 32, 32), Qt.AlignmentFlag.AlignCenter, FALLBACK_PROBE)
        finally:
            painter.end()
    except Exception:  # pragma: no cover - a warm-up must never break start-up
        LOGGER.debug("Font fallback warm-up failed", exc_info=True)
