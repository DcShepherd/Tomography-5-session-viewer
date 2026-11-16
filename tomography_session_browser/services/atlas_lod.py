"""The Atlas tab's zoom-dependent batch-position display, as a pure rule.

At a given zoom the Atlas tab draws each batch position either as its own
status glyph (a *leaf*) or merged into a cluster ring with nearby positions,
and numbers leaves only from :data:`ATLAS_LABEL_MIN_ZOOM` upward, placing each
number where it collides with nothing. ``ImagePreviewView`` and the PDF
report both follow this rule; ``tests/test_atlas_lod_parity.py`` checks that
they reach the same answer for the same zoom.

Display-only and Qt-free: positions are the markers' projected image
coordinates, ``screen_scale`` is screen units (dp) per image pixel, and
``logical_zoom`` is relative to fitting the whole atlas (1.0 = fit).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from tomography_session_browser.domain.markers import ImageMarker
from tomography_session_browser.services.batch_label_service import (
    AtlasLabelPlacement,
    atlas_batch_label_placements,
)
from tomography_session_browser.services.marker_clustering import (
    NO_CLUSTER_ABOVE_ZOOM,
    ClusterInput,
    MarkerCluster,
    ScreenTransform,
    cluster_markers,
)

#: Batch-position numbers appear from 200 % of the fit zoom.
ATLAS_LABEL_MIN_ZOOM = 2.0

#: Glyph key of a failed tilt series that no batch position claims. A display
#: key only: the marker's status stays ``failed``.
UNLINKED_FAILED_KEY = "failed_unlinked"
#: Glyph key of a batch position the resolver cannot navigate to.
UNRESOLVED_LINK_KEY = "unattributed"
#: The order of the glyph keys in a legend or a card, top to bottom.
ATLAS_GLYPH_KEYS = ("collected", "partial", "queued", "failed", UNLINKED_FAILED_KEY, UNRESOLVED_LINK_KEY)
#: Glyph keys drawn with a dashed ring: neither has a confident batch link.
DASHED_GLYPH_KEYS = frozenset((UNLINKED_FAILED_KEY, UNRESOLVED_LINK_KEY))


def is_unlinked_failure(marker: ImageMarker) -> bool:
    """A failed tilt series drawn on the Atlas without a batch-position link."""

    return marker.metadata.get("atlas_lod_role") == "unattributed"


def atlas_glyph_key(marker: ImageMarker) -> str:
    """The one rule for which glyph an Atlas batch marker draws and counts under.

    The leaf painter, the goo blobs, the cluster inputs, the on-screen card and
    the PDF legend all use it, so their glyphs and counts agree.
    """

    if is_unlinked_failure(marker):
        return UNLINKED_FAILED_KEY
    if not bool(marker.metadata.get("navigation_enabled", True)):
        return UNRESOLVED_LINK_KEY
    return (marker.status or "queued").lower()


def glyph_colour_status(key: str) -> str:
    """The status whose colour ``key`` draws in: an unlinked failure is failed red."""

    return "failed" if key == UNLINKED_FAILED_KEY else key


@dataclass(frozen=True, slots=True)
class AtlasBatchView:
    """What the Atlas tab shows of the batch positions at one zoom."""

    leaves: tuple[ImageMarker, ...]
    clusters: tuple[tuple[MarkerCluster, tuple[ImageMarker, ...]], ...]
    labels: dict[str, AtlasLabelPlacement]


def atlas_cluster_inputs(batch_markers: Sequence[ImageMarker]) -> list[ClusterInput]:
    """Cluster inputs for batch-position leaves, as the Atlas tab builds them.

    A position the resolver cannot navigate to clusters as "unattributed".
    """

    return [
        ClusterInput(
            id=marker.id,
            x=float(marker.x),
            y=float(marker.y),
            status=atlas_glyph_key(marker),
        )
        for marker in batch_markers
        if marker.x is not None and marker.y is not None
    ]


def atlas_batch_view(
    batch_markers: Sequence[ImageMarker],
    unattributed: Sequence[ImageMarker] = (),
    *,
    screen_scale: float,
    logical_zoom: float,
    clustering: bool = True,
    labels: bool = True,
    clusters: Sequence[MarkerCluster] | None = None,
) -> AtlasBatchView:
    """Split batch positions into leaves and clusters and place their numbers.

    ``unattributed`` failed tilt series are always leaves. ``clusters`` lets a
    caller that caches the clustering pass it in; otherwise it is computed.
    """

    scale = max(float(screen_scale), 0.01)
    if clusters is None:
        clusters = cluster_markers(
            atlas_cluster_inputs(batch_markers),
            transform=ScreenTransform(scale, scale),
            logical_zoom=logical_zoom if clustering else NO_CLUSTER_ABOVE_ZOOM + 1.0,
        )
    by_id = {marker.id: marker for marker in batch_markers}
    leaves: list[ImageMarker] = []
    groups: list[tuple[MarkerCluster, tuple[ImageMarker, ...]]] = []
    for cluster in clusters:
        if not cluster.is_cluster:
            leaf = by_id.get(cluster.member_ids[0])
            if leaf is not None:
                leaves.append(leaf)
            continue
        groups.append((cluster, tuple(by_id[member_id] for member_id in cluster.member_ids)))
    leaves.extend(unattributed)
    placements = (
        atlas_batch_label_placements(leaves, view_scale=scale)
        if labels and logical_zoom >= ATLAS_LABEL_MIN_ZOOM
        else {}
    )
    return AtlasBatchView(tuple(leaves), tuple(groups), placements)


__all__ = [
    "ATLAS_GLYPH_KEYS",
    "DASHED_GLYPH_KEYS",
    "ATLAS_LABEL_MIN_ZOOM",
    "UNLINKED_FAILED_KEY",
    "UNRESOLVED_LINK_KEY",
    "AtlasBatchView",
    "atlas_batch_view",
    "atlas_cluster_inputs",
    "atlas_glyph_key",
    "glyph_colour_status",
    "is_unlinked_failure",
]
