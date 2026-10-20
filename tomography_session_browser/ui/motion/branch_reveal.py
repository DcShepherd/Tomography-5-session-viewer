"""Branch reveal components for the motion."""
from __future__ import annotations
import math
from PySide6.QtCore import QPoint, QRect, QRectF, Qt
from PySide6.QtGui import QPainter, QPixmap
from PySide6.QtWidgets import QWidget
from tomography_session_browser.ui.motion.spring import Spring
from tomography_session_browser.ui.motion.tokens import MOTION_PANEL, SpringSpec


class BranchReveal(QWidget):
    """Rows opening or closing under a tree row (plan F5.6).

    The tree expands or collapses at once; this covers the viewport from the
    toggled row down with two pictures of it, open and closed. The open rows
    are uncovered in place while the rows that follow ride down from under
    the toggled row (or back up over them). It is opaque, so each frame is two
    blits, and clicks pass through. Qt's own animation was rejected: it
    swallows clicks on other rows while it runs.
    """

    def __init__(
        self,
        viewport: QWidget,
        top: int,
        opened: QPixmap,
        closed: QPixmap,
        extent: int,
        *,
        opening: bool,
        spec: SpringSpec = MOTION_PANEL,
    ) -> None:
        super().__init__(viewport)
        self.setObjectName("branchReveal")
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent)
        self.setGeometry(0, top, viewport.width(), viewport.height() - top)
        self._top = top
        self._opened = opened
        self._closed = closed
        self._extent = float(max(0, min(extent, self.height())))
        start = 0.0 if opening else self._extent
        self._shown = start
        self._offset = Spring(
            start,
            spec=spec,
            on_update=self._moved,
            on_settled=self._finish,
            widget=self,
            settle_distance=0.5,
            settle_velocity=20.0,
        )
        self.destroyed.connect(
            lambda *_args, spring=self._offset: spring.reset(spring.value) if spring.is_animating else None
        )
        self.show()
        self.raise_()
        self._offset.set_target(self._extent if opening else 0.0)
        if not self._offset.is_animating:  # nothing to travel, or motion is off
            self._finish()

    @property
    def offset(self) -> float:
        """How far the following rows sit below the toggled row."""

        return self._offset.value

    def dismiss(self) -> None:
        self._offset.reset(self._offset.value)
        self._finish()

    def source_at(self, position: QPoint) -> tuple[bool, QPoint]:
        """Which picture and source coordinate paints this viewport position."""
        ratio = self._opened.devicePixelRatio() or 1.0
        offset = round(self._offset.value * ratio) / ratio
        opened = position.y() < self._top + offset
        return opened, QPoint(position.x(), position.y() if opened else round(position.y() - offset))

    def _moved(self, value: float) -> None:
        low = min(self._shown, value)
        self._shown = value
        top = max(0, math.floor(low) - 1)
        self.update(QRect(0, top, self.width(), self.height() - top))

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt API
        ratio = self._opened.devicePixelRatio() or 1.0
        source_top = round(self._top * ratio)
        rows = max(0, min(self._opened.height(), self._closed.height()) - source_top)
        offset = max(0, min(rows, round(self._offset.value * ratio)))
        painter = QPainter(self)
        # Opaque widget: nothing is painted beneath it. Viewport pictures are
        # opaque natively; the fill only guards a style that leaves gaps.
        painter.fillRect(event.rect(), self.palette().color(self.backgroundRole()))
        width = float(self.width())
        pixel_width = float(self._opened.width())
        if offset:
            painter.drawPixmap(
                QRectF(0.0, 0.0, width, offset / ratio),
                self._opened,
                QRectF(0.0, float(source_top), pixel_width, float(offset)),
            )
        if rows - offset:
            painter.drawPixmap(
                QRectF(0.0, offset / ratio, width, (rows - offset) / ratio),
                self._closed,
                QRectF(0.0, float(source_top), pixel_width, float(rows - offset)),
            )

    def _finish(self) -> None:
        self.hide()
        self.deleteLater()

