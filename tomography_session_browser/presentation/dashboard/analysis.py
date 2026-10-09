"""What a dashboard scope's tilt series add up to, worked out once.

Every section of the dashboard reads the same verdicts: which grid holds each
series and batch, each series' validation (against its own grid's expected
count), the batches inferred by name for failed series that recorded none,
and which series completed. ``ScopeAnalysis.of`` computes them once per scope
so the cards, the donut, the batch rows, the search-map rollups, the
timeline and the warnings cannot disagree.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from tomography_session_browser.presentation.dashboard.models import DashboardScope, InferredBatchGroup
from tomography_session_browser.presentation.dashboard.scopes import scope_samples, validate_scope_against_its_grids
from tomography_session_browser.presentation.dashboard.sections.batches import (
    group_matches_any_batch,
    inferred_failed_batch_groups,
)
from tomography_session_browser.services.batch_inference import entity_owner_keys
from tomography_session_browser.services.tilt_series_validation import STATUS_COMPLETE, TiltSeriesValidation


@dataclass(frozen=True, slots=True)
class ScopeAnalysis:
    #: ``entity.id`` -> the grid (sample folder) that holds it.
    owners: Mapping[str, str]
    #: One validation per tilt series, each against its own grid.
    validations: list[TiltSeriesValidation]
    validation_by_id: dict[str, TiltSeriesValidation]
    #: Batches inferred by name for failed series with no recorded batch.
    inferred_batch_groups: list[InferredBatchGroup]
    #: Failed series whose inferred batch matches no batch in the scope.
    orphaned_failed_tilts: int
    successful_tilt_ids: frozenset[str]

    @classmethod
    def of(cls, scope: DashboardScope) -> ScopeAnalysis:
        owners = entity_owner_keys(scope_samples(scope))
        validations = validate_scope_against_its_grids(scope, owners)
        validation_by_id = {validation.tilt_series_id: validation for validation in validations}
        inferred_batch_groups = inferred_failed_batch_groups(
            scope.batch_positions,
            scope.tilt_series,
            validation_by_id,
            owners,
        )
        return cls(
            owners=owners,
            validations=validations,
            validation_by_id=validation_by_id,
            inferred_batch_groups=inferred_batch_groups,
            orphaned_failed_tilts=sum(
                len(group.tilt_series)
                for group in inferred_batch_groups
                if not group_matches_any_batch(group, scope.batch_positions)
            ),
            successful_tilt_ids=frozenset(
                validation.tilt_series_id for validation in validations if validation.status == STATUS_COMPLETE
            ),
        )
