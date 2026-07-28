from __future__ import annotations

from pathlib import Path

import pytest
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QApplication

from tomography_session_browser.domain.markers import ImageMarker, MarkerType
from tomography_session_browser.reports.graphics import OverlayedImage
from tomography_session_browser.domain.models import BatchPosition, MrcMetadata, TiltSeries
from tomography_session_browser.services.batch_label_service import (
    LABEL_GAP_PX,
    LABEL_MAX_SCREEN_FONT_SIZE_PX,
    LABEL_MIN_SCREEN_FONT_SIZE_PX,
    batch_label_markers,
    batch_label_screen_font_size_px,
    compact_label_for_tilt,
    exposure_batch_label,
    atlas_batch_label_placements,
    visible_batch_position_label_ids,
)
from tomography_session_browser.services.marker_service import batch_position_markers
from tomography_session_browser.ui.image_viewer import ImagePreviewView


def _app() -> QApplication:
    return QApplication.instance() or QApplication([])


def _batch(name: str = "Position_001") -> BatchPosition:
    return BatchPosition(
        id="bp-1",
        name=name,
        exposure_mrc_metadata=MrcMetadata(
            path=Path("exposure.mrc"),
            size_bytes=0,
            nx=200,
            ny=200,
            voxel_size=(1.0, 1.0, None),
        ),
        metadata={
            "ExposureTemplateAreaParameters": {
                "Name": "Exposure",
                "PositionX": 0.0,
                "PositionY": 0.0,
            },
            "AdditionalExposureTemplateAreas": {
                "ExposureTemplateAreaParameters": [
                    {"Name": "Position_001_2", "PositionX": 25.0, "PositionY": 0.0},
                    {"Name": "Position_001_3", "PositionX": -25.0, "PositionY": 0.0},
                ]
            },
        },
    )


def test_atlas_leaf_label_collision_nudges_before_dropping() -> None:
    markers = [
        ImageMarker(
            id="batch-1",
            marker_type=MarkerType.BATCH_POSITION,
            linked_object_id="batch-1",
            source_object_id="atlas",
            x=10.0,
            y=10.0,
            label="1",
        ),
        ImageMarker(
            id="batch-2",
            marker_type=MarkerType.BATCH_POSITION,
            linked_object_id="batch-2",
            source_object_id="atlas",
            x=12.0,
            y=10.0,
            label="2",
        ),
        ImageMarker(
            id="batch-3",
            marker_type=MarkerType.BATCH_POSITION,
            linked_object_id="batch-3",
            source_object_id="atlas",
            x=80.0,
            y=10.0,
            label="3",
        ),
    ]
    assert visible_batch_position_label_ids(markers, view_scale=1.0) == {
        "batch-1",
        "batch-2",
        "batch-3",
    }
    placements = atlas_batch_label_placements(markers, view_scale=1.0)
    assert placements["batch-1"].leader is False
    assert placements["batch-2"].leader is True
    assert abs(placements["batch-2"].y_offset_px) <= 26.0


def test_batch_position_markers_only_add_main_exposure_label() -> None:
    markers = batch_position_markers(_batch(), [])

    labels = [
        marker.label
        for marker in markers
        if marker.marker_type == MarkerType.BATCH_LABEL
    ]

    assert labels == ["1"]


def test_batch_label_markers_keep_all_exposure_labels_outside_batch_position_tab() -> None:
    markers = [marker for marker in batch_position_markers(_batch(), []) if marker.marker_type != MarkerType.BATCH_LABEL]

    labels = [
        marker.label
        for marker in batch_label_markers(markers)
        if marker.marker_type == MarkerType.BATCH_LABEL
    ]

    assert labels == ["1", "1.2", "1.3"]


def test_exposure_label_uses_robust_filename_fallback_when_index_missing() -> None:
    marker = ImageMarker(
        id="m",
        marker_type=MarkerType.EXPOSURE_AREA,
        linked_object_id="vellio_1",
        source_object_id="search-map",
        x=50,
        y=50,
        radius=8,
        metadata={
            "batch_name": "vellio_1",
            "batch_label": "1",
            "area_name": "vellio_1_3",
            "image_size": (100, 100),
        },
    )

    assert exposure_batch_label(marker) == "1.3"


def test_label_placement_keeps_edge_labels_inside_image() -> None:
    exposure = ImageMarker(
        id="edge",
        marker_type=MarkerType.EXPOSURE_AREA,
        linked_object_id="bp-1",
        source_object_id="search-map",
        x=4,
        y=4,
        radius=6,
        metadata={
            "batch_label": "1",
            "exposure_index": 0,
            "image_size": (80, 60),
        },
    )

    labels = batch_label_markers([exposure], image_size=(80, 60))

    assert len(labels) == 1
    x, y, width, height = labels[0].bbox
    assert 0 <= x <= 80 - width
    assert 0 <= y <= 60 - height


def test_label_placement_uses_consistent_offset_from_exposure_geometry() -> None:
    exposure = ImageMarker(
        id="centred",
        marker_type=MarkerType.EXPOSURE_AREA,
        linked_object_id="bp-1",
        source_object_id="search-map",
        bbox=(120, 120, 80, 60),
        metadata={
            "batch_label": "8",
            "exposure_index": 0,
            "image_size": (360, 300),
        },
    )

    labels = batch_label_markers([exposure], image_size=(360, 300))

    assert len(labels) == 1
    label = labels[0]
    assert label.metadata["label_source_geometry"] == exposure.bbox
    _assert_label_gap_matches_placement(label)


def test_index_zero_label_stays_close_when_subordinate_labels_are_nearby() -> None:
    main = ImageMarker(
        id="main",
        marker_type=MarkerType.EXPOSURE_AREA,
        linked_object_id="bp-4",
        source_object_id="search-map",
        bbox=(100, 100, 46, 46),
        metadata={
            "batch_label": "4",
            "exposure_index": 0,
            "image_size": (320, 260),
        },
    )
    subordinate = ImageMarker(
        id="sub",
        marker_type=MarkerType.EXPOSURE_AREA,
        linked_object_id="bp-4",
        source_object_id="search-map",
        bbox=(100, 38, 46, 46),
        metadata={
            "batch_label": "4",
            "exposure_index": 2,
            "image_size": (320, 260),
        },
    )

    labels = {label.label: label for label in batch_label_markers([main, subordinate], image_size=(320, 260))}

    label = labels["4"]
    _assert_label_gap_matches_placement(label)


def test_label_placement_avoids_label_overlap_in_crowded_regions() -> None:
    exposures = [
        ImageMarker(
            id=f"e-{index}",
            marker_type=MarkerType.EXPOSURE_AREA,
            linked_object_id=f"bp-{index}",
            source_object_id="search-map",
            x=100 + index * 6,
            y=100,
            radius=8,
            metadata={
                "batch_label": str(index + 1),
                "exposure_index": 0,
                "image_size": (240, 200),
            },
        )
        for index in range(3)
    ]

    labels = batch_label_markers(exposures, image_size=(240, 200))
    bboxes = [label.bbox for label in labels]

    assert len(labels) == 3
    assert all(_intersection_area(left, right) == 0 for left, right in _pairs(bboxes))


def test_failed_tilt_name_fallback_derives_repeat_label() -> None:
    tilt = TiltSeries(id="ts", name="vellio_1_2", mrc_path=Path("vellio_1_2.mrc"))

    assert compact_label_for_tilt(tilt) == "1.2"


def test_batch_label_screen_font_size_is_clamped_across_zoom_levels() -> None:
    low_zoom = batch_label_screen_font_size_px(0.25)
    normal_zoom = batch_label_screen_font_size_px(1.0)
    high_zoom = batch_label_screen_font_size_px(6.0)

    assert LABEL_MIN_SCREEN_FONT_SIZE_PX <= low_zoom < normal_zoom <= high_zoom <= LABEL_MAX_SCREEN_FONT_SIZE_PX


def test_batch_label_visibility_is_independent_from_exposure_visibility() -> None:
    _app()
    view = ImagePreviewView()
    exposure = ImageMarker(
        id="exposure",
        marker_type=MarkerType.EXPOSURE_AREA,
        linked_object_id="bp-1",
        source_object_id="search-map",
        x=50,
        y=50,
        radius=8,
        metadata={"image_size": (100, 100)},
    )
    label = ImageMarker(
        id="label",
        marker_type=MarkerType.BATCH_LABEL,
        linked_object_id="bp-1",
        source_object_id="search-map",
        x=60,
        y=40,
        bbox=(60, 40, 18, 20),
        label="1",
        metadata={"image_size": (100, 100)},
    )
    view.set_markers([exposure, label])

    view.set_marker_type_visible(MarkerType.EXPOSURE_AREA, False)

    displayed = {marker.id for marker in view._display_markers()}
    assert "exposure" not in displayed
    assert "label" in displayed


def test_batch_label_viewer_renders_transparent_text_without_backing_plate() -> None:
    _app()
    view = ImagePreviewView()
    image = QImage(120, 120, QImage.Format.Format_RGB32)
    image.fill(0x7F7F7F)
    view.set_image(image)
    label = ImageMarker(
        id="label",
        marker_type=MarkerType.BATCH_LABEL,
        linked_object_id="bp-1",
        source_object_id="search-map",
        x=70,
        y=40,
        bbox=(70, 40, 34, 42),
        label="5",
        metadata={
            "image_size": (120, 120),
            "label_source_geometry": (30, 30, 40, 40),
            "label_placement": "right",
        },
    )

    view.set_markers([label])

    label_items = [item for item in view._marker_items if item.data(1) == "batch_label"]
    assert len(label_items) == 3
    assert {item.__class__.__name__ for item in label_items} == {"QGraphicsPathItem"}


def test_batch_label_viewer_uses_stable_anchor_at_fit_scale() -> None:
    _app()
    view = ImagePreviewView()
    image = QImage(1000, 1000, QImage.Format.Format_RGB32)
    image.fill(0x7F7F7F)
    view.set_image(image)
    view.scale(0.45, 0.45)
    label = ImageMarker(
        id="label",
        marker_type=MarkerType.BATCH_LABEL,
        linked_object_id="bp-1",
        source_object_id="search-map",
        x=220,
        y=180,
        bbox=(220, 180, 34, 42),
        label="5",
        metadata={
            "image_size": (1000, 1000),
            "label_source_geometry": (100, 100, 60, 60),
            "label_placement": "right",
        },
    )

    view.set_markers([label])

    text_item = max(
        (item for item in view._marker_items if item.data(1) == "batch_label"),
        key=lambda item: item.zValue(),
    )
    assert text_item.pos().x() == pytest.approx(220)
    assert text_item.pos().y() == pytest.approx(180)


def test_pdf_batch_label_draws_transparent_text_without_label_box() -> None:
    marker = ImageMarker(
        id="label",
        marker_type=MarkerType.BATCH_LABEL,
        linked_object_id="bp-1",
        source_object_id="search-map",
        x=70,
        y=40,
        bbox=(70, 40, 34, 42),
        label="5",
        metadata={
            "label_source_geometry": (30, 30, 40, 40),
            "label_placement": "right",
        },
    )
    flowable = object.__new__(OverlayedImage)
    flowable.canv = _RecordingCanvas()
    flowable._draw_w = 120
    flowable._draw_h = 120

    flowable._draw_batch_label(marker, 1.0, 1.0)

    assert flowable.canv.round_rect_calls == []
    assert flowable.canv.rect_calls == []
    assert len(flowable.canv.draw_string_calls) >= 3


def test_pdf_batch_label_uses_marker_coordinates_not_source_geometry() -> None:
    marker = ImageMarker(
        id="label",
        marker_type=MarkerType.BATCH_LABEL,
        linked_object_id="bp-1",
        source_object_id="search-map",
        x=20,
        y=30,
        bbox=(20, 30, 34, 42),
        label="5",
        metadata={
            "label_source_geometry": (10_000, 10_000, 400, 400),
            "label_placement": "below_right",
        },
    )
    flowable = object.__new__(OverlayedImage)
    flowable.canv = _RecordingCanvas()
    flowable._draw_w = 200
    flowable._draw_h = 100

    flowable._draw_batch_label(marker, 2.0, 0.5)

    final_x, final_y, _ = flowable.canv.draw_string_calls[-1]
    assert final_x == pytest.approx(40)
    assert 0 < final_y < 100
    assert final_x < 100


def _pairs(values):
    for left_index, left in enumerate(values):
        for right in values[left_index + 1 :]:
            yield left, right


def _intersection_area(left, right) -> float:
    lx, ly, lw, lh = left
    rx, ry, rw, rh = right
    return max(0.0, min(lx + lw, rx + rw) - max(lx, rx)) * max(
        0.0,
        min(ly + lh, ry + rh) - max(ly, ry),
    )


def _assert_label_gap_matches_placement(label: ImageMarker) -> None:
    source_x, source_y, source_w, source_h = label.metadata["label_source_geometry"]
    label_x, label_y, label_w, label_h = label.bbox
    placement = label.metadata["label_placement"]
    if placement in {"right", "above_right", "below_right"}:
        assert label_x - (source_x + source_w) == pytest.approx(LABEL_GAP_PX)
    elif placement in {"left", "above_left", "below_left"}:
        assert source_x - (label_x + label_w) == pytest.approx(LABEL_GAP_PX)
    elif placement == "above":
        assert source_y - (label_y + label_h) == pytest.approx(LABEL_GAP_PX)
    elif placement == "below":
        assert label_y - (source_y + source_h) == pytest.approx(LABEL_GAP_PX)
    else:  # pragma: no cover - catches future placement names in tests
        raise AssertionError(f"Unexpected placement: {placement}")


class _RecordingCanvas:
    def __init__(self) -> None:
        self.draw_string_calls = []
        self.round_rect_calls = []
        self.rect_calls = []

    def stringWidth(self, text, _font, font_size):  # noqa: N802
        return len(text) * font_size * 0.62

    def saveState(self):  # noqa: N802
        pass

    def restoreState(self):  # noqa: N802
        pass

    def setFont(self, *_args):  # noqa: N802
        pass

    def setFillColor(self, *_args):  # noqa: N802
        pass

    def drawString(self, *args):  # noqa: N802
        self.draw_string_calls.append(args)

    def roundRect(self, *args, **kwargs):  # noqa: N802
        self.round_rect_calls.append((args, kwargs))

    def rect(self, *args, **kwargs):
        self.rect_calls.append((args, kwargs))
