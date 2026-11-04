"""Accepted-plane histogram with draggable and keyboard-adjustable limits."""
from tomography_session_browser.presentation.help_content import LocalBinding, InteractionKind
import math
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QWidget
from tomography_session_browser.services.volume.models import Histogram
from tomography_session_browser.ui.theme import current_palette


class HistogramControl(QWidget):
    limits_changed = Signal(float, float)

    def __init__(self, parent) -> None:
        super().__init__(parent)
        self.setMinimumHeight(100)
        self.setMaximumHeight(140)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAccessibleName("Intensity histogram and black/white limits")
        self.setToolTip("The values in this plane. Drag B or W to set black or white. The Left and Right keys "
                        "move the selected line. Tab selects the other line.")
        self.data: Histogram | None = None
        self.black, self.white, self.handle = 0., 1., 0
        self.dragging = False

    def set_data(self, data: Histogram | None, black: float, white: float) -> None:
        self.data, self.black, self.white = data, black, white
        if data:
            self.setAccessibleDescription(f"Black {black:g}; white {white:g}; mean {data.mean:g}; standard deviation {data.std:g}.")
        else:
            self.setAccessibleDescription("")
        self.update()

    def x_for(self, value: float) -> float:
        assert self.data is not None
        return 12 + (self.width()-24) * max(0., min(1., (value-self.data.minimum) / max(1e-20, self.data.maximum-self.data.minimum)))

    def paintEvent(self, event) -> None:  # noqa: N802
        painter, palette = QPainter(self), current_palette()
        painter.fillRect(self.rect(), QColor(palette.surface_alt))
        if self.data is None:
            painter.setPen(QColor(palette.text_muted))
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "Histogram awaits a plane")
            return
        counts = self.data.counts
        maximum = max(counts, default=1) or 1
        path = QPainterPath()
        path.moveTo(12, self.height()-22)
        for index, count in enumerate(counts):
            path.lineTo(12 + index * (self.width()-24) / max(1, len(counts)-1),
                        self.height()-22 - (self.height()-38) * math.log1p(count) / math.log1p(maximum))
        path.lineTo(self.width()-12, self.height()-22)
        fill = QColor(palette.accent)
        fill.setAlpha(45)
        painter.fillPath(path, fill)
        painter.setPen(QPen(QColor(palette.accent), 1))
        painter.drawPath(path)
        painter.setPen(QColor(palette.text_muted))
        painter.drawText(self.rect().adjusted(8, 4, -8, 0), Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignRight, "Log counts")
        for index, value, label in ((0, self.black, "B"), (1, self.white, "W")):
            x = round(self.x_for(value))
            painter.setPen(QPen(QColor(palette.text_strong if self.handle == index else palette.text_muted), 2))
            painter.drawLine(x, 6, x, self.height()-19)
            painter.drawText(max(1, min(self.width()-15, x-4)), self.height()-4, label)

    def move_limit(self, value: float) -> None:
        if self.data is None:
            return
        epsilon = max(1e-12, (self.data.maximum-self.data.minimum) * 1e-6)
        if self.handle == 0:
            self.black = min(value, self.white-epsilon)
        else:
            self.white = max(value, self.black+epsilon)
        self.update()

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if self.data and event.button() == Qt.MouseButton.LeftButton:
            self.setFocus()
            x = event.position().x()
            self.handle = 0 if abs(x-self.x_for(self.black)) <= abs(x-self.x_for(self.white)) else 1
            self.dragging = True
            self.move_limit(self.data.minimum + max(0., min(1., (x-12)/max(1, self.width()-24))) * (self.data.maximum-self.data.minimum))

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        if self.dragging and self.data:
            self.move_limit(self.data.minimum + max(0., min(1., (event.position().x()-12)/max(1, self.width()-24))) * (self.data.maximum-self.data.minimum))

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if self.dragging:
            self.dragging = False
            self.limits_changed.emit(self.black, self.white)

    def keyPressEvent(self, event) -> None:  # noqa: N802
        if self.data and event.key() in (Qt.Key.Key_Left, Qt.Key.Key_Right):
            step = (self.data.maximum-self.data.minimum) / 128
            self.move_limit((self.black if self.handle == 0 else self.white) + step * (1 if event.key() == Qt.Key.Key_Right else -1))
            self.limits_changed.emit(self.black, self.white)
            event.accept()
        else:
            super().keyPressEvent(event)

    def focusNextPrevChild(self, next_: bool) -> bool:  # noqa: N802
        if (next_ and self.handle == 0) or (not next_ and self.handle == 1):
            self.handle = 1-self.handle
            self.update()
            return True
        self.handle = 0 if next_ else 1
        return super().focusNextPrevChild(next_)


# Contextual keys handled by this module; consumed by the shared Help sheet.
LOCAL_KEYS = (
    LocalBinding('tomograms.histogram.histogram-handle-left', 'Histogram handle', InteractionKind.KEYBOARD, ('Left', 'Right'), 'Move the selected black or white limit'),
)
