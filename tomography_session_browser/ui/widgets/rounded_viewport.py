"""Keep a scroll area's square viewport inside its rounded border.

The style sheet gives lists, text boxes and tables a rounded border, but their
viewport is a square child one border-width in: its corners covered the
border's arcs, so a focus ring broke off before each corner and a square
corner showed inside it. The viewport is masked to the curve instead.
"""
from __future__ import annotations

from PySide6.QtCore import QEvent, QObject, QPointF, QRectF, Qt
from PySide6.QtGui import QPainterPath, QRegion
from PySide6.QtWidgets import QAbstractScrollArea, QWidget
import shiboken6

from tomography_session_browser.presentation.design_tokens import RADIUS_CARD


def corner_radii(viewport: QRectF, area: QRectF, radius: float) -> tuple[float, float, float, float]:
    """Each viewport corner's radius (top left, top right, bottom right, bottom left).

    A corner as far in as the border's curve reaches needs none; otherwise it
    follows the curve, less its inset.
    """

    insets = (
        max(viewport.left() - area.left(), viewport.top() - area.top()),
        max(area.right() - viewport.right(), viewport.top() - area.top()),
        max(area.right() - viewport.right(), area.bottom() - viewport.bottom()),
        max(viewport.left() - area.left(), area.bottom() - viewport.bottom()),
    )
    return tuple(max(0.0, radius - inset) for inset in insets)  # type: ignore[return-value]


def rounded_rect_path(rect: QRectF, radii: tuple[float, float, float, float]) -> QPainterPath:
    top_left, top_right, bottom_right, bottom_left = radii
    path = QPainterPath(QPointF(rect.left() + top_left, rect.top()))
    path.lineTo(rect.right() - top_right, rect.top())
    path.arcTo(QRectF(rect.right() - 2 * top_right, rect.top(), 2 * top_right, 2 * top_right), 90, -90)
    path.lineTo(rect.right(), rect.bottom() - bottom_right)
    path.arcTo(QRectF(rect.right() - 2 * bottom_right, rect.bottom() - 2 * bottom_right,
                      2 * bottom_right, 2 * bottom_right), 0, -90)
    path.lineTo(rect.left() + bottom_left, rect.bottom())
    path.arcTo(QRectF(rect.left(), rect.bottom() - 2 * bottom_left, 2 * bottom_left, 2 * bottom_left), 270, -90)
    path.lineTo(rect.left(), rect.top() + top_left)
    path.arcTo(QRectF(rect.left(), rect.top(), 2 * top_left, 2 * top_left), 180, -90)
    path.closeSubpath()
    return path


class RoundedViewport(QObject):
    """Masks ``area``'s viewport, and its scroll bars' containers, to its rounded border.

    A scroll bar is square too: once it shows, it is what covers the right-hand arcs.
    """

    def __init__(self, area: QAbstractScrollArea, radius: float = RADIUS_CARD) -> None:
        super().__init__(area)
        self.area = area
        self.radius = radius
        area.installEventFilter(self)
        for child in self.edge_children():
            child.installEventFilter(self)

    def edge_children(self) -> list[QWidget]:
        return [child for child in self.area.findChildren(QWidget, options=Qt.FindChildOption.FindDirectChildrenOnly)
                if not child.isWindow()]

    def eventFilter(self, watched, event) -> bool:  # noqa: N802 - Qt API
        kind = event.type()
        # Not ChildAdded: that comes from the child's constructor, before it is a widget.
        if kind == QEvent.Type.ChildPolished and watched is self.area:
            child = event.child()
            if isinstance(child, QWidget) and shiboken6.isValid(child):
                child.installEventFilter(self)
        if kind in (QEvent.Type.Resize, QEvent.Type.Show, QEvent.Type.Move, QEvent.Type.ChildPolished):
            self.update_masks()
        return False

    def update_masks(self) -> None:
        area = QRectF(self.area.rect())
        for child in self.edge_children():
            if not shiboken6.isValid(child) or child.width() <= 0 or child.height() <= 0:
                continue
            geometry = QRectF(child.geometry())
            radii = corner_radii(geometry, area, self.radius)
            if not any(radii):
                if not child.mask().isEmpty():
                    child.clearMask()
                continue
            path = rounded_rect_path(QRectF(0, 0, geometry.width(), geometry.height()), radii)
            child.setMask(QRegion(path.toFillPolygon().toPolygon()))


def round_viewport(area: QAbstractScrollArea, radius: float = RADIUS_CARD) -> RoundedViewport:
    """Keep ``area``'s viewport inside its rounded border (``radius``, the style sheet's)."""

    return RoundedViewport(area, radius)
