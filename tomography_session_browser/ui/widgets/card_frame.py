"""Optional card feedback, painted without restyling or moving its contents."""
from __future__ import annotations

from PySide6.QtCore import QEvent, QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import QFrame, QWidget

from tomography_session_browser.presentation.design_tokens import RADIUS_CARD
from tomography_session_browser.ui.motion.colors import blend, css_colour
from tomography_session_browser.ui.motion.preferences import motion_enabled
from tomography_session_browser.ui.motion.tokens import DURATION_MICRO_MS, DURATION_MICRO_CLOSE_MS, INTERRUPTING_INPUT
from tomography_session_browser.ui.motion.tween import Tween
from tomography_session_browser.ui.theme import current_palette

_ACTIVATE_KEYS = frozenset((Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Space))


class CardFrame(QFrame):
    """An inert frame until its owner enables navigation feedback.

    Input still goes to the owner's Qt handlers in the same event turn. Child
    controls that consume a press keep their own feedback and activation.
    QSS reserves the border and paints the surface. This frame paints the
    border in every state so translucent tokens are composited only once.
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._feedback_enabled = False
        self._hovered = False
        self._pressed = False
        self._hover_progress = 0.0
        self._hover_start = self._hover_target = 0.0
        self._hover_tween = Tween(DURATION_MICRO_MS, on_update=self._moved, widget=self)
        # One lifetime connection; repeated pointer entries do not accumulate
        # callbacks or leave the ticker holding a destroyed widget wrapper.
        self.destroyed.connect(self._hover_tween.stop)

    def set_feedback_enabled(self, enabled: bool) -> None:
        self._feedback_enabled = enabled
        self.setProperty("cardFeedback", enabled)
        style = self.style()
        if style is not None:
            style.unpolish(self)
            style.polish(self)
        self._reset_feedback()

    def _reset_feedback(self) -> None:
        self._hover_tween.stop()
        self._hovered = self._pressed = False
        self._hover_progress = 0.0
        self.update()

    def _moved(self, amount: float) -> None:
        self._hover_progress = self._hover_start + (self._hover_target - self._hover_start) * amount
        self.update()

    def _hover_to(self, hovered: bool) -> None:
        self._hovered = hovered
        start, target = self._hover_progress, float(hovered)
        self._hover_tween.stop()
        if self._pressed or not motion_enabled(self) or start == target:
            self._hover_progress = target
            self.update()
            return

        self._hover_start, self._hover_target = start, target
        self._hover_tween.duration_s = (DURATION_MICRO_MS if hovered else DURATION_MICRO_CLOSE_MS) / 1000
        self._hover_tween.start()

    def event(self, event) -> bool:  # noqa: N802 - Qt API
        if getattr(self, "_feedback_enabled", False):
            kind = event.type()
            if kind in (QEvent.Type.Hide, QEvent.Type.Close, QEvent.Type.EnabledChange,
                        QEvent.Type.WindowDeactivate, QEvent.Type.UngrabMouse):
                self._reset_feedback()
            elif self.isEnabled():
                if kind in INTERRUPTING_INPUT:
                    self._hover_tween.stop()
                    self._hover_progress = float(self._hovered)
                    self._pressed = False
                    self.update()
                if kind in (QEvent.Type.Enter, QEvent.Type.Leave):
                    self._hover_to(kind == QEvent.Type.Enter)
                elif (kind in (QEvent.Type.MouseButtonPress, QEvent.Type.MouseButtonDblClick,
                               QEvent.Type.MouseButtonRelease) and event.button() == Qt.MouseButton.LeftButton):
                    self._pressed = kind != QEvent.Type.MouseButtonRelease
                    self._hover_to(self._hovered)
                elif kind in (QEvent.Type.KeyPress, QEvent.Type.KeyRelease) and event.key() in _ACTIVATE_KEYS:
                    self._pressed = kind == QEvent.Type.KeyPress
                    self._hover_to(self._hovered)
                elif kind == QEvent.Type.FocusOut:
                    self._pressed = False
                    self.update()
        return super().event(event)

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt API
        super().paintEvent(event)
        if not self._feedback_enabled:
            return
        # Re-read the preference here too: changing it mid-hover settles the
        # next visible paint without starting another timer or app-wide filter.
        if not motion_enabled(self):
            self._hover_tween.stop()
            self._hover_progress = float(self._hovered)
        palette = current_palette()
        linked = bool(self.property("linked"))
        resting = palette.accent_line if linked else palette.border
        hovered = palette.accent if linked else palette.accent_line
        border = blend(css_colour(resting), css_colour(hovered), self._hover_progress)
        if self._pressed or self.hasFocus() or self.property("keyboardFocus"):
            border = QColor(palette.accent)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(QPen(border, 1))
        painter.setBrush(css_colour(palette.accent_soft) if self._pressed else Qt.BrushStyle.NoBrush)
        painter.drawRoundedRect(QRectF(self.rect()).adjusted(.5, .5, -.5, -.5), RADIUS_CARD, RADIUS_CARD)
        painter.end()
