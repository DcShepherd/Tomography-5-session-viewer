from __future__ import annotations

from dataclasses import dataclass
import hashlib
import math
from typing import Iterable, Sequence

from tomography_session_browser.services.atlas_marker_style import cluster_diameter_dp
from tomography_session_browser.services.batch_position_status import worst_status


CLUSTER_RADIUS_PX = 30.0
MAX_ATLAS_ZOOM = 8.0
# Clustering stays active at the maximum zoom so truly coincident members can
# still open the member popup.  This threshold is deliberately just above the
# user-visible ceiling.
NO_CLUSTER_ABOVE_ZOOM = MAX_ATLAS_ZOOM + 0.001
MIN_CLUSTER_SIZE = 2
ZOOM_DEBOUNCE_MS = 24
SEARCH_MAP_FOOTPRINT_MIN_SCREEN_PX = 48.0
SEARCH_MAP_TILE_MIN_SCREEN_PX = 24.0
PER_EXPOSURE_MIN_SCREEN_PX = 24.0
ZOOM_BUCKET_STEP = 1.0 / 64.0

@dataclass(frozen=True, slots=True)
class ScreenTransform:
    scale_x: float
    scale_y: float
    offset_x: float = 0.0
    offset_y: float = 0.0

    def to_screen(self, point: tuple[float, float]) -> tuple[float, float]:
        return (
            point[0] * self.scale_x + self.offset_x,
            point[1] * self.scale_y + self.offset_y,
        )

    def to_scene(self, point: tuple[float, float]) -> tuple[float, float]:
        if self.scale_x == 0 or self.scale_y == 0:
            raise ValueError("Screen transform scale must be non-zero")
        return (
            (point[0] - self.offset_x) / self.scale_x,
            (point[1] - self.offset_y) / self.scale_y,
        )


@dataclass(frozen=True, slots=True)
class ClusterInput:
    id: str
    x: float
    y: float
    status: str


@dataclass(frozen=True, slots=True)
class MarkerCluster:
    member_ids: tuple[str, ...]
    centroid_scene: tuple[float, float]
    centroid_screen: tuple[float, float]
    status: str
    status_counts: tuple[tuple[str, int], ...]
    bounds_scene: tuple[float, float, float, float]

    @property
    def is_cluster(self) -> bool:
        return len(self.member_ids) >= MIN_CLUSTER_SIZE

    @property
    def diameter_px(self) -> float:
        return cluster_diameter_dp(len(self.member_ids))


def cluster_markers(
    markers: Iterable[ClusterInput],
    *,
    transform: ScreenTransform,
    logical_zoom: float,
    radius_px: float = CLUSTER_RADIUS_PX,
    no_cluster_above_zoom: float = NO_CLUSTER_ABOVE_ZOOM,
) -> tuple[MarkerCluster, ...]:
    """Deterministically cluster projected marker centres in screen space."""

    ordered = sorted(markers, key=lambda marker: marker.id)
    groups: list[list[ClusterInput]] = []
    screen_centroids: list[tuple[float, float]] = []
    clustering_enabled = logical_zoom < no_cluster_above_zoom
    for marker in ordered:
        screen = transform.to_screen((marker.x, marker.y))
        assigned = False
        if clustering_enabled:
            for index, centroid in enumerate(screen_centroids):
                if math.dist(screen, centroid) > radius_px:
                    continue
                groups[index].append(marker)
                points = [transform.to_screen((item.x, item.y)) for item in groups[index]]
                screen_centroids[index] = (
                    sum(point[0] for point in points) / len(points),
                    sum(point[1] for point in points) / len(points),
                )
                assigned = True
                break
        if not assigned:
            groups.append([marker])
            screen_centroids.append(screen)

    results: list[MarkerCluster] = []
    for group, screen_centroid in zip(groups, screen_centroids):
        statuses = [marker.status for marker in group]
        unique_statuses = sorted(set(statuses))
        counts = tuple((status, statuses.count(status)) for status in unique_statuses)
        xs = [marker.x for marker in group]
        ys = [marker.y for marker in group]
        scene_centroid = transform.to_scene(screen_centroid)
        results.append(
            MarkerCluster(
                member_ids=tuple(marker.id for marker in group),
                centroid_scene=scene_centroid,
                centroid_screen=screen_centroid,
                status=worst_status(statuses),
                status_counts=counts,
                bounds_scene=(min(xs), min(ys), max(xs) - min(xs), max(ys) - min(ys)),
            )
        )
    return tuple(results)


def marker_set_fingerprint(markers: Sequence[ClusterInput]) -> str:
    digest = hashlib.sha256()
    for marker in sorted(markers, key=lambda item: item.id):
        digest.update(
            f"{marker.id}\0{marker.x:.8f}\0{marker.y:.8f}\0{marker.status}\n".encode("utf-8")
        )
    return digest.hexdigest()


def zoom_bucket(screen_scale: float) -> int:
    return round(max(float(screen_scale), 0.0) / ZOOM_BUCKET_STEP)


def screen_extent_visible(
    width: float,
    height: float,
    *,
    screen_scale: float,
    threshold_px: float,
    use_short_edge: bool = False,
) -> bool:
    extent = min(abs(width), abs(height)) if use_short_edge else max(abs(width), abs(height))
    return extent * max(screen_scale, 0.0) >= threshold_px
