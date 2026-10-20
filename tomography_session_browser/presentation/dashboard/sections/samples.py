"""The by-sample rows of a collection summary (design review B3.1)."""
from __future__ import annotations

from dataclasses import dataclass

from tomography_session_browser.domain.models import Sample
from tomography_session_browser.domain.traversal import has_collection_data
from tomography_session_browser.presentation.dashboard.analysis import ScopeAnalysis
from tomography_session_browser.presentation.dashboard.models import DashboardScope
from tomography_session_browser.presentation.dashboard.sections.batches import group_matches_any_batch
from tomography_session_browser.presentation.dashboard.sections.status import search_map_is_incomplete
from tomography_session_browser.presentation.workspace import display_sample_name
from tomography_session_browser.services.item_status import ItemStatusContext
from tomography_session_browser.services.tilt_series_validation import (
    STATUS_COMPLETE,
    STATUS_FAILED,
    STATUS_INCOMPLETE,
)


@dataclass(frozen=True, slots=True)
class BySampleRowModel:
    """One sample (or one grid seen in several sessions) of a collection scope.

    Counts what the sample *recorded*. Planned acquisitions that were never
    recorded are not counted. No totals row: the outcome panel above is the
    total, and the rows must add up to it.
    """

    label: str
    #: Every sample the row covers (a grid's atlas and collection samples).
    sample_ids: tuple[str, ...]
    #: Those holding collection data: the row's link target when there is exactly one.
    collection_sample_ids: tuple[str, ...]
    tilt_complete: int
    tilt_incomplete: int
    tilt_failed: int
    tilt_unknown: int
    #: Recorded batch positions.
    batch_positions: int
    #: Failed series whose batch was inferred from their names and that no
    #: recorded batch holds: shown beside the recorded ones, never added to them.
    inferred_failed_batches: int
    search_maps: int
    #: Search maps whose list row reads Incomplete (the Search map tab's own rule).
    incomplete_search_maps: int
    overviews: int

    @property
    def tilt_total(self) -> int:
        return self.tilt_complete + self.tilt_incomplete + self.tilt_failed + self.tilt_unknown


def by_sample_rows(
    scope: DashboardScope,
    analysis: ScopeAnalysis,
    status_context: ItemStatusContext,
) -> list[BySampleRowModel]:
    """A row per sample group of ``scope`` that holds collection data, from the
    scope's own verdicts. A group with none (an atlas slot never collected on)
    is the atlas page's to list; every counted entity implies collection data,
    so the rows still add up to the scope. The row is named for its collection
    sample ("alpha"), or the group's label when there are several."""

    orphan_groups = [
        group
        for group in analysis.inferred_batch_groups
        if not group_matches_any_batch(group, scope.batch_positions)
    ]
    rows: list[BySampleRowModel] = []
    for group_label, samples in scope.sample_groups:
        collection = [sample for sample in samples if has_collection_data(sample)]
        if not collection:
            continue
        label = display_sample_name(collection[0].name) if len(collection) == 1 else group_label
        grids = {str(sample.path) for sample in samples}
        tilts = _in_scope(samples, analysis)
        statuses = [analysis.validation_by_id[tilt_id].status for tilt_id in tilts]
        map_ids = {id(search_map) for sample in samples for search_map in sample.search_maps}
        batch_ids = {id(batch) for sample in samples for batch in sample.batch_positions}
        overview_ids = {id(overview) for sample in samples for overview in sample.overviews}
        search_maps = [search_map for search_map in scope.search_maps if id(search_map) in map_ids]
        rows.append(
            BySampleRowModel(
                label=label,
                sample_ids=tuple(sample.id for sample in samples),
                collection_sample_ids=tuple(sample.id for sample in collection),
                tilt_complete=statuses.count(STATUS_COMPLETE),
                tilt_incomplete=statuses.count(STATUS_INCOMPLETE),
                tilt_failed=statuses.count(STATUS_FAILED),
                tilt_unknown=len(statuses)
                - statuses.count(STATUS_COMPLETE)
                - statuses.count(STATUS_INCOMPLETE)
                - statuses.count(STATUS_FAILED),
                batch_positions=sum(id(batch) in batch_ids for batch in scope.batch_positions),
                inferred_failed_batches=sum(1 for group in orphan_groups if group.owner in grids),
                search_maps=len(search_maps),
                incomplete_search_maps=sum(
                    1 for search_map in search_maps if search_map_is_incomplete(search_map, status_context)
                ),
                overviews=sum(id(overview) in overview_ids for overview in scope.overviews),
            )
        )
    return rows


def _in_scope(samples: list[Sample], analysis: ScopeAnalysis) -> list[str]:
    """The ids of the samples' tilt series that the scope holds, once each."""

    seen: dict[str, None] = {}
    for sample in samples:
        for tilt in sample.tilt_series:
            if tilt.id in analysis.validation_by_id:
                seen.setdefault(tilt.id, None)
    return list(seen)
