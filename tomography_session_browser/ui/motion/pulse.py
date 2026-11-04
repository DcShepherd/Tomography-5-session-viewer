"""Pulse components for the motion."""
from __future__ import annotations
from collections.abc import Callable
import math
from PySide6.QtCore import QEvent, QPointF, QRect, Qt
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import QWidget
from tomography_session_browser.ui.motion.tokens import DURATION_PULSE_MS
from tomography_session_browser.ui.motion.tween import Tween


class MarkerPulse(QWidget):
    """One ring expanding from a marker and fading, where a jump landed.

    Plan F5.7. It floats over the canvas as a widget of its own, so a scene
    render (an image export, a crossfade's picture) never contains it, and it
    touches no marker. ``locate`` gives the marker's centre and radius in this
    widget's coordinates, or None; it is asked on every paint, so the ring
    stays on the marker if the view moves. Only the ring's own band is
    repainted each frame. Clicks pass through.
    """

    def __init__(
        self,
        parent: QWidget,
        locate: Callable[[], tuple[QPointF, float] | None],
        colour: QColor,
        *,
        duration_ms: float = DURATION_PULSE_MS,
        spread_px: float = 22.0,
        width_px: float = 2.5,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("markerPulse")
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setGeometry(parent.rect())
        self._locate = locate
        self._colour = QColor(colour)
        self._spread = spread_px
        self._width = width_px
        self.progress = 0.0
        self._painted: QRect | None = None
        self._tween = Tween(duration_ms, on_update=self._stepped, on_finished=self._finish, widget=self)
        self.destroyed.connect(lambda *_args, tween=self._tween: tween.stop() if tween.is_animating else None)
        parent.installEventFilter(self)
        self.show()
        self.raise_()
        self._tween.start()

    def ring(self) -> tuple[QPointF, float, float] | None:
        """The ring now: centre, radius and opacity."""

        found = self._locate()
        if found is None:
            return None
        centre, radius = found
        return centre, radius + self._spread * self.progress, 0.9 * (1.0 - self.progress)

    def _ring_rect(self) -> QRect | None:
        ring = self.ring()
        if ring is None:
            return None
        centre, radius, _opacity = ring
        reach = radius + self._width + 2.0
        return QRect(
            math.floor(centre.x() - reach), math.floor(centre.y() - reach), math.ceil(2 * reach) + 1, math.ceil(2 * reach) + 1
        )

    def _stepped(self, progress: float) -> None:
        self.progress = progress
        rect = self._ring_rect()
        dirty = rect if self._painted is None else (rect.united(self._painted) if rect is not None else self._painted)
        self._painted = rect
        if dirty is not None:
            self.update(dirty)

    def paintEvent(self, _event) -> None:  # noqa: N802 - Qt API
        ring = self.ring()
        if ring is None:
            return
        centre, radius, opacity = ring
        colour = QColor(self._colour)
        colour.setAlphaF(max(0.0, min(1.0, opacity)))
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(QPen(colour, self._width))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawEllipse(centre, radius, radius)

    def eventFilter(self, watched, event) -> bool:  # noqa: N802 - Qt API
        if event.type() == QEvent.Type.Resize and watched is self.parentWidget():
            self.setGeometry(watched.rect())
        return False

    def dismiss(self) -> None:
        self._tween.stop()
        self._finish()

    def _finish(self) -> None:
        parent = self.parentWidget()
        if parent is not None:
            parent.removeEventFilter(self)
        self.hide()
        self.deleteLater()

