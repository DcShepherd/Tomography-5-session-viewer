"""One geometry and event owner for each review row's capture affordance."""
from PySide6.QtCore import QEvent, QObject, QRect, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QStyle, QToolTip

from tomography_session_browser.presentation.design_tokens import TYPE_CAPTION, RADIUS_CONTROL
from tomography_session_browser.ui.icons import themed_icon
from tomography_session_browser.ui.theme import current_palette


class CaptureColumn(QObject):
    requested = Signal(str)

    def __init__(self, listing) -> None:
        super().__init__(listing)
        self.listing = listing
        self.counts = {}
        self.failures = frozenset()
        self.refreshing = False
        self.available = True
        self.error = ""
        self.width = 0
        self.hover_key = None
        self.pressed = False
        self.open_key: str | None = None
        self.icon = themed_icon("camera", size=14)
        listing.tree.viewport().installEventFilter(self)

    def set_counts(self, counts, failures, *, available=True, refreshing=False, error="") -> None:
        failures = frozenset(failures)
        if counts is self.counts and (failures, available, refreshing, error) == (self.failures, self.available, self.refreshing, self.error):
            return
        self.counts, self.failures, self.refreshing, self.error = counts, failures, refreshing, error
        self.available = available
        self.reflow()

    def reflow(self) -> None:
        values = [self.counts.get(key, 0) for key in self.listing.ordered_keys
                  if self.counts.get(key, 0) or key in self.failures]
        font = QFont(self.listing.tree.font())
        font.setPointSizeF(TYPE_CAPTION)
        metrics = QFontMetrics(font)
        self.width = 36 + max((metrics.horizontalAdvance(str(value) if self.available else "…") for value in values), default=0) if values else 0
        self.listing.tree.viewport().update()

    def rect(self, row_rect) -> QRect:
        height = min(20, row_rect.height())
        return QRect(row_rect.right() + 1 - self.width - 8, row_rect.center().y() - height // 2, self.width, height)

    def set_open(self, key: str | None) -> None:
        self.open_key = key
        if key is None:
            self.pressed = False
        self.listing.tree.viewport().update()

    def paint(self, painter, option, key: str) -> None:
        if not self.width:
            return
        count = self.counts.get(key, 0)
        if not count and key not in self.failures:
            return
        rect = self.rect(option.rect)
        palette = current_palette()
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        active = key == self.open_key or (self.pressed and key == self.hover_key)
        if active or key == self.hover_key:
            fill = QColor(palette.text_strong)
            fill.setAlphaF(.16 if active else .09)
            painter.setBrush(fill)
            painter.setPen(QPen(QColor(palette.accent), 1) if active else Qt.PenStyle.NoPen)
            painter.drawRoundedRect(rect.adjusted(0, 0, -1, -1), RADIUS_CONTROL, RADIUS_CONTROL)
        self.icon.paint(painter, QRect(rect.left() + 6, rect.center().y() - 7, 14, 14))
        painter.setPen(QColor(palette.text_strong if option.state & QStyle.StateFlag.State_Selected else palette.text_muted))
        font = QFont(option.font)
        font.setPointSizeF(TYPE_CAPTION)
        font.setWeight(QFont.Weight.Normal)
        painter.setFont(font)
        painter.drawText(rect.adjusted(24, 0, -6, 0), Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight,
                         str(count) if self.available else "…")
        if key in self.failures:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(palette.chart_amber))
            painter.drawEllipse(rect.left() + 16, rect.center().y() - 8, 5, 5)
        painter.restore()

    def key_at(self, point):
        item = self.listing.tree.itemAt(point)
        key = item.data(0, Qt.ItemDataRole.UserRole) if item is not None else None
        if (isinstance(key, str) and self.width and
                (self.counts.get(key, 0) or key in self.failures) and
                self.contains(self.rect(self.listing.tree.visualItemRect(item)), point)):
            return key
        return None

    @staticmethod
    def contains(rect, point) -> bool:
        shape = QPainterPath()
        shape.addRoundedRect(QRectF(rect), RADIUS_CONTROL, RADIUS_CONTROL)
        return shape.contains(point)

    def eventFilter(self, watched, event) -> bool:  # noqa: N802
        if event.type() == QEvent.Type.FontChange:
            self.reflow()
        elif event.type() in (QEvent.Type.MouseMove, QEvent.Type.MouseButtonPress):
            key = self.key_at(event.position().toPoint())
            if event.type() == QEvent.Type.MouseButtonPress:
                self.pressed = False
            if event.type() == QEvent.Type.MouseButtonPress and event.button() == Qt.MouseButton.LeftButton and key:
                self.hover_key = key
                self.pressed = True
                watched.update()
                self.requested.emit(key)
                return True
            if key != self.hover_key:
                self.hover_key = key
                watched.setCursor(Qt.CursorShape.PointingHandCursor if key else Qt.CursorShape.ArrowCursor)
                watched.update()
        elif event.type() == QEvent.Type.MouseButtonRelease and self.pressed:
            self.pressed = False
            watched.update()
            return True
        elif event.type() == QEvent.Type.ToolTip:
            key = self.key_at(event.pos())
            if key:
                message = f"Show the {self.counts.get(key, 0)} captures of this tomogram." if self.available else "Capture count unavailable. Open capture history."
                if key in self.failures:
                    message += " Snapshots need recovery."
                if self.refreshing:
                    message += " Refreshing; this is the last known count." if self.available else " Loading capture history."
                if self.error:
                    message += " Refresh unavailable. Open All captures and use Refresh."
                QToolTip.showText(event.globalPos(), message, watched)
                return True
        elif event.type() == QEvent.Type.Leave:
            self.hover_key = None
            watched.unsetCursor()
            watched.update()
        return super().eventFilter(watched, event)
