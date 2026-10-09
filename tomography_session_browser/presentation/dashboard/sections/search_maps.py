"""Build Search-map overview rows and exposure acquisition rollups."""
from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any
from tomography_session_browser.domain.models import BatchPosition, SearchMap, TiltSeries
from tomography_session_browser.parsers.path_utils import casefold_natural_key
from tomography_session_browser.presentation.dashboard.models import (
    InferredBatchGroup,
    SearchMapAcquisitionRollup,
    SearchMapOverviewModel,
    SearchMapOverviewRowModel,
    TILE_STATUS_COMPLETE,
    TILE_STATUS_FAILED,
    TILE_STATUS_MISSING,
    TILE_STATUS_NEUTRAL,
    TILE_STATUS_WARNING,
)
from tomography_session_browser.presentation.dashboard.sections.batches import inferred_failed_batch_groups
from tomography_session_browser.presentation.dashboard.sections.status import batch_position_status, tile_status_from_validation_status
from tomography_session_browser.services.entity_links import (
    planned_exposures_for_batch_position,
    search_map_batches,
)
from tomography_session_browser.services.markers.inferred_failed import (
    inferred_failed_tilt_ids_for_search_map,
)


def search_map_overview_rows(
    search_maps: list[SearchMap],
    batch_positions: list[BatchPosition],
    tilt_series: list[TiltSeries],
    validations: list[Any],
    *,
    successful_tilt_ids: Iterable[str],
    owners: Mapping[str, str] | None = None,
    inferred_groups: list[InferredBatchGroup] | None = None,
) -> SearchMapOverviewModel:
    validation_by_id = {validation.tilt_series_id: validation for validation in validations}
    rows: list[SearchMapOverviewRowModel] = []
    complete = incomplete = failed = unknown = not_associated = 0

    for search_map in search_maps:
        rollup = search_map_acquisition_rollup(
            search_map,
            search_maps,
            batch_positions,
            tilt_series,
            validation_by_id,
            successful_tilt_ids=successful_tilt_ids,
            owners=owners,
            inferred_groups=inferred_groups,
        )
        linked_batch_ids: set[str] = set(search_map.linked_batch_position_ids or [])
        linked_batches: list[BatchPosition] = []
        seen_linked_batch_objects: set[str] = set()
        for batch in batch_positions:
            if batch.linked_search_map_id == search_map.id:
                linked_batch_ids.add(batch.id)
                linked_batches.append(batch)
                seen_linked_batch_objects.add(batch.id)
            elif batch.id in linked_batch_ids and batch.id not in seen_linked_batch_objects:
                linked_batches.append(batch)
                seen_linked_batch_objects.add(batch.id)
        acquisition_associated = bool(linked_batch_ids or rollup.tilt_series_ids)
        acquired_batch_positions = sum(
            1
            for batch in linked_batches
            if _batch_position_acquired_for_overview(batch, validation_by_id)
        )
        row_failed = rollup.failed_exposure_areas
        row_incomplete = rollup.incomplete_tilt_series
        row_unknown = rollup.unknown_tilt_series

        if not acquisition_associated:
            status = TILE_STATUS_NEUTRAL
            not_associated += 1
        elif row_failed:
            status = TILE_STATUS_FAILED
            failed += 1
        elif rollup.planned <= 0:
            status = TILE_STATUS_NEUTRAL
            unknown += 1
        elif rollup.acquired >= rollup.planned and not (row_incomplete or row_unknown):
            status = TILE_STATUS_COMPLETE
            complete += 1
        elif rollup.acquired <= 0:
            status = TILE_STATUS_MISSING
            unknown += 1
        else:
            status = TILE_STATUS_WARNING
            incomplete += 1

        rows.append(
            SearchMapOverviewRowModel(
                id=search_map.id,
                label=search_map.name or search_map.id,
                status=status,
                planned=rollup.planned,
                acquired=rollup.acquired,
                failed_tilt_series=row_failed,
                incomplete_tilt_series=row_incomplete,
                unknown_tilt_series=row_unknown,
                acquisition_associated=acquisition_associated,
                failed_batch_groups=rollup.failed_batch_groups,
                batch_positions=len(linked_batch_ids) + rollup.failed_batch_groups,
                acquired_batch_positions=acquired_batch_positions,
            )
        )

    rows.sort(key=lambda row: (not row.acquisition_associated, casefold_natural_key(row.label)))
    return SearchMapOverviewModel(
        rows=rows,
        complete=complete,
        incomplete=incomplete,
        failed=failed,
        unknown=unknown,
        not_associated=not_associated,
    )


def _batch_position_acquired_for_overview(
    batch: BatchPosition,
    validation_by_id: dict[str, Any],
) -> bool:
    statuses = [
        tile_status_from_validation_status(validation.status)
        for tilt_id in batch.linked_tilt_series_ids
        if (validation := validation_by_id.get(tilt_id)) is not None
    ]
    if statuses:
        return all(status == TILE_STATUS_COMPLETE for status in statuses)
    return batch_position_status(batch) == TILE_STATUS_COMPLETE


def acquired_exposures_for_batch(
    batch: BatchPosition,
    *,
    successful_tilt_ids: Iterable[str] | None = None,
) -> int:
    """Successfully-acquired exposure count for a batch position.

    When ``successful_tilt_ids`` is provided (the dashboard /
    report path), we count only the linked tilt series whose
    validator status is ``COMPLETE`` — failed tilt series do *not*
    contribute. This is what the search-map progress bar should
    reflect: how many planned exposure areas resulted in a usable
    tilt series.

    When the caller omits the validator data we fall back to the
    raw count of linked tilt series (treating them all as
    successful), which matches the legacy behaviour.
    """

    if successful_tilt_ids is not None:
        successful = set(successful_tilt_ids)
        return sum(1 for tid in batch.linked_tilt_series_ids if tid in successful)
    return len(batch.linked_tilt_series_ids)


def search_map_acquisition_rollup(
    search_map: SearchMap,
    search_maps: Iterable[SearchMap],
    batch_positions: Iterable[BatchPosition],
    tilt_series: Iterable[TiltSeries],
    validation_by_id: dict[str, Any],
    *,
    successful_tilt_ids: Iterable[str] | None = None,
    owners: Mapping[str, str] | None = None,
    inferred_groups: list[InferredBatchGroup] | None = None,
) -> SearchMapAcquisitionRollup:
    """Exposure progress and the tilt series ``search_map`` accounts for.

    ``inferred_groups``: the ``inferred_failed_batch_groups`` of these batches,
    series, verdicts and owners, when the caller already has them (they are
    the same for every map in a loop); computed here otherwise.
    """

    batches = list(batch_positions)
    maps = list(search_maps)
    tilts = list(tilt_series)
    planned, acquired = search_map_exposure_progress(
        search_map,
        batches,
        successful_tilt_ids=successful_tilt_ids,
    )
    planned = max(planned, acquired)
    linked_ids: list[str] = []
    seen_ids: set[str] = set()

    def add_linked_id(tilt_id: str | None) -> None:
        if not tilt_id or tilt_id in seen_ids:
            return
        linked_ids.append(tilt_id)
        seen_ids.add(tilt_id)

    for tilt_id in search_map.linked_tilt_series_ids or []:
        add_linked_id(tilt_id)
    for batch in search_map_batches(search_map, batches):
        for tilt_id in batch.linked_tilt_series_ids or []:
            add_linked_id(tilt_id)

    inferred_groups, stage_ids = _inferred_failed_groups_for_search_map(
        search_map,
        maps,
        batches,
        tilts,
        validation_by_id,
        owners=owners,
        inferred_groups=inferred_groups,
    )
    for group in inferred_groups:
        for tilt in group.tilt_series:
            add_linked_id(tilt.id)
    for tilt_id in stage_ids:
        add_linked_id(tilt_id)

    failed = incomplete = unknown = 0
    for tilt_id in linked_ids:
        validation = validation_by_id.get(tilt_id)
        if validation is None:
            continue
        if validation.status == TILE_STATUS_FAILED:
            failed += 1
        elif validation.status == "incomplete":
            incomplete += 1
        elif validation.status == "unknown":
            unknown += 1
    if failed or incomplete or unknown:
        planned = max(planned, acquired + failed + incomplete + unknown)

    return SearchMapAcquisitionRollup(
        planned=planned,
        acquired=acquired,
        tilt_series_ids=linked_ids,
        failed_exposure_areas=failed,
        incomplete_tilt_series=incomplete,
        unknown_tilt_series=unknown,
        failed_batch_groups=len({(group.owner, group.key) for group in inferred_groups}),
        stage_inferred_failed_tilts=len(stage_ids),
    )


def _inferred_failed_groups_for_search_map(
    search_map: SearchMap,
    _search_maps: Iterable[SearchMap],
    batch_positions: list[BatchPosition],
    tilt_series: list[TiltSeries],
    validation_by_id: dict[str, Any],
    *,
    owners: Mapping[str, str] | None = None,
    inferred_groups: list[InferredBatchGroup] | None = None,
) -> tuple[list[InferredBatchGroup], frozenset[str]]:
    if inferred_groups is None:
        inferred_groups = inferred_failed_batch_groups(batch_positions, tilt_series, validation_by_id, owners)
    if not inferred_groups:
        return [], frozenset()

    assigned: dict[tuple[str | None, str], InferredBatchGroup] = {}
    linked_batches = search_map_batches(search_map, batch_positions)
    for batch in linked_batches:
        for group in inferred_groups:
            if batch.id in group.batch_ids:
                assigned[(group.owner, group.key)] = group

    failed_tilt_ids = frozenset(
        tilt_id
        for tilt_id, validation in validation_by_id.items()
        if getattr(validation, "status", None) == TILE_STATUS_FAILED
    )
    stage_ids = inferred_failed_tilt_ids_for_search_map(
        search_map,
        tilt_series=tilt_series,
        failed_tilt_ids=failed_tilt_ids,
        batch_positions=batch_positions,
    )
    if stage_ids:
        for group in inferred_groups:
            if any(tilt.id in stage_ids for tilt in group.tilt_series):
                assigned[(group.owner, group.key)] = group

    all_inferred_tilt_ids = {tilt.id for group in inferred_groups for tilt in group.tilt_series}
    assigned_tilt_ids = {tilt.id for group in assigned.values() for tilt in group.tilt_series}
    stage_ids = frozenset(
        tilt_id
        for tilt_id in stage_ids
        if tilt_id in assigned_tilt_ids or tilt_id not in all_inferred_tilt_ids
    )

    return list(assigned.values()), stage_ids


def unresolved_inferred_failed_batch_groups(
    search_maps: Iterable[SearchMap],
    batch_positions: list[BatchPosition],
    tilt_series: list[TiltSeries],
    validation_by_id: dict[str, Any],
    owners: Mapping[str, str] | None = None,
    *,
    inferred_groups: list[InferredBatchGroup],
) -> list[InferredBatchGroup]:
    """The ``inferred_groups`` (from ``inferred_failed_batch_groups``) that no
    search map takes, through its batches or its image's stage extent."""

    unresolved = {(group.owner, group.key): group for group in inferred_groups}
    for search_map in search_maps:
        groups, _stage_ids = _inferred_failed_groups_for_search_map(
            search_map,
            search_maps,
            batch_positions,
            tilt_series,
            validation_by_id,
            owners=owners,
            inferred_groups=inferred_groups,
        )
        for group in groups:
            unresolved.pop((group.owner, group.key), None)
    return list(unresolved.values())


def search_map_exposure_progress(
    search_map: SearchMap,
    batch_positions: Iterable[BatchPosition],
    *,
    successful_tilt_ids: Iterable[str] | None = None,
) -> tuple[int, int]:
    """Return ``(planned, acquired)`` exposure counts for ``search_map``.

    Aggregates planned + successfully-acquired exposures over every
    batch position that links to ``search_map.id``. Failed tilt
    series are excluded from the acquired total so the bar reflects
    *successful* acquisitions only — this matches what the user
    expects to see when, e.g., a sample loses two tilt
    series and the bar should drop accordingly.

    ``successful_tilt_ids`` carries the validator's complete-status
    set; pass ``None`` for legacy behaviour (treat every linked
    tilt series as successful).
    """

    planned = 0
    acquired = 0
    for batch in search_map_batches(search_map, batch_positions):
        planned += planned_exposures_for_batch_position(batch)
        acquired += acquired_exposures_for_batch(
            batch, successful_tilt_ids=successful_tilt_ids
        )
    return planned, acquired
