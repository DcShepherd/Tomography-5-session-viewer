"""Derive batch progress rows and the existing inferred failed-batch groups."""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any
from tomography_session_browser.services.tilt_series_validation import validation_for
from tomography_session_browser.domain.models import BatchPosition, TiltSeries
from tomography_session_browser.parsers.path_utils import casefold_natural_key, natural_key
from tomography_session_browser.presentation.dashboard.models import (
    BatchPositionProgressModel,
    INFERRED_BATCH_TILE_PREFIX,
    InferredBatchGroup,
    TILE_STATUS_COMPLETE,
    TILE_STATUS_FAILED,
    TILE_STATUS_NEUTRAL,
    TILE_STATUS_WARNING,
)
from tomography_session_browser.presentation.dashboard.sections.status import batch_position_status, tile_status_from_validation_status, tilt_status_counts
from tomography_session_browser.presentation.dashboard.sections.tilt_labels import group_tilt_series_frame_text
from tomography_session_browser.services.batch_inference import (
    batch_inference_key,
    inferred_batch_label_for_tilt,
    inferred_group_token,
    inferred_label_matches_batch,
)
from tomography_session_browser.services.entity_links import (
    batch_tilt_series,
    explicitly_linked_tilt_ids,
)
from tomography_session_browser.services.item_status import batch_outcome, build_item_status_context
from tomography_session_browser.services.timeline_service import datetime_sort_key


def dedupe_tilt_series(tilt_series: list[TiltSeries]) -> list[TiltSeries]:
    unique: list[TiltSeries] = []
    seen: set[str] = set()
    for tilt in tilt_series:
        key = tilt.id
        if key in seen:
            continue
        seen.add(key)
        unique.append(tilt)
    return unique


def batch_position_progress_rows(
    batch_positions: list[BatchPosition],
    tilt_series: list[TiltSeries],
    validation_by_id: dict[str, Any],
    *,
    inferred_batch_groups: list[InferredBatchGroup] | None = None,
) -> list[BatchPositionProgressModel]:
    """Return sample-level rows that summarise each batch position.

    The selected sample dashboard is about acquisition positions rather than
    the project-wide sample list, so each row counts the tilt series linked to
    one batch position and colours the progress strip from the same validator
    statuses used by the donut and metric tiles.
    """

    rows: list[BatchPositionProgressModel] = []
    inferred_batch_groups = inferred_batch_groups or []
    context = build_item_status_context(
        batch_positions=batch_positions, tilt_series=tilt_series, validations=validation_by_id,
    )
    for batch in batch_positions:
        outcome = batch_outcome(batch, context)
        counts = outcome.counts
        exposure_count = counts.planned
        complete, warning, failed = counts.complete, counts.incomplete, counts.failed
        queued = counts.missing + counts.unknown
        acquisition_status = outcome.status
        status = {
            "done": TILE_STATUS_COMPLETE,
            "failed": TILE_STATUS_FAILED,
            "partial": TILE_STATUS_WARNING,
            "incomplete": TILE_STATUS_WARNING,
        }.get(acquisition_status, TILE_STATUS_NEUTRAL)
        label = batch_position_label(batch)
        rows.append(
            BatchPositionProgressModel(
                id=batch.id,
                label=label,
                exposure_count=exposure_count,
                tilt_series=outcome.member_count,
                complete=complete,
                warning=warning,
                failed=failed,
                queued=queued,
                status=status,
                tooltip=_batch_position_progress_tooltip(
                    label=label,
                    exposure_count=exposure_count,
                    linked_tilts=outcome.member_count,
                    complete=complete,
                    warning=warning,
                    failed=failed,
                    queued=queued,
                    batch_status=batch_position_status(batch),
                ),
            )
        )
    for group in inferred_batch_groups:
        complete, warning, failed, queued = tilt_status_counts(group.tilt_series, validation_by_id)
        status = _dominant_batch_row_status(
            batch_status=TILE_STATUS_NEUTRAL,
            complete=complete,
            warning=warning,
            failed=failed,
            queued=queued,
        )
        frame_text = group_tilt_series_frame_text(group.tilt_series, validation_by_id)
        rows.append(
            BatchPositionProgressModel(
                id=f"{INFERRED_BATCH_TILE_PREFIX}{inferred_group_token(group.label, group.owner)}",
                label=group.label,
                exposure_count=len(group.tilt_series),
                tilt_series=len(group.tilt_series),
                complete=complete,
                warning=warning,
                failed=failed,
                queued=queued,
                status=status,
                tooltip=_batch_position_progress_tooltip(
                    label=group.label,
                    exposure_count=len(group.tilt_series),
                    linked_tilts=len(group.tilt_series),
                    complete=complete,
                    warning=warning,
                    failed=failed,
                    queued=queued,
                    batch_status=TILE_STATUS_NEUTRAL,
                    frame_text=frame_text,
                    inferred=True,
                ),
                inferred=True,
            )
        )
    rows.sort(key=lambda row: casefold_natural_key(row.label))
    return rows


def tilt_series_for_batch(
    batch: BatchPosition,
    tilt_series: list[TiltSeries],
    batch_positions: list[BatchPosition],
) -> list[TiltSeries]:
    linked = batch_tilt_series(batch, tilt_series, batch_positions=batch_positions)
    linked.sort(
        key=lambda tilt: (
            *datetime_sort_key(tilt.acquisition_time_start),
            natural_key(tilt.name or tilt.id),
        )
    )
    return linked


def inferred_failed_batch_groups(
    batch_positions: list[BatchPosition],
    tilt_series: list[TiltSeries],
    validation_by_id: dict[str, Any],
    owners: Mapping[str, str] | None = None,
) -> list[InferredBatchGroup]:
    """Infer batch membership for unlinked failed tilt series by name.

    Tomography 5 sometimes leaves failed exposure attempts without a
    ``linked_batch_position_id`` or BatchPosition reference. We apply this
    conservative heuristic only to validator-failed, explicitly-unlinked tilt
    series: a final numeric repeat suffix is stripped from names shaped like
    ``<sample>_<batch>_<repeat>``. Thus ``violet_1_2`` groups with
    ``violet_1``, while ``violet_1``, ``violet_2`` and ``violet_3`` remain
    distinct batch positions.

    ``owners`` (``batch_inference.entity_owner_keys``) keeps a group on its
    own grid: batch names repeat across grids, and an orphan from one grid
    must not join another grid's batch of the same name.
    """

    explicitly_linked = explicitly_linked_tilt_ids(batch_positions, tilt_series)
    grouped: dict[tuple[str | None, str], InferredBatchGroup] = {}
    for tilt in sorted(
        tilt_series,
        key=lambda item: (
            *datetime_sort_key(item.acquisition_time_start),
            natural_key(item.name or item.id),
        ),
    ):
        if tilt.id in explicitly_linked or tilt.linked_batch_position_id:
            continue
        validation = validation_for(validation_by_id, tilt)
        status = (
            tile_status_from_validation_status(validation.status)
            if validation is not None
            else TILE_STATUS_NEUTRAL
        )
        if status != TILE_STATUS_FAILED:
            continue
        label = inferred_batch_label_for_tilt(tilt)
        if label is None:
            continue
        key = batch_inference_key(label)
        owner = owners.get(tilt.id) if owners else None
        if (owner, key) not in grouped:
            grouped[(owner, key)] = InferredBatchGroup(key=key, label=label, tilt_series=[], owner=owner)
        grouped[(owner, key)].tilt_series.append(tilt)
    for group in grouped.values():
        first = group.tilt_series[0].id
        group.batch_ids = frozenset(
            batch.id
            for batch in batch_positions
            if inferred_label_matches_batch(group.label, batch, tilt_id=first, owners=owners)
        )
    return list(grouped.values())


def batch_position_label(batch: BatchPosition) -> str:
    return batch.name or batch.id or "Batch position"


def _dominant_batch_row_status(
    *,
    batch_status: str,
    complete: int,
    warning: int,
    failed: int,
    queued: int,
) -> str:
    if batch_status == TILE_STATUS_FAILED or failed:
        return TILE_STATUS_FAILED
    if warning:
        return TILE_STATUS_WARNING
    if complete and not queued:
        return TILE_STATUS_COMPLETE
    if complete:
        return TILE_STATUS_WARNING
    return batch_status if batch_status != TILE_STATUS_NEUTRAL else TILE_STATUS_NEUTRAL


def _batch_position_progress_tooltip(
    *,
    label: str,
    exposure_count: int,
    linked_tilts: int,
    complete: int,
    warning: int,
    failed: int,
    queued: int,
    batch_status: str,
    frame_text: str | None = None,
    inferred: bool = False,
) -> str:
    lines = [
        label,
        f"Exposures: {exposure_count}",
        f"Tilt series: {linked_tilts}",
    ]
    if complete:
        lines.append(f"Complete: {complete}")
    if failed:
        lines.append(f"Failed: {failed}")
    if warning:
        lines.append(f"Incomplete: {warning}")
    if queued:
        lines.append(f"Pending: {queued}")
    if frame_text:
        lines.append(f"Tilt series frames: {frame_text}")
    if inferred:
        lines.append("Inferred batch position from failed tilt series names")
    if batch_status != TILE_STATUS_NEUTRAL:
        lines.append(f"Batch status: {batch_status.replace('_', ' ')}")
    return "\n".join(lines)
