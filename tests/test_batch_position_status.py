from __future__ import annotations

import pytest

from tomography_session_browser.domain.models import BatchPosition
from tomography_session_browser.services import batch_position_status as service
from tomography_session_browser.services.batch_position_status import (
    STATUS_COLLECTED,
    STATUS_FAILED,
    STATUS_PARTIAL,
    STATUS_QUEUED,
    STATUS_UNATTRIBUTED,
    aggregate_batch_position_status,
    worst_status,
)
from tomography_session_browser.services.item_status import (
    BatchPositionStatusCounts,
    ItemListStatus,
    ItemStatusContext,
)


EMPTY_CONTEXT = ItemStatusContext((), (), (), (), {}, {})


@pytest.mark.parametrize(
    ("counts", "source_status", "expected"),
    [
        (BatchPositionStatusCounts(2, 2, 0, 0, 0, 0), "done", STATUS_COLLECTED),
        (BatchPositionStatusCounts(2, 1, 0, 1, 0, 0), "partial", STATUS_PARTIAL),
        (BatchPositionStatusCounts(2, 1, 1, 0, 0, 0), "partial", STATUS_PARTIAL),
        (BatchPositionStatusCounts(2, 0, 0, 0, 2, 0), "missing", STATUS_QUEUED),
        (BatchPositionStatusCounts(2, 0, 0, 2, 0, 0), "failed", STATUS_FAILED),
    ],
)
def test_status_aggregation_precedence(
    monkeypatch: pytest.MonkeyPatch,
    counts: BatchPositionStatusCounts,
    source_status: str,
    expected: str,
) -> None:
    monkeypatch.setattr(
        service,
        "batch_position_status_counts",
        lambda *_args, **_kwargs: counts,
    )
    monkeypatch.setattr(
        service,
        "item_list_status",
        lambda *_args, **_kwargs: ItemListStatus(source_status, ""),
    )
    result = aggregate_batch_position_status(
        BatchPosition(id="batch-1", name="Position_1"),
        EMPTY_CONTEXT,
    )
    assert result.status == expected
    assert result.planned == counts.planned
    assert result.acquired == counts.acquired


def test_unattributed_remains_distinct_in_severity_aggregation() -> None:
    assert worst_status((STATUS_COLLECTED, STATUS_UNATTRIBUTED)) == STATUS_UNATTRIBUTED
    assert worst_status((STATUS_UNATTRIBUTED, STATUS_FAILED)) == STATUS_FAILED
