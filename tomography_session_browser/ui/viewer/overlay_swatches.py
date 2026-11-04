"""The small drawing beside each Overlays row: what that layer looks like on the image.

Drawn here rather than shipped as files, from the marker palette the canvas and
the PDF read (``presentation.marker_palette``): the swatch of a layer takes the
style the layer's marker has, so changing a colour or a fill there changes it
here too. The canvas is near-black in both themes and so is the panel, so a
swatch never depends on the theme.
"""
from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import QPointF, QRectF, QSize, Qt
from PySide6.QtGui import QBrush, QColor, QFont, QIcon, QPainter, QPen, QPixmap
from PySide6.QtWidgets import QCheckBox

from tomography_session_browser.domain.markers import ImageMarker, MarkerType
from tomography_session_browser.presentation.design_tokens import CANVAS_CHROME
from tomography_session_browser.presentation.marker_palette import STATUS_COMPLETE, MarkerStyle, marker_style
from tomography_session_browser.ui.viewer.constants import SWATCH_HEIGHT_PX, SWATCH_WIDTH_PX

Drawing = Callable[[QPainter], None]


def _style(marker_type: str, status: str | None = None) -> MarkerStyle:
    """The style a marker of this type and status has on the canvas."""

    marker = ImageMarker("swatch", marker_type, None, None, x=0.0, y=0.0, status=status)
    return marker_style(marker, queued_stroke=CANVAS_CHROME.atlas_marker_queued, fallback_stroke=CANVAS_CHROME.text)


def _colour(hex_colour: str, alpha: int = 255) -> QColor:
    colour = QColor(hex_colour)
    colour.setAlpha(alpha)
    return colour


def _pen(hex_colour: str, width: float, *, dash: tuple[float, ...] = (), alpha: int = 255) -> QPen:
    pen = QPen(_colour(hex_colour, alpha), width)
    if dash:
        pen.setDashPattern(list(dash))
        pen.setCapStyle(Qt.PenCapStyle.RoundCap if dash[0] < 1 else Qt.PenCapStyle.FlatCap)
    return pen


def _outline(painter: QPainter, style: MarkerStyle, width: float = 1.5) -> None:
    """Pen and brush for a marker drawn in ``style``: its line, its colour, its fill."""

    dash = {"dash": (2.0, 1.3), "dot": (0.5, 1.6)}.get(style.line, ())
    painter.setPen(_pen(style.stroke, width, dash=dash))
    painter.setBrush(QBrush(_colour(style.fill, style.fill_alpha)) if style.fill_alpha else Qt.BrushStyle.NoBrush)


def _frame(painter: QPainter, marker_type: str) -> None:
    _outline(painter, _style(marker_type))
    painter.drawRect(QRectF(2, 2, 18, 10))


def _tile_grids(painter: QPainter) -> None:
    style = _style(MarkerType.SEARCH_MAP)
    painter.setPen(_pen(style.stroke, 1.0, alpha=204))
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.drawRect(QRectF(2, 2, 18, 10))
    for x in (8, 14):
        painter.drawLine(QPointF(x, 2), QPointF(x, 12))
    painter.drawLine(QPointF(2, 7), QPointF(20, 7))


def _circle(painter: QPainter, style: MarkerStyle, centre: QPointF, radius: float, width: float = 1.5) -> None:
    _outline(painter, style, width)
    painter.drawEllipse(centre, radius, radius)


def _batch_position(painter: QPainter) -> None:
    _circle(painter, _style(MarkerType.BATCH_POSITION, "complete"), QPointF(11, 7), 5)


def _per_exposure(painter: QPainter) -> None:
    style = _style(MarkerType.BATCH_POSITION, "complete")
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(_colour(style.stroke))
    for x in (5, 11, 17):
        painter.drawEllipse(QPointF(x, 7), 2, 2)


def _exposure_area(painter: QPainter) -> None:
    _circle(painter, _style(MarkerType.EXPOSURE_AREA, "complete"), QPointF(11, 7), 5, 1.2)


def _camera_fov(painter: QPainter) -> None:
    _outline(painter, _style(MarkerType.CAMERA_FOV))
    painter.drawRect(QRectF(3, 3, 16, 8))


def _focus_and_tracking(painter: QPainter) -> None:
    _circle(painter, _style(MarkerType.FOCUS_AREA), QPointF(6.5, 7), 4, 1.2)
    _circle(painter, _style(MarkerType.TRACKING_AREA), QPointF(15.5, 7), 4, 1.2)


def _text(painter: QPainter, rect: QRectF, text: str, pixel_size: int, *, bold: bool = False, colour: str | None = None) -> None:
    font = QFont()
    font.setPixelSize(pixel_size)
    font.setBold(bold)
    painter.setFont(font)
    painter.setPen(_colour(colour or CANVAS_CHROME.text))
    painter.drawText(rect, int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter), text)


def _position_numbers(painter: QPainter) -> None:
    painter.setPen(_pen(STATUS_COMPLETE, 1.2))
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.drawEllipse(QPointF(6, 7), 4, 4)
    _text(painter, QRectF(12, 0, 10, SWATCH_HEIGHT_PX), "12", 9)


def _cluster(painter: QPainter) -> None:
    painter.setPen(_pen(STATUS_COMPLETE, 2.0))
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.drawEllipse(QPointF(11, 7), 5.5, 5.5)
    _text(painter, QRectF(9.2, 0, 6, SWATCH_HEIGHT_PX), "3", 8)


def _names(painter: QPainter) -> None:
    _text(painter, QRectF(2, 0, 20, SWATCH_HEIGHT_PX), "Aa", 10, bold=True)


def _legend(painter: QPainter) -> None:
    painter.setPen(_pen(CANVAS_CHROME.text_muted, 1.0))
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.drawRoundedRect(QRectF(2, 2, 18, 10), 2, 2)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(_colour(STATUS_COMPLETE))
    painter.drawEllipse(QPointF(6, 7), 2, 2)
    painter.setPen(_pen(CANVAS_CHROME.text_muted, 1.0))
    painter.drawLine(QPointF(10, 7), QPointF(17, 7))


def _scale_bar(painter: QPainter) -> None:
    painter.setPen(_pen(CANVAS_CHROME.text, 1.5))
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.drawPolyline([QPointF(3, 5), QPointF(3, 9), QPointF(19, 9), QPointF(19, 5)])


def _collection(painter: QPainter) -> None:
    pen = _pen(CANVAS_CHROME.text, 1.3)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    painter.setPen(pen)
    for (x1, y1), (x2, y2) in (((4, 4), (18, 3)), ((4, 7), (18, 7)), ((4, 10), (18, 11))):
        painter.drawLine(QPointF(x1, y1), QPointF(x2, y2))


_DRAWINGS: dict[str, Drawing] = {
    "overview": lambda painter: _frame(painter, MarkerType.OVERVIEW),
    "search_map": lambda painter: _frame(painter, MarkerType.SEARCH_MAP),
    "tile_grids": _tile_grids,
    "batch_position": _batch_position,
    "per_exposure": _per_exposure,
    "exposure_area": _exposure_area,
    "camera_fov": _camera_fov,
    "focus_and_tracking": _focus_and_tracking,
    "position_numbers": _position_numbers,
    "cluster": _cluster,
    "names": _names,
    "legend": _legend,
    "scale_bar": _scale_bar,
    "collection": _collection,
}

SWATCH_NAMES = tuple(_DRAWINGS)


#: How much of a swatch shows beside a row that is switched off for this item.
DISABLED_OPACITY = 0.4


def swatch_pixmap(name: str, ratio: float = 1.0, *, disabled: bool = False) -> QPixmap:
    """The swatch ``name`` drawn at ``ratio`` device pixels per logical pixel."""

    draw = _DRAWINGS[name]
    pixmap = QPixmap(round(SWATCH_WIDTH_PX * ratio), round(SWATCH_HEIGHT_PX * ratio))
    pixmap.setDevicePixelRatio(ratio)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    if disabled:
        painter.setOpacity(DISABLED_OPACITY)
    draw(painter)
    painter.end()
    return pixmap


_ICONS: dict[str, QIcon] = {}


def swatch_icon(name: str) -> QIcon:
    """The swatch as an icon with a sharp picture for a 1x and a 2x screen, made once.

    Dimmed when its row is disabled, like the label beside it.
    """

    icon = _ICONS.get(name)
    if icon is None:
        icon = QIcon()
        for ratio in (1.0, 2.0):
            icon.addPixmap(swatch_pixmap(name, ratio))
            icon.addPixmap(swatch_pixmap(name, ratio, disabled=True), QIcon.Mode.Disabled)
        _ICONS[name] = icon
    return icon


def with_swatch(box: QCheckBox, name: str) -> QCheckBox:
    """Show swatch ``name`` between ``box`` and its label."""

    box.setIcon(swatch_icon(name))
    box.setIconSize(QSize(SWATCH_WIDTH_PX, SWATCH_HEIGHT_PX))
    return box
