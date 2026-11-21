"""Overlay tones on Search maps, Overviews and Search tiles.

The batch dot takes the shared batch outcome
(``batch_position_status.aggregate_batch_position_status``), the outcome the
Atlas markers, lists and dashboard rows use. Each exposure area takes its own
tilt series' validation verdict; an exposure with no resolved series keeps the
batch's tone, so nothing is guessed. A camera field follows its exposure, and
the other templates follow the batch. A queued batch stays queued throughout.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from tomography_session_browser.domain.markers import ImageMarker, MarkerType
from tomography_session_browser.domain.models import BatchPosition, TiltSeries
from tomography_session_browser.domain.status import AtlasMarkerStatus
from tomography_session_browser.services.batch_position_status import BatchPositionOverlayStatus
from tomography_session_browser.services.navigation import resolve_exposure_tilt_series
from tomography_session_browser.services.tilt_series_validation import TiltSeriesValidation, validation_for


def apply_outcome_tones(
    batch_marker: ImageMarker | None,
    template_markers: Sequence[ImageMarker],
    batch: BatchPosition,
    overlay_status: BatchPositionOverlayStatus,
    *,
    tilt_series: Sequence[TiltSeries],
    validations: Mapping[str, TiltSeriesValidation],
) -> None:
    """Set the status words the palette reads on one batch's markers."""

    tone = str(overlay_status.status)
    if batch_marker is not None:
        batch_marker.status = tone
    queued = overlay_status.status == AtlasMarkerStatus.QUEUED
    exposure_tones: dict[str, str] = {}
    for marker in template_markers:
        if marker.marker_type == MarkerType.EXPOSURE_AREA:
            marker.status = tone if queued else _exposure_tone(marker, batch, tone, tilt_series, validations)
            exposure_tones[marker.id] = marker.status
    for marker in template_markers:
        if marker.marker_type == MarkerType.CAMERA_FOV:
            marker.status = exposure_tones.get(str(marker.metadata.get("exposure_marker_id") or ""), tone)
        elif marker.marker_type != MarkerType.EXPOSURE_AREA:
            marker.status = tone


def _exposure_tone(
    marker: ImageMarker,
    batch: BatchPosition,
    batch_tone: str,
    tilt_series: Sequence[TiltSeries],
    validations: Mapping[str, TiltSeriesValidation],
) -> str:
    tilt = resolve_exposure_tilt_series(marker, batch=batch, tilt_series=tilt_series).tilt_series
    validation = validation_for(validations, tilt) if tilt is not None else None
    return str(validation.status) if validation is not None else batch_tone
