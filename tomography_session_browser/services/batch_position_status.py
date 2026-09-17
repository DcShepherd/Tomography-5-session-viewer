from __future__ import annotations

from dataclasses import dataclass

from tomography_session_browser.domain.models import BatchPosition
from tomography_session_browser.services.item_status import (
    ItemStatusContext,
    STATUS_DONE,
    STATUS_FAILED_LABEL,
    batch_position_status_counts,
    item_list_status,
)


STATUS_COLLECTED = "collected"
STATUS_PARTIAL = "partial"
STATUS_QUEUED = "queued"
STATUS_FAILED = "failed"
STATUS_UNATTRIBUTED = "unattributed"

STATUS_SEVERITY = {
    STATUS_COLLECTED: 0,
    STATUS_QUEUED: 1,
    STATUS_PARTIAL: 2,
    STATUS_UNATTRIBUTED: 3,
    STATUS_FAILED: 4,
}


@dataclass(frozen=True, slots=True)
class BatchPositionOverlayStatus:
    batch_position_id: str
    status: str
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
    if source_status == STATUS_FAILED_LABEL:
        status = STATUS_FAILED
        failed_count = max(failed_count, 1)
    elif acquired <= 0:
        status = STATUS_QUEUED
    elif source_status == STATUS_DONE:
        status = STATUS_COLLECTED
    else:
        status = STATUS_PARTIAL
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
        return STATUS_QUEUED
    return max(statuses, key=lambda value: STATUS_SEVERITY.get(value, 1))
