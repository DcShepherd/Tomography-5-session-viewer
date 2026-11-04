"""Timed elements cut from one outgoing picture, or filled with a colour."""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPixmap, QRegion
from tomography_session_browser.ui.motion.easing import EASE_STANDARD


@dataclass(frozen=True, slots=True)
class RectMotion:
    start: QRectF
    end: QRectF

    def at(self, progress: float) -> QRectF:
        p = max(0., min(1., progress))
        return QRectF(*(a + (b-a)*p for a, b in zip(
            self.start.getRect(), self.end.getRect(), strict=True)))


@dataclass(frozen=True, slots=True)
class OpacityWindow:
    start: float = 0.
    end: float = 1.
    from_: float = 1.
    to: float = 1.

    def at(self, progress: float) -> float:
        p = max(0., min(1., (progress-self.start) / (self.end-self.start)))
        return self.from_ + (self.to-self.from_)*p


@dataclass(frozen=True, slots=True)
class PictureElement:
    source: QRectF | str
    motion: RectMotion
    opacity: OpacityWindow = OpacityWindow()
    easing: Callable[[float], float] = EASE_STANDARD


@dataclass(frozen=True, slots=True)
class PictureLayout:
    elements: tuple[PictureElement, ...]
    reveal_ms: int = 0

    def covers(self, bounds: QRectF, progress: float) -> bool:
        """An opaque fill hides every pixel now, so nothing beneath needs painting."""
        return any(isinstance(element.source, str) and QColor(element.source).alpha() == 255
                   and element.opacity.at(progress) == 1
                   and element.motion.at(element.easing(min(1., progress))).contains(bounds)
                   for element in self.elements)

    def damage(self, before: float, after: float) -> QRegion:
        """Cover old and new pixels, including disappearing fractional edges."""
        region = QRegion()
        for element in self.elements:
            old_alpha, new_alpha = element.opacity.at(before), element.opacity.at(after)
            if old_alpha == new_alpha == 0:
                continue
            old = element.motion.at(element.easing(min(1., before)))
            new = element.motion.at(element.easing(min(1., after)))
            if old != new or old_alpha != new_alpha:
                region |= QRegion(old.united(new).adjusted(-1, -1, 1, 1).toAlignedRect())
        return region

    def paint(self, painter: QPainter, bounds: QRectF, picture: QPixmap, progress: float) -> None:
        """Time is linear; each element owns its easing and opacity window."""
        painter.save()
        painter.setClipRect(bounds, Qt.ClipOperation.IntersectClip)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        ratio = picture.devicePixelRatioF()
        for element in self.elements:
            opacity = element.opacity.at(progress)
            if opacity == 0:
                continue
            painter.setOpacity(opacity)
            target = element.motion.at(element.easing(min(1., progress)))
            source = element.source
            if isinstance(source, str):
                painter.fillRect(target, QColor(source))
            elif not target.isEmpty():
                painter.drawPixmap(target, picture, QRectF(source.x()*ratio, source.y()*ratio,
                                                          source.width()*ratio, source.height()*ratio))
        painter.restore()


def fit_rect(source: QRectF, bounds: QRectF) -> QRectF:
    """Uniformly fit outgoing pixels, keeping their centre and aspect ratio."""
    if source.isEmpty():
        return QRectF(bounds.center(), source.size())
    scale = min(bounds.width()/source.width(), bounds.height()/source.height())
    result = QRectF(0, 0, source.width()*scale, source.height()*scale)
    result.moveCenter(bounds.center())
    return result
