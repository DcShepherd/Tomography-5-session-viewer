"""Orientation inset: the volume box, its labelled axes and the slice plane inside it.

Drawn as the reviewed mockup draws it. The volume stays still in one
three-quarter view at its physical proportions; the slice plane tilts inside it,
in the active pane's colour, clipped to the volume exactly where the reslice
samples. A white arrow shows which way PageUp moves the plane; the pivot is a
dot, amber while locked. Each axis takes the colour of the plane it is normal to
(X coral, Y violet, Z blue). The inset is display chrome and never exported.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPen, QPolygonF
from PySide6.QtWidgets import QToolButton, QWidget

from tomography_session_browser.domain.tomograms import Volume
from tomography_session_browser.presentation.design_tokens import CANVAS_CHROME, RADIUS_CARD, TOMOGRAM_PLANE_COLOURS, TYPE_CAPTION
from tomography_session_browser.services.volume.models import RenderRecipe
from tomography_session_browser.services.volume.oblique import AXES, mapping
from tomography_session_browser.services.volume.render import calibrated_spacing

#: The inset's side, in logical pixels.
SIZE = 176
#: Each volume axis is drawn in the colour of the plane it is normal to.
AXIS_COLOURS = (TOMOGRAM_PLANE_COLOURS["YZ"], TOMOGRAM_PLANE_COLOURS["XZ"], TOMOGRAM_PLANE_COLOURS["XY"])


@dataclass(frozen=True)
class OrientationModel:
    """Where the displayed slice sits in the volume, in physical units (Å, or voxels if uncalibrated)."""

    extent: np.ndarray                 # The box: from the origin corner to the far corner.
    outline: tuple[np.ndarray, ...]    # The sampled slice, clipped to the box.
    centre: np.ndarray                 # The slice's centre, where the PageUp arrow starts.
    normal: np.ndarray                 # Unit vector: the direction PageUp moves the plane.
    pivot: np.ndarray


def round_pen(colour: str, width: float) -> QPen:
    pen = QPen(QColor(colour), width)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    return pen


def clip_to_box(polygon: list[np.ndarray], extent: np.ndarray) -> tuple[np.ndarray, ...]:
    """Sutherland–Hodgman against the box's six faces."""
    for axis in range(3):
        for limit, sign in ((0., 1.), (float(extent[axis]), -1.)):
            inside = [(point[axis] - limit) * sign >= 0 for point in polygon]
            clipped = []
            for index, point in enumerate(polygon):
                following = polygon[(index + 1) % len(polygon)]
                if inside[index]:
                    clipped.append(point)
                if inside[index] != inside[(index + 1) % len(polygon)]:
                    t = (limit - point[axis]) / (following[axis] - point[axis])
                    clipped.append(point + (following - point) * t)
            polygon = clipped
            if not polygon:
                return ()
    return tuple(polygon)


def orientation_model(volume: Volume | None, recipe: RenderRecipe | None) -> OrientationModel | None:
    """The displayed slice's place in the volume, from the same mapping the reslice uses."""
    if volume is None or volume.grid is None or recipe is None:
        return None
    spacing = calibrated_spacing(volume, recipe.spacing_override)
    scale = np.asarray(spacing or (1., 1., 1.), dtype=float)
    matrix, offset = mapping(volume, recipe.rotation, recipe.pivot_xyz, recipe.pivot_grid_xyz, spacing)
    shape = np.asarray(volume.grid.shape_xyz, dtype=float)
    h, v, n = AXES[recipe.axis]

    def physical(u: float, w: float) -> np.ndarray:
        reference = np.zeros(3)
        reference[h], reference[v], reference[n] = u, w, recipe.plane
        return (matrix @ reference + offset) * scale

    extent = (shape - 1) * scale
    last_u, last_w = shape[h] - 1, shape[v] - 1
    outline = clip_to_box([physical(0, 0), physical(last_u, 0), physical(last_u, last_w), physical(0, last_w)], extent)
    normal = matrix[:, n] * scale
    pivot = np.asarray(recipe.pivot_xyz if recipe.pivot_xyz is not None else (shape - 1) / 2, dtype=float) * scale
    return OrientationModel(extent, outline, physical(last_u / 2, last_w / 2), normal / np.linalg.norm(normal), pivot)


class OrientationBox(QWidget):
    rotated = Signal(float, float)
    reset_requested = Signal()
    dismissed = Signal()

    def __init__(self, parent):
        super().__init__(parent)
        self.rotation = (0., 0., 0.)
        self.axis = "XY"
        self.locked = False
        self.volume: Volume | None = None
        self.recipe: RenderRecipe | None = None
        self.drag = None
        self.setAccessibleName("Volume orientation; drag to rotate, double-click to zero")
        self.setAccessibleDescription("The plane in the volume. The arrow shows the PageUp direction. The dot is the pivot, amber "
                        "when locked. Drag to tilt the plane. Double-click to set the angles to zero.")
        self.setCursor(Qt.CursorShape.OpenHandCursor)
        self.close_button = QToolButton(self)
        self.close_button.setObjectName("tomogramInsetClose")
        self.close_button.setText("×")
        self.close_button.setAccessibleName("Hide orientation box")
        self.close_button.setToolTip("Hide this box. Press B to show it again.")
        self.close_button.setCursor(Qt.CursorShape.ArrowCursor)
        self.close_button.clicked.connect(self.dismissed)
        self.hide()

    def resizeEvent(self, event):  # noqa: N802
        self.close_button.setGeometry(self.width() - 28, 6, 22, 22)

    def project(self, point: np.ndarray, extent: np.ndarray, scale: float) -> QPointF:
        """The mockup's fixed three-quarter view: X right and down, Y toward the viewer, Z up."""
        q = point - extent / 2
        x = q[0] * .87 - q[1] * .5
        y = -q[2] + q[0] * .29 + q[1] * .5
        return QPointF(self.width() / 2 + x * scale, self.height() / 2 + 6 + y * scale)

    def paintEvent(self, event):  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        card = QColor(CANVAS_CHROME.canvas)
        card.setAlpha(235)
        painter.setPen(QPen(QColor(CANVAS_CHROME.border_strong), 1))
        painter.setBrush(card)
        painter.drawRoundedRect(QRectF(self.rect()).adjusted(.5, .5, -.5, -.5), RADIUS_CARD, RADIUS_CARD)
        model = orientation_model(self.volume, self.recipe)
        if model is None or not np.all(np.isfinite(model.extent)) or model.extent.max() <= 0:
            return
        extent = model.extent
        scale = min(self.width(), self.height()) * .42 * 1.15 / float(extent.max())
        point = lambda value: self.project(np.asarray(value, dtype=float), extent, scale)  # noqa: E731
        # The volume box.
        corners = [np.array((x, y, z)) * extent for x in (0, 1) for y in (0, 1) for z in (0, 1)]
        painter.setPen(QPen(QColor(CANVAS_CHROME.control_border), 1))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        for index, a in enumerate(corners):
            for b in corners[index + 1:]:
                if np.count_nonzero(a != b) == 1:
                    painter.drawLine(point(a), point(b))
        # Labelled axes from the origin corner.
        font = QFont(painter.font())
        font.setPointSizeF(TYPE_CAPTION)
        font.setBold(True)
        painter.setFont(font)
        origin = point((0, 0, 0))
        for index, label in enumerate("XYZ"):
            tip = np.zeros(3)
            tip[index] = extent[index]
            end = point(tip)
            painter.setPen(round_pen(AXIS_COLOURS[index], 2))
            painter.drawLine(origin, end)
            direction = end - origin
            length = max(1e-6, (direction.x() ** 2 + direction.y() ** 2) ** .5)
            centre = end + direction / length * 10
            painter.drawText(QRectF(centre.x() - 8, centre.y() - 8, 16, 16), Qt.AlignmentFlag.AlignCenter, label)
        # The slice plane, clipped to the volume, in the active pane's colour.
        if len(model.outline) >= 3:
            colour = QColor(TOMOGRAM_PLANE_COLOURS.get(self.axis, TOMOGRAM_PLANE_COLOURS["XY"]))
            fill = QColor(colour)
            fill.setAlpha(90)
            painter.setPen(QPen(colour, 1.5))
            painter.setBrush(fill)
            painter.drawPolygon(QPolygonF([point(corner) for corner in model.outline]))
        # Which way PageUp goes: the plane's normal, from its centre.
        start = point(model.centre)
        end = point(model.centre + model.normal * extent.max() * .28)
        painter.setPen(round_pen(CANVAS_CHROME.text_strong, 1.5))
        painter.drawLine(start, end)
        direction = end - start
        length = (direction.x() ** 2 + direction.y() ** 2) ** .5
        if length > 1:
            unit = direction / length
            side = QPointF(-unit.y(), unit.x())
            painter.setBrush(QColor(CANVAS_CHROME.text_strong))
            painter.drawPolygon(QPolygonF([end, end - unit * 6 + side * 3.5, end - unit * 6 - side * 3.5]))
        # The pivot: amber while locked.
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(CANVAS_CHROME.chart_amber if self.locked else CANVAS_CHROME.text_strong))
        painter.drawEllipse(point(model.pivot), 4, 4)

    def mousePressEvent(self, event):  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self.drag = event.position()
            self.setCursor(Qt.CursorShape.ClosedHandCursor)

    def mouseMoveEvent(self, event):  # noqa: N802
        if self.drag is not None:
            delta = event.position()-self.drag
            self.drag = event.position()
            self.rotated.emit(delta.x()*.3, delta.y()*.3)

    def mouseReleaseEvent(self, event):  # noqa: N802
        self.drag = None
        self.setCursor(Qt.CursorShape.OpenHandCursor)

    def mouseDoubleClickEvent(self, event):  # noqa: N802
        self.reset_requested.emit()
