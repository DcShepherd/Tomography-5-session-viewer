"""Format tilt-series status and frame labels shared by progress and timelines."""
from __future__ import annotations

from typing import Any
from tomography_session_browser.domain.models import TiltSeries
from tomography_session_browser.presentation.dashboard.models import (
    TILE_STATUS_COMPLETE,
    TILE_STATUS_FAILED,
    TILE_STATUS_NEUTRAL,
    TILE_STATUS_WARNING,
)
from tomography_session_browser.presentation.dashboard.sections.status import tile_status_from_validation_status


def timeline_status_for(batch_status: str, validation: Any | None) -> str:
    if validation is None:
        return batch_status if batch_status != TILE_STATUS_NEUTRAL else TILE_STATUS_NEUTRAL
    status = tile_status_from_validation_status(validation.status)
    if status == TILE_STATUS_FAILED:
        return TILE_STATUS_FAILED
    if status == TILE_STATUS_WARNING:
        return TILE_STATUS_WARNING
    if status == TILE_STATUS_COMPLETE:
        return TILE_STATUS_COMPLETE
    # A failed batch with no usable per-tilt verdict should still be visible,
    # but a failed child must not recolour every sibling in the same parent
    # lane. Validation is the most specific unit when it exists.
    if batch_status == TILE_STATUS_FAILED:
        return TILE_STATUS_FAILED
    return batch_status if batch_status != TILE_STATUS_NEUTRAL else TILE_STATUS_NEUTRAL


def group_tilt_series_frame_text(
    tilt_series: list[TiltSeries],
    validation_by_id: dict[str, Any],
) -> str:
    values: list[str] = []
    seen: set[str] = set()
    for tilt in tilt_series:
        value = _tilt_series_expected_frame_text(tilt, validation_by_id.get(tilt.id))
        if value in seen:
            continue
        values.append(value)
        seen.add(value)
    if not values:
        return "unknown"
    if len(values) <= 3:
        return ", ".join(values)
    return f"{', '.join(values[:3])}, +{len(values) - 3} more"


def tilt_names_with_status(
    tilt_series: list[TiltSeries],
    validation_by_id: dict[str, Any],
    status: str,
) -> list[str]:
    names: list[str] = []
    for tilt in tilt_series:
        validation = validation_by_id.get(tilt.id)
        if timeline_status_for(TILE_STATUS_NEUTRAL, validation) == status:
            names.append(tilt.name or tilt.id)
    return names


def format_names_for_tooltip(label: str, names: list[str], *, limit: int = 8) -> str:
    if not names:
        return ""
    if len(names) <= limit:
        return f"{label}: {', '.join(names)}"
    shown = ", ".join(names[:limit])
    return f"{label}: {shown}, +{len(names) - limit} more"


def _tilt_series_expected_frame_text(tilt: TiltSeries, validation: Any | None) -> str:
    expected = getattr(validation, "expected_count", None) if validation is not None else None
    if expected:
        return str(expected)
    actual = getattr(validation, "actual_count", None) if validation is not None else None
    if actual:
        return str(actual)
    if tilt.tilt_count:
        return str(tilt.tilt_count)
    if tilt.mrc_metadata is not None and tilt.mrc_metadata.nz:
        return str(tilt.mrc_metadata.nz)
    if tilt.sections:
        return str(len(tilt.sections))
    return "unknown"


def tilt_series_frame_text(tilt: TiltSeries, validation: Any | None) -> str:
    actual = getattr(validation, "actual_count", None) if validation is not None else None
    expected = getattr(validation, "expected_count", None) if validation is not None else None
    if expected:
        if actual is not None and actual != expected:
            return f"{actual} / {expected} expected"
        return str(expected)
    if actual:
        return str(actual)
    if tilt.tilt_count:
        return str(tilt.tilt_count)
    if tilt.mrc_metadata is not None and tilt.mrc_metadata.nz:
        return str(tilt.mrc_metadata.nz)
    if tilt.sections:
        return str(len(tilt.sections))
    return "unknown"
