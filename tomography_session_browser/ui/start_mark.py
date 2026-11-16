"""The start mark: the Stack mark on a small card while the application loads.

``app.run`` shows it as soon as the ``QApplication`` exists, before the
application modules are imported (about 1.5 s of the start-up). The main
window shows only after the card has shown for ``MINIMUM_VISIBLE_MS``, so a
fast start never draws the window under the card; the card closes at the
window's first paint. Batch 4d board decisions:

* the mark only, with no words, on a 320 x 200 card in the saved theme;
* centred where the window will open: the saved window geometry, else the
  primary screen, where a first-run window opens too;
* above other windows while it shows, with no taskbar button or title bar.
  It never takes focus or activation.

It has no animation or sound. This module imports only Qt, the design
tokens and the Stack mark drawing, to keep the first paint fast.
"""

from __future__ import annotations

from typing import Final

from PySide6.QtCore import QByteArray, QElapsedTimer, QEvent, QEventLoop, QObject, QPoint, QRect, QRectF, QSize, QTimer, Qt
from PySide6.QtGui import QColor, QGuiApplication, QPainter, QPainterPath, QPen, QRegion
from PySide6.QtWidgets import QApplication, QWidget

from tomography_session_browser.presentation.design_tokens import RADIUS_POPOVER, ThemePalette
from tomography_session_browser.ui.stack_mark import draw_stack_mark

CARD_SIZE: Final[QSize] = QSize(320, 200)
MARK_SIZE: Final[int] = 72
MINIMUM_VISIBLE_MS: Final[int] = 600
WINDOW_TITLE: Final[str] = "Tomography Session Browser"


class StartMark(QWidget):
    """A frameless card with the Stack mark. It never takes focus."""

    def __init__(self, palette: ThemePalette) -> None:
        flags = (
            Qt.WindowType.SplashScreen
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.WindowDoesNotAcceptFocus
        )
        super().__init__(None, flags)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setWindowTitle(WINDOW_TITLE)
        self.setFixedSize(CARD_SIZE)
        if QGuiApplication.platformName() == "xcb":
            # A native mask also works when X11 has no compositing manager.
            shape = QPainterPath()
            shape.addRoundedRect(QRectF(self.rect()), RADIUS_POPOVER, RADIUS_POPOVER)
            self.setMask(QRegion(shape.toFillPolygon().toPolygon()))
        else:
            self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.first_paint = QElapsedTimer()
        self.palette_tokens = palette
        self.paint_count = 0

    def paintEvent(self, _event) -> None:  # noqa: N802 - Qt virtual
        tokens = self.palette_tokens
        painter = QPainter(self)
        try:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            painter.setBrush(QColor(tokens.background))
            painter.setPen(QPen(QColor(tokens.border_strong), 1))
            painter.drawRoundedRect(
                QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5),
                RADIUS_POPOVER, RADIUS_POPOVER,
            )
            mark = QRectF((self.width() - MARK_SIZE) / 2, (self.height() - MARK_SIZE) / 2, MARK_SIZE, MARK_SIZE)
            draw_stack_mark(painter, mark, tokens.name)
        finally:
            painter.end()
        if not self.first_paint.isValid():
            self.first_paint.start()
        self.paint_count += 1


def start_mark_centre(saved_geometry: str) -> QPoint:
    """The centre of where the main window will open.

    The saved geometry goes through ``restoreGeometry`` on an empty, hidden
    widget, the same call the window makes. A monitor that is no longer
    attached is therefore handled in the same way. A maximised window uses
    its screen's centre. With nothing saved, or nothing usable, this is the
    primary screen's centre, where a first-run window opens.
    """

    if saved_geometry:
        probe = QWidget()
        try:
            if probe.restoreGeometry(QByteArray.fromBase64(saved_geometry.encode("ascii", errors="ignore"))):
                centre = probe.geometry().center()
                screen = QGuiApplication.screenAt(centre)
                if screen is not None:
                    if probe.isMaximized() or probe.isFullScreen():
                        return screen.availableGeometry().center()
                    return centre
        finally:
            probe.deleteLater()
    screen = QGuiApplication.primaryScreen()
    if screen is None:
        return QRect(QPoint(0, 0), CARD_SIZE).center()
    return screen.availableGeometry().center()


def show_start_mark(palette: ThemePalette, saved_geometry: str) -> StartMark:
    """Show and paint the mark now, before any event loop runs."""

    mark = StartMark(palette)
    mark.move(start_mark_centre(saved_geometry) - QRect(QPoint(0, 0), CARD_SIZE).center())
    mark.show()
    # One pass, so the native window appears and paints on its first expose.
    # Input waits for the main window.
    QApplication.processEvents(QEventLoop.ProcessEventsFlag.ExcludeUserInputEvents)
    if mark.paint_count == 0:
        # No expose arrived in that pass: paint now, as no event loop runs
        # until the window is built.
        mark.repaint()
    return mark


class StartMarkHandoff(QObject):
    """Hand over from the card to the main window, once.

    ``show`` holds the window back until the card has shown for
    ``MINIMUM_VISIBLE_MS``, so a fast start never draws the window under the
    card. The card closes at the window's first paint.
    """

    def __init__(self, mark: StartMark) -> None:
        super().__init__()
        self._mark: StartMark | None = mark
        self._window: QWidget | None = None
        self.visible_ms: int | None = None
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setTimerType(Qt.TimerType.PreciseTimer)
        self._timer.timeout.connect(self._show_window)

    def watch(self, window: QWidget) -> None:
        """Close the card when ``window`` first paints or closes. The caller shows it."""
        self._window = window
        window.installEventFilter(self)
        window.destroyed.connect(self.window_destroyed)

    def show(self, window: QWidget) -> None:
        """Watch ``window`` and show it once the card has had its minimum time."""
        self.watch(window)
        mark = self._mark
        shown_ms = mark.first_paint.elapsed() if mark is not None and mark.first_paint.isValid() else MINIMUM_VISIBLE_MS
        if shown_ms >= MINIMUM_VISIBLE_MS:
            window.show()
        else:
            self._timer.start(MINIMUM_VISIBLE_MS - shown_ms)

    def _show_window(self) -> None:
        if self._window is not None:
            self._window.show()

    def close(self) -> None:
        mark, self._mark = self._mark, None
        if mark is None:
            return
        self._timer.stop()
        window, self._window = self._window, None
        if window is not None:
            window.removeEventFilter(self)
            window.destroyed.disconnect(self.window_destroyed)
        if mark.first_paint.isValid():
            self.visible_ms = mark.first_paint.elapsed()
        mark.close()

    def window_destroyed(self) -> None:
        self._window = None
        self.close()

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802 - Qt virtual
        if watched is self._window and event.type() in (QEvent.Type.Paint, QEvent.Type.Close):
            self.close()
        return False
