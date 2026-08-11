"""N4: navigation and displayed status must consume the same link rules.

Tomography 5 writes a batch/search-map relationship on whichever side happened
to be serialised. Every assertion below is driven from one fixture builder that
can emit the *same* relationship in either direction, so a rule honoured by one
consumer and not the other shows up as a direct inequality rather than as two
tests that quietly drifted apart.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tomography_session_browser.domain.models import (
    BatchPosition,
    MdocSection,
    SearchMap,
    TiltSeries,
)
from tomography_session_browser.services.item_status import (
    batch_positions_for_search_map,
    build_item_status_context,
    item_list_status,
)
from tomography_session_browser.services.navigation_service import (
    resolve_batch_position_search_map,
)

# The two ways the same scientific relationship can be recorded.
DIRECTION_BATCH_TO_MAP = "batch.linked_search_map_id"
DIRECTION_MAP_TO_BATCH = "search_map.linked_batch_position_ids"
BOTH_DIRECTIONS = "both sides recorded"

ALL_DIRECTIONS = (DIRECTION_BATCH_TO_MAP, DIRECTION_MAP_TO_BATCH, BOTH_DIRECTIONS)


def _scenario(direction: str, tmp_path: Path, *, tilt_frames: int = 20):
    """Build one batch + search map related through ``direction``."""

    mrc_path = tmp_path / "tilt.mrc"
    mrc_path.write_bytes(b"0")
    tilt = TiltSeries(
        id="tilt-1",
        name="Position_1",
        mrc_path=mrc_path,
        sections=[MdocSection(z_value=index) for index in range(tilt_frames)],
    )
    batch = BatchPosition(
        id="batch-1",
        name="Position_1",
        linked_tilt_series_ids=[tilt.id],
    )
    search_map = SearchMap(id="sm-1", name="SearchMap_1")

    if direction in (DIRECTION_BATCH_TO_MAP, BOTH_DIRECTIONS):
        batch.linked_search_map_id = search_map.id
    if direction in (DIRECTION_MAP_TO_BATCH, BOTH_DIRECTIONS):
        search_map.linked_batch_position_ids = [batch.id]

    context = build_item_status_context(
        search_maps=[search_map],
        batch_positions=[batch],
        tilt_series=[tilt],
    )
    return search_map, batch, tilt, context


@pytest.mark.parametrize("direction", ALL_DIRECTIONS)
def test_search_map_sees_the_batch_in_either_direction(direction: str, tmp_path: Path) -> None:
    search_map, batch, _tilt, context = _scenario(direction, tmp_path)

    assert batch_positions_for_search_map(search_map, context) == [batch]


@pytest.mark.parametrize("direction", ALL_DIRECTIONS)
def test_navigation_and_status_agree_on_the_same_relationship(
    direction: str, tmp_path: Path
) -> None:
    """Whatever navigation resolves, the status row must also count."""

    search_map, batch, _tilt, context = _scenario(direction, tmp_path)

    target = resolve_batch_position_search_map(batch, search_maps=[search_map])
    assert target.navigable is True
    assert target.search_map is search_map

    assert batch in batch_positions_for_search_map(target.search_map, context)


@pytest.mark.parametrize("direction", ALL_DIRECTIONS)
def test_search_map_status_text_is_direction_independent(
    direction: str, tmp_path: Path
) -> None:
    """The badge and summary must not depend on which side recorded the link."""

    search_map, _batch, _tilt, context = _scenario(direction, tmp_path)
    status = item_list_status(search_map, context)

    assert status.status == "done"
    assert "1 batch position" in status.summary
    assert "1/1 tilt series" in status.summary
    assert "no linked batch positions" not in status.summary


def test_all_directions_produce_identical_status(tmp_path: Path) -> None:
    """One relationship, three encodings, one result."""

    summaries = set()
    badges = set()
    for index, direction in enumerate(ALL_DIRECTIONS):
        scenario_dir = tmp_path / str(index)
        scenario_dir.mkdir()
        search_map, _batch, _tilt, context = _scenario(direction, scenario_dir)
        status = item_list_status(search_map, context)
        badges.add(status.status)
        summaries.add(status.summary)

    assert len(badges) == 1, badges
    assert len(summaries) == 1, summaries


def test_two_sided_metadata_counts_the_batch_once(tmp_path: Path) -> None:
    """Recording the link on both sides must not double-count the batch."""

    search_map, batch, _tilt, context = _scenario(BOTH_DIRECTIONS, tmp_path)

    linked = batch_positions_for_search_map(search_map, context)

    assert linked == [batch]
    assert item_list_status(search_map, context).summary.count("batch position") == 1


def test_inferred_failed_batch_does_not_attach_a_batch_to_a_search_map(
    tmp_path: Path,
) -> None:
    """An orphaned failed tilt must not create a Search-map/batch relationship.

    The tilt's name yields an inferred batch label matching the sibling batch,
    which is deliberately display-only. The Search map records no link to that
    batch, so it must still report none.
    """

    mrc_path = tmp_path / "orphan.mrc"
    mrc_path.write_bytes(b"0")
    orphan = TiltSeries(
        id="tilt-orphan",
        name="Position_1_2",
        mrc_path=mrc_path,
        sections=[MdocSection(z_value=0)],
    )
    sibling_batch = BatchPosition(id="batch-1", name="Position_1")
    search_map = SearchMap(id="sm-1", name="SearchMap_1")

    context = build_item_status_context(
        search_maps=[search_map],
        batch_positions=[sibling_batch],
        tilt_series=[orphan],
    )

    # The inferred label exists...
    assert context.inferred_batch_labels.get(orphan.id) == "Position_1"
    # ...but it is not a link the Search map may consume.
    assert batch_positions_for_search_map(search_map, context) == []
    assert "no linked batch positions" in item_list_status(search_map, context).summary
