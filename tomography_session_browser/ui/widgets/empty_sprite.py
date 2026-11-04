"""Bounded decorative raster: six masters and one DPR-aware scaled pixmap."""
from functools import lru_cache
from pathlib import Path

from PySide6.QtCore import QEvent, Qt
from PySide6.QtGui import QImage, QPainter, QPixmap
from PySide6.QtWidgets import QWidget


@lru_cache(maxsize=6)
def master(name: str) -> QImage:
    return QImage(str(Path(__file__).resolve().parents[2] / "assets" / "sprites" / f"{name}.png"))


class EmptySprite(QWidget):
    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.name = ""
        self.logical_side = 112
        self.requested_side = 112
        self.cache_key: tuple = ()
        self.pixmap = QPixmap()
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)

    def configure(self, name: str, side: int) -> None:
        self.requested_side = side
        image = master(name) if name else QImage()
        dpr = max(1., self.devicePixelRatioF())
        self.name = name
        self.logical_side = side = min(side, max(1, int(max(image.width(), image.height()) / dpr))) if name else side
        self.setFixedSize(side, side)
        self.setVisible(bool(name))
        self.update()

    def event(self, event: QEvent) -> bool:
        if event.type() == QEvent.Type.DevicePixelRatioChange:
            self.configure(self.name, self.requested_side)
            self.cache_key = ()
            self.update()
        return super().event(event)

    def paintEvent(self, event) -> None:  # noqa: N802
        if not self.name:
            return
        dpr = self.devicePixelRatioF()
        key = self.name, self.logical_side, dpr
        if self.cache_key != key:
            image = master(self.name)
            bound = min(max(image.width(), image.height()), max(1, round(self.logical_side * dpr)))
            self.pixmap = QPixmap.fromImage(image.scaled(bound, bound, Qt.AspectRatioMode.KeepAspectRatio,
                                                       Qt.TransformationMode.SmoothTransformation))
            self.pixmap.setDevicePixelRatio(dpr)
            self.cache_key = key
        painter = QPainter(self)
        painter.drawPixmap(0, round(self.height() - self.pixmap.height() / dpr), self.pixmap)
