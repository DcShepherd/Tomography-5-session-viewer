"""Fade components for the motion."""
from __future__ import annotations
from PySide6.QtCore import QRect, Qt
from PySide6.QtGui import QColor, QPainter, QPixmap
from PySide6.QtWidgets import QWidget
from tomography_session_browser.ui.motion.preferences import motion_enabled
from tomography_session_browser.ui.motion.tokens import DURATION_MICRO_MS, PAGE_FADE_START_OPACITY
from tomography_session_browser.ui.motion.tween import Tween


class PageFade(QWidget):
    """A veil in the page's colour lifting off a tab page as it appears.

    Plan F5.3. The page is live underneath from the first frame; the veil
    starts part-transparent and fades out in ``DURATION_MICRO_MS``. A snapshot
    crossfade was measured at ~30 ms per page grab, added to every tab
    switch before anything showed; this adds no latency, at the cost of the
    page repainting under the veil for the three or four frames it lasts.
    Mouse events pass straight through.
    """

    def __init__(
        self,
        parent: QWidget,
        geometry: QRect,
        colour: QColor,
        *,
        start_opacity: float = PAGE_FADE_START_OPACITY,
        duration_ms: float = DURATION_MICRO_MS,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("pageFade")
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setGeometry(geometry)
        self._colour = QColor(colour)
        self._start_opacity = start_opacity
        self.opacity = start_opacity
        self._fade = Tween(duration_ms, on_update=self._faded, on_finished=self._finish, widget=self)
        self.destroyed.connect(lambda *_args, fade=self._fade: fade.stop() if fade.is_animating else None)
        self.show()
        self.raise_()
        self._fade.start()

    def _faded(self, progress: float) -> None:
        self.opacity = self._start_opacity * (1.0 - progress)
        self.update()

    def paintEvent(self, _event) -> None:  # noqa: N802 - Qt API
        colour = QColor(self._colour)
        colour.setAlphaF(max(0.0, min(1.0, self.opacity)))
        QPainter(self).fillRect(self.rect(), colour)

    def dismiss(self) -> None:
        """Remove the veil now (another switch replaces it)."""

        self._fade.stop()
        self._finish()

    def _finish(self) -> None:
        self.hide()
        self.deleteLater()


def veil(
    widget: QWidget,
    colour: QColor,
    *,
    start_opacity: float,
    duration_ms: float = DURATION_MICRO_MS,
) -> PageFade | None:
    """Say that ``widget``'s content was just replaced: a veil lifts off it.

    For a list refilled or a dashboard rebuilt. It replaced fades that put a
    ``QGraphicsOpacityEffect`` on the whole widget, which renders the subtree
    off screen every frame (plan principle 8). The veil sits in the widget's
    parent, over its rectangle only (a scroll area's viewport moves its own
    children when it scrolls). None when motion is off or nothing shows.
    """

    parent = widget.parentWidget()
    window = widget.window()
    if parent is None or not motion_enabled(widget) or not widget.isVisible() or not window.isVisible():
        return None
    # A window can hold motion back (the main window, while its loader is
    # up: the loader's hand-off pictures the page, and would catch a veil
    # half-lifted).
    held = getattr(window, "motion_held", None)
    if callable(held) and held():
        return None
    return PageFade(parent, widget.geometry(), colour, start_opacity=start_opacity, duration_ms=duration_ms)


class PictureFade(QWidget):
    """A picture of what was shown, dissolving into what replaced it.

    Plan F5.4: the previous image fades out over the new one. It shows the
    old picture, fully opaque, until :meth:`start`; given the new picture
    too, it is opaque and composites both, so each frame is two blits rather
    than a repaint of the canvas beneath (21–27 ms a frame natively). Fixed
    time, eased out, on the shared ticker; clicks pass through; it removes
    itself.
    """

    def __init__(self, parent: QWidget, geometry: QRect, picture: QPixmap, *, duration_ms: float) -> None:
        super().__init__(parent)
        self.setObjectName("pictureFade")
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setGeometry(geometry)
        self._picture = picture
        self._beneath: QPixmap | None = None
        self.opacity = 1.0
        self._fade = Tween(duration_ms, on_update=self._faded, on_finished=self._finish, widget=self)
        self.destroyed.connect(lambda *_args, fade=self._fade: fade.stop() if fade.is_animating else None)
        self.show()
        self.raise_()

    def start(self, beneath: QPixmap | None = None) -> None:
        """Begin dissolving into ``beneath`` (or the live canvas, without it)."""

        if beneath is not None:
            self._beneath = beneath
            self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent)
        self._fade.start()

    @property
    def started(self) -> bool:
        return self._fade.is_animating or self._fade.settled

    def _faded(self, progress: float) -> None:
        self.opacity = 1.0 - progress
        self.update()

    def paintEvent(self, _event) -> None:  # noqa: N802 - Qt API
        painter = QPainter(self)
        if self._beneath is not None:
            painter.fillRect(self.rect(), QColor(0, 0, 0))
            painter.drawPixmap(0, 0, self._beneath)
        painter.setOpacity(max(0.0, min(1.0, self.opacity)))
        painter.drawPixmap(0, 0, self._picture)

    def dismiss(self) -> None:
        """Remove it now (an interaction, or another image, replaces it)."""

        self._fade.stop()
        self._finish()

    def _finish(self) -> None:
        self.hide()
        self.deleteLater()

