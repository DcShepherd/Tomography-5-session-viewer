from __future__ import annotations

import time

from tomography_session_browser.services.marker_clustering import (
    CLUSTER_RADIUS_PX,
    NO_CLUSTER_ABOVE_ZOOM,
    ClusterInput,
    ScreenTransform,
    cluster_markers,
)


def _markers() -> list[ClusterInput]:
    return [
        ClusterInput("b-03", 30.0, 0.0, "partial"),
        ClusterInput("b-01", 0.0, 0.0, "collected"),
        ClusterInput("b-02", 15.0, 0.0, "failed"),
        ClusterInput("b-04", 200.0, 0.0, "queued"),
    ]


def test_clustering_is_deterministic_across_input_orders() -> None:
    transform = ScreenTransform(1.0, 1.0)
    first = cluster_markers(_markers(), transform=transform, logical_zoom=1.0)
    second = cluster_markers(reversed(_markers()), transform=transform, logical_zoom=1.0)
    assert first == second
    assert first[0].status == "failed"


def test_leaf_count_is_non_decreasing_as_zoom_increases() -> None:
    markers = _markers()
    leaf_counts = []
    for scale in (0.5, 1.0, 2.0, 4.0, 6.0):
        clusters = cluster_markers(
            markers,
            transform=ScreenTransform(scale, scale),
            logical_zoom=scale,
        )
        leaf_counts.append(sum(len(cluster.member_ids) == 1 for cluster in clusters))
    assert leaf_counts == sorted(leaf_counts)


def test_fixed_screen_radius_dissolves_clusters_with_zoom() -> None:
    markers = [
        ClusterInput("a", 0.0, 0.0, "collected"),
        ClusterInput("b", CLUSTER_RADIUS_PX * 0.75, 0.0, "collected"),
    ]
    low = cluster_markers(
        markers,
        transform=ScreenTransform(1.0, 1.0),
        logical_zoom=1.0,
    )
    high = cluster_markers(
        markers,
        transform=ScreenTransform(2.0, 2.0),
        logical_zoom=2.0,
    )
    assert len(low) == 1
    assert len(high) == 2


def test_empty_singleton_coincident_and_hard_ceiling() -> None:
    transform = ScreenTransform(1.0, 1.0)
    assert cluster_markers([], transform=transform, logical_zoom=1.0) == ()
    singleton = cluster_markers(
        [ClusterInput("one", 2.0, 3.0, "queued")],
        transform=transform,
        logical_zoom=1.0,
    )
    assert singleton[0].member_ids == ("one",)
    coincident = [
        ClusterInput("a", 2.0, 3.0, "failed"),
        ClusterInput("b", 2.0, 3.0, "collected"),
    ]
    assert len(cluster_markers(coincident, transform=transform, logical_zoom=1.0)) == 1
    assert len(
        cluster_markers(
            coincident,
            transform=transform,
            logical_zoom=NO_CLUSTER_ABOVE_ZOOM + 0.01,
        )
    ) == 2


def test_cluster_centroid_round_trips_through_viewer_transform() -> None:
    transform = ScreenTransform(2.5, 1.75, 120.0, -45.0)
    markers = [
        ClusterInput("a", 10.0, 20.0, "collected"),
        ClusterInput("b", 12.0, 24.0, "partial"),
    ]
    cluster = cluster_markers(
        markers,
        transform=transform,
        logical_zoom=1.0,
    )[0]
    assert transform.to_screen(cluster.centroid_scene) == cluster.centroid_screen
    assert transform.to_scene(cluster.centroid_screen) == cluster.centroid_scene


def test_realistic_clustering_stays_inside_frame_budget() -> None:
    markers = [
        ClusterInput(
            f"batch-{index:03d}",
            float((index % 20) * 18),
            float((index // 20) * 18),
            ("collected", "partial", "queued", "failed")[index % 4],
        )
        for index in range(200)
    ]
    started = time.perf_counter()
    for _ in range(10):
        cluster_markers(
            markers,
            transform=ScreenTransform(0.8, 0.8),
            logical_zoom=1.0,
        )
    average_ms = (time.perf_counter() - started) * 1000 / 10
    assert average_ms < 16.0
