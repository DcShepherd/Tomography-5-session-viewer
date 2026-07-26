from __future__ import annotations

from datetime import datetime
from pathlib import Path

from tomography_session_browser.domain.models import BatchPosition, MdocSection, TiltSeries
from tomography_session_browser.parsers.search_tile_parser import (
    _tilts_by_batch as search_tile_tilts_by_batch,
)
from tomography_session_browser.services.item_status import (
    _tilt_series_for_batch as status_tilt_series_for_batch,
    build_item_status_context,
)
from tomography_session_browser.services.timeline_service import (
    datetime_sort_key,
    parse_datetime,
    parse_section_datetime,
)
from tomography_session_browser.ui.session_presenter import (
    _parse_datetime,
    _parse_loose_datetime,
    _tilt_series_for_batch as presenter_tilt_series_for_batch,
)


def _dated_tilts(tmp_path: Path) -> tuple[BatchPosition, list[TiltSeries]]:
    batch = BatchPosition(id="batch", name="Position_1")
    january = TiltSeries(
        id="january",
        name="january",
        mrc_path=tmp_path / "january.mrc",
        acquisition_time_start="31-Jan-26  09:00:00",
        sections=[MdocSection(z_value=index) for index in range(5)],
        linked_batch_position_id=batch.id,
    )
    february = TiltSeries(
        id="february",
        name="february",
        mrc_path=tmp_path / "february.mrc",
        acquisition_time_start="01-Feb-26  09:00:00",
        sections=[MdocSection(z_value=index) for index in range(5)],
        linked_batch_position_id=batch.id,
    )
    batch.linked_tilt_series_ids = [february.id, january.id]
    return batch, [february, january]


def test_all_datetime_entry_points_share_two_digit_year_parsing() -> None:
    text = "31-Jan-26  09:00:00"
    expected = datetime(2026, 1, 31, 9, 0, 0)

    assert parse_datetime(text) == expected
    assert parse_section_datetime({"DateTime": text}) == expected
    assert _parse_datetime(text) == expected
    assert _parse_loose_datetime(text) == expected


def test_datetime_sort_key_is_chronological_and_puts_invalid_values_last() -> None:
    values = ["not-a-date", "01-Feb-26  09:00:00", "31-Jan-26  09:00:00"]

    assert sorted(values, key=datetime_sort_key) == [
        "31-Jan-26  09:00:00",
        "01-Feb-26  09:00:00",
        "not-a-date",
    ]


def test_batch_tilt_ordering_is_chronological_in_all_three_consumers(
    tmp_path: Path,
) -> None:
    batch, tilts = _dated_tilts(tmp_path)
    context = build_item_status_context(
        batch_positions=[batch],
        tilt_series=tilts,
    )

    presenter_order = presenter_tilt_series_for_batch(batch, tilts)
    status_order = status_tilt_series_for_batch(batch, context)
    parser_order = search_tile_tilts_by_batch([batch], tilts)[batch.id]

    assert [tilt.id for tilt in presenter_order] == ["january", "february"]
    assert [tilt.id for tilt in status_order] == ["january", "february"]
    assert [tilt.id for tilt in parser_order] == ["january", "february"]
