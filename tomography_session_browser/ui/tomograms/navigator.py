"""Accepted current-slice overview; viewport navigation, never export chrome.

The same card as the orientation box: rounded, translucent, with the shared
close button at its top right. Help and accessibility name the slice.
"""
from __future__ import annotations
from tomography_session_browser.presentation.help_content import LocalBinding, InteractionKind

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QImage, QPainter, QPen
from PySide6.QtWidgets import QToolButton, QWidget

from tomography_session_browser.presentation.design_tokens import CANVAS_CHROME, RADIUS_CARD

#: The thumbnail's margin inside the card.
INSET = 6


class SliceNavigator(QWidget):
    centre_requested = Signal(float, float)
    dismissed = Signal()

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setAccessibleName("Current slice navigator")
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setCursor(Qt.CursorShape.CrossCursor)
        self.image = QImage()
        self.shape: tuple[int, int] = (1, 1)
        self.region = (0., 0., 1., 1.)
        self.axis = "XY"
        self.set_axis("XY")
        self.close_button = QToolButton(self)
        self.close_button.setObjectName("tomogramInsetClose")
        self.close_button.setText("×")
        self.close_button.setAccessibleName("Hide navigator")
        self.close_button.setToolTip("Hide the navigator. Press N to show it again.")
        self.close_button.setCursor(Qt.CursorShape.ArrowCursor)
        self.close_button.clicked.connect(self.dismissed)
        self.hide()

    def set_axis(self, axis: str) -> None:
        """Expose the slice identity without a tooltip over the image."""
        self.axis = axis
        self.setAccessibleDescription(f"The full {axis} plane. Click, drag or use the arrow keys to move the view.")

    def image_rect(self) -> QRectF:
        available = QRectF(INSET, INSET, self.width() - 2 * INSET, self.height() - 2 * INSET)
        if self.image.isNull():
            return available
        size = self.image.size().scaled(available.size().toSize(), Qt.AspectRatioMode.KeepAspectRatio)
        return QRectF(available.center().x()-size.width()/2, available.center().y()-size.height()/2,
                      size.width(), size.height())

    def viewport_rect(self) -> QRectF:
        rect = self.image_rect()
        x0, y0, x1, y1 = self.region
        nx, ny = self.shape
        return QRectF(rect.x()+x0/nx*rect.width(), rect.y()+y0/ny*rect.height(),
                      (x1-x0)/nx*rect.width(), (y1-y0)/ny*rect.height())

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        card = QColor(CANVAS_CHROME.canvas)
        card.setAlpha(235)
        focused = self.hasFocus()
        painter.setPen(QPen(QColor(CANVAS_CHROME.accent if focused else CANVAS_CHROME.border_strong), 2 if focused else 1))
        painter.setBrush(card)
        painter.drawRoundedRect(QRectF(self.rect()).adjusted(1, 1, -1, -1), RADIUS_CARD, RADIUS_CARD)
        painter.drawImage(self.image_rect(), self.image)
        # The viewport: white over a dark edge, so it reads on bright and dark planes.
        viewport = self.viewport_rect()
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(QColor(CANVAS_CHROME.canvas), 3))
        painter.drawRect(viewport)
        painter.setPen(QPen(QColor(CANVAS_CHROME.text_strong), 1.5))
        painter.drawRect(viewport)

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self.close_button.setGeometry(self.width() - 28, 6, 22, 22)

    def navigate(self, point: QPointF) -> None:
        rect = self.image_rect()
        if rect.contains(point) and rect.width() > 0 and rect.height() > 0:
            self.centre_requested.emit((point.x()-rect.x())/rect.width()*self.shape[0],
                                       (point.y()-rect.y())/rect.height()*self.shape[1])

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self.setFocus(Qt.FocusReason.MouseFocusReason)
            self.navigate(event.position())
            event.accept()

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        if event.buttons() & Qt.MouseButton.LeftButton:
            self.navigate(event.position())
            event.accept()

    def keyPressEvent(self, event) -> None:  # noqa: N802
        steps = {Qt.Key.Key_Left: (-1, 0), Qt.Key.Key_Right: (1, 0),
                 Qt.Key.Key_Up: (0, -1), Qt.Key.Key_Down: (0, 1)}
        if event.key() in steps:
            dx, dy = steps[event.key()]
            x0, y0, x1, y1 = self.region
            self.centre_requested.emit(max(0., min(self.shape[0], (x0+x1)/2+dx*self.shape[0]/20)),
                                       max(0., min(self.shape[1], (y0+y1)/2+dy*self.shape[1]/20)))
            event.accept()
        else:
            super().keyPressEvent(event)


# Contextual keys handled by this module; consumed by the shared Help sheet.
LOCAL_KEYS = (
    LocalBinding('tomograms.navigator.navigator-left', 'Navigator', InteractionKind.KEYBOARD, ('Left', 'Right', 'Up', 'Down'), 'Move the view within the full current plane'),
)
