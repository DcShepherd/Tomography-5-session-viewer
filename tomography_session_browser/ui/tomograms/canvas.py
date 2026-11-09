"""Volume pixels only: viewport input and paint, without marker/raster APIs."""
from __future__ import annotations

from PySide6.QtCore import QEvent, QPoint, QRect, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QImage, QPainter, QColor, QPen
from PySide6.QtWidgets import QWidget
from tomography_session_browser.presentation.design_tokens import CANVAS_CHROME, TOMOGRAM_PLANE_COLOURS
from tomography_session_browser.services.volume.models import RenderResult
from tomography_session_browser.ui.tomograms.navigator import INSET as NAVIGATOR_INSET, SliceNavigator
from tomography_session_browser.ui.tomograms.orientation import SIZE as ORIENTATION_SIZE, OrientationBox
from tomography_session_browser.ui.widgets.wheel_notches import WheelNotches


class VolumeCanvas(QWidget):
    viewport_changed = Signal()
    zoom_requested = Signal(float)
    pan_requested = Signal(float, float)
    cursor_moved = Signal(float, float)
    activated = Signal()
    hovered = Signal()
    scrub_requested = Signal(object)
    intersection_requested = Signal(float, float)
    navigator_requested = Signal(float, float)
    navigator_dismissed = Signal()
    rotation_requested = Signal(float, float, bool)
    bar_moved = Signal(float, float)

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setObjectName("volumeCanvas")
        # paintEvent fills the whole rectangle first, so Qt need not repaint the
        # styled backgrounds behind the canvas every time it changes.
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent, True)
        self.setMinimumSize(120, 120)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAccessibleName("Tomogram XY canvas")
        self.setMouseTracking(True)
        self.image: QImage | None = None
        self.position = (0, 0)
        self.region: tuple[float, float, float, float] | None = None
        self.drag: QPoint | None = None
        self.press: QPoint | None = None
        self.rotating = False
        self.rolling = False
        self.bar_drag: tuple[QPoint, QRect] | None = None
        self.bar_preview: QRect | None = None
        self.hover_pending = True
        self.notches = WheelNotches()  # Shift + wheel steps whole planes.
        self.axis = "XY"
        self.active = True
        self.ortho = False
        self.frame_owner: object | None = None
        self.crosshair: tuple[float, float] | None = None
        self.navigator_enabled = True
        self.navigator = SliceNavigator(self)
        self.navigator.centre_requested.connect(self.navigator_requested)
        self.navigator.dismissed.connect(self.navigator_dismissed)
        self.orientation = OrientationBox(self)
        self.orientation_enabled = False
        self.pivot_point: tuple[float, float] | None = None
        self.overlay_state: tuple | None = None
        self.overlay_sources: tuple = (None, None)
        self.annotations_visible = True

    def sync_orientation(self) -> None:
        # Every rotation input reaches here, but the overlays show the displayed
        # frame. Raising and repainting them when nothing they show has changed
        # repaints part of the canvas on every input.
        box = self.orientation
        state = (self.size(), self.active, self.ortho, self.orientation_enabled, self.navigator_enabled, self.annotations_visible,
                 box.rotation, box.axis, box.recipe, box.locked, self.region, self.bar_rect())
        sources = (box.volume, self.frame_owner)
        if state == self.overlay_state and all(new is old for new, old in zip(sources, self.overlay_sources)):
            return
        self.overlay_state, self.overlay_sources = state, sources
        rect = self.inset_rect(ORIENTATION_SIZE, top=True)
        self.orientation.setGeometry(rect)
        self.orientation.setVisible(self.annotations_visible and not rect.isEmpty() and self.orientation_enabled and self.active and min(self.width(), self.height()) >= 240)
        self.orientation.raise_()
        self.orientation.update()
        self.sync_navigator()

    def accept_image(self, image: QImage, position: tuple[int, int], region: tuple[float, float, float, float], owner: RenderResult | None = None) -> None:
        self.image, self.position, self.region = image, position, region
        if owner is not None and owner.navigator is not None:
            pixels = owner.navigator
            self.navigator.image = QImage(pixels.data, pixels.shape[1], pixels.shape[0], pixels.strides[0], QImage.Format.Format_Grayscale8).copy()
        else:
            self.navigator.image = QImage()
        self.frame_owner = owner
        self.navigator.region = region
        self.navigator.set_axis(self.axis)
        self.sync_navigator()
        self.update()

    def sync_navigator(self) -> None:
        nav = self.navigator
        nx, ny = nav.shape
        cropped = self.region is not None and (self.region[0] > .01 or self.region[1] > .01 or
                  self.region[2] < nx-.01 or self.region[3] < ny-.01)
        nav.setVisible(self.annotations_visible and self.navigator_enabled and self.active and cropped and not nav.image.isNull() and min(self.width(), self.height()) >= 240)
        size = min(166, max(120, round(min(self.width(), self.height()) * .32)))
        # The card follows the slice's shape, so a tall slice leaves no dark side margins.
        iw, ih = (nav.image.width(), nav.image.height()) if not nav.image.isNull() else nav.shape
        inner = size - 2 * NAVIGATOR_INSET
        short = max(60, round(inner * min(iw, ih) / max(iw, ih, 1)) + 2 * NAVIGATOR_INSET)
        nav.setGeometry(self.inset_rect(size if iw >= ih else short, top=False, height=short if iw >= ih else size))
        if nav.geometry().isEmpty():
            nav.hide()
        nav.raise_()
        nav.update()

    def image_size(self) -> QSize:
        """Geometry stays in logical pixels; the raster may have a higher DPI."""
        if isinstance(self.frame_owner, RenderResult):
            return QSize(*self.frame_owner.recipe.output_size)
        return self.image.deviceIndependentSize().toSize() if self.image is not None else QSize()

    def event(self, event) -> bool:
        result = super().event(event)
        if event.type() == QEvent.Type.DevicePixelRatioChange:
            self.notches.reset()
            self.viewport_changed.emit()
        return result

    def bar_rect(self) -> QRect:
        owner = self.frame_owner
        if not isinstance(owner, RenderResult) or owner.bar_bounds is None:
            return QRect()
        x, y, w, h = owner.bar_bounds
        sx = owner.recipe.output_size[0] / owner.pixels.shape[1]
        sy = owner.recipe.output_size[1] / owner.pixels.shape[0]
        return QRectF(self.position[0] + x*sx, self.position[1] + y*sy, w*sx, h*sy).toAlignedRect()

    def inset_rect(self, size: int, *, top: bool, height: int | None = None) -> QRect:
        """Keep image chrome clear of the annotation, including dragged bars."""
        height = size if height is None else height
        corners = ((1, 0), (0, 0), (1, 1), (0, 1)) if top else ((1, 1), (0, 1), (0, 0), (1, 0))
        for right, bottom in corners:
            rect = QRect(self.width()-size-10 if right else 10, self.height()-height-10 if bottom else 10, size, height)
            if not rect.intersects(self.bar_rect()) and (top or not self.orientation.isVisible() or not rect.intersects(self.orientation.geometry())):
                return rect
        return QRect()  # No free corner: keep the scientific annotation visible.

    def clear(self) -> None:
        self.hover_pending = True
        self.notches.reset()
        self.bar_drag = self.bar_preview = None
        self.unsetCursor()
        self.image, self.region = None, None
        self.navigator.image = QImage()
        self.frame_owner = None
        self.crosshair = None
        self.navigator.hide()
        self.orientation.hide()
        self.overlay_state = None  # The overlays were hidden directly; the next sync restores them.
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor(CANVAS_CHROME.background))
        if self.image is not None:
            # Explicit logical bounds keep fractional-DPI rounding out of cursor,
            # crosshair and scale-bar geometry. Pixels are rendered at screen DPI.
            painter.drawImage(QRect(*self.position, self.image_size().width(), self.image_size().height()), self.image)
            if not self.annotations_visible:
                return
            if self.pivot_point and self.region:
                x0, y0, x1, y1 = self.region
                u, v = self.pivot_point
                if x0 <= u < x1 and y0 <= v < y1:
                    px = self.position[0]+(u-x0)*self.image_size().width()/(x1-x0)
                    py = self.position[1]+(v-y0)*self.image_size().height()/(y1-y0)
                    painter.setPen(QPen(QColor(CANVAS_CHROME.accent), 2))
                    painter.drawEllipse(QPoint(round(px), round(py)), 5, 5)
            if self.crosshair and self.region:
                x0, y0, x1, y1 = self.region
                u, v = self.crosshair
                px = self.position[0] + (u-x0) * self.image_size().width() / (x1-x0)
                py = self.position[1] + (v-y0) * self.image_size().height() / (y1-y0)
                vertical, horizontal = {"XY": ("YZ", "XZ"), "XZ": ("YZ", "XY"), "YZ": ("XY", "XZ")}[self.axis]
                if x0 <= u < x1:
                    painter.setPen(QPen(QColor(TOMOGRAM_PLANE_COLOURS[vertical]), 1, Qt.PenStyle.DashLine))
                    painter.drawLine(round(px), self.position[1], round(px), self.position[1]+self.image_size().height())
                if y0 <= v < y1:
                    painter.setPen(QPen(QColor(TOMOGRAM_PLANE_COLOURS[horizontal]), 1, Qt.PenStyle.DashLine))
                    painter.drawLine(self.position[0], round(py), self.position[0]+self.image_size().width(), round(py))
        if not self.annotations_visible:
            return
        if self.bar_preview is not None:
            painter.setPen(QPen(QColor(CANVAS_CHROME.text_strong), 1, Qt.PenStyle.DashLine))
            painter.drawRect(self.bar_preview)
        if not self.ortho:
            return  # Single: one pane is the snapshot pane; no outline or tag to repeat that.
        painter.setPen(QColor(TOMOGRAM_PLANE_COLOURS[self.axis]))
        painter.drawRect(self.rect().adjusted(0, 0, -1, -1))
        if self.active:
            painter.setPen(QPen(QColor(CANVAS_CHROME.accent), 2))
            painter.drawRect(self.rect().adjusted(2, 2, -2, -2))
        if getattr(self, "hide_tag", False):
            return
        metrics = painter.fontMetrics()
        tag, note = self.axis, " · snapshot" if self.active else ""
        painter.fillRect(4, 4, metrics.horizontalAdvance(tag + note) + 12, metrics.height() + 4,
                         QColor(CANVAS_CHROME.background))
        painter.setPen(QColor(TOMOGRAM_PLANE_COLOURS[self.axis]))
        painter.drawText(10, 6 + metrics.ascent(), tag)
        if note:
            painter.setPen(QColor(CANVAS_CHROME.accent))
            painter.drawText(10 + metrics.horizontalAdvance(tag), 6 + metrics.ascent(), note)

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self.notches.reset()
        self.sync_navigator()
        self.sync_orientation()
        self.viewport_changed.emit()

    def wheelEvent(self, event) -> None:  # noqa: N802
        if event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
            self.scrub_requested.emit(event)
            event.accept()  # A blocked scrub must not bubble and pause playback or scroll a parent.
            return
        self.notches.reset()
        self.zoom_requested.emit(1.2 if event.angleDelta().y() > 0 else 1 / 1.2)
        event.accept()

    def enterEvent(self, event) -> None:  # noqa: N802
        self.hover_pending = True
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802
        self.hover_pending = True
        self.notches.reset()
        super().leaveEvent(event)

    def mousePressEvent(self, event) -> None:  # noqa: N802
        self.notches.reset()
        if self.active and event.button() == Qt.MouseButton.LeftButton and not event.modifiers() and self.bar_rect().contains(event.position().toPoint()):
            self.bar_drag = (event.position().toPoint(), self.bar_rect())
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
            event.accept()
            return
        modifiers = event.modifiers()
        self.rotating = bool((modifiers & Qt.KeyboardModifier.ControlModifier and event.button() == Qt.MouseButton.LeftButton) or
                             (modifiers & Qt.KeyboardModifier.ShiftModifier and event.button() in (Qt.MouseButton.MiddleButton, Qt.MouseButton.RightButton)))
        self.rolling = bool(modifiers & Qt.KeyboardModifier.ShiftModifier and (event.button() == Qt.MouseButton.RightButton or modifiers & Qt.KeyboardModifier.ControlModifier))
        if event.button() == Qt.MouseButton.LeftButton or self.rotating:
            self.activated.emit()
            self.drag = event.position().toPoint()
            self.press = self.drag
            self.setFocus(Qt.FocusReason.MouseFocusReason)
            event.accept()

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        point = event.position().toPoint()
        if self.hover_pending and not event.buttons() and self.drag is None and self.bar_drag is None:
            self.hovered.emit()
            self.hover_pending = not self.active
        if self.bar_drag is not None:
            start, rect = self.bar_drag
            if self.image is not None:
                x = max(self.position[0]+12, min(self.position[0]+self.image_size().width()-12-rect.width(), rect.x()+point.x()-start.x()))
                y = max(self.position[1]+12, min(self.position[1]+self.image_size().height()-12-rect.height(), rect.y()+point.y()-start.y()))
                self.bar_preview = QRect(x, y, rect.width(), rect.height())
                self.update()
            event.accept()
            return
        if self.drag is not None:
            delta = point - self.drag
            self.drag = point
            if self.rotating:
                self.rotation_requested.emit(delta.x()*.3, delta.y()*.3, self.rolling)
            else:
                self.pan_requested.emit(delta.x(), delta.y())
        if self.image is not None and self.region is not None:
            x0, y0, x1, y1 = self.region
            x, y = point.x() - self.position[0], point.y() - self.position[1]
            if 0 <= x < self.image_size().width() and 0 <= y < self.image_size().height():
                self.cursor_moved.emit(x0 + x * (x1 - x0) / self.image_size().width(),
                                       y0 + y * (y1 - y0) / self.image_size().height())

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if self.bar_drag is not None:
            start, rect = self.bar_drag
            self.bar_drag = None
            self.bar_preview = None
            self.unsetCursor()
            self.update()
            if self.image is not None:
                point = rect.topLeft() + event.position().toPoint() - start - QPoint(*self.position) - QPoint(12, 12)
                self.bar_moved.emit(max(0., min(1., point.x()/max(1, self.image_size().width()-24-rect.width()))),
                                    max(0., min(1., point.y()/max(1, self.image_size().height()-24-rect.height()))))
            event.accept()
            return
        if self.press is not None and not self.rotating and (event.position().toPoint()-self.press).manhattanLength() < 3:
            self.mouseDoubleClickEvent(event)
        self.drag = None
        self.press = None
        self.rotating = False

    def mouseDoubleClickEvent(self, event) -> None:  # noqa: N802
        if self.image is None or self.region is None:
            return
        x, y = event.position().x() - self.position[0], event.position().y() - self.position[1]
        if 0 <= x < self.image_size().width() and 0 <= y < self.image_size().height():
            x0, y0, x1, y1 = self.region
            self.intersection_requested.emit(x0 + x * (x1 - x0) / self.image_size().width(),
                                              y0 + y * (y1 - y0) / self.image_size().height())
