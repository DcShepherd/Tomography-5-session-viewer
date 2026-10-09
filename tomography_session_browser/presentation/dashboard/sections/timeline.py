"""Build acquisition timeline rows and the scoped acquisition window."""
from __future__ import annotations

from datetime import datetime
from typing import Any
from tomography_session_browser.domain.models import BatchPosition, Sample, TiltSeries
from tomography_session_browser.presentation.dashboard.models import (
    DashboardScope,
    InferredBatchGroup,
    TILE_STATUS_FAILED,
    TILE_STATUS_NEUTRAL,
    TimelineItemModel,
)
from tomography_session_browser.presentation.dashboard.scopes import sample_group_key
from tomography_session_browser.presentation.dashboard.sections.batches import (
    batch_position_label,
    dedupe_tilt_series,
    group_matches_any_batch,
    inferred_tilts_for_batch,
    tilt_series_for_batch,
)
from tomography_session_browser.presentation.dashboard.sections.status import batch_position_status, tilt_status_counts
from tomography_session_browser.presentation.dashboard.sections.tilt_labels import (
    format_names_for_tooltip,
    group_tilt_series_frame_text,
    tilt_names_with_status,
    tilt_series_frame_text,
    timeline_status_for,
)
from tomography_session_browser.presentation.formatting import format_duration_between
from tomography_session_browser.services.batch_inference import inferred_group_token
from tomography_session_browser.services.entity_links import planned_exposures_for_batch_position
from tomography_session_browser.services.timeline_service import parse_datetime


def timeline_items_for_scope(
    batch_positions: list[BatchPosition],
    tilt_series: list[TiltSeries],
    validation_by_id: dict[str, Any],
    *,
    inferred_batch_groups: list[InferredBatchGroup] | None = None,
) -> dict[str, TimelineItemModel]:
    """Build timeline lane labels/tooltips for a sample-like dashboard scope."""

    items: dict[str, TimelineItemModel] = {}
    assigned_tilt_ids: set[str] = set()
    inferred_batch_groups = inferred_batch_groups or []
    for batch in batch_positions:
        inferred_tilts = inferred_tilts_for_batch(batch, inferred_batch_groups)
        linked_tilts = dedupe_tilt_series(tilt_series_for_batch(batch, tilt_series) + inferred_tilts)
        if not linked_tilts:
            continue
        label = batch_position_label(batch)
        exposure_count = planned_exposures_for_batch_position(batch, linked_tilts)
        batch_status = batch_position_status(batch)
        complete, warning, failed, queued = tilt_status_counts(linked_tilts, validation_by_id)
        failed_names = tilt_names_with_status(linked_tilts, validation_by_id, TILE_STATUS_FAILED)
        tooltip = _timeline_group_tooltip(
            label=label,
            exposure_count=exposure_count,
            complete=complete,
            warning=warning,
            failed=failed,
            queued=queued,
            frame_text=group_tilt_series_frame_text(linked_tilts, validation_by_id),
            inferred=bool(inferred_tilts),
            failed_names=failed_names,
        )
        for tilt in linked_tilts:
            validation = validation_by_id.get(tilt.id)
            status = timeline_status_for(batch_status, validation)
            assigned_tilt_ids.add(tilt.id)
            items[tilt.id] = TimelineItemModel(
                tilt_series_id=tilt.id,
                lane_key=batch.id,
                lane_label=label,
                tooltip=tooltip,
                status=status,
            )
    for group in inferred_batch_groups:
        if group_matches_any_batch(group, batch_positions):
            continue
        complete, warning, failed, queued = tilt_status_counts(group.tilt_series, validation_by_id)
        failed_names = tilt_names_with_status(group.tilt_series, validation_by_id, TILE_STATUS_FAILED)
        tooltip = _timeline_group_tooltip(
            label=group.label,
            exposure_count=len(group.tilt_series),
            complete=complete,
            warning=warning,
            failed=failed,
            queued=queued,
            frame_text=group_tilt_series_frame_text(group.tilt_series, validation_by_id),
            inferred=True,
            failed_names=failed_names,
        )
        for tilt in group.tilt_series:
            validation = validation_by_id.get(tilt.id)
            assigned_tilt_ids.add(tilt.id)
            items[tilt.id] = TimelineItemModel(
                tilt_series_id=tilt.id,
                lane_key=f"inferred:{inferred_group_token(group.label, group.owner)}",
                lane_label=group.label,
                tooltip=tooltip,
                status=timeline_status_for(TILE_STATUS_NEUTRAL, validation),
            )
    for tilt in tilt_series:
        if tilt.id in assigned_tilt_ids:
            continue
        validation = validation_by_id.get(tilt.id)
        status = timeline_status_for(TILE_STATUS_NEUTRAL, validation)
        label = tilt.name or tilt.id
        items[tilt.id] = TimelineItemModel(
            tilt_series_id=tilt.id,
            lane_key=tilt.id,
            lane_label=label,
            tooltip=_timeline_unlinked_tilt_tooltip(tilt=tilt, validation=validation, status=status),
            status=status,
        )
    return items


def timeline_items_by_sample(
    sample_groups: list[tuple[str, list[Sample]]],
    validation_by_id: dict[str, Any],
) -> dict[str, TimelineItemModel]:
    """Group project/linked-session timeline lanes by sample.

    Large linked sessions can contain hundreds of tilt series. At that scale a
    lane per batch position is too dense, so the top-level dashboard groups the
    timeline by the same sample rows shown in the Samples card. Sample-scoped
    dashboards still use the more detailed batch-position lanes above.
    """

    items: dict[str, TimelineItemModel] = {}
    for label, samples in sample_groups:
        tilts = dedupe_tilt_series([tilt for sample in samples for tilt in sample.tilt_series])
        if not tilts:
            continue
        complete, warning, failed, queued = tilt_status_counts(tilts, validation_by_id)
        failed_names = tilt_names_with_status(tilts, validation_by_id, TILE_STATUS_FAILED)
        tooltip_lines = [
            label,
            f"Tilt series: {len(tilts)}",
            f"Complete: {complete}",
            f"Incomplete: {warning}",
            f"Failed: {failed}",
            f"Unknown: {queued}",
        ]
        failed_text = format_names_for_tooltip("Failed tilt series", failed_names)
        if failed_text:
            tooltip_lines.append(failed_text)
        tooltip = "\n".join(tooltip_lines)
        key = sample_group_key(label, samples)
        for tilt in tilts:
            validation = validation_by_id.get(tilt.id)
            items[tilt.id] = TimelineItemModel(
                tilt_series_id=tilt.id,
                lane_key=key,
                lane_label=label,
                tooltip=tooltip,
                status=timeline_status_for(TILE_STATUS_NEUTRAL, validation),
            )
    return items


def _timeline_group_tooltip(
    *,
    label: str,
    exposure_count: int,
    complete: int,
    warning: int,
    failed: int,
    queued: int,
    frame_text: str,
    inferred: bool,
    failed_names: list[str] | None = None,
) -> str:
    lines = [
        label,
        f"Exposures: {exposure_count}",
    ]
    if complete:
        lines.append(f"Complete: {complete}")
    if failed:
        lines.append(f"Failed: {failed}")
    if warning:
        lines.append(f"Incomplete: {warning}")
    if queued:
        lines.append(f"Pending: {queued}")
    lines.append(f"Tilt series frames: {frame_text}")
    failed_text = format_names_for_tooltip("Failed tilt series", failed_names or [])
    if failed_text:
        lines.append(failed_text)
    if inferred:
        lines.append("Inferred batch position from failed tilt series names")
    return "\n".join(lines)


def _timeline_unlinked_tilt_tooltip(
    *,
    tilt: TiltSeries,
    validation: Any | None,
    status: str,
) -> str:
    lines = [
        tilt.name or tilt.id,
        "Batch position: unavailable",
        f"Tilt series frames: {tilt_series_frame_text(tilt, validation)}",
    ]
    pretty_status = status.replace("_", " ")
    if pretty_status and pretty_status != "complete":
        lines.append(f"Status: {pretty_status}")
    return "\n".join(lines)


def scope_microscope_name(scope: DashboardScope) -> str | None:
    for tilt in scope.tilt_series:
        for section in tilt.sections:
            name = section.metadata.get("MicroscopeName") or section.metadata.get("InstrumentModel")
            if name:
                return str(name)
    return None


def scope_acquisition_window(
    scope: DashboardScope,
) -> tuple[str | None, str | None, str | None]:
    """Return ``(start_iso, end_iso, duration_human)`` for the acquisition window.

    ``duration_human`` is the elapsed wall-clock time between the earliest
    start and the latest end, formatted as e.g. ``"1 day, 14 hours,
    4 minutes"``. ``None`` when either end of the window is unavailable.
    """

    starts: list[datetime] = []
    ends: list[datetime] = []
    for tilt in scope.tilt_series:
        if tilt.acquisition_time_start:
            parsed = parse_datetime(tilt.acquisition_time_start)
            if parsed:
                starts.append(parsed)
        if tilt.acquisition_time_end:
            parsed = parse_datetime(tilt.acquisition_time_end)
            if parsed:
                ends.append(parsed)
    if not starts or not ends:
        return None, None, None
    start_dt = min(starts)
    end_dt = max(ends)
    return (
        start_dt.isoformat(sep=" ", timespec="minutes"),
        end_dt.isoformat(sep=" ", timespec="minutes"),
        format_duration_between(start_dt, end_dt),
    )
