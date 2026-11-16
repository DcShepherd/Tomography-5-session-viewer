from __future__ import annotations

from dataclasses import dataclass

from tomography_session_browser.domain.models import BatchPosition
from tomography_session_browser.domain.status import AtlasMarkerStatus, RowStatus, marker_severity
from tomography_session_browser.services.item_status import (
    BatchOutcome,
    ItemStatusContext,
    batch_outcome,
)


@dataclass(frozen=True, slots=True)
class BatchPositionOverlayStatus:
    batch_position_id: str
    status: AtlasMarkerStatus
    planned: int
    acquired: int
    complete: int
    incomplete: int
    failed: int
    missing: int
    unknown: int


def aggregate_batch_position_status(
    batch: BatchPosition,
    context: ItemStatusContext,
) -> BatchPositionOverlayStatus:
    """Map the shared batch outcome to Atlas marker status."""
    return overlay_status_from_outcome(batch, batch_outcome(batch, context))


def overlay_status_from_outcome(batch: BatchPosition, outcome: BatchOutcome) -> BatchPositionOverlayStatus:
    """The Atlas marker status of ``batch`` from its already computed outcome."""
    counts = outcome.counts
    acquired = counts.acquired
    source_status = outcome.status
    if source_status == RowStatus.FAILED:
        status = AtlasMarkerStatus.FAILED
    elif acquired <= 0:
        status = AtlasMarkerStatus.QUEUED
    elif source_status == RowStatus.DONE:
        status = AtlasMarkerStatus.COLLECTED
    else:
        status = AtlasMarkerStatus.PARTIAL
    return BatchPositionOverlayStatus(
        batch_position_id=batch.id,
        status=status,
        planned=counts.planned,
        acquired=acquired,
        complete=counts.complete,
        incomplete=counts.incomplete,
        failed=counts.failed,
        missing=counts.missing,
        unknown=counts.unknown,
    )


def worst_status(statuses: list[str] | tuple[str, ...]) -> str:
    if not statuses:
        return AtlasMarkerStatus.QUEUED
    return max(statuses, key=marker_severity)
