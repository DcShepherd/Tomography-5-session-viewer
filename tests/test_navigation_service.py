from __future__ import annotations

from pathlib import Path

import pytest

from navigation_contract import NAVIGATION_CONTRACT, contract_case_ids
from tomography_session_browser.domain.models import (
    BatchPosition,
    MdocSection,
    Overview,
    SearchMap,
    SearchTile,
    TiltSeries,
)
from tomography_session_browser.domain.markers import ImageMarker, MarkerType
from tomography_session_browser.services.navigation_service import (
    STATE_AMBIGUOUS,
    STATE_NAVIGABLE,
    NavigationResolution,
    resolve_batch_position_overview,
    resolve_batch_position_search_map,
    resolve_exposure_tilt_series,
    resolve_tilt_series_navigation_targets,
)


def test_exposure_resolver_rejects_conflicting_explicit_tilt_ids(tmp_path: Path) -> None:
    batch = BatchPosition(id="batch", name="position_1", linked_tilt_series_ids=["a", "b"])
    first = _tilt(tmp_path, tilt_id="a", name="position_1", linked_batch_position_id=batch.id)
    second = _tilt(tmp_path, tilt_id="b", name="position_1_2", linked_batch_position_id=batch.id)
    marker = ImageMarker(
        id="exposure",
        marker_type=MarkerType.EXPOSURE_AREA,
        linked_object_id=batch.id,
        source_object_id=batch.id,
        metadata={"tilt_series_id": first.id, "raw": {"TiltSeriesId": second.id}},
    )

    result = resolve_exposure_tilt_series(marker, batch=batch, tilt_series=[first, second])

    assert result.tilt_series is None
    assert result.ambiguous is True
    assert result.resolution.candidate_ids == ("a", "b")


def test_exposure_resolver_uses_one_consistent_explicit_tilt_id(tmp_path: Path) -> None:
    batch = BatchPosition(id="batch", name="position_1")
    tilt = _tilt(tmp_path, tilt_id="a", name="position_1", linked_batch_position_id=batch.id)
    marker = ImageMarker(
        id="exposure",
        marker_type=MarkerType.EXPOSURE_AREA,
        linked_object_id=batch.id,
        source_object_id=batch.id,
        metadata={"tilt_series_id": tilt.id, "raw": {"TiltSeriesId": tilt.id}},
    )

    result = resolve_exposure_tilt_series(marker, batch=batch, tilt_series=[tilt])

    assert result.tilt_series is tilt
    assert result.resolution.navigable is True


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


# ---------------------------------------------------------------------------
# G0 contract coverage
#
# Each builder below reproduces exactly one frozen case from
# tests/navigation_contract.py and returns the NavigationResolution the service
# produced for that relationship. The parametrised test asserts the state
# matches the contract, and test_every_contract_case_is_covered fails if a case
# is added to the table without a builder here.
# ---------------------------------------------------------------------------


def _overview(overview_id: str, name: str, **kwargs) -> Overview:
    return Overview(id=overview_id, name=name, image_path=Path(f"{overview_id}.jpg"), **kwargs)


def _targets(tilt, **kwargs):
    kwargs.setdefault("batch_positions", [])
    kwargs.setdefault("search_maps", [])
    kwargs.setdefault("search_tiles", [])
    kwargs.setdefault("overviews", [])
    return resolve_tilt_series_navigation_targets(tilt, **kwargs)


def _c_tilt_batch_none(tmp_path: Path) -> NavigationResolution:
    return _targets(_tilt(tmp_path)).batch_position_resolution


def _c_tilt_batch_explicit_one(tmp_path: Path) -> NavigationResolution:
    batch = BatchPosition(id="batch-1", name="Position_1")
    tilt = _tilt(tmp_path, linked_batch_position_id=batch.id)
    return _targets(tilt, batch_positions=[batch]).batch_position_resolution


def _c_tilt_batch_explicit_absent(tmp_path: Path) -> NavigationResolution:
    tilt = _tilt(tmp_path, linked_batch_position_id="not-in-scope")
    return _targets(tilt).batch_position_resolution


def _c_tilt_batch_reciprocal_one(tmp_path: Path) -> NavigationResolution:
    tilt = _tilt(tmp_path)
    batch = BatchPosition(id="batch-1", name="Position_1", linked_tilt_series_ids=[tilt.id])
    return _targets(tilt, batch_positions=[batch]).batch_position_resolution


def _c_tilt_batch_reciprocal_many(tmp_path: Path) -> NavigationResolution:
    tilt = _tilt(tmp_path)
    batches = [
        BatchPosition(id="batch-1", name="Position_1", linked_tilt_series_ids=[tilt.id]),
        BatchPosition(id="batch-2", name="Position_2", linked_tilt_series_ids=[tilt.id]),
    ]
    return _targets(tilt, batch_positions=batches).batch_position_resolution


def _c_tilt_tile_none(tmp_path: Path) -> NavigationResolution:
    return _targets(_tilt(tmp_path)).search_tile_resolution


def _c_tilt_tile_direct_one(tmp_path: Path) -> NavigationResolution:
    tilt = _tilt(tmp_path)
    tile = SearchTile(id="tile-1", name="Exposure 1", linked_tilt_series_ids=[tilt.id])
    return _targets(tilt, search_tiles=[tile]).search_tile_resolution


def _c_tilt_tile_direct_many(tmp_path: Path) -> NavigationResolution:
    tilt = _tilt(tmp_path)
    tiles = [
        SearchTile(id="tile-1", name="Exposure 1", linked_tilt_series_ids=[tilt.id]),
        SearchTile(id="tile-2", name="Exposure 2", linked_tilt_series_ids=[tilt.id]),
    ]
    return _targets(tilt, search_tiles=tiles).search_tile_resolution


def _c_tilt_tile_via_batch_one(tmp_path: Path) -> NavigationResolution:
    batch = BatchPosition(id="batch-1", name="Position_1")
    tilt = _tilt(tmp_path, linked_batch_position_id=batch.id)
    tile = SearchTile(id="tile-1", name="Exposure 1", batch_position_id=batch.id)
    return _targets(tilt, batch_positions=[batch], search_tiles=[tile]).search_tile_resolution


def _c_tilt_tile_via_batch_many(tmp_path: Path) -> NavigationResolution:
    batch = BatchPosition(id="batch-1", name="Position_1")
    tilt = _tilt(tmp_path, linked_batch_position_id=batch.id)
    tiles = [
        SearchTile(id="tile-1", name="Exposure 1", batch_position_id=batch.id),
        SearchTile(id="tile-2", name="Exposure 2", batch_position_id=batch.id),
    ]
    return _targets(tilt, batch_positions=[batch], search_tiles=tiles).search_tile_resolution


def _c_tilt_map_none(tmp_path: Path) -> NavigationResolution:
    return _targets(_tilt(tmp_path)).search_map_resolution


def _c_tilt_map_via_batch_one(tmp_path: Path) -> NavigationResolution:
    search_map = SearchMap(id="sm-1", name="SearchMap_1")
    batch = BatchPosition(id="batch-1", name="Position_1", linked_search_map_id=search_map.id)
    tilt = _tilt(tmp_path, linked_batch_position_id=batch.id)
    return _targets(
        tilt, batch_positions=[batch], search_maps=[search_map]
    ).search_map_resolution


def _c_tilt_map_reciprocal_one(tmp_path: Path) -> NavigationResolution:
    tilt = _tilt(tmp_path)
    search_map = SearchMap(id="sm-1", name="SearchMap_1", linked_tilt_series_ids=[tilt.id])
    return _targets(tilt, search_maps=[search_map]).search_map_resolution


def _c_tilt_map_reciprocal_many(tmp_path: Path) -> NavigationResolution:
    tilt = _tilt(tmp_path)
    maps = [
        SearchMap(id="sm-1", name="SearchMap_1", linked_tilt_series_ids=[tilt.id]),
        SearchMap(id="sm-2", name="SearchMap_2", linked_tilt_series_ids=[tilt.id]),
    ]
    return _targets(tilt, search_maps=maps).search_map_resolution


def _c_tilt_overview_none(tmp_path: Path) -> NavigationResolution:
    return _targets(_tilt(tmp_path)).overview_resolution


def _c_tilt_overview_via_batch_one(tmp_path: Path) -> NavigationResolution:
    overview = _overview("ov-1", "Overview_1")
    batch = BatchPosition(id="batch-1", name="Position_1", linked_overview_id=overview.id)
    tilt = _tilt(tmp_path, linked_batch_position_id=batch.id)
    return _targets(tilt, batch_positions=[batch], overviews=[overview]).overview_resolution


def _c_tilt_overview_via_search_map_one(tmp_path: Path) -> NavigationResolution:
    overview = _overview("ov-1", "Overview_1")
    tilt = _tilt(tmp_path)
    search_map = SearchMap(
        id="sm-1",
        name="SearchMap_1",
        overview=overview,
        linked_tilt_series_ids=[tilt.id],
    )
    return _targets(tilt, search_maps=[search_map], overviews=[overview]).overview_resolution


def _c_tilt_overview_reciprocal_many(tmp_path: Path) -> NavigationResolution:
    tilt = _tilt(tmp_path)
    search_map = SearchMap(id="sm-1", name="SearchMap_1", linked_tilt_series_ids=[tilt.id])
    overviews = [
        _overview("ov-1", "Overview_A", linked_search_map_ids=[search_map.id]),
        _overview("ov-2", "Overview_B", linked_search_map_ids=[search_map.id]),
    ]
    return _targets(tilt, search_maps=[search_map], overviews=overviews).overview_resolution


def _c_batch_overview_none(_tmp_path: Path) -> NavigationResolution:
    return resolve_batch_position_overview(
        BatchPosition(id="batch-1"), search_maps=(), overviews=()
    ).resolution


def _c_batch_overview_explicit_one(_tmp_path: Path) -> NavigationResolution:
    overview = _overview("ov-1", "Overview_1")
    batch = BatchPosition(id="batch-1", linked_overview_id=overview.id)
    return resolve_batch_position_overview(
        batch, search_maps=(), overviews=(overview,)
    ).resolution


def _c_batch_overview_explicit_absent(_tmp_path: Path) -> NavigationResolution:
    batch = BatchPosition(id="batch-1", linked_overview_id="not-in-scope")
    return resolve_batch_position_overview(batch, search_maps=(), overviews=()).resolution


def _c_batch_overview_explicit_absent_reciprocal_one(_tmp_path: Path) -> NavigationResolution:
    overview = _overview("ov-1", "Overview_A", linked_batch_position_ids=["batch-1"])
    batch = BatchPosition(id="batch-1", linked_overview_id="not-in-scope")
    return resolve_batch_position_overview(
        batch, search_maps=(), overviews=(overview,)
    ).resolution


def _c_batch_overview_reciprocal_many(_tmp_path: Path) -> NavigationResolution:
    overviews = (
        _overview("ov-1", "Overview_A", linked_batch_position_ids=["batch-1"]),
        _overview("ov-2", "Overview_B", linked_batch_position_ids=["batch-1"]),
    )
    return resolve_batch_position_overview(
        BatchPosition(id="batch-1"), search_maps=(), overviews=overviews
    ).resolution


def _c_batch_map_none(_tmp_path: Path) -> NavigationResolution:
    return resolve_batch_position_search_map(
        BatchPosition(id="batch-1"), search_maps=()
    ).resolution


def _c_batch_map_explicit_one(_tmp_path: Path) -> NavigationResolution:
    search_map = SearchMap(id="sm-1", name="SearchMap_1")
    batch = BatchPosition(id="batch-1", linked_search_map_id=search_map.id)
    return resolve_batch_position_search_map(batch, search_maps=(search_map,)).resolution


def _c_batch_map_explicit_absent_reciprocal_one(_tmp_path: Path) -> NavigationResolution:
    search_map = SearchMap(id="sm-1", name="SearchMap_1", linked_batch_position_ids=["batch-1"])
    batch = BatchPosition(id="batch-1", linked_search_map_id="not-in-scope")
    return resolve_batch_position_search_map(batch, search_maps=(search_map,)).resolution


def _c_batch_map_explicit_absent_chained_only(_tmp_path: Path) -> NavigationResolution:
    # The map is reachable only by sharing a tilt series with the batch, which
    # is chained evidence and must not rescue a dangling explicit ID.
    search_map = SearchMap(id="sm-1", name="SearchMap_1", linked_tilt_series_ids=["tilt-1"])
    batch = BatchPosition(
        id="batch-1",
        linked_search_map_id="not-in-scope",
        linked_tilt_series_ids=["tilt-1"],
    )
    return resolve_batch_position_search_map(batch, search_maps=(search_map,)).resolution


def _c_batch_map_reciprocal_many(_tmp_path: Path) -> NavigationResolution:
    maps = (
        SearchMap(id="sm-1", name="SearchMap_1", linked_batch_position_ids=["batch-1"]),
        SearchMap(id="sm-2", name="SearchMap_2", linked_batch_position_ids=["batch-1"]),
    )
    return resolve_batch_position_search_map(
        BatchPosition(id="batch-1"), search_maps=maps
    ).resolution


def _exposure_marker(batch: BatchPosition, **metadata: object) -> ImageMarker:
    return ImageMarker(
        id="exposure",
        marker_type=MarkerType.EXPOSURE_AREA,
        linked_object_id=batch.id,
        source_object_id=batch.id,
        metadata=metadata,
    )


def _c_exposure_tilt_none(_tmp_path: Path) -> NavigationResolution:
    batch = BatchPosition(id="batch-1", name="position")
    return resolve_exposure_tilt_series(
        _exposure_marker(batch), batch=batch, tilt_series=()
    ).resolution


def _c_exposure_tilt_explicit_one(tmp_path: Path) -> NavigationResolution:
    batch = BatchPosition(id="batch-1", name="position")
    tilt = _tilt(
        tmp_path,
        tilt_id="tilt-1",
        name="position",
        linked_batch_position_id=batch.id,
    )
    return resolve_exposure_tilt_series(
        _exposure_marker(batch, tilt_series_id=tilt.id),
        batch=batch,
        tilt_series=(tilt,),
    ).resolution


def _c_exposure_tilt_explicit_many(tmp_path: Path) -> NavigationResolution:
    batch = BatchPosition(id="batch-1", name="position")
    first = _tilt(tmp_path, tilt_id="tilt-1", linked_batch_position_id=batch.id)
    second = _tilt(tmp_path, tilt_id="tilt-2", linked_batch_position_id=batch.id)
    marker = _exposure_marker(
        batch,
        tilt_series_id=first.id,
        raw={"TiltSeriesId": second.id},
    )
    return resolve_exposure_tilt_series(
        marker, batch=batch, tilt_series=(first, second)
    ).resolution


def _partial_exposure_case(tmp_path: Path, names: tuple[str, ...], selected: int = 1) -> NavigationResolution:
    batch = BatchPosition(id="batch", name="target_1", metadata={
        "ExposureTemplateAreaParameters": {},
        "AdditionalExposureTemplateAreas": {"ExposureTemplateAreaParameters": [{}, {}]},
    })
    tilts = [_tilt(tmp_path, tilt_id=f"t{i}", name=name, linked_batch_position_id=batch.id)
             for i, name in enumerate(names)]
    batch.linked_tilt_series_ids = [tilt.id for tilt in tilts]
    return resolve_exposure_tilt_series(_exposure_marker(batch, exposure_index=selected), batch=batch, tilt_series=tilts).resolution


def _c_exposure_missing_middle(tmp_path: Path) -> NavigationResolution:
    return _partial_exposure_case(tmp_path, ("target_1", "target_1_3"))


def _c_exposure_missing_singleton(tmp_path: Path) -> NavigationResolution:
    return _partial_exposure_case(tmp_path, ("target_1",))


def _c_exposure_unrelated_singleton(tmp_path: Path) -> NavigationResolution:
    batch = BatchPosition(id="queued", name="queued_2", metadata={"ExposureTemplateAreaParameters": {}})
    tilt = _tilt(tmp_path, tilt_id="t", name="target_1", linked_batch_position_id="other")
    return resolve_exposure_tilt_series(_exposure_marker(batch, exposure_index=0), batch=batch, tilt_series=[tilt]).resolution


def _c_exposure_explicit_collision(tmp_path: Path) -> NavigationResolution:
    batch = BatchPosition(id="b", name="target_1")
    first = _tilt(tmp_path, tilt_id="duplicate", name="target_1")
    second = _tilt(tmp_path / "other", tilt_id="duplicate", name="unrelated")
    return resolve_exposure_tilt_series(_exposure_marker(batch, exposure_index=0, tilt_series_id="duplicate"),
                                      batch=batch, tilt_series=[first, second]).resolution


def _c_exposure_complete_order(tmp_path: Path) -> NavigationResolution:
    return _partial_exposure_case(tmp_path, ("custom_a", "custom_b", "custom_c"))


CONTRACT_BUILDERS = {
    "exposure_missing_middle": _c_exposure_missing_middle,
    "exposure_missing_singleton": _c_exposure_missing_singleton,
    "exposure_unrelated_singleton": _c_exposure_unrelated_singleton,
    "exposure_explicit_collision": _c_exposure_explicit_collision,
    "exposure_complete_order": _c_exposure_complete_order,
    "tilt_batch_none": _c_tilt_batch_none,
    "tilt_batch_explicit_one": _c_tilt_batch_explicit_one,
    "tilt_batch_explicit_absent": _c_tilt_batch_explicit_absent,
    "tilt_batch_reciprocal_one": _c_tilt_batch_reciprocal_one,
    "tilt_batch_reciprocal_many": _c_tilt_batch_reciprocal_many,
    "tilt_tile_none": _c_tilt_tile_none,
    "tilt_tile_direct_one": _c_tilt_tile_direct_one,
    "tilt_tile_direct_many": _c_tilt_tile_direct_many,
    "tilt_tile_via_batch_one": _c_tilt_tile_via_batch_one,
    "tilt_tile_via_batch_many": _c_tilt_tile_via_batch_many,
    "tilt_map_none": _c_tilt_map_none,
    "tilt_map_via_batch_one": _c_tilt_map_via_batch_one,
    "tilt_map_reciprocal_one": _c_tilt_map_reciprocal_one,
    "tilt_map_reciprocal_many": _c_tilt_map_reciprocal_many,
    "tilt_overview_none": _c_tilt_overview_none,
    "tilt_overview_via_batch_one": _c_tilt_overview_via_batch_one,
    "tilt_overview_via_search_map_one": _c_tilt_overview_via_search_map_one,
    "tilt_overview_reciprocal_many": _c_tilt_overview_reciprocal_many,
    "batch_overview_none": _c_batch_overview_none,
    "batch_overview_explicit_one": _c_batch_overview_explicit_one,
    "batch_overview_explicit_absent": _c_batch_overview_explicit_absent,
    "batch_overview_explicit_absent_reciprocal_one": (
        _c_batch_overview_explicit_absent_reciprocal_one
    ),
    "batch_overview_reciprocal_many": _c_batch_overview_reciprocal_many,
    "batch_map_none": _c_batch_map_none,
    "batch_map_explicit_one": _c_batch_map_explicit_one,
    "batch_map_explicit_absent_reciprocal_one": _c_batch_map_explicit_absent_reciprocal_one,
    "batch_map_explicit_absent_chained_only": _c_batch_map_explicit_absent_chained_only,
    "batch_map_reciprocal_many": _c_batch_map_reciprocal_many,
    "exposure_tilt_none": _c_exposure_tilt_none,
    "exposure_tilt_explicit_one": _c_exposure_tilt_explicit_one,
    "exposure_tilt_explicit_many": _c_exposure_tilt_explicit_many,
}


def test_every_contract_case_is_covered() -> None:
    """The frozen contract and the executable cases must not drift apart."""

    assert set(CONTRACT_BUILDERS) == set(contract_case_ids())


@pytest.mark.parametrize("case", NAVIGATION_CONTRACT, ids=lambda case: case.case_id)
def test_navigation_contract_case(case, tmp_path: Path) -> None:
    resolution = CONTRACT_BUILDERS[case.case_id](tmp_path)

    assert resolution.state == case.expected_state, (
        f"{case.relationship} ({case.rule}) expected {case.expected_state}, "
        f"got {resolution.state}: {resolution.explanation}"
    )

    if case.expected_state == STATE_NAVIGABLE:
        assert resolution.navigable is True
        assert resolution.target is not None
        assert resolution.provenance, "a navigable result must record which rule fired"
    else:
        assert resolution.navigable is False
        assert resolution.target is None

    if case.expected_state == STATE_AMBIGUOUS:
        assert resolution.ambiguous is True
        assert len(resolution.candidate_ids) == case.candidates
        assert "ambiguous" in resolution.explanation.lower()

    assert resolution.explanation, "every outcome must explain itself"


def test_ambiguous_resolution_names_its_candidates(tmp_path: Path) -> None:
    """An inert action has to say what it is torn between."""

    resolution = _c_tilt_map_reciprocal_many(tmp_path)

    assert resolution.candidate_ids == ("sm-1", "sm-2")
    assert "SearchMap_1" in resolution.explanation
    assert "SearchMap_2" in resolution.explanation


def test_same_entity_reached_twice_is_one_candidate_not_an_ambiguity() -> None:
    """Two-sided metadata pointing at the same Overview must stay navigable."""

    overview = _overview("ov-1", "Overview_1", linked_batch_position_ids=["batch-1"])
    search_map = SearchMap(id="sm-1", name="SearchMap_1", overview=overview)
    batch = BatchPosition(id="batch-1", linked_search_map_id=search_map.id)

    result = resolve_batch_position_overview(
        batch, search_maps=(search_map,), overviews=(overview,)
    )

    assert result.navigable is True
    assert result.overview is overview
    assert result.resolution.candidate_ids == ("ov-1",)


# --- duplicate records versus a genuine ID collision -----------------------


def test_duplicate_records_of_one_batch_are_not_an_ambiguity(tmp_path: Path) -> None:
    """Records sharing an ID and looking alike are one destination.

    This resolves an inconsistency between two code paths: ``_classify()``
    collapsed same-ID records blindly by picking the first — a silent guess —
    while the explicit-ID branches called the same situation ambiguous. Both
    now apply this rule, and its counterpart below.
    """

    first = BatchPosition(id="batch-1", name="Position_1", status="Acquired")
    duplicate = BatchPosition(id="batch-1", name="Position_1", status="Acquired")
    tilt = _tilt(tmp_path, linked_batch_position_id="batch-1")

    resolution = _targets(
        tilt, batch_positions=[first, duplicate]
    ).batch_position_resolution

    assert resolution.navigable is True
    assert resolution.target is first
    assert resolution.candidate_ids == ("batch-1",)


def test_two_different_batches_sharing_an_id_stay_ambiguous(tmp_path: Path) -> None:
    """A genuine collision must not be collapsed by the duplicate rule.

    Same ID but different content means the metadata really is undecidable, and
    picking either would be the guess the resolver exists to refuse.
    """

    first = BatchPosition(id="batch-1", name="Position_1", status="Acquired")
    other = BatchPosition(id="batch-1", name="Position_9", status="Failed")
    tilt = _tilt(tmp_path, linked_batch_position_id="batch-1")

    resolution = _targets(
        tilt, batch_positions=[first, other]
    ).batch_position_resolution

    assert resolution.ambiguous is True
    assert resolution.target is None


def test_same_display_fields_with_different_links_stay_ambiguous(tmp_path: Path) -> None:
    """Relationship identity is scientific content, not a display detail."""

    first = BatchPosition(
        id="batch-1",
        name="Position_1",
        status="Acquired",
        linked_overview_id="overview-a",
    )
    other = BatchPosition(
        id="batch-1",
        name="Position_1",
        status="Acquired",
        linked_overview_id="overview-b",
    )
    tilt = _tilt(tmp_path, linked_batch_position_id="batch-1")

    resolution = _targets(
        tilt, batch_positions=[first, other]
    ).batch_position_resolution

    assert resolution.ambiguous is True
    assert resolution.target is None


def test_search_maps_sharing_an_id_but_holding_different_overviews_stay_ambiguous(
    tmp_path: Path,
) -> None:
    """A held reference must be as discriminating as the entity it points at.

    The fingerprint compared ``overview.id`` alone, so two Search maps that
    were identical apart from pointing at *different* Overview records with the
    same id collapsed into one candidate and navigation silently picked the
    first.
    """

    first = SearchMap(
        id="sm-1",
        name="SearchMap_1",
        overview=Overview(id="ov-1", name="Overview_A", image_path=tmp_path / "a.jpg"),
        linked_tilt_series_ids=["tilt-1"],
    )
    second = SearchMap(
        id="sm-1",
        name="SearchMap_1",
        overview=Overview(id="ov-1", name="Overview_A", image_path=tmp_path / "b.jpg"),
        linked_tilt_series_ids=["tilt-1"],
    )
    tilt = _tilt(tmp_path, tilt_id="tilt-1")

    resolution = _targets(tilt, search_maps=[first, second]).search_map_resolution

    assert resolution.ambiguous is True
    assert resolution.target is None


def test_the_same_object_listed_twice_is_one_candidate(tmp_path: Path) -> None:
    """Object identity alone is enough; no signature comparison needed."""

    batch = BatchPosition(id="batch-1", name="Position_1")
    tilt = _tilt(tmp_path, linked_batch_position_id="batch-1")

    resolution = _targets(
        tilt, batch_positions=[batch, batch]
    ).batch_position_resolution

    assert resolution.navigable is True
