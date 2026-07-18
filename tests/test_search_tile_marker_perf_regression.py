"""Perf-regression tests for :func:`build_search_tiles`.

These pin down the optimisation that precomputes search-map markers once
per ``SearchMap`` instead of once per ``(batch, exposure target)``. The
former scaled poorly on multigrid samples: on a large reference
dataset the per-batch pattern cost ~6 s on a warm load and was the
dominant share of :func:`SessionLoader.load`. The fix is in
:func:`tomography_session_browser.parsers.search_tile_parser._precompute_search_map_marker_index`.
"""

from __future__ import annotations

from typing import Any

import pytest

from tomography_session_browser.domain.models import BatchPosition, SearchMap
from tomography_session_browser.parsers import search_tile_parser as stp
from tomography_session_browser.services.marker_service import ImageFrame


def _make_batch(
    sm_id: str,
    sm_name: str,
    suffix: str,
    *,
    extra_targets: int,
) -> BatchPosition:
    """Return a BatchPosition with one primary exposure plus ``extra_targets``
    additional exposure template areas. Each extra target would have caused a
    fresh ``search_map_markers`` call under the old code path.
    """

    additional = [
        {
            "PositionX": float(index + 1),
            "PositionY": float(index + 1),
            "_attributes": {},
            "Name": f"Extra-{suffix}-{index}",
        }
        for index in range(extra_targets)
    ]
    return BatchPosition(
        id=f"b-{suffix}",
        name=f"Pos-{suffix}",
        linked_search_map_id=sm_id,
        metadata={
            "PositionOnTileSet": {
                "StagePositionX": 0.0,
                "StagePositionY": 0.0,
                "TileSetName": sm_name,
            },
            "ExposureTemplateAreaParameters": {
                "PositionX": 0.0,
                "PositionY": 0.0,
                "_attributes": {},
            },
            "AdditionalExposureTemplateAreas": {
                "ExposureTemplateAreaParameters": additional,
            },
        },
    )


@pytest.fixture
def stub_image_frame(monkeypatch: pytest.MonkeyPatch) -> ImageFrame:
    frame = ImageFrame(
        image_size=(1024, 1024),
        stage_position=(0.0, 0.0),
        pixel_size=1e-6,
        source="SearchMap",
    )
    monkeypatch.setattr(stp, "_image_frame", lambda sm: frame)
    return frame


def test_build_search_tiles_calls_search_map_markers_at_most_once_per_map(
    monkeypatch: pytest.MonkeyPatch,
    stub_image_frame: ImageFrame,
) -> None:
    """The marker computation must be precomputed per SearchMap.

    With the regression, the call count would scale with
    ``len(batch_positions) × (1 + extra_targets_per_batch)``. With the fix
    it should never exceed ``len(search_maps)``.
    """

    call_count = 0

    def fake_search_map_markers(search_map: SearchMap, batches: Any, **kwargs: Any) -> list[Any]:
        nonlocal call_count
        call_count += 1
        return []

    monkeypatch.setattr(stp, "search_map_markers", fake_search_map_markers)

    search_maps = [
        SearchMap(id=f"sm-{i}", name=f"SearchMap_{i:02d}") for i in range(3)
    ]
    batch_positions: list[BatchPosition] = []
    for sm_index, search_map in enumerate(search_maps):
        for batch_index in range(3):
            batch_positions.append(
                _make_batch(
                    search_map.id,
                    search_map.name,
                    suffix=f"{sm_index}-{batch_index}",
                    extra_targets=2,
                )
            )

    stp.build_search_tiles(
        search_maps=search_maps,
        batch_positions=batch_positions,
        tilt_series=[],
    )

    assert call_count <= len(search_maps), (
        f"search_map_markers was invoked {call_count} times for "
        f"{len(search_maps)} search maps with 3 batches × 3 targets each. "
        "Regression: marker computation should be precomputed once per "
        "search map by _precompute_search_map_marker_index, not once per "
        "(batch, exposure target)."
    )


def test_build_search_tiles_skips_marker_compute_for_maps_with_no_linked_batches(
    monkeypatch: pytest.MonkeyPatch,
    stub_image_frame: ImageFrame,
) -> None:
    """Empty search maps (no linked batch positions) should not trigger
    ``search_map_markers`` at all — they have nothing to project."""

    call_count = 0

    def fake_search_map_markers(*_args: Any, **_kwargs: Any) -> list[Any]:
        nonlocal call_count
        call_count += 1
        return []

    monkeypatch.setattr(stp, "search_map_markers", fake_search_map_markers)

    search_maps = [
        SearchMap(id=f"sm-{i}", name=f"SearchMap_{i:02d}") for i in range(5)
    ]
    # Only the first search map gets a batch; the other four are empty.
    batch_positions = [
        _make_batch(search_maps[0].id, search_maps[0].name, suffix="solo", extra_targets=1)
    ]

    stp.build_search_tiles(
        search_maps=search_maps,
        batch_positions=batch_positions,
        tilt_series=[],
    )

    assert call_count <= 1, (
        f"search_map_markers was invoked {call_count} times when only one "
        "search map had a linked batch. Empty search maps must short-circuit."
    )
