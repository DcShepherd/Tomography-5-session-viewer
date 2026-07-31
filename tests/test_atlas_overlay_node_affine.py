"""Regression tests for the Atlas.dm node-table overlay projection.

These pin the fix for atlas overview / search-map overlay positioning. Before
the fix the atlas projected child stage coordinates with a normalised per-tile
basis plus an empirical 180 degree rotation, which only approximated the true
mapping and produced displacement that grew with distance from the atlas centre.

Tomography 5 records the exact stage -> atlas-pixel mapping in the Atlas.dm node
table (``StagePosition`` -> ``AtlasPixelPosition`` per tile). Fitting an affine
to that table reproduces the rotation, anisotropic scale, handedness flip and
origin exactly. The tests below cover:

* unit conversion (metres -> pixels) and anisotropic scale recovery
* rotation direction / sign
* corner-origin vs centre-origin of the tile rectangle
* transform composition order (reload alignment applied before the affine)
* the atlas-pixel conversion through the real ``_image_frame`` / ``_stage_to_image``
* linked metadata lookup against an optional reference atlas (skipped if absent)
* documented fallbacks when the node table is missing, too small, or unfittable
"""

from __future__ import annotations

import math
import os
from pathlib import Path

import pytest

from tomography_session_browser.domain.models import (
    Atlas,
    BatchPosition,
    MrcMetadata,
    Overview,
    SearchMap,
    TiltSeries,
)
from tomography_session_browser.parsers.atlas_parser import parse_atlas_folder
from tomography_session_browser.parsers.searchmap_parser import parse_search_map_folder
from tomography_session_browser.services import marker_service as ms
from tomography_session_browser.services.marker_service import (
    AlignmentTransform,
    AtlasPixelAffine,
    MarkerContext,
    _apply_alignment_transform,
    _atlas_pixel_affine,
    _collect_atlas_node_pairs,
    _fit_affine_least_squares,
    _image_frame,
    _stage_to_image,
    _template_markers_in_frame,
    atlas_lod_markers,
    atlas_markers,
)
from tomography_session_browser.domain.markers import MarkerType


# --- synthetic node-table helpers -----------------------------------------


def _node(stage_xy: tuple[float, float], rect: tuple[float, float, float, float]) -> dict:
    sx, sy = stage_xy
    px, py, pw, ph = rect
    return {
        "StagePosition": {"X": sx, "Y": sy, "Z": 0.0},
        "AtlasPixelPosition": {"x": px, "y": py, "width": pw, "height": ph},
    }


def _atlas_dm(nodes: list[dict]) -> dict:
    # Mirrors the parsed Atlas.dm shape: repeated TileXml nodes under the atlas.
    return {"Atlas": {"TilesEfficient": {"TileXml": nodes}}}


def _nodes_from_affine(
    coeffs: tuple[float, float, float, float, float, float],
    stage_points: list[tuple[float, float]],
    *,
    tile_w: float,
    tile_h: float,
) -> list[dict]:
    """Build node rectangles whose CENTRES match the given stage->pixel affine."""

    a, b, c, d, e, g = coeffs
    nodes = []
    for sx, sy in stage_points:
        cx = a * sx + b * sy + c
        cy = d * sx + e * sy + g
        nodes.append(_node((sx, sy), (cx - tile_w / 2, cy - tile_h / 2, tile_w, tile_h)))
    return nodes


def _atlas_with_nodes(nodes: list[dict], *, nx: int = 4000, ny: int = 3000) -> Atlas:
    # A real atlas always exposes a pixel size (MRC voxel or per-tile XML), which
    # _image_frame requires before it reaches the node-affine override. Supply a
    # placeholder voxel size; the node affine replaces it with its own value.
    return Atlas(
        id="atlas",
        mrc_metadata=MrcMetadata(
            path=Path("atlas.mrc"), size_bytes=0, nx=nx, ny=ny, voxel_size=(1.0, 1.0, None)
        ),
        metadata={"Atlas.dm": _atlas_dm(nodes)},
    )


# --- core affine recovery --------------------------------------------------


def test_node_table_recovers_isotropic_affine_with_unit_conversion() -> None:
    # 1 micron per pixel, Y inverted (image y grows downward), origin at (1000, 800).
    # px = stage_x / 1e-6 + 1000 ; py = -stage_y / 1e-6 + 800
    coeffs = (1e6, 0.0, 1000.0, 0.0, -1e6, 800.0)
    stage_pts = [(0.0, 0.0), (100e-6, 0.0), (0.0, 100e-6), (100e-6, 100e-6), (-50e-6, 50e-6)]
    atlas = _atlas_with_nodes(_nodes_from_affine(coeffs, stage_pts, tile_w=200, tile_h=200))

    affine = _atlas_pixel_affine(atlas)
    assert affine is not None
    assert affine.residual_px < 1e-6
    assert affine.sample_count == 5
    # mean of the two per-axis pixel sizes -> 1 micron/px
    assert affine.pixel_size == pytest.approx(1e-6, rel=1e-9)
    # stage (0,0) lands at the recorded origin; a +100 micron X step is +100 px
    assert affine.apply(0.0, 0.0) == pytest.approx((1000.0, 800.0))
    assert affine.apply(100e-6, 0.0) == pytest.approx((1100.0, 800.0))
    # +Y stage moves UP the image (smaller py) because of the Y inversion
    assert affine.apply(0.0, 100e-6) == pytest.approx((1000.0, 700.0))


def test_node_table_recovers_anisotropic_scale() -> None:
    # Different metres-per-pixel on each axis: X = 2 micron/px, Y = 4 micron/px.
    coeffs = (1 / 2e-6, 0.0, 500.0, 0.0, 1 / 4e-6, 600.0)
    stage_pts = [(0.0, 0.0), (200e-6, 0.0), (0.0, 200e-6), (200e-6, 200e-6)]
    atlas = _atlas_with_nodes(_nodes_from_affine(coeffs, stage_pts, tile_w=100, tile_h=100))

    affine = _atlas_pixel_affine(atlas)
    assert affine is not None
    a, b, c, d, e, g = affine.coefficients
    assert 1.0 / math.hypot(a, d) == pytest.approx(2e-6, rel=1e-6)  # X axis m/px
    assert 1.0 / math.hypot(b, e) == pytest.approx(4e-6, rel=1e-6)  # Y axis m/px
    assert affine.pixel_size == pytest.approx(3e-6, rel=1e-6)  # mean of 2 and 4


def test_node_table_recovers_rotation_direction() -> None:
    # Rotate the pixel axes by +10 degrees relative to stage; verify the sign of
    # the recovered off-diagonal terms (a +X stage step gains a +Y pixel
    # component for a positive rotation in image coordinates).
    theta = math.radians(10.0)
    scale = 1e6  # px per metre
    coeffs = (
        scale * math.cos(theta), -scale * math.sin(theta), 0.0,
        scale * math.sin(theta), scale * math.cos(theta), 0.0,
    )
    stage_pts = [(0.0, 0.0), (10e-6, 0.0), (0.0, 10e-6), (10e-6, 10e-6)]
    atlas = _atlas_with_nodes(_nodes_from_affine(coeffs, stage_pts, tile_w=50, tile_h=50))

    affine = _atlas_pixel_affine(atlas)
    assert affine is not None
    a, b, c, d, e, g = affine.coefficients
    recovered = math.degrees(math.atan2(d, a))
    assert recovered == pytest.approx(10.0, abs=1e-4)
    # +X stage step -> positive y pixel component (rotation sign preserved)
    px0, py0 = affine.apply(0.0, 0.0)
    px1, py1 = affine.apply(10e-6, 0.0)
    assert py1 - py0 > 0


def test_node_pixel_uses_tile_rectangle_centre_not_corner() -> None:
    # Single mapping: stage (0,0) -> tile rect top-left (10, 20), size 100 x 80.
    # The fitted point must be the rectangle CENTRE (60, 60), not the corner.
    nodes = [
        _node((0.0, 0.0), (10, 20, 100, 80)),
        _node((10e-6, 0.0), (10 + 100, 20, 100, 80)),
        _node((0.0, 10e-6), (10, 20 + 100, 100, 80)),
        _node((10e-6, 10e-6), (110, 120, 100, 80)),
    ]
    atlas = _atlas_with_nodes(nodes)
    affine = _atlas_pixel_affine(atlas)
    assert affine is not None
    assert affine.apply(0.0, 0.0) == pytest.approx((60.0, 60.0))  # rect centre


def test_collect_node_pairs_reads_stage_and_pixel_rect() -> None:
    nodes = [_node((1.0, 2.0), (5, 6, 10, 20)), _node((3.0, 4.0), (7, 8, 10, 20))]
    pairs = _collect_atlas_node_pairs(_atlas_dm(nodes))
    assert pairs == [((1.0, 2.0), (5.0, 6.0, 10.0, 20.0)), ((3.0, 4.0), (7.0, 8.0, 10.0, 20.0))]


# --- image-frame integration ----------------------------------------------


def test_image_frame_uses_node_affine_and_montage_size() -> None:
    coeffs = (1e6, 0.0, 1000.0, 0.0, -1e6, 800.0)
    stage_pts = [(0.0, 0.0), (100e-6, 0.0), (0.0, 100e-6), (100e-6, 100e-6)]
    # tiles are 200 px; far tile centre at (1100,700)/(1000,900) so montage
    # extent is the far rectangle corner.
    atlas = _atlas_with_nodes(
        _nodes_from_affine(coeffs, stage_pts, tile_w=200, tile_h=200), nx=9999, ny=9999
    )
    frame = _image_frame(atlas)
    assert frame is not None
    assert frame.atlas_pixel_affine is not None
    # image_size is the montage frame, NOT the camera-readout MRC nx/ny
    assert frame.image_size != (9999, 9999)
    # _stage_to_image now routes through the affine
    assert _stage_to_image(frame, (0.0, 0.0)) == pytest.approx((1000.0, 800.0))
    assert _stage_to_image(frame, (100e-6, 0.0)) == pytest.approx((1100.0, 800.0))


def test_atlas_markers_apply_alignment_before_affine() -> None:
    # Composition order: a child's reload (BackwardAlignment) translation must be
    # applied in stage space BEFORE the node affine maps to pixels.
    coeffs = (1e6, 0.0, 1000.0, 0.0, -1e6, 800.0)
    stage_pts = [(0.0, 0.0), (100e-6, 0.0), (0.0, 100e-6), (100e-6, 100e-6)]
    atlas = _atlas_with_nodes(_nodes_from_affine(coeffs, stage_pts, tile_w=200, tile_h=200))

    overview = Overview(
        id="ov",
        name="Overview_1",
        image_path=Path("ov.mrc"),
        mrc_metadata=MrcMetadata(
            path=Path("ov.mrc"),
            size_bytes=0,
            nx=20,
            ny=20,
            frame_metadata=[{"stage_x": 0.0, "stage_y": 0.0, "raw_fields": {"pixel_size_x": 1e-6}}],
        ),
    )
    # Search map linked to the overview carries the reload translation (+10 um X).
    search_map = SearchMap(
        id="sm",
        name="SearchMap_1",
        metadata={
            "SearchMap.dm": {
                "BackwardAlignmentTransformation": {
                    "Rotation": 0.0,
                    "Translation": {"width": 10e-6, "height": 0.0},
                }
            }
        },
    )
    overview.linked_search_map_ids = [search_map.id]

    frame = _image_frame(atlas)
    markers = atlas_markers(atlas, MarkerContext(overviews=(overview,), search_maps=(search_map,)))
    ov_marker = next(m for m in markers if m.marker_type == MarkerType.OVERVIEW)
    cx = ov_marker.bbox[0] + ov_marker.bbox[2] / 2
    cy = ov_marker.bbox[1] + ov_marker.bbox[3] / 2

    # Expected: alignment shifts stage (0,0) -> (10um, 0) in stage space, THEN the
    # affine maps it. At 1 um/px a 10 um shift is 10 px, so 1000 -> 1010.
    expected = frame.atlas_pixel_affine.apply(*_apply_alignment_transform(
        (0.0, 0.0),
        AlignmentTransform(rotation_rad=0.0, translation=(10e-6, 0.0), source="t"),
        origin=(0.0, 0.0),
    ))
    assert (cx, cy) == pytest.approx(expected)
    assert (cx, cy) == pytest.approx((1010.0, 800.0))
    # Contrast: without the reload alignment the overview would sit at (1000, 800);
    # the 10 px difference confirms the alignment was composed before the affine.
    assert frame.atlas_pixel_affine.apply(0.0, 0.0) == pytest.approx((1000.0, 800.0))
    # No legacy reflection metadata when the affine path is used.
    assert "legacy_overlay_transform" not in ov_marker.metadata


def test_inferred_failed_tilt_uses_atlas_affine_without_search_map_reflection() -> None:
    coeffs = (1e6, 0.0, 1000.0, 0.0, -1e6, 800.0)
    stage_pts = [
        (0.0, 0.0),
        (100e-6, 0.0),
        (0.0, 100e-6),
        (100e-6, 100e-6),
    ]
    atlas = _atlas_with_nodes(
        _nodes_from_affine(coeffs, stage_pts, tile_w=200, tile_h=200)
    )
    failed = TiltSeries(
        id="vellio-1",
        name="vellio_1",
        mrc_path=Path("vellio_1.mrc"),
        mrc_metadata=MrcMetadata(
            path=Path("vellio_1.mrc"),
            size_bytes=0,
            nx=1,
            ny=1,
            frame_metadata=[{"stage_x": 25e-6, "stage_y": 50e-6}],
        ),
    )

    marker = next(
        marker
        for marker in atlas_lod_markers(
            atlas,
            MarkerContext(
                tilt_series=(failed,),
                failed_tilt_ids=frozenset({failed.id}),
            ),
        )
        if marker.metadata.get("atlas_lod_role") == "unattributed"
    )

    frame = _image_frame(atlas)
    assert frame is not None and frame.atlas_pixel_affine is not None
    expected = frame.atlas_pixel_affine.apply(25e-6, 50e-6)
    assert (marker.x, marker.y) == pytest.approx(expected)
    assert "legacy_overlay_transform" not in marker.metadata


def test_atlas_affine_preserves_focus_and_tracking_offsets_from_exposure() -> None:
    coeffs = (1e6, 0.0, 1000.0, 0.0, -1e6, 800.0)
    stage_pts = [
        (0.0, 0.0),
        (100e-6, 0.0),
        (0.0, 100e-6),
        (100e-6, 100e-6),
    ]
    atlas = _atlas_with_nodes(
        _nodes_from_affine(coeffs, stage_pts, tile_w=200, tile_h=200)
    )
    batch = BatchPosition(
        id="batch-1",
        name="batch_1",
        metadata={
            "ExposureTemplateAreaParameters": {
                "PositionX": 0.0,
                "PositionY": 0.0,
            },
            "TrackingTemplateAreaParameters": {
                "PositionX": 10e-6,
                "PositionY": -5e-6,
            },
            "FocusTemplateAreaParameters": {
                "PositionX": 10e-6,
                "PositionY": -5e-6,
            },
        },
    )
    frame = _image_frame(atlas)
    assert frame is not None

    markers = _template_markers_in_frame(
        atlas.id,
        frame,
        batch,
        (0.0, 0.0),
    )
    by_type = {marker.marker_type: marker for marker in markers}
    exposure = by_type[MarkerType.EXPOSURE_AREA]
    tracking = by_type[MarkerType.TRACKING_AREA]
    focus = by_type[MarkerType.FOCUS_AREA]

    assert (exposure.x, exposure.y) == pytest.approx(
        frame.atlas_pixel_affine.apply(0.0, 0.0)
    )
    expected_offset_position = frame.atlas_pixel_affine.apply(10e-6, -5e-6)
    assert (tracking.x, tracking.y) == pytest.approx(expected_offset_position)
    assert (focus.x, focus.y) == pytest.approx(expected_offset_position)
    assert "legacy_overlay_transform" not in tracking.metadata
    assert "legacy_overlay_transform" not in focus.metadata


# --- documented fallbacks --------------------------------------------------


def test_no_node_table_falls_back_to_none() -> None:
    atlas = Atlas(id="a", mrc_metadata=MrcMetadata(path=Path("a.mrc"), size_bytes=0, nx=10, ny=10))
    assert _atlas_pixel_affine(atlas) is None


def test_too_few_nodes_falls_back_to_none() -> None:
    nodes = [_node((0.0, 0.0), (0, 0, 10, 10)), _node((1e-6, 0.0), (10, 0, 10, 10))]
    atlas = _atlas_with_nodes(nodes)
    assert _atlas_pixel_affine(atlas) is None


def test_unfittable_nodes_rejected_by_residual_guard() -> None:
    # Collinear stage points cannot define a 2D affine -> degenerate solve -> None.
    nodes = [_node((float(i) * 1e-6, 0.0), (i * 10, 0, 10, 10)) for i in range(6)]
    atlas = _atlas_with_nodes(nodes)
    assert _atlas_pixel_affine(atlas) is None


def test_fit_affine_reports_residual() -> None:
    pairs = [
        ((0.0, 0.0), (0.0, 0.0, 0.0, 0.0)),
        ((1.0, 0.0), (1.0, 0.0, 0.0, 0.0)),
        ((0.0, 1.0), (0.0, 1.0, 0.0, 0.0)),
        ((1.0, 1.0), (1.0, 1.0, 0.0, 0.0)),
    ]
    fit = _fit_affine_least_squares(pairs)
    assert fit is not None
    coeffs, residual = fit
    assert residual < 1e-9
    assert coeffs == pytest.approx((1.0, 0.0, 0.0, 0.0, 1.0, 0.0))


# --- optional reference data (skipped when the dataset is not present) ------

_ROOT = Path(__file__).resolve().parent.parent
_ATLAS_DIR = Path(
    os.environ.get(
        "TOMOAPP_REFERENCE_ATLAS_DIR",
        _ROOT / "reference_atlas" / "Sample3" / "Atlas",
    )
)
_SM_DISPLACED = Path(
    os.environ.get(
        "TOMOAPP_REFERENCE_SEARCH_MAP_DISPLACED",
        _ROOT / "reference_collection" / "Sample3" / "SearchMaps" / "SearchMap_displaced",
    )
)
_SM_CORRECT = Path(
    os.environ.get(
        "TOMOAPP_REFERENCE_SEARCH_MAP_CORRECT",
        _ROOT / "reference_collection" / "Sample3" / "SearchMaps" / "SearchMap_correct",
    )
)

_has_data = _ATLAS_DIR.exists() and _SM_DISPLACED.exists() and _SM_CORRECT.exists()


@pytest.mark.skipif(not _has_data, reason="Reference atlas dataset not present")
def test_reference_atlas_recovers_node_affine() -> None:
    atlas = parse_atlas_folder(_ATLAS_DIR)
    affine = _atlas_pixel_affine(atlas)
    assert affine is not None
    assert affine.sample_count == 35
    assert affine.residual_px < 1.0  # microscope-recorded table fits to sub-pixel
    # det of the linear part is negative -> stage/image handedness flip is captured
    a, b, _c, d, e, _g = affine.coefficients
    assert (a * e - b * d) < 0


@pytest.mark.skipif(not _has_data, reason="Reference atlas dataset not present")
def test_reference_search_map_overlays_match_node_ground_truth() -> None:
    atlas = parse_atlas_folder(_ATLAS_DIR)
    sm_correct = parse_search_map_folder(_SM_CORRECT)
    sm_displaced = parse_search_map_folder(_SM_DISPLACED)
    overviews = tuple(s.overview for s in (sm_correct, sm_displaced) if s.overview)
    frame = _image_frame(atlas)
    markers = atlas_markers(
        atlas,
        MarkerContext(overviews=overviews, search_maps=(sm_correct, sm_displaced)),
    )

    def ground_truth_centre(sm: SearchMap) -> tuple[float, float]:
        stage = ms._stage_position(sm)
        align = ms._search_map_alignment_transform(sm)
        corrected = _apply_alignment_transform(stage, align, origin=frame.stage_position)
        return frame.atlas_pixel_affine.apply(*corrected)

    # Both overview region overlays must land on the node-table ground truth.
    for overview, sm in zip(overviews, (sm_correct, sm_displaced)):
        marker = next(
            m for m in markers
            if m.marker_type == MarkerType.OVERVIEW and m.linked_object_id == overview.id
        )
        cx = marker.bbox[0] + marker.bbox[2] / 2
        cy = marker.bbox[1] + marker.bbox[3] / 2
        gx, gy = ground_truth_centre(sm)
        assert math.hypot(cx - gx, cy - gy) < 1.0  # sub-pixel agreement
