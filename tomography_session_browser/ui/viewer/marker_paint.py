"""Marker paint for the image viewer."""
from __future__ import annotations
from typing import Any
from PySide6.QtCore import QPointF
from PySide6.QtGui import QColor, QFont, QPainterPath
from tomography_session_browser.domain.markers import ImageMarker, MarkerType
from tomography_session_browser.services.atlas_marker_style import IMAGE_STATUS_COLOURS
from tomography_session_browser.ui.theme import current_palette
from tomography_session_browser.presentation.marker_palette import MarkerStyle, marker_style


def marker_outline(marker: ImageMarker) -> MarkerStyle:
    """The line and halo of ``marker``'s outline (``presentation.marker_palette``):
    the neutral frames are dashed or dotted and haloed; a selected marker is
    solid, unhaloed."""

    theme = current_palette()
    if marker.selected:
        return MarkerStyle(theme.overlay_selected, theme.overlay_selected, 0, "selected")
    return marker_style(marker, queued_stroke=theme.atlas_marker_queued, fallback_stroke=theme.text)


def marker_colors(marker: ImageMarker) -> tuple[QColor, QColor]:
    """Outline and fill for ``marker`` on the canvas.

    Selection is the viewer's own state; every other colour comes from
    ``presentation.marker_palette``, the rule the PDF draws with too
    (harmonisation F1), so a marker reads the same on screen and in print.
    """

    theme = current_palette()

    def fill(hex_color: str, alpha: int) -> QColor:
        color = QColor(hex_color)
        color.setAlpha(alpha)
        return color

    if marker.selected and marker.marker_type == MarkerType.EXPOSURE_AREA:
        return QColor(theme.marker_exposure), fill(theme.marker_exposure, 105)
    if marker.selected:
        return QColor(theme.overlay_selected), fill(theme.overlay_selected, 90)
    style = marker_style(marker, queued_stroke=theme.atlas_marker_queued, fallback_stroke=theme.text)
    return QColor(style.stroke), fill(style.fill, style.fill_alpha)


def batch_label_text_path(text: str, font: QFont) -> QPainterPath:
    path = QPainterPath()
    path.addText(0.0, 0.0, font, text)
    bounds = path.boundingRect()
    if bounds.isEmpty():
        return path
    normalised = QPainterPath()
    normalised.addText(-bounds.left(), -bounds.top(), font, text)
    return normalised


def batch_label_scene_origin(
    marker: ImageMarker,
    *,
    label_size: tuple[float, float],
    image_size: tuple[float, float],
) -> tuple[float, float]:
    label_w, label_h = label_size
    image_w, image_h = image_size
    x = float(marker.x or 0.0)
    y = float(marker.y or 0.0)

    return (
        min(max(0.0, x), max(0.0, image_w - label_w)),
        min(max(0.0, y), max(0.0, image_h - label_h)),
    )


def metadata_box(value: Any) -> tuple[float, float, float, float] | None:
    if not (
        isinstance(value, tuple | list)
        and len(value) == 4
        and all(isinstance(part, int | float) for part in value)
    ):
        return None
    x, y, width, height = value
    if width <= 0 or height <= 0:
        return None
    return float(x), float(y), float(width), float(height)


def metadata_point(value: Any) -> tuple[float, float] | None:
    if not (
        isinstance(value, tuple | list)
        and len(value) == 2
        and all(isinstance(part, int | float) for part in value)
    ):
        return None
    return float(value[0]), float(value[1])


def batch_label_color(visible_marker_types: set[str]) -> QColor:
    theme = current_palette()
    if MarkerType.EXPOSURE_AREA in visible_marker_types:
        return QColor(theme.overlay_batch_label_exposure)
    if MarkerType.CAMERA_FOV in visible_marker_types:
        return QColor(theme.overlay_batch_label_camera)
    return QColor(theme.overlay_batch_label_default)


def exposure_highlight_color() -> QColor:
    return QColor(current_palette().marker_focus)


def atlas_status_color(status: str) -> QColor:
    normalized = (status or "").strip().lower()
    return QColor(
        IMAGE_STATUS_COLOURS.get(normalized, IMAGE_STATUS_COLOURS["queued"])
    )


def marker_scene_extent(marker: ImageMarker) -> tuple[float, float]:
    if marker.bbox is not None:
        return abs(float(marker.bbox[2])), abs(float(marker.bbox[3]))
    if marker.polygon:
        xs = [point[0] for point in marker.polygon]
        ys = [point[1] for point in marker.polygon]
        return max(xs) - min(xs), max(ys) - min(ys)
    radius = abs(float(marker.radius or 0.0))
    return radius * 2, radius * 2


def marker_label_anchor(marker: ImageMarker) -> QPointF:
    if marker.x is not None and marker.y is not None:
        return QPointF(marker.x, marker.y)
    if marker.bbox is not None:
        x, y, width, height = marker.bbox
        return QPointF(x + width, y + height / 2)
    if marker.polygon:
        x = sum(point[0] for point in marker.polygon) / len(marker.polygon)
        y = sum(point[1] for point in marker.polygon) / len(marker.polygon)
        return QPointF(x, y)
    return QPointF(0, 0)


def marker_scene_geometry(marker: ImageMarker) -> tuple[QPointF, float] | None:
    """A displayed marker's centre and radius, in scene units."""

    if marker.bbox is not None:
        x, y, width, height = marker.bbox
        return QPointF(x + width / 2.0, y + height / 2.0), max(width, height) / 2.0
    if marker.polygon:
        xs = [point[0] for point in marker.polygon]
        ys = [point[1] for point in marker.polygon]
        return (
            QPointF((min(xs) + max(xs)) / 2.0, (min(ys) + max(ys)) / 2.0),
            max(max(xs) - min(xs), max(ys) - min(ys)) / 2.0,
        )
    if marker.x is not None and marker.y is not None:
        return QPointF(float(marker.x), float(marker.y)), float(marker.radius or 0.0)
    return None


def relative_point(rect, point: QPointF) -> QPointF:
    width = rect.width() or 1.0
    height = rect.height() or 1.0
    return QPointF((point.x() - rect.left()) / width, (point.y() - rect.top()) / height)


def absolute_point(rect, relative: QPointF | None) -> QPointF:
    if relative is None:
        return rect.center()
    return QPointF(rect.left() + rect.width() * relative.x(), rect.top() + rect.height() * relative.y())

