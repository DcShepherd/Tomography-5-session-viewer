"""Panel slide components for the motion."""
from __future__ import annotations
from collections.abc import Callable
from PySide6.QtCore import QRect, Qt
from PySide6.QtGui import QColor, QPainter, QPixmap
from PySide6.QtWidgets import QWidget
from tomography_session_browser.ui.motion.spring import Spring
from tomography_session_browser.ui.motion.tokens import MOTION_PANEL, SpringSpec


class SlideBackdrop(QWidget):
    """An opaque fill over a panel that is sliding in, until its picture lands."""

    def __init__(self, parent: QWidget, geometry: QRect, colour: QColor) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent)
        self._colour = QColor(colour)
        self.setGeometry(geometry)

    def paintEvent(self, _event) -> None:  # noqa: N802 - Qt API
        QPainter(self).fillRect(self.rect(), self._colour)


class PanelSlide(QWidget):
    """A side panel's snapshot sliding in from, or out to, its own edge.

    Plan F5.2. The panel itself is shown or hidden at once, so the window lays
    out a single time. This widget is the panel's picture, moved on a spring,
    and it removes itself when it settles. Animating the dock's real width
    re-laid out the whole window on every frame (40–125 ms a frame natively).

    It is opaque and *moved*, so each frame Qt repaints only the strip it
    uncovers; a transparent overlay repainted everything beneath it every
    frame. Mouse events pass straight through. Entering, the panel is already
    in place underneath, so an opaque ``backdrop`` in the panel's own colour
    covers it until the picture arrives; leaving, the widened workspace shows
    behind the picture as it goes.

    A toggle mid-way turns it round (``reverse``): the spring keeps its value
    and velocity, so the picture heads back from where it is instead of
    vanishing and starting over from the far edge.
    """

    def __init__(
        self,
        parent: QWidget,
        geometry: QRect,
        picture: QPixmap,
        *,
        from_left: bool,
        entering: bool,
        backdrop: QColor | None = None,
        spec: SpringSpec = MOTION_PANEL,
        on_finished: Callable[[], None] | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("panelSlide")
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent)
        self._home = QRect(geometry)
        self._picture = picture
        self._from_left = from_left
        self._entering = entering
        # Called when an entering slide arrives, before its picture goes
        # (never on ``dismiss``): the real panel can take the picture's place.
        self._on_finished = on_finished
        self.backdrop = (
            SlideBackdrop(parent, geometry, backdrop) if entering and backdrop is not None else None
        )
        width = max(1, geometry.width())
        # How far out the picture is: 0 at the panel's place, 1 past its edge.
        self._out = Spring(
            1.0 if entering else 0.0,
            spec=spec,
            on_update=self._moved,
            on_settled=self._arrived,
            widget=self,
            # Within half a pixel of home, and all but still.
            settle_distance=0.5 / width,
            settle_velocity=20.0 / width,
        )

        # The shared ticker must never step a slide destroyed mid-way (the
        # window closing, or a second toggle replacing it). The handler holds
        # the spring and the backdrops, not this widget.
        backdrops = [self.backdrop] if self.backdrop is not None else []
        self._backdrops = backdrops

        def _cleanup(*_args, spring=self._out, backdrops=backdrops) -> None:
            if spring.is_animating:
                spring.reset(spring.value)
            for backdrop in backdrops:
                try:
                    backdrop.deleteLater()
                except RuntimeError:
                    pass  # went with its parent

        self.destroyed.connect(_cleanup)
        self.resize(geometry.size())
        self._moved(self._out.value)
        if self.backdrop is not None:
            self.backdrop.show()
            self.backdrop.raise_()
        self.show()
        self.raise_()
        self._out.set_target(0.0 if entering else 1.0)

    def dismiss(self) -> None:
        """Remove the slide now, finished or not."""

        self._out.reset(self._out.value)
        self._finish()

    def reverse(self, *, backdrop: QColor | None = None, on_finished: Callable[[], None] | None = None) -> None:
        """Head back the other way from where the picture is now.

        Turning to enter, the real panel is back in place underneath, so a
        ``backdrop`` covers it until the picture arrives, and ``on_finished``
        runs then. Turning to leave, any backdrop goes: the widened page
        shows behind the picture.
        """

        self._entering = not self._entering
        if self._entering:
            self._on_finished = on_finished
            if self.backdrop is None and backdrop is not None:
                self.backdrop = SlideBackdrop(self.parentWidget(), self._home, backdrop)
                self._backdrops.append(self.backdrop)
            if self.backdrop is not None:
                self.backdrop.show()
                self.backdrop.raise_()
            self.raise_()
        else:
            self._on_finished = None
            if self.backdrop is not None:
                self.backdrop.hide()
        self._out.set_target(0.0 if self._entering else 1.0)

    @property
    def entering(self) -> bool:
        return self._entering

    @property
    def progress(self) -> float:
        """How far along the current direction it has travelled, 0 to 1."""

        out = min(1.0, max(0.0, self._out.value))
        return 1.0 - out if self._entering else out

    def picture_offset(self) -> int:
        """How far the picture sits from the panel's place, towards its edge."""

        out = min(1.0, max(0.0, self._out.value))
        distance = round(out * self._home.width())
        return -distance if self._from_left else distance

    def _moved(self, _value: float) -> None:
        self.move(self._home.x() + self.picture_offset(), self._home.y())

    def paintEvent(self, _event) -> None:  # noqa: N802 - Qt API
        QPainter(self).drawPixmap(0, 0, self._picture)

    def _arrived(self) -> None:
        on_finished, self._on_finished = self._on_finished, None
        if on_finished is not None and self._entering:
            on_finished()
        self._finish()

    def _finish(self) -> None:
        if self.backdrop is not None:
            self.backdrop.hide()
        self.hide()
        self.deleteLater()

