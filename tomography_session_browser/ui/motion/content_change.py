"""Small content replacements in reserved layout slots; the canvas never moves."""
from __future__ import annotations

from collections.abc import Callable
from typing import Literal
import math
import time

from PySide6.QtCore import QEvent, QObject, QRect, QTimer, Qt, Signal
from PySide6.QtGui import QColor, QPixmap, QRegion
from PySide6.QtWidgets import QApplication, QWidget
import shiboken6

from tomography_session_browser.ui.motion.fade import PictureFade
from tomography_session_browser.ui.motion.gc_pause import GcPause
from tomography_session_browser.ui.motion.picture_layout import PictureLayout
from tomography_session_browser.ui.motion.preferences import motion_enabled
from tomography_session_browser.ui.motion.section_reveal import settle_layouts
from tomography_session_browser.ui.motion.tokens import DURATION_MICRO_MS, INTERRUPTING_INPUT

EndReason = Literal["finished", "newer_request", "input", "playback", "timeout", "hidden", "closed",
                    "geometry", "reduced_motion"]


class ContentTransition(QObject):
    """Own one outgoing picture (and at most one of the committed surface) and every interruption/retirement path.

    Defaults retain the immediate slot replacement used by headers and choosers.
    Deferred users call ``begin`` when content is ready; a timeout bounds only
    that wait. Layout users move on commit, then wait at the end for publication
    before revealing live panes. Every exit releases pictures and restores
    annotations immediately.
    """

    ended = Signal(str)

    def __init__(
        self, widget: QWidget, *, deferred_start: bool = False, timeout_ms: int | None = None,
        hide_annotations: Callable[[], None] | None = None,
        restore_annotations: Callable[[], None] | None = None,
    ) -> None:
        super().__init__(widget)
        if (hide_annotations is None) != (restore_annotations is None):
            raise ValueError("Annotation hide and restore callbacks must be supplied together")
        if timeout_ms is not None and timeout_ms <= 0:
            raise ValueError("A transition timeout must be positive")
        self.widget = widget
        self.fade: PictureFade | None = None
        self.state: Literal["idle", "holding", "moving", "waiting", "fading"] = "idle"
        self._deferred_start = deferred_start
        self._hide_annotations = hide_annotations
        self._restore_annotations = restore_annotations
        self._timeout_ms = timeout_ms
        self._timeout = QTimer(self)
        self._timeout.setTimerType(Qt.TimerType.PreciseTimer)
        self._timeout.setSingleShot(True)
        self._timeout.timeout.connect(lambda: self.end("timeout"))
        self._surface = widget
        self._changing_geometry = False
        self._move_deadline = 0.
        self._follow: Callable[[], None] | None = None
        self._follow_until = 0.
        self._gc = GcPause()
        # A widget destroyed mid-transition skips end(); the collector still comes back.
        self.destroyed.connect(lambda *_args, gc_pause=self._gc: gc_pause.resume())

    @property
    def in_progress(self) -> bool:
        return self.state != "idle"

    @property
    def committing_geometry(self) -> bool:
        """The surface's geometry is still changing as part of the commit."""
        return self._changing_geometry

    def replace(self, change: Callable[[], None], *, surface: QWidget | None = None) -> None:
        target = surface if surface is not None else self.widget
        parent = target if surface is not None else target.parentWidget()
        geometry = target.rect() if surface is not None else target.geometry()
        animate = (parent is not None and parent.isVisible() and motion_enabled(self.widget)
                   and not geometry.isEmpty())
        # Picture the displayed state before retiring an interrupted fade.
        picture = parent.grab(geometry) if animate else None
        self.end("newer_request")
        self._surface = target
        # A shell layout commits synchronously. Its deliberate geometry changes
        # must not cancel its own picture; later resize/hide/input still do.
        self._changing_geometry = surface is not None
        try:
            if self._deferred_start and picture is not None and parent is not None:
                # A cached result may begin inside change; establish ownership
                # first. A shell's deadline starts at its committed request.
                self._show(parent, geometry, picture)
            change()
            if surface is not None:
                settle_layouts(self.widget, surface)
                if self.fade is not None:
                    self.fade.setGeometry(surface.rect())
                if self.state == "holding" and self._timeout_ms is not None:
                    self._timeout.start(self._timeout_ms)
        except BaseException:
            self.end("geometry")
            raise
        finally:
            self._changing_geometry = False
        if self._deferred_start or picture is None or parent is None:
            return
        settle_layouts(target, parent)
        self._show(parent, target.rect() if surface is not None else target.geometry(), picture)

    def show(self, picture: QPixmap) -> None:
        """Use a caller-rendered picture over the widget's own content surface."""

        self.end("newer_request")
        self._show(self.widget, self.widget.rect(), picture)

    def _show(self, parent: QWidget, geometry: QRect, picture: QPixmap) -> None:
        if picture.isNull() or not parent.isVisible() or not motion_enabled(self.widget):
            return
        self.fade = PictureFade(parent, geometry, picture, duration_ms=DURATION_MICRO_MS)
        self.state = "holding"
        self.fade.finished.connect(lambda: self.end("finished"))
        self.fade.motion_finished.connect(self._moved)
        QApplication.instance().installEventFilter(self)
        self._gc.pause()
        if self._hide_annotations is not None:
            self._hide_annotations()
        if self.state != "holding":
            return  # a callback may have synchronously interrupted the request
        if self._timeout_ms is not None and not self._changing_geometry:
            self._timeout.start(self._timeout_ms)
        if not self._deferred_start:
            self.begin()

    def begin(self, beneath: QPixmap | None = None, *, duration_ms: float = DURATION_MICRO_MS) -> None:
        """Fade into the current live content, or a supplied incoming picture."""

        if self.state != "holding":
            return
        if not motion_enabled(self.widget):
            self.end("reduced_motion")
            return
        self._timeout.stop()
        self.state = "fading"
        self.fade.start(beneath, duration_ms=duration_ms)

    def begin_next_turn(self, picture: Callable[[], QPixmap]) -> None:
        """Let the caller finish scene updates before rendering the incoming image.

        The identity check precedes rendering: retired requests never capture or
        start a newer transition. Qt also cancels the call if its owner dies.
        """

        fade = self.fade
        if fade is None:
            return
        def ready() -> None:
            if self.fade is fade and self.state == "holding":
                self.begin(picture())
        QTimer.singleShot(0, self, ready)

    def move(self, layout: PictureLayout, *, duration_ms: float,
             beneath: tuple[QRegion, QColor] | None = None) -> None:
        """Move on commit; publication releases only the bounded end hold.

        ``beneath`` (a region and a fill) pictures the committed surface once
        for the elements to move over. Nothing live repaints under the picture
        then, so a live change there shows only when the picture retires.
        """
        if self.state != "holding" or self.fade is None:
            return
        self._timeout.stop()
        self.state = "moving"
        # The deadline counts from the commit: picturing beneath shortens the end hold.
        self._move_deadline = time.monotonic() + (duration_ms + (self._timeout_ms or 0)) / 1000
        incoming = self.fade.picture_beneath(*beneath) if beneath is not None else None
        self.fade.start(incoming, layout=layout, duration_ms=duration_ms)

    def follow(self, move_again: Callable[[], None], *, within_ms: float) -> None:
        """Move again, rather than retire, when the platform places the surface late.

        For a commit that maximised or restored a window: Windows applies a
        maximise only after the commit returns, and X11 and Wayland window
        managers answer every state change later. A move or resize of the
        surface within ``within_ms`` of now joins the commit: once its layouts
        settle, the picture covers the surface again and ``move_again`` moves
        from the start. Later ones still retire it, as any resize does.
        """
        self._follow, self._follow_until = move_again, time.monotonic() + within_ms / 1000

    def _follows(self, watched) -> bool:
        return (watched is self._surface and self._follow is not None and not self._changing_geometry
                and self.state in ("moving", "fading") and time.monotonic() < self._follow_until)

    def _placed_late(self) -> None:
        self._changing_geometry = False
        if self.state in ("moving", "fading") and self._follow is not None and self.fade is not None:
            self.state = "holding"
            self.fade.setGeometry(self._surface.rect())
            self._follow()

    def _moved(self) -> None:
        if self.state == "moving":
            self.state = "waiting"
            if self._timeout_ms is not None:
                remaining = math.ceil((self._move_deadline - time.monotonic()) * 1000)
                if remaining <= 0:
                    self.end("timeout")
                else:
                    self._timeout.start(min(self._timeout_ms, remaining))
        elif self.state == "fading":
            self.fade.reveal()

    def release(self) -> None:
        """The matching destination published, before or after motion ended."""
        if self.state == "moving":
            self.state = "fading"
        elif self.state == "waiting":
            self._timeout.stop()
            self.state = "fading"
            self.fade.reveal()

    def dismiss(self) -> None:
        self.end("input")

    def end(self, reason: EndReason) -> None:
        """Retire this transition synchronously; repeated exits are harmless."""

        if self.state == "idle":
            return
        self.state = "idle"
        self._timeout.stop()
        self._follow = None
        self._gc.resume()
        app = QApplication.instance()
        if app is not None:
            app.removeEventFilter(self)
        fade, self.fade = self.fade, None
        if fade is not None and shiboken6.isValid(fade):
            fade.dismiss()
        if self._restore_annotations is not None:
            self._restore_annotations()
        self.ended.emit(reason)

    def eventFilter(self, watched, event) -> bool:  # noqa: N802 - Qt API
        if not shiboken6.isValid(self.widget):
            self.dismiss()
            return False
        kind = event.type()
        if kind in INTERRUPTING_INPUT:
            self.end("input")
        elif kind in (QEvent.Type.Move, QEvent.Type.Resize) and self._follows(watched):
            # Its children resize as this event is delivered; move again after.
            self._changing_geometry = True
            QTimer.singleShot(0, self, self._placed_late)
        elif kind in (QEvent.Type.Hide, QEvent.Type.Close) and isinstance(watched, QWidget):
            if (kind == QEvent.Type.Close or not self._changing_geometry) and (
                watched is self.widget or watched.isAncestorOf(self.widget)
            ):
                self.end("closed" if kind == QEvent.Type.Close else "hidden")
        elif not self._changing_geometry and kind in (QEvent.Type.Resize, QEvent.Type.DevicePixelRatioChange) and watched in (
            self.widget, self.widget.parentWidget(), self._surface,
        ):
            self.end("geometry")
        elif kind == QEvent.Type.DynamicPropertyChange and not motion_enabled(self.widget):
            self.end("reduced_motion")
        return False
