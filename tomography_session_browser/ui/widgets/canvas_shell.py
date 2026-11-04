"""A rounded canvas boundary that clips every child, including transition pictures."""
import math

from PySide6.QtCore import QEvent, QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPainterPath, QRegion
from PySide6.QtWidgets import QApplication, QWidget

from tomography_session_browser.presentation.design_tokens import CANVAS_CHROME, RADIUS_CARD
from tomography_session_browser.ui.theme import current_palette


def rounded_canvas_path(rect: QRectF, radius: float = RADIUS_CARD) -> QPainterPath:
    path = QPainterPath()
    path.addRoundedRect(rect, radius, radius)
    return path


def edge_width(device_pixel_ratio: float) -> float:
    """The edge's width in logical pixels: whole device pixels, at least one logical.

    One logical pixel is 1.5 device pixels at 150 %: a straight edge then fell
    across two rows, soft and heavier than its arc. Whole pixels keep it crisp
    and as wide round the corner as along the side, and cover the canvas's own
    1 px border band.
    """

    return math.ceil(device_pixel_ratio - 1e-6) / device_pixel_ratio


class CanvasFrame(QWidget):
    """The shell's rounded edge, drawn over everything in it.

    The mask clips to whole logical pixels, so on its own it leaves a stair-
    stepped corner (coarser still at 150 %), and a border drawn by the canvas
    itself is square: the mask cut it off short of every corner. This paints
    the page's background outside the true curve and, inside it, a ring of
    constant width on the same curve, accented while ``focus_source`` has focus.
    """

    def __init__(self, shell: QWidget) -> None:
        super().__init__(shell)
        self.setObjectName("viewerCanvasFrame")
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.focus_source: QWidget | None = None

    def focused(self) -> bool:
        return self.focus_source is not None and self.focus_source.hasFocus()

    def border_colour(self) -> QColor:
        return QColor(CANVAS_CHROME.accent if self.focused() else current_palette().border)

    def paintEvent(self, _event) -> None:  # noqa: N802 - Qt API
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        bounds = QRectF(self.rect())
        outside = QPainterPath()
        outside.addRect(bounds)
        outside = outside.subtracted(rounded_canvas_path(bounds))
        painter.fillPath(outside, QColor(current_palette().background))
        width = edge_width(self.devicePixelRatioF())
        ring = rounded_canvas_path(bounds).subtracted(
            rounded_canvas_path(bounds.adjusted(width, width, -width, -width), RADIUS_CARD - width))
        painter.fillPath(ring, self.border_colour())


class CanvasShell(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("viewerCanvasShell")
        self.frame = CanvasFrame(self)
        app = QApplication.instance()
        if isinstance(app, QApplication):
            app.focusChanged.connect(self._focus_changed)

    def set_focus_source(self, widget: QWidget) -> None:
        """The widget whose focus the border shows (the canvas)."""
        self.frame.focus_source = widget
        self.frame.update()

    def _focus_changed(self, old: QWidget | None, new: QWidget | None) -> None:
        source = self.frame.focus_source
        if source is not None and source in (old, new):
            self.frame.update()

    def childEvent(self, event) -> None:  # noqa: N802 - Qt API
        super().childEvent(event)
        # A child added later (a floating control) must not cover the edge.
        if event.type() == QEvent.Type.ChildAdded and event.child() is not getattr(self, "frame", None):
            frame = getattr(self, "frame", None)
            if frame is not None:
                frame.raise_()

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt API
        super().resizeEvent(event)
        self.clip_children()

    def showEvent(self, event) -> None:  # noqa: N802 - Qt API
        super().showEvent(event)
        self.clip_children()

    def clip_children(self) -> None:
        # The mask is whole pixels, rounded either way; a tighter corner keeps it
        # outside the frame's curve so the antialiased edge is never cut.
        path = QPainterPath()
        path.addRoundedRect(QRectF(self.rect()), RADIUS_CARD / 2, RADIUS_CARD / 2)
        self.setMask(QRegion(path.toFillPolygon().toPolygon()))
        self.frame.setGeometry(self.rect())
        self.frame.raise_()

