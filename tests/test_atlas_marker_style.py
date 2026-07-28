from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QApplication

from tomography_session_browser.domain.markers import ImageMarker, MarkerType
from tomography_session_browser.services.atlas_marker_style import (
    CLUSTER_MIN_SEGMENT_DEGREES,
    STATUS_ORDER,
    cluster_diameter_dp,
    cluster_ring_segments,
)
from tomography_session_browser.services.marker_clustering import CLUSTER_RADIUS_PX
from tomography_session_browser.ui.image_viewer import (
    ImagePreviewView,
    MAX_SCALE_BAR_SCREEN_PX,
    ScaleBarWidget,
    _AtlasLodMarkerItem,
    _nice_scale_length_meters,
)


def _app() -> QApplication:
    app = QApplication.instance()
    return app or QApplication([])


def _marker(
    marker_id: str,
    *,
    x: float = 100.0,
    y: float = 100.0,
    status: str = "collected",
    navigable: bool = True,
) -> ImageMarker:
    return ImageMarker(
        id=marker_id,
        marker_type=MarkerType.BATCH_POSITION,
        linked_object_id=marker_id,
        source_object_id="atlas",
        x=x,
        y=y,
        label=marker_id,
        status=status,
        metadata={
            "atlas_lod_role": "batch_position",
            "navigation_enabled": navigable,
        },
    )


def test_cluster_geometry_and_segment_order_match_handoff() -> None:
    assert CLUSTER_RADIUS_PX == 30.0
    assert [cluster_diameter_dp(value) for value in (2, 4, 5, 9, 10, 24, 25)] == [
        22.0,
        22.0,
        28.0,
        28.0,
        34.0,
        34.0,
        40.0,
    ]
    segments = cluster_ring_segments(
        {
            "collected": 80,
            "partial": 1,
            "queued": 1,
            "failed": 1,
            "unattributed": 1,
        }
    )
    assert tuple(segment.status for segment in segments) == STATUS_ORDER
    assert all(
        segment.sweep_degrees >= CLUSTER_MIN_SEGMENT_DEGREES
        for segment in segments
    )
    assert sum(segment.sweep_degrees for segment in segments) == pytest.approx(330.0)


def test_leaf_visual_size_and_non_navigable_hover_contract() -> None:
    _app()
    normal = _AtlasLodMarkerItem(_marker("1"))
    inert = _AtlasLodMarkerItem(_marker("2", navigable=False))
    assert normal._radius == 7.0
    assert normal.acceptHoverEvents() is True
    assert inert.acceptHoverEvents() is False
    assert normal.shape().boundingRect().width() == pytest.approx(22.0)


def test_labels_are_hidden_below_200_percent() -> None:
    _app()
    view = ImagePreviewView()
    view.resize(800, 600)
    view.set_atlas_lod_enabled(True)
    view.set_image(QImage(1000, 1000, QImage.Format.Format_RGB32))
    markers = [
        _marker("1", x=100.0, y=100.0),
        _marker("2", x=100.0 + CLUSTER_RADIUS_PX + 100.0, y=100.0),
    ]
    view.set_markers(markers)
    assert all(marker.label is None for marker in view._last_display_markers)

    view.set_atlas_zoom(2.0)
    view._redraw_markers()
    assert {marker.label for marker in view._last_display_markers if marker.label} == {
        "1",
        "2",
    }


def test_scale_bar_uses_requested_snapped_width_with_compact_geometry() -> None:
    _app()
    scale_bar = ScaleBarWidget()
    scale_bar.set_scale(64.0, "50 µm")
    assert scale_bar._length_px == 64.0
    scale_bar.set_scale(190.0, "20 µm")
    assert scale_bar._length_px == MAX_SCALE_BAR_SCREEN_PX
    assert scale_bar._label == "20 µm"


def test_scale_bar_physical_length_snaps_to_one_two_five_steps() -> None:
    assert _nice_scale_length_meters(1.4e-6) == pytest.approx(1e-6)
    assert _nice_scale_length_meters(2.6e-6) == pytest.approx(2e-6)
    assert _nice_scale_length_meters(4.2e-6) == pytest.approx(5e-6)
