"""An on/off switch: a pill track and a knob that slides to the on side.

A check box drew the shared focus border around itself after every click.
The switch draws its ring for keyboard focus only, and the knob position, not
colour alone, says the state.
"""
from __future__ import annotations

from PySide6.QtCore import QEasingCurve, QRectF, QSize, Qt, QVariantAnimation
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import QAbstractButton, QSizePolicy, QWidget

from tomography_session_browser.ui.motion.preferences import motion_enabled
from tomography_session_browser.ui.motion.colors import css_colour
from tomography_session_browser.ui.motion.tokens import DURATION_MICRO_MS
from tomography_session_browser.ui.theme import current_palette
from tomography_session_browser.ui.widgets.keyboard_focus import keyboard_focus_reason

TRACK_WIDTH, TRACK_HEIGHT, KNOB = 32, 18, 14
#: Room around the track for the keyboard focus ring.
RING = 3


def mix(start: QColor, end: QColor, amount: float) -> QColor:
    return QColor.fromRgbF(*(a + (b - a) * amount for a, b in zip(start.getRgbF(), end.getRgbF())))


class Switch(QAbstractButton):
    def __init__(self, parent: QWidget | None = None, *, name: str = "") -> None:
        super().__init__(parent)
        self.setCheckable(True)
        self.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        if name:
            self.setAccessibleName(name)
        self.keyboard_focus = False
        self.position = 0.
        self.slide = QVariantAnimation(self)
        self.slide.setDuration(DURATION_MICRO_MS)
        self.slide.setEasingCurve(QEasingCurve.Type.OutCubic)
        self.slide.valueChanged.connect(self.moved)
        self.toggled.connect(self.animate)

    def sizeHint(self) -> QSize:  # noqa: N802 - Qt API
        return QSize(TRACK_WIDTH + 2 * RING, TRACK_HEIGHT + 2 * RING)

    def minimumSizeHint(self) -> QSize:  # noqa: N802 - Qt API
        return self.sizeHint()

    def animate(self, checked: bool) -> None:
        end = 1. if checked else 0.
        start = self.knob_position(1. - end)
        self.slide.stop()
        if not self.isVisible() or not motion_enabled(self):
            self.moved(end)
            return
        self.slide.setStartValue(start)
        self.slide.setEndValue(end)
        self.slide.start()

    def moved(self, value) -> None:
        self.position = float(value)
        self.update()

    def knob_position(self, at_rest: float | None = None) -> float:
        """Mid-slide, the animated position; at rest, the checked state.

        Settings sync with signals blocked, so a state set that way never
        reaches ``animate``; reading the state here keeps the knob right.
        """
        if self.slide.state() == QVariantAnimation.State.Running:
            return self.position
        return float(self.isChecked()) if at_rest is None else at_rest

    def focusInEvent(self, event) -> None:  # noqa: N802 - Qt API
        self.keyboard_focus = keyboard_focus_reason(event.reason())
        super().focusInEvent(event)

    def focusOutEvent(self, event) -> None:  # noqa: N802 - Qt API
        self.keyboard_focus = False
        super().focusOutEvent(event)

    def hitButton(self, pos) -> bool:  # noqa: N802 - Qt API
        return self.rect().contains(pos)

    def paintEvent(self, _event) -> None:  # noqa: N802 - Qt API
        palette = current_palette()
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        track = QRectF(RING, (self.height() - TRACK_HEIGHT) / 2, TRACK_WIDTH, TRACK_HEIGHT)
        if self.keyboard_focus and self.hasFocus():
            painter.setPen(QPen(QColor(palette.accent), 1.5))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            ring = track.adjusted(-2, -2, 2, 2)
            painter.drawRoundedRect(ring, ring.height() / 2, ring.height() / 2)
        position = self.knob_position()
        fill = mix(QColor(palette.control_border), QColor(palette.accent), position)
        if self.isDown():
            fill = css_colour(palette.accent_soft)
        knob_colour = QColor(palette.accent if self.isDown() else palette.surface)
        if not self.isEnabled():
            fill.setAlphaF(.4)
            knob_colour.setAlphaF(.7)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(fill)
        painter.drawRoundedRect(track, TRACK_HEIGHT / 2, TRACK_HEIGHT / 2)
        inset = (TRACK_HEIGHT - KNOB) / 2
        x = track.left() + inset + position * (TRACK_WIDTH - KNOB - 2 * inset)
        painter.setBrush(knob_colour)
        painter.drawEllipse(QRectF(x, track.top() + inset, KNOB, KNOB))
        painter.end()
