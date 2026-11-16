"""Shared visual contract for Atlas batch-position markers.

The live Qt viewer and the ReportLab export use the same numeric geometry and
status palette.  Keep this module Qt-free so report generation and pure tests
do not acquire a GUI dependency.
"""

from __future__ import annotations

from collections.abc import Hashable
from dataclasses import dataclass
from typing import Generic, TypeVar

from tomography_session_browser.domain.status import AtlasMarkerStatus


#: A cluster ring's segments, clockwise: the most severe first.
STATUS_ORDER = tuple(reversed(AtlasMarkerStatus))

#: Keyed by marker status; an ``ImageMarker.status`` is a plain word.
IMAGE_STATUS_COLOURS: dict[str, str] = {
    AtlasMarkerStatus.COLLECTED: "#48B06C",
    AtlasMarkerStatus.PARTIAL: "#C78B09",
    AtlasMarkerStatus.QUEUED: "#599FD8",
    AtlasMarkerStatus.FAILED: "#FD7468",
    AtlasMarkerStatus.UNATTRIBUTED: "#8F9AA4",
}

PRINT_STATUS_COLOURS: dict[str, str] = {
    AtlasMarkerStatus.COLLECTED: "#137D41",
    AtlasMarkerStatus.PARTIAL: "#8E5E00",
    AtlasMarkerStatus.QUEUED: "#036EAE",
    AtlasMarkerStatus.FAILED: "#C53732",
    AtlasMarkerStatus.UNATTRIBUTED: "#606A74",
}

MARKER_INK = "#090E12"
MARKER_SELECTED = "#EED059"
MARKER_LABEL_TEXT = "#D6DDE6"

LEAF_DIAMETER_DP = 14.0
LEAF_RING_RADIUS_DP = 5.0
LEAF_FILL_RADIUS_DP = 3.4
LEAF_HALO_WIDTH_DP = 3.0
LEAF_STATUS_WIDTH_DP = 1.6
LEAF_FAILED_GLYPH_WIDTH_DP = 1.5
LEAF_HIT_RADIUS_DP = 11.0
STATE_RING_OFFSET_DP = 4.0

CLUSTER_HALO_WIDTH_DP = 3.6
CLUSTER_SEGMENT_WIDTH_DP = 2.4
CLUSTER_INNER_GAP_DP = 2.2
CLUSTER_GAP_DEGREES = 6.0
CLUSTER_MIN_SEGMENT_DEGREES = 14.0

LABEL_FONT_SIZE_DP = 10.0
LABEL_HEIGHT_DP = 16.0
LABEL_HORIZONTAL_PADDING_DP = 5.0
LABEL_CORNER_RADIUS_DP = 3.0
LABEL_OFFSET_DP = LEAF_RING_RADIUS_DP + 6.0
LABEL_MAX_NUDGE_DP = 18.0


#: What a ring segment stands for: a status word today, any hashable key later.
SegmentKey = TypeVar("SegmentKey", bound=Hashable)


@dataclass(frozen=True, slots=True)
class RingSegment(Generic[SegmentKey]):
    key: SegmentKey
    start_degrees: float
    sweep_degrees: float


def cluster_ring_segments(
    status_counts: dict[str, int] | tuple[tuple[str, int], ...],
) -> tuple[RingSegment[str], ...]:
    """Return fixed-order clockwise cluster-ring segments for status counts."""

    counts = dict(status_counts)
    return ring_segments(tuple(
        (status, int(counts.get(status, 0))) for status in STATUS_ORDER
    ))


def ring_segments(
    ordered_counts: tuple[tuple[SegmentKey, int], ...],
) -> tuple[RingSegment[SegmentKey], ...]:
    """Clockwise ring segments for ``(key, count)`` pairs, in the order given.

    Every present key receives at least 14 degrees.  If applying that floor
    would overflow the available sweep, the excess is removed proportionally
    from segments that remain above the floor.
    """

    present = [(key, int(count)) for key, count in ordered_counts if int(count) > 0]
    if not present:
        return ()

    gap = CLUSTER_GAP_DEGREES if len(present) > 1 else 0.0
    available = 360.0 - gap * len(present)
    total = sum(count for _key, count in present)
    sweeps = [available * count / total for _status, count in present]
    sweeps = [max(CLUSTER_MIN_SEGMENT_DEGREES, sweep) for sweep in sweeps]

    overflow = sum(sweeps) - available
    while overflow > 1e-9:
        reducible = [
            index
            for index, sweep in enumerate(sweeps)
            if sweep > CLUSTER_MIN_SEGMENT_DEGREES + 1e-9
        ]
        if not reducible:
            break
        capacity = sum(
            sweeps[index] - CLUSTER_MIN_SEGMENT_DEGREES for index in reducible
        )
        if capacity <= 1e-9:
            break
        for index in reducible:
            share = (
                sweeps[index] - CLUSTER_MIN_SEGMENT_DEGREES
            ) / capacity
            reduction = min(
                sweeps[index] - CLUSTER_MIN_SEGMENT_DEGREES,
                overflow * share,
            )
            sweeps[index] -= reduction
        overflow = sum(sweeps) - available

    start = -90.0 + gap / 2.0
    segments: list[RingSegment[SegmentKey]] = []
    for (key, _count), sweep in zip(present, sweeps):
        segments.append(RingSegment(key, start, sweep))
        start += sweep + gap
    return tuple(segments)


def cluster_diameter_dp(member_count: int) -> float:
    if member_count <= 4:
        return 22.0
    if member_count <= 9:
        return 28.0
    if member_count <= 24:
        return 34.0
    return 40.0

