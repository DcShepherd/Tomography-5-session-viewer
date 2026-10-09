"""Beam and camera field-of-view sizes in image pixels, and the camera-FOV
markers drawn from a batch's exposures. The beam-diameter contexts and the
microscope-data display scale are Tomography 5 specific; do not tidy them."""
from __future__ import annotations

from collections.abc import Iterable

from tomography_session_browser.domain.coerce import (
    as_float,
    as_str,
)
from tomography_session_browser.domain.markers import (
    ImageMarker,
    MarkerType,
)
from tomography_session_browser.domain.models import BatchPosition
from tomography_session_browser.parsers.xml_parser import find_first
from tomography_session_browser.services.batch_position_status import STATUS_QUEUED
from tomography_session_browser.services.pixel_size import overlay_mrc_pixel_size
from tomography_session_browser.services.markers.context import (
    BEAM_DIAMETER_EXPOSURE_MRC_CONTEXT,
    BEAM_DIAMETER_LOW_DOSE_CONTEXT,
    BEAM_DIAMETER_MICROSCOPE_CONTEXT,
    LOGGER,
    MICROSCOPE_DATA_BEAM_DISPLAY_SCALE,
    QueuedOverlayReference,
)
from tomography_session_browser.services.markers.shapes import bbox_intersects


def camera_fov_markers_from_exposures(
    batch: BatchPosition,
    markers: Iterable[ImageMarker],
    target_pixel_size: float | None,
    image_size: tuple[int, int] | None,
    *,
    source_id: str | None = None,
    queued: bool = False,
    queued_reference: QueuedOverlayReference | None = None,
) -> list[ImageMarker]:
    """Create acquisition camera-FOV rectangles for exposure markers.

    Tomography 5 batch XML records exposure positions as stage offsets, but
    the actual collected camera footprint is best derived from the exposure
    preview MRC dimensions and calibrated pixel size. This keeps the FOV tied
    to recorded acquisition metadata while leaving beam diameter handling
    unchanged; if the batch lacks BeamDiameter, renderers can fill this marker
    as the visual collected-region fallback.
    """

    if target_pixel_size is None or target_pixel_size <= 0 or image_size is None:
        return []
    dimensions = (
        _queued_camera_fov_dimensions(queued_reference, target_pixel_size)
        if queued
        else None
    ) or _camera_fov_dimensions(batch, target_pixel_size)
    if dimensions is None:
        return []
    width_px, height_px, width_m, height_m, source = dimensions
    if width_px <= 0 or height_px <= 0:
        return []
    has_beam_diameter = (
        queued_beam_radius_pixels(queued_reference, target_pixel_size) is not None
        if queued
        else beam_radius_pixels(batch, target_pixel_size) is not None
    )
    out: list[ImageMarker] = []
    for marker in markers:
        if marker.marker_type != MarkerType.EXPOSURE_AREA or marker.x is None or marker.y is None:
            continue
        bbox = (
            marker.x - width_px / 2,
            marker.y - height_px / 2,
            width_px,
            height_px,
        )
        if not bbox_intersects(image_size, bbox):
            continue
        area_name = as_str(marker.metadata.get("area_name")) or marker.label or "Exposure"
        fallback_note = (
            ""
            if has_beam_diameter
            else "\nBeam diameter unavailable; hollow queued camera FOV shown."
            if queued
            else "\nBeam diameter unavailable; filled camera FOV shown as collected-region fallback."
        )
        out.append(
            ImageMarker(
                id=f"{marker.id}:{MarkerType.CAMERA_FOV}",
                marker_type=MarkerType.CAMERA_FOV,
                linked_object_id=marker.linked_object_id,
                source_object_id=source_id or marker.source_object_id,
                x=marker.x,
                y=marker.y,
                bbox=bbox,
                label=None,
                tooltip=(
                    f"Camera FOV for {area_name}\n"
                    f"{width_m * 1e6:.2f} x {height_m * 1e6:.2f} µm"
                    f"{fallback_note}"
                ),
                status=STATUS_QUEUED if queued else marker.status,
                unresolved=False,
                metadata={
                    "batch_id": batch.id,
                    "area_name": area_name,
                    "image_size": image_size,
                    "coordinate_source": "exposure_camera_fov_from_mrc_metadata",
                    "camera_fov_source": source,
                    "camera_fov_size_px": (width_px, height_px),
                    "camera_fov_size_m": (width_m, height_m),
                    "has_beam_diameter": has_beam_diameter,
                    "filled_fallback": not has_beam_diameter and not queued,
                    "queued_position": queued,
                    "queued_fov_fallback": queued and not has_beam_diameter,
                    "queued_camera_source_batch_id": (
                        queued_reference.camera_source_batch_id
                        if queued and queued_reference is not None
                        else None
                    ),
                    "exposure_marker_id": marker.id,
                    "exposure_index": marker.metadata.get("exposure_index"),
                    "exposure_type": marker.metadata.get("exposure_type"),
                },
            )
        )
    return out


def _camera_fov_dimensions(
    batch: BatchPosition,
    target_pixel_size: float,
) -> tuple[float, float, float, float, str] | None:
    metadata = batch.exposure_mrc_metadata or batch.tracking_mrc_metadata
    source = "exposure MRC" if batch.exposure_mrc_metadata is not None else "tracking MRC"
    if metadata is None or metadata.nx is None or metadata.ny is None:
        return None
    source_pixel_size = overlay_mrc_pixel_size(metadata)
    if source_pixel_size is None or source_pixel_size <= 0:
        return None
    width_m = metadata.nx * source_pixel_size
    height_m = metadata.ny * source_pixel_size
    return (
        width_m / target_pixel_size,
        height_m / target_pixel_size,
        width_m,
        height_m,
        source,
    )


def _queued_camera_fov_dimensions(
    reference: QueuedOverlayReference | None,
    target_pixel_size: float,
) -> tuple[float, float, float, float, str] | None:
    if (
        reference is None
        or reference.camera_fov_m is None
        or target_pixel_size <= 0
    ):
        return None
    width_m, height_m = reference.camera_fov_m
    if width_m <= 0 or height_m <= 0:
        return None
    source = reference.camera_source or "successful tilt-series exposure MRC"
    return (
        width_m / target_pixel_size,
        height_m / target_pixel_size,
        width_m,
        height_m,
        source,
    )


def exposure_marker_radius(
    batch_positions: Iterable[BatchPosition], pixel_size: float | None
) -> float:
    """Pick a radius for inferred-failed markers that visually
    matches the exposure-area overlays on the same frame.

    Walks the available batch positions in order, returning the
    first parseable beam-diameter-derived radius (so a sample with
    real ``BeamDiameter`` metadata uses the same beam size for the
    inferred marker that the templated exposures already use). Falls
    back to ``area_radius(EXPOSURE_AREA)`` so the marker is at least
    as visible as a default-sized exposure overlay when no beam
    metadata is on disk.
    """

    for batch in batch_positions:
        radius = beam_radius_pixels(batch, pixel_size)
        if radius is not None:
            return radius
    return area_radius(MarkerType.EXPOSURE_AREA)


def area_radius(marker_type: str) -> float:
    if marker_type == MarkerType.EXPOSURE_AREA:
        return 18.0
    if marker_type == MarkerType.FOCUS_AREA:
        return 16.0
    if marker_type == MarkerType.TRACKING_AREA:
        return 16.0
    if marker_type == MarkerType.CONDITION_AREA:
        return 16.0
    return 12.0


def queued_beam_radius_pixels(
    reference: QueuedOverlayReference | None,
    pixel_size: float | None,
) -> float | None:
    if (
        reference is None
        or reference.beam_diameter_m is None
        or reference.beam_diameter_m <= 0
        or pixel_size is None
        or pixel_size <= 0
    ):
        return None
    return max(3.0, (reference.beam_diameter_m / 2.0) / pixel_size)


def beam_radius_pixels(batch: BatchPosition, pixel_size: float | None) -> float | None:
    if pixel_size is None or pixel_size <= 0:
        return None
    diameter_value = as_float(find_first(batch.metadata, "BeamDiameter"))
    if diameter_value is None or diameter_value <= 0:
        return None
    context = as_str(batch.metadata.get("BeamDiameterContext"))
    radius_m = _beam_diameter_to_meters(diameter_value, context=context)
    if radius_m is None:
        LOGGER.debug("Unsupported BeamDiameter value batch=%s value=%s", batch.id, diameter_value)
        return None
    # The low-dose optics value used by the reference collection overlay is named
    # ``BeamDiameter`` in XML but behaves as the rendered radius after unit
    # conversion. Keep marker.radius in that same display convention.
    return max(3.0, radius_m / pixel_size)


def _beam_diameter_to_meters(value: float, *, context: str | None = None) -> float | None:
    if value <= 0:
        return None
    if context == BEAM_DIAMETER_EXPOSURE_MRC_CONTEXT:
        # The FEI extended header records the actual exposure-state beam
        # diameter in meters at the specimen plane. Convert directly to the
        # rendered radius — no empirical scaling required.
        return value / 2
    if context == BEAM_DIAMETER_LOW_DOSE_CONTEXT:
        return value * 1e-3
    if context == BEAM_DIAMETER_MICROSCOPE_CONTEXT:
        # Some reference search XML lacks the low-dose
        # BackwardAlignmentTransformation optics block used for
        # the visible exposure overlay. The remaining microscopeData optics
        # BeamDiameter is roughly 10x larger than the reference low-dose display
        # radius for the same acquisition family, so use it only as a scaled
        # fallback instead of applying the millimetre heuristic below.
        return value * MICROSCOPE_DATA_BEAM_DISPLAY_SCALE
    if value > 1e-5:
        # Tomography metadata stores BeamDiameter in millimetres in observed TFS XML.
        return value * 1e-3
    return value
