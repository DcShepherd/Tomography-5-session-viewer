from __future__ import annotations

from pathlib import Path

from tomography_session_browser.domain.models import (
    BatchPosition,
    MdocSection,
    SearchTile,
    TiltSeries,
)
from tomography_session_browser.services.navigation_service import (
    resolve_tilt_series_navigation_targets,
)


def _tilt(
    tmp_path: Path,
    *,
    tilt_id: str = "tilt",
    name: str = "vellio_1_2",
    count: int = 1,
    linked_batch_position_id: str | None = None,
) -> TiltSeries:
    return TiltSeries(
        id=tilt_id,
        name=name,
        mrc_path=tmp_path / f"{tilt_id}.mrc",
        sections=[MdocSection(z_value=index) for index in range(count)],
        linked_batch_position_id=linked_batch_position_id,
    )


def test_failed_orphan_batch_name_is_display_only_not_a_navigation_link(
    tmp_path: Path,
) -> None:
    tilt = _tilt(tmp_path, count=1)
    sibling = BatchPosition(id="batch-1", name="vellio_1")

    targets = resolve_tilt_series_navigation_targets(
        tilt,
        batch_positions=[sibling],
        search_maps=[],
        search_tiles=[],
        overviews=[],
    )

    assert targets.batch_position is None
    assert targets.search_tile is None
    assert targets.inferred_batch_label == "vellio_1"


def test_nonfailed_orphan_does_not_receive_an_inferred_batch_label(
    tmp_path: Path,
) -> None:
    tilt = _tilt(tmp_path, count=5)

    targets = resolve_tilt_series_navigation_targets(
        tilt,
        batch_positions=[],
        search_maps=[],
        search_tiles=[],
        overviews=[],
    )

    assert targets.batch_position is None
    assert targets.inferred_batch_label is None


def test_explicit_batch_link_remains_navigable(tmp_path: Path) -> None:
    batch = BatchPosition(id="batch-1", name="vellio_1")
    tilt = _tilt(tmp_path, linked_batch_position_id=batch.id)

    targets = resolve_tilt_series_navigation_targets(
        tilt,
        batch_positions=[batch],
        search_maps=[],
        search_tiles=[],
        overviews=[],
    )

    assert targets.batch_position is batch
    assert targets.inferred_batch_label is None


def test_multiple_direct_search_tiles_are_ambiguous(tmp_path: Path) -> None:
    tilt = _tilt(tmp_path)
    tiles = [
        SearchTile(id="tile-1", name="Exposure 1", linked_tilt_series_ids=[tilt.id]),
        SearchTile(id="tile-2", name="Exposure 2", linked_tilt_series_ids=[tilt.id]),
    ]

    targets = resolve_tilt_series_navigation_targets(
        tilt,
        batch_positions=[],
        search_maps=[],
        search_tiles=tiles,
        overviews=[],
    )

    assert targets.search_tile is None


def test_multiple_tiles_for_explicit_batch_are_ambiguous(tmp_path: Path) -> None:
    batch = BatchPosition(id="batch-1", name="vellio_1")
    tilt = _tilt(tmp_path, linked_batch_position_id=batch.id)
    tiles = [
        SearchTile(id="tile-1", name="Exposure 1", batch_position_id=batch.id),
        SearchTile(id="tile-2", name="Exposure 2", batch_position_id=batch.id),
    ]

    targets = resolve_tilt_series_navigation_targets(
        tilt,
        batch_positions=[batch],
        search_maps=[],
        search_tiles=tiles,
        overviews=[],
    )

    assert targets.batch_position is batch
    assert targets.search_tile is None


def test_unique_direct_search_tile_remains_navigable(tmp_path: Path) -> None:
    tilt = _tilt(tmp_path)
    tile = SearchTile(
        id="tile-1",
        name="Exposure 1",
        linked_tilt_series_ids=[tilt.id],
    )

    targets = resolve_tilt_series_navigation_targets(
        tilt,
        batch_positions=[],
        search_maps=[],
        search_tiles=[tile],
        overviews=[],
    )

    assert targets.search_tile is tile
