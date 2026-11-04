"""Fade components for the motion."""
from __future__ import annotations
from PySide6.QtCore import QEvent, QRect, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPixmap, QRegion
from PySide6.QtWidgets import QApplication, QWidget
from tomography_session_browser.ui.motion.preferences import motion_enabled
from tomography_session_browser.ui.motion.tokens import DURATION_MICRO_MS, PAGE_FADE_START_OPACITY
from tomography_session_browser.ui.motion.tween import Tween
from tomography_session_browser.ui.motion.picture_layout import PictureLayout
from tomography_session_browser.ui.motion.easing import ease_out_cubic

#: Passes over pending layouts before a picture of a committed surface (a
#: layout's activation can ask its parent's to run again; Focus needs two).
PENDING_LAYOUT_PASSES = 3


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

    finished = Signal()
    motion_finished = Signal()

    def __init__(self, parent: QWidget, geometry: QRect, picture: QPixmap, *, duration_ms: float) -> None:
        super().__init__(parent)
        self.setObjectName("pictureFade")
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setGeometry(geometry)
        self._picture = picture
        self._beneath: QPixmap | None = None
        self._layout: PictureLayout | None = None
        self._progress = 0.
        self.opacity = 1.0
        self._finished = False
        self._fade = Tween(duration_ms, on_update=self._faded, on_finished=self._completed,
                           widget=self, easing=lambda t: t)
        self.destroyed.connect(lambda *_args, fade=self._fade: fade.stop() if fade.is_animating else None)
        self.show()
        self.raise_()

    def start(self, beneath: QPixmap | None = None, *, layout: PictureLayout | None = None,
              duration_ms: float | None = None) -> None:
        """Begin dissolving into ``beneath`` (or the live canvas, without it).

        With a layout, ``beneath`` is the committed surface the elements move
        over, so the picture stays opaque until it retires.
        """

        self._layout = layout
        if beneath is not None:
            self._beneath = beneath
        if self._progress:  # Moving again, from the start, for a surface placed late.
            self._progress = 0.
            self.update()
        if layout is not None or beneath is not None:
            self._cover(0.)
        if duration_ms is not None:
            self._fade.duration_s = max(1e-3, duration_ms / 1000.)
        self._fade.start()

    def picture_beneath(self, region: QRegion, fill: QColor) -> QPixmap:
        """Picture the widgets under this one once, within ``region``; ``fill`` elsewhere.

        Pending layouts go first, so it shows them as they will next paint.
        No event is processed while it is hidden, so no frame shows the gap.
        """

        for _ in range(PENDING_LAYOUT_PASSES):
            QApplication.sendPostedEvents(None, QEvent.Type.LayoutRequest)
        ratio, parent = self.devicePixelRatioF(), self.parentWidget()
        picture = QPixmap(self.size() * ratio)
        picture.setDevicePixelRatio(ratio)
        picture.fill(fill)
        if parent is None:
            return picture
        self.hide()
        try:
            parent.render(picture, region.boundingRect().topLeft(), region.translated(self.pos()))
        finally:
            self.show()
        return picture

    def _completed(self) -> None:
        if self._layout is not None:
            self.motion_finished.emit()
        else:
            self._finish()

    def reveal(self) -> None:
        """After the move and publication, uncover the live side panes."""
        if self._layout is None or not self._layout.reveal_ms:
            self._finish()
            return
        if self._beneath is not None:  # The live panes show through from here.
            self._beneath = None
            self._cover(1.)
            self.update()
        duration = self._fade.duration_s * 1000
        reveal_ms = self._layout.reveal_ms
        self._fade = Tween(reveal_ms, on_update=lambda p: self._faded(1.+p*reveal_ms/duration),
                           on_finished=self._finish, widget=self, easing=lambda t: t)
        self.destroyed.connect(lambda *_args, fade=self._fade: fade.stop() if fade.is_animating else None)
        self._fade.start()

    @property
    def started(self) -> bool:
        return self._fade.is_animating or self._fade.settled

    def _faded(self, progress: float) -> None:
        previous = self._progress
        self._progress = progress
        self.opacity = 1.0 - ease_out_cubic(min(1., progress))
        if self._layout is None:
            self.update()
        else:
            self._cover(progress)
            damage = self._layout.damage(previous, progress)
            if not damage.isEmpty():
                self.update(damage)

    def _cover(self, progress: float) -> None:
        # While a picture or a fill hides the whole window, Qt paints none of
        # the live widgets beneath (190 of them under Focus, about 30 ms a frame).
        opaque = self._beneath is not None or (
            self._layout is not None and self._layout.covers(QRectF(self.rect()), progress))
        if opaque != self.testAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent):
            self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent, opaque)

    def paintEvent(self, _event) -> None:  # noqa: N802 - Qt API
        painter = QPainter(self)
        if self._layout is not None:
            if self._beneath is not None:
                painter.drawPixmap(0, 0, self._beneath)
            self._layout.paint(painter, QRectF(self.rect()), self._picture, self._progress)
            return
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
        if self._finished:
            return
        self._finished = True
        # Release device-sized images now, rather than at DeferredDelete.
        self._picture = QPixmap()
        self._beneath = None
        self._layout = None
        self.hide()
        self.finished.emit()
        self.deleteLater()
