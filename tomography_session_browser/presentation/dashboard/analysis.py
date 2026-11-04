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

from tomography_session_browser.services.tilt_series_validation import validation_key
from tomography_session_browser.domain.status import ValidationStatus
from tomography_session_browser.presentation.dashboard.models import DashboardScope, InferredBatchGroup
from tomography_session_browser.presentation.dashboard.scopes import scope_samples
from tomography_session_browser.presentation.dashboard.sections.batches import (
    group_matches_any_batch,
    inferred_failed_batch_groups,
)
from tomography_session_browser.services.batch_inference import entity_owner_keys
from tomography_session_browser.services.tilt_series_validation import GridValidations, TiltSeriesValidation


@dataclass(frozen=True, slots=True)
class ScopeAnalysis:
    #: ``entity.id`` -> the grid (sample folder) that holds it.
    owners: Mapping[str, str]
    #: One validation per tilt series, each against its own grid.
    validations: list[TiltSeriesValidation]
    validation_by_key: dict[str, TiltSeriesValidation]
    #: Batches inferred by name for failed series with no recorded batch.
    inferred_batch_groups: list[InferredBatchGroup]
    #: Failed series whose inferred batch matches no batch in the scope.
    orphaned_failed_tilts: int
    successful_tilt_ids: frozenset[str]

    @classmethod
    def of(cls, scope: DashboardScope, grid_validations: GridValidations) -> ScopeAnalysis:
        """``grid_validations`` judges each series by its whole grid: a
        dashboard narrowed to one search map still reads its row's verdict."""

        owners = entity_owner_keys(scope_samples(scope))
        validation_by_id = grid_validations.for_series(scope.tilt_series)
        validations = [validation_by_id[validation_key(validation_by_id, tilt)] for tilt in scope.tilt_series]
        inferred_batch_groups = inferred_failed_batch_groups(
            scope.batch_positions,
            scope.tilt_series,
            validation_by_id,
            owners,
        )
        return cls(
            owners=owners,
            validations=validations,
            validation_by_key=validation_by_id,
            inferred_batch_groups=inferred_batch_groups,
            orphaned_failed_tilts=sum(
                len(group.tilt_series)
                for group in inferred_batch_groups
                if not group_matches_any_batch(group, scope.batch_positions)
            ),
            successful_tilt_ids=frozenset(
                key for key, validation in validation_by_id.items() if validation.status == ValidationStatus.COMPLETE
            ),
        )
