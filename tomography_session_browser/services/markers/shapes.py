"""Small marker-geometry helpers shared by the builders: centres, image
intersection, off-image filtering, failure tagging, and rescaling a marker to
the size of the image actually shown (screen and PDF alike)."""
from __future__ import annotations

from collections.abc import Iterable

from tomography_session_browser.domain.markers import ImageMarker
from tomography_session_browser.domain.models import BatchPosition
from tomography_session_browser.services.markers.context import LOGGER


def marker_center_point(marker: ImageMarker) -> tuple[float, float] | None:
    if marker.x is not None and marker.y is not None:
        return marker.x, marker.y
    if marker.bbox is not None:
        x, y, width, height = marker.bbox
        return x + width / 2, y + height / 2
    return None


def move_marker_center(marker: ImageMarker, center: tuple[float, float]) -> None:
    if marker.x is not None and marker.y is not None:
        marker.x, marker.y = center
        return
    if marker.bbox is not None:
        _, _, width, height = marker.bbox
        marker.bbox = (center[0] - width / 2, center[1] - height / 2, width, height)


def bbox_intersects(image_size: tuple[int, int], bbox: tuple[float, float, float, float]) -> bool:
    image_width, image_height = image_size
    x, y, width, height = bbox
    return x + width >= 0 and y + height >= 0 and x <= image_width and y <= image_height


def marker_intersects_image(image_size: tuple[int, int], marker: ImageMarker) -> bool:
    """Whether any of the marker's bounds lie on the image, in its pixel space."""
    if marker.bbox is not None:
        return bbox_intersects(image_size, marker.bbox)
    if marker.polygon:
        xs = [point[0] for point in marker.polygon]
        ys = [point[1] for point in marker.polygon]
        return bbox_intersects(
            image_size,
            (min(xs), min(ys), max(xs) - min(xs), max(ys) - min(ys)),
        )
    if marker.x is None or marker.y is None:
        return False
    radius = max(marker.radius or 0.0, 0.0)
    return (
        marker.x + radius >= 0
        and marker.y + radius >= 0
        and marker.x - radius <= image_size[0]
        and marker.y - radius <= image_size[1]
    )


def filter_off_image_template_markers(
    markers: list[ImageMarker],
    *,
    image_size: tuple[int, int],
    source_id: str,
    batch: BatchPosition,
) -> None:
    kept: list[ImageMarker] = []
    for marker in markers:
        if marker_intersects_image(image_size, marker):
            kept.append(marker)
            continue
        LOGGER.debug(
            "Skipping off-image corrected template marker source=%s batch=%s type=%s id=%s",
            source_id,
            batch.id,
            marker.marker_type,
            marker.id,
        )
    markers[:] = kept


def filter_off_image_markers(
    markers: list[ImageMarker],
    *,
    image_size: tuple[int, int],
    source_id: str,
) -> None:
    kept: list[ImageMarker] = []
    for marker in markers:
        if marker_intersects_image(image_size, marker):
            kept.append(marker)
            continue
        LOGGER.debug(
            "Skipping off-image corrected marker source=%s type=%s id=%s",
            source_id,
            marker.marker_type,
            marker.id,
        )
    markers[:] = kept


def mark_markers_failed(markers: Iterable[ImageMarker]) -> None:
    """Tag every marker with ``status = "failed"`` so renderers paint
    them in the red status colour. Used when the batch the markers
    came from has at least one validator-flagged failed tilt series.
    """

    for marker in markers:
        marker.status = "failed"


def batch_tooltip(batch: BatchPosition) -> str:
    lines = [f"Batch position: {batch.name or batch.id}"]
    if batch.status:
        lines.append(f"Status: {batch.status}")
    if batch.linked_tilt_series_ids:
        lines.append(f"Linked tilt series: {len(batch.linked_tilt_series_ids)}")
    return "\n".join(lines)


def project_marker_to_size(
    marker: ImageMarker, target_size: tuple[int, int]
) -> ImageMarker:
    """Return a copy of ``marker`` rescaled into ``target_size`` pixels.

    The marker's coordinates were computed in ``metadata['image_size']``
    space (the size returned by :func:`image_size_for_markers` —
    typically the SearchMap.dm ``ImageSize`` or the MRC ``nx/ny``).
    The displayed image might be a downsampled JPG with a different
    pixel resolution; this helper applies the same scale-to-display
    transform the GUI's ``_project_marker`` uses, so report rendering
    and on-screen rendering produce the same overlay positions.

    Falls back to the unmodified marker when ``metadata['image_size']``
    is missing or non-positive (preserves the previous behaviour for
    markers that don't carry the metadata).
    """

    image_size = marker.metadata.get("image_size") if marker.metadata else None
    if not (
        isinstance(image_size, tuple)
        and len(image_size) == 2
        and isinstance(image_size[0], (int, float))
        and isinstance(image_size[1], (int, float))
    ):
        return marker
    src_w = float(image_size[0])
    src_h = float(image_size[1])
    tgt_w = float(target_size[0])
    tgt_h = float(target_size[1])
    if src_w <= 0 or src_h <= 0 or tgt_w <= 0 or tgt_h <= 0:
        return marker
    scale_x = tgt_w / src_w
    scale_y = tgt_h / src_h
    bbox = None
    if marker.bbox is not None:
        x, y, w, h = marker.bbox
        bbox = (x * scale_x, y * scale_y, w * scale_x, h * scale_y)
    polygon = (
        [(x * scale_x, y * scale_y) for x, y in marker.polygon]
        if marker.polygon
        else None
    )
    return ImageMarker(
        id=marker.id,
        marker_type=marker.marker_type,
        linked_object_id=marker.linked_object_id,
        source_object_id=marker.source_object_id,
        x=marker.x * scale_x if marker.x is not None else None,
        y=marker.y * scale_y if marker.y is not None else None,
        radius=(
            marker.radius * (scale_x + scale_y) / 2.0
            if marker.radius is not None
            else None
        ),
        bbox=bbox,
        polygon=polygon,
        label=marker.label,
        tooltip=marker.tooltip,
        status=marker.status,
        visible=marker.visible,
        selected=marker.selected,
        unresolved=marker.unresolved,
        metadata=dict(marker.metadata),
    )
