"""Format tilt-series status and frame labels shared by progress and timelines."""
from __future__ import annotations

from typing import Any
from tomography_session_browser.services.tilt_series_validation import TiltSeriesValidation, validation_for
from tomography_session_browser.domain.models import TiltSeries
from tomography_session_browser.presentation.dashboard.models import (
    TILE_STATUS_COMPLETE,
    TILE_STATUS_FAILED,
    TILE_STATUS_NEUTRAL,
    TILE_STATUS_WARNING,
)
from tomography_session_browser.presentation.dashboard.sections.status import tile_status_from_validation_status


def timeline_status_for(validation: Any | None) -> str:
    """A series' colour comes from its own verdict, never its batch's recorded flag.

    A batch interruption is acquisition history, shown on the batch; it does
    not make an unverified series look failed.
    """
    if validation is None:
        return TILE_STATUS_NEUTRAL
    status = tile_status_from_validation_status(validation.status)
    if status in (TILE_STATUS_FAILED, TILE_STATUS_WARNING, TILE_STATUS_COMPLETE):
        return status
    return TILE_STATUS_NEUTRAL


def group_tilt_series_frame_text(
    tilt_series: list[TiltSeries],
    validation_by_id: dict[str, Any],
) -> str:
    values: list[str] = []
    seen: set[str] = set()
    for tilt in tilt_series:
        value = _tilt_series_expected_frame_text(tilt, validation_for(validation_by_id, tilt))
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
        validation = validation_for(validation_by_id, tilt)
        if timeline_status_for(validation) == status:
            names.append(tilt.name or tilt.id)
    return names


def format_names_for_tooltip(label: str, names: list[str], *, limit: int = 8) -> str:
    if not names:
        return ""
    if len(names) <= limit:
        return f"{label}: {', '.join(names)}"
    shown = ", ".join(names[:limit])
    return f"{label}: {shown}, +{len(names) - limit} more"


def _counts(validation: TiltSeriesValidation | None) -> tuple[int | None, int | None]:
    """``(actual, expected)`` from the verdict's observation and plan."""
    if validation is None:
        return None, None
    return validation.observation.reported_count, validation.plan.expected_count


def _tilt_series_expected_frame_text(tilt: TiltSeries, validation: TiltSeriesValidation | None) -> str:
    actual, expected = _counts(validation)
    if expected:
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


def tilt_series_frame_text(tilt: TiltSeries, validation: TiltSeriesValidation | None) -> str:
    actual, expected = _counts(validation)
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
