from __future__ import annotations

from dataclasses import dataclass

from tomography_session_browser.domain.models import BatchPosition
from tomography_session_browser.domain.status import AtlasMarkerStatus, RowStatus, marker_severity
from tomography_session_browser.services.item_status import (
    ItemStatusContext,
    batch_position_status_counts,
    item_list_status,
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
    """Map existing validation/item-status evidence to Atlas marker status.

    Inferred orphan labels are intentionally excluded. They are useful display
    context elsewhere, but are not strong enough to attach a failed tilt series
    to a neighbouring batch in the Atlas overlay.
    """

    counts = batch_position_status_counts(batch, context, include_inferred=False)
    acquired = counts.acquired
    source_status = item_list_status(batch, context, include_inferred=False).status
    failed_count = counts.failed
    if source_status == RowStatus.FAILED:
        status = AtlasMarkerStatus.FAILED
        failed_count = max(failed_count, 1)
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
        failed=failed_count,
        missing=counts.missing,
        unknown=counts.unknown,
    )


def worst_status(statuses: list[str] | tuple[str, ...]) -> str:
    if not statuses:
        return AtlasMarkerStatus.QUEUED
    return max(statuses, key=marker_severity)
