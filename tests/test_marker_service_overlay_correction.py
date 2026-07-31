from __future__ import annotations

import math
from pathlib import Path

import pytest

from tomography_session_browser.domain.markers import MarkerType
from tomography_session_browser.domain.models import (
    Atlas,
    BatchPosition,
    MrcMetadata,
    Overview,
    SearchMap,
    SearchTile,
    TiltSeries,
)
from tomography_session_browser.parsers.search_tile_parser import (
    _ExposureTarget,
    _TileCandidate,
    _match_tile_from_search_map_projection,
)
from tomography_session_browser.services.marker_service import (
    MarkerContext,
    _image_frame,
    _mrc_pixel_size,
    atlas_lod_markers,
    atlas_markers,
    overview_markers,
    search_map_markers,
    search_tile_markers,
)


def _mrc_metadata(
    name: str,
    *,
    size: tuple[int, int],
    stage_position: tuple[float, float],
    pixel_size: float = 1.0,
) -> MrcMetadata:
    return MrcMetadata(
        path=Path(name),
        size_bytes=0,
        nx=size[0],
        ny=size[1],
        voxel_size=(pixel_size, pixel_size, None),
        frame_metadata=[
            {
                "stage_x": stage_position[0],
                "stage_y": stage_position[1],
                "raw_fields": {"pixel_size_x": pixel_size},
            }
        ],
    )


def _search_map() -> SearchMap:
    return SearchMap(
        id="search-map",
        name="SearchMap_test",
        mrc_metadata=_mrc_metadata(
            "search_map.mrc",
            size=(100, 100),
            stage_position=(0.0, 0.0),
        ),
    )


def _atlas() -> Atlas:
    return Atlas(
        id="atlas",
        mrc_metadata=_mrc_metadata(
            "atlas.mrc",
            size=(100, 100),
            stage_position=(0.0, 0.0),
        ),
    )


def _overview() -> Overview:
    return Overview(
        id="overview",
        name="Overview_001",
        image_path=Path("overview.jpg"),
        mrc_metadata=_mrc_metadata(
            "overview.mrc",
            size=(20, 20),
            stage_position=(20.0, 10.0),
        ),
        linked_search_map_ids=["atlas-search-map"],
    )


def _atlas_search_map() -> SearchMap:
    return SearchMap(
        id="atlas-search-map",
        name="SearchMap_001",
        mrc_metadata=_mrc_metadata(
            "atlas_search_map.mrc",
            size=(10, 10),
            stage_position=(10.0, 0.0),
        ),
        tile_paths=[
            Path("Tile_001.jpg"),
            Path("Tile_002.jpg"),
            Path("Tile_003.jpg"),
            Path("Tile_004.jpg"),
        ],
    )


def _aligned_atlas_search_map() -> SearchMap:
    search_map = _atlas_search_map()
    search_map.metadata["SearchMap.dm"] = {
        "BackwardAlignmentTransformation": {
            "Rotation": 0.0,
            "Translation": {
                "width": 5.0,
                "height": -3.0,
            },
        }
    }
    return search_map


def _batch() -> BatchPosition:
    return BatchPosition(
        id="batch",
        name="batch",
        linked_search_map_id="search-map",
        metadata={
            "PositionOnTileSet": {
                "StagePositionX": -20.0,
                "StagePositionY": 0.0,
            },
            "ExposureTemplateAreaParameters": {
                "Name": "Exposure",
                "PositionX": 0.0,
                "PositionY": 0.0,
            },
            "AdditionalExposureTemplateAreas": {
                "ExposureTemplateAreaParameters": [
                    {
                        "Name": "batch_2",
                        "PositionX": -40.0,
                        "PositionY": 5.0,
                    },
                    {
                        "Name": "batch_99",
                        "PositionX": 130.0,
                        "PositionY": 0.0,
                    },
                ]
            },
        },
    )


def _tile_candidate() -> _TileCandidate:
    return _TileCandidate(
        image_path=Path("Tile_020.mrc"),
        xml_path=Path("Tile_020.xml"),
        metadata={},
        stage_position=(20.0, -5.0),
        acquisition_time=None,
        tile_index=20,
        image_size=(20, 20),
        pixel_size=1.0,
    )


def test_mrc_pixel_size_uses_explicit_field_units() -> None:
    raw_metres = MrcMetadata(
        path=Path("raw.mrc"),
        size_bytes=4,
        frame_metadata=[{"raw_fields": {"pixel_size_x": 1.2e-6}}],
    )
    frame_angstrom = MrcMetadata(
        path=Path("frame.mrc"),
        size_bytes=4,
        frame_metadata=[{"pixel_size": 12_000.0}],
    )
    voxel_angstrom = MrcMetadata(
        path=Path("voxel.mrc"),
        size_bytes=4,
        voxel_size=(12_000.0, 12_000.0, None),
    )

    assert _mrc_pixel_size(raw_metres) == pytest.approx(1.2e-6)
    assert _mrc_pixel_size(frame_angstrom) == pytest.approx(1.2e-6)
    assert _mrc_pixel_size(voxel_angstrom) == pytest.approx(1.2e-6)


def test_image_frame_diagnostic_is_debug_not_info(caplog: pytest.LogCaptureFixture) -> None:
    overview = Overview(
        id="overview",
        name="Overview",
        image_path=Path("overview.mrc"),
        mrc_metadata=MrcMetadata(
            path=Path("overview.mrc"),
            size_bytes=4,
            nx=10,
            ny=10,
            frame_metadata=[
                {
                    "stage_x": 0.0,
                    "stage_y": 0.0,
                    "raw_fields": {"pixel_size_x": 1e-6},
                }
            ],
        ),
    )

    with caplog.at_level("DEBUG"):
        frame = _image_frame(overview)

    assert frame is not None
    records = [record for record in caplog.records if record.message.startswith("image frame:")]
    assert records
    assert all(record.levelname == "DEBUG" for record in records)


def test_atlas_tab_overview_and_search_map_regions_do_not_use_exposure_correction() -> None:
    markers = atlas_markers(
        _atlas(),
        MarkerContext(
            overviews=(_overview(),),
            search_maps=(_atlas_search_map(),),
        ),
    )

    overview_marker = next(marker for marker in markers if marker.marker_type == MarkerType.OVERVIEW)
    search_marker = next(marker for marker in markers if marker.marker_type == MarkerType.SEARCH_MAP)

    assert "legacy_overlay_transform" not in overview_marker.metadata
    assert "overlay_transform_provenance" not in overview_marker.metadata
    assert "legacy_overlay_transform" not in search_marker.metadata
    assert "overlay_transform_provenance" not in search_marker.metadata
    overview_center = (
        overview_marker.bbox[0] + overview_marker.bbox[2] / 2,
        overview_marker.bbox[1] + overview_marker.bbox[3] / 2,
    )
    search_center = (
        search_marker.bbox[0] + search_marker.bbox[2] / 2,
        search_marker.bbox[1] + search_marker.bbox[3] / 2,
    )
    assert overview_center == (30.0, 60.0)
    assert overview_marker.metadata["atlas_overlay_transform"] == "rotate_180_about_image_center"
    assert overview_marker.metadata["atlas_overlay_rotation_anchor"] == (50.0, 50.0)
    assert overview_marker.metadata["atlas_overlay_position_before_rotation"] == (70.0, 40.0)
    assert search_center == pytest.approx((42.395833333333336, 52.395833333333336))
    assert search_marker.metadata["atlas_overlay_transform"] == "rotate_180_about_image_center"
    assert search_marker.metadata["atlas_overlay_rotation_anchor"] == (50.0, 50.0)


def test_atlas_tab_overview_and_search_map_names_are_tooltips_not_labels() -> None:
    markers = atlas_markers(
        _atlas(),
        MarkerContext(
            overviews=(_overview(),),
            search_maps=(_atlas_search_map(),),
        ),
    )

    overview_marker = next(marker for marker in markers if marker.marker_type == MarkerType.OVERVIEW)
    search_markers = [marker for marker in markers if marker.marker_type == MarkerType.SEARCH_MAP]

    assert overview_marker.label is None
    assert overview_marker.tooltip == "Overview: Overview_001"
    assert {marker.label for marker in search_markers} == {None}
    assert all("SearchMap_001" in (marker.tooltip or "") for marker in search_markers)


def test_atlas_tab_reconstructs_search_map_grid_from_unique_tile_metadata() -> None:
    search_map = _atlas_search_map()
    search_map.tile_paths = [
        Path("Tile_001.mrc"),
        Path("Tile_001.jpg"),
        Path("Tile_002.mrc"),
        Path("Tile_002.jpg"),
        Path("Tile_003.mrc"),
        Path("Tile_003.jpg"),
    ]
    search_map.tile_metadata_paths = [
        Path("Tile_001.xml"),
        Path("Tile_002.xml"),
        Path("Tile_003.xml"),
    ]

    markers = atlas_markers(
        _atlas(),
        MarkerContext(search_maps=(search_map,)),
    )

    search_markers = [marker for marker in markers if marker.marker_type == MarkerType.SEARCH_MAP]

    assert len(search_markers) == 3
    assert {marker.metadata["tile_count"] for marker in search_markers} == {3}
    assert {
        marker.metadata["tile_layout_source"]
        for marker in search_markers
    } == {"tile_metadata_paths_unique_stems_reconstructed"}


def test_atlas_tab_applies_sample_alignment_to_search_map_and_linked_overview() -> None:
    search_map = _aligned_atlas_search_map()
    markers = atlas_markers(
        _atlas(),
        MarkerContext(
            overviews=(_overview(),),
            search_maps=(search_map,),
        ),
    )

    overview_marker = next(marker for marker in markers if marker.marker_type == MarkerType.OVERVIEW)
    search_marker = next(marker for marker in markers if marker.marker_type == MarkerType.SEARCH_MAP)

    overview_center = (
        overview_marker.bbox[0] + overview_marker.bbox[2] / 2,
        overview_marker.bbox[1] + overview_marker.bbox[3] / 2,
    )
    search_center = (
        search_marker.bbox[0] + search_marker.bbox[2] / 2,
        search_marker.bbox[1] + search_marker.bbox[3] / 2,
    )

    assert overview_center == (25.0, 57.0)
    assert overview_marker.metadata["raw_stage_position"] == (20.0, 10.0)
    assert overview_marker.metadata["stage_position"] == (25.0, 7.0)
    assert overview_marker.metadata["alignment_transform_source"] == "BackwardAlignmentTransformation"
    assert overview_marker.metadata["alignment_translation"] == (5.0, -3.0)
    assert search_center == pytest.approx((37.395833333333336, 49.395833333333336))
    assert search_marker.metadata["raw_stage_position"] == (10.0, 0.0)
    assert search_marker.metadata["stage_position"] == (15.0, -3.0)
    assert search_marker.metadata["alignment_transform_source"] == "BackwardAlignmentTransformation"
    assert search_marker.metadata["alignment_translation"] == (5.0, -3.0)


def test_atlas_tab_uses_nested_atlas_reference_transformation_for_stage_basis() -> None:
    atlas = _atlas()
    atlas.metadata["Atlas_001.xml"] = {
        "ReferenceTransformation": {
            "matrix": {
                "_m11": 0.0,
                "_m12": 1.0,
                "_m21": 1.0,
                "_m22": 0.0,
            }
        }
    }
    search_map = _atlas_search_map()
    search_map.mrc_metadata = _mrc_metadata(
        "atlas_search_map.mrc",
        size=(10, 10),
        stage_position=(10.0, 0.0),
    )
    search_map.tile_paths = []

    markers = atlas_markers(
        atlas,
        MarkerContext(search_maps=(search_map,)),
    )

    search_marker = next(marker for marker in markers if marker.marker_type == MarkerType.SEARCH_MAP)
    search_center = (
        search_marker.bbox[0] + search_marker.bbox[2] / 2,
        search_marker.bbox[1] + search_marker.bbox[3] / 2,
    )

    assert search_center == pytest.approx((50.0, 40.0))


def test_atlas_reference_transformation_direction_is_scaled_to_frame_pixel_size() -> None:
    atlas = _atlas()
    atlas.mrc_metadata = _mrc_metadata(
        "atlas.mrc",
        size=(100, 100),
        stage_position=(0.0, 0.0),
        pixel_size=2.0,
    )
    atlas.metadata["Atlas_001.xml"] = {
        "ReferenceTransformation": {
            "matrix": {
                "_m11": 0.0,
                "_m12": 10.0,
                "_m21": 10.0,
                "_m22": 0.0,
            }
        }
    }
    search_map = _atlas_search_map()
    search_map.mrc_metadata = _mrc_metadata(
        "atlas_search_map.mrc",
        size=(10, 10),
        stage_position=(20.0, 0.0),
        pixel_size=2.0,
    )
    search_map.tile_paths = []

    markers = atlas_markers(
        atlas,
        MarkerContext(search_maps=(search_map,)),
    )

    search_marker = next(marker for marker in markers if marker.marker_type == MarkerType.SEARCH_MAP)
    search_center = (
        search_marker.bbox[0] + search_marker.bbox[2] / 2,
        search_marker.bbox[1] + search_marker.bbox[3] / 2,
    )

    assert search_center == pytest.approx((50.0, 40.0))


def test_atlas_alignment_rotation_uses_target_frame_centre_as_origin() -> None:
    atlas = Atlas(
        id="atlas",
        mrc_metadata=_mrc_metadata(
            "atlas.mrc",
            size=(100, 100),
            stage_position=(100.0, 100.0),
        ),
    )
    search_map = SearchMap(
        id="search-map",
        name="SearchMap_rotated",
        mrc_metadata=_mrc_metadata(
            "search_map.mrc",
            size=(10, 10),
            stage_position=(110.0, 100.0),
        ),
        metadata={
            "SearchMap.dm": {
                "BackwardAlignmentTransformation": {
                    "Rotation": math.pi / 2,
                    "Translation": {"width": 0.0, "height": 0.0},
                }
            }
        },
    )

    markers = atlas_markers(
        atlas,
        MarkerContext(search_maps=(search_map,)),
    )

    search_marker = next(marker for marker in markers if marker.marker_type == MarkerType.SEARCH_MAP)
    search_center = (
        search_marker.bbox[0] + search_marker.bbox[2] / 2,
        search_marker.bbox[1] + search_marker.bbox[3] / 2,
    )

    assert search_center == pytest.approx((50.0, 60.0))
    assert search_marker.metadata["stage_position"] == pytest.approx((100.0, 110.0))
    assert search_marker.metadata["alignment_rotation_origin"] == (100.0, 100.0)


def test_search_map_keeps_exposure_visible_after_grouped_correction() -> None:
    markers = search_map_markers(_search_map(), [_batch()])

    exposure_by_name = {
        marker.metadata.get("area_name"): marker
        for marker in markers
        if marker.marker_type == MarkerType.EXPOSURE_AREA
    }

    edge_exposure = exposure_by_name["batch_2"]
    assert edge_exposure.metadata["raw_image_position"][0] < 0
    assert edge_exposure.metadata["local_rotation_anchor"] == "batch"
    assert edge_exposure.metadata["legacy_overlay_transform"] == "global_reflection+local_180_rotation"
    assert edge_exposure.metadata["overlay_transform_provenance"] == "legacy_display_correction"
    assert 0 <= edge_exposure.x <= 100
    assert 0 <= edge_exposure.y <= 100
    assert "batch_99" not in exposure_by_name


def test_search_map_inferred_failed_tilt_keeps_legacy_reflection() -> None:
    failed = TiltSeries(
        id="failed-tilt",
        name="failed_1",
        mrc_path=Path("failed_1.mrc"),
        mrc_metadata=_mrc_metadata(
            "failed_1.mrc",
            size=(1, 1),
            stage_position=(10.0, 0.0),
        ),
    )

    marker = next(
        marker
        for marker in search_map_markers(
            _search_map(),
            [],
            failed_tilt_ids=frozenset({failed.id}),
            tilt_series=(failed,),
        )
        if marker.metadata.get("inferred_from_stage")
    )

    assert (marker.x, marker.y) == pytest.approx((39.0, 49.0))
    assert marker.metadata["legacy_overlay_transform"] == "global_reflection"


def test_legacy_atlas_inferred_failed_tilt_receives_one_atlas_rotation() -> None:
    failed = TiltSeries(
        id="failed-tilt",
        name="failed_1",
        mrc_path=Path("failed_1.mrc"),
        mrc_metadata=_mrc_metadata(
            "failed_1.mrc",
            size=(1, 1),
            stage_position=(10.0, 0.0),
        ),
    )

    marker = next(
        marker
        for marker in atlas_lod_markers(
            _atlas(),
            MarkerContext(
                tilt_series=(failed,),
                failed_tilt_ids=frozenset({failed.id}),
            ),
        )
        if marker.metadata.get("atlas_lod_role") == "unattributed"
    )

    assert (marker.x, marker.y) == pytest.approx((40.0, 50.0))
    assert marker.metadata["atlas_overlay_transform"] == (
        "rotate_180_about_image_center"
    )
    assert "legacy_overlay_transform" not in marker.metadata


def test_search_map_focus_and_tracking_keep_same_recorded_relative_position() -> None:
    batch = _batch()
    batch.metadata["TrackingTemplateAreaParameters"] = {
        "Name": "Tracking",
        "PositionX": -10.0,
        "PositionY": 3.0,
    }
    batch.metadata["FocusTemplateAreaParameters"] = {
        "Name": "Focus",
        "PositionX": -10.0,
        "PositionY": 3.0,
    }

    markers = search_map_markers(_search_map(), [batch])
    by_type = {
        marker.marker_type: marker
        for marker in markers
        if marker.marker_type
        in {
            MarkerType.EXPOSURE_AREA,
            MarkerType.TRACKING_AREA,
            MarkerType.FOCUS_AREA,
        }
        and marker.metadata.get("area_name") in {"Exposure", "Tracking", "Focus"}
    }
    exposure = by_type[MarkerType.EXPOSURE_AREA]
    tracking = by_type[MarkerType.TRACKING_AREA]
    focus = by_type[MarkerType.FOCUS_AREA]

    assert (tracking.x, tracking.y) == pytest.approx((focus.x, focus.y))
    raw_delta = (
        tracking.metadata["raw_image_position"][0]
        - exposure.metadata["raw_image_position"][0],
        tracking.metadata["raw_image_position"][1]
        - exposure.metadata["raw_image_position"][1],
    )
    displayed_delta = (
        tracking.x - exposure.x,
        tracking.y - exposure.y,
    )
    assert displayed_delta == pytest.approx((-raw_delta[0], -raw_delta[1]))
    assert "local_rotation_anchor" not in tracking.metadata
    assert "local_rotation_anchor" not in focus.metadata


def test_overview_focus_and_tracking_at_same_offset_stay_coincident() -> None:
    batch = _batch()
    batch.linked_overview_id = "overview"
    batch.metadata["TrackingTemplateAreaParameters"] = {
        "Name": "Tracking",
        "PositionX": -10.0,
        "PositionY": 3.0,
    }
    batch.metadata["FocusTemplateAreaParameters"] = {
        "Name": "Focus",
        "PositionX": -10.0,
        "PositionY": 3.0,
    }

    markers = overview_markers(
        Overview(
            id="overview",
            name="Overview",
            image_path=Path("overview.mrc"),
            mrc_metadata=_mrc_metadata(
                "overview.mrc",
                size=(100, 100),
                stage_position=(0.0, 0.0),
            ),
        ),
        MarkerContext(batch_positions=(batch,)),
    )
    by_type = {marker.marker_type: marker for marker in markers}
    tracking = by_type[MarkerType.TRACKING_AREA]
    focus = by_type[MarkerType.FOCUS_AREA]

    assert (tracking.x, tracking.y) == pytest.approx((focus.x, focus.y))
    assert tracking.metadata["legacy_overlay_transform"] == "global_reflection"
    assert focus.metadata["legacy_overlay_transform"] == "global_reflection"


def test_search_tile_projection_includes_corrected_edge_exposure() -> None:
    search_tile = SearchTile(
        id="search-tile",
        name="Tile 20",
        search_map_id="search-map",
        batch_position_id="batch",
        mrc_metadata=_mrc_metadata(
            "Tile_020.mrc",
            size=(20, 20),
            stage_position=(20.0, -5.0),
        ),
    )

    markers = search_tile_markers(
        search_tile,
        [_batch()],
        search_maps=[_search_map()],
    )

    edge_exposure = next(
        marker
        for marker in markers
        if marker.marker_type == MarkerType.EXPOSURE_AREA
        and marker.metadata.get("area_name") == "batch_2"
    )
    assert edge_exposure.metadata["coordinate_source"] == "search_map_overlay_projected_to_search_tile"
    assert 0 <= edge_exposure.x <= 20
    assert 0 <= edge_exposure.y <= 20


def test_search_tab_matching_uses_corrected_edge_exposure_projection() -> None:
    batch = _batch()
    target = _ExposureTarget(
        display_name="batch_2",
        marker_area_name="batch_2",
        stage_position=(-60.0, 5.0),
        raw=batch.metadata["AdditionalExposureTemplateAreas"]["ExposureTemplateAreaParameters"][0],
        is_primary=False,
    )

    candidate, method = _match_tile_from_search_map_projection(
        [_tile_candidate()],
        _search_map(),
        batch,
        target,
        [],
    )

    assert candidate == _tile_candidate()
    assert method == "search-map overlay projection"
