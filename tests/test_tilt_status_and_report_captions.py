from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from reportlab.lib.units import inch

from tomography_session_browser.domain.models import BatchPosition, MrcMetadata, Sample, SearchMap, TiltSeries
from tomography_session_browser.reports.report_generator import (
    _linked_batch_positions_for_search_map,
    _search_map_caption,
    _search_map_completion_block,
)
from tomography_session_browser.services.tilt_series_validation import (
    STATUS_COMPLETE,
    STATUS_FAILED,
    STATUS_INCOMPLETE,
    validate_tilt_series,
)


def _tilt_with_expected_count(tmp_path: Path, actual: int, expected: int = 35) -> TiltSeries:
    mrc_path = tmp_path / f"tilt_{actual}.mrc"
    step = 2.0
    max_tilt = -34.0 + ((expected - 1) * step)
    return TiltSeries(
        id=f"ts-{actual}",
        name=f"tilt_{actual}",
        mrc_path=mrc_path,
        tilt_range=(-34.0, max_tilt),
        metadata={"TiltStep": step},
        mrc_metadata=MrcMetadata(path=mrc_path, size_bytes=4, nz=actual),
    )


def test_tilt_series_with_fewer_than_five_images_is_failed(tmp_path: Path) -> None:
    validation = validate_tilt_series(_tilt_with_expected_count(tmp_path, actual=4))

    assert validation.status == STATUS_FAILED
    assert validation.actual_count == 4
    assert validation.expected_count == 35


def test_tilt_series_with_at_least_five_but_fewer_than_expected_is_incomplete(tmp_path: Path) -> None:
    validation = validate_tilt_series(_tilt_with_expected_count(tmp_path, actual=5))

    assert validation.status == STATUS_INCOMPLETE
    assert validation.actual_count == 5
    assert validation.expected_count == 35


def test_tilt_series_with_expected_image_count_is_complete(tmp_path: Path) -> None:
    validation = validate_tilt_series(_tilt_with_expected_count(tmp_path, actual=35))

    assert validation.status == STATUS_COMPLETE
    assert validation.actual_count == 35
    assert validation.expected_count == 35


def test_search_map_caption_names_one_associated_batch_position(tmp_path: Path) -> None:
    search_map = SearchMap(
        id="sm-1",
        name="SearchMap_20260319_234438",
        linked_batch_position_ids=["bp-1"],
    )
    sample = Sample(id="sample-1", name="2. Rothia", path=tmp_path)
    batches = [BatchPosition(id="bp-1", name="Position_001")]
    warnings: list[str] = []

    linked_batches = _linked_batch_positions_for_search_map(search_map, batches)
    caption = _search_map_caption(1, search_map, sample, (512, 436), linked_batches, warnings)

    assert caption == (
        "Figure 1. Search map SearchMap_20260319_234438 for data collection 2. Rothia. "
        "Overlays show batch position 1 and target areas (exposure area, focus, tracking). "
        "Numeric labels identify batch positions and exposure areas. "
        "Native size: 512 × 436 px."
    )
    assert warnings == []


def test_search_map_caption_names_multiple_associated_batch_positions(tmp_path: Path) -> None:
    search_map = SearchMap(id="sm-1", name="SearchMap_20260319_234438")
    sample = Sample(id="sample-1", name="2. Rothia", path=tmp_path)
    batches = [
        BatchPosition(id="bp-3", name="Position_003", linked_search_map_id="sm-1"),
        BatchPosition(id="bp-1", name="Position_001", linked_search_map_id="sm-1"),
        BatchPosition(id="bp-2", name="Position_002", linked_search_map_id="sm-1"),
    ]
    warnings: list[str] = []

    linked_batches = _linked_batch_positions_for_search_map(search_map, batches)
    caption = _search_map_caption(1, search_map, sample, (512, 436), linked_batches, warnings)

    assert caption == (
        "Figure 1. Search map SearchMap_20260319_234438 for data collection 2. Rothia. "
        "Overlays show batch positions 1, 2 and 3 with target areas "
        "(exposure area, focus, tracking). Numeric labels identify batch positions and exposure areas. "
        "Native size: 512 × 436 px."
    )
    assert warnings == []


def test_search_map_caption_falls_back_when_batch_association_is_unresolved(tmp_path: Path) -> None:
    search_map = SearchMap(id="sm-1", name="SearchMap_20260319_234438")
    sample = Sample(id="sample-1", name="2. Rothia", path=tmp_path)
    warnings: list[str] = []

    caption = _search_map_caption(1, search_map, sample, (512, 436), [], warnings)

    assert caption == (
        "Figure 1. Search map SearchMap_20260319_234438 for data collection 2. Rothia. "
        "Overlays show batch positions and target areas (exposure area, focus, tracking). "
        "Numeric labels identify batch positions and exposure areas. "
        "Native size: 512 × 436 px."
    )
    assert len(warnings) == 1
    assert "could not resolve associated batch positions" in warnings[0]


def test_search_map_caption_reports_inferred_failed_tilt_without_generic_warning(tmp_path: Path) -> None:
    search_map = SearchMap(id="sm-1", name="SearchMap_20260320_022428")
    sample = Sample(id="sample-1", name="1. vellio", path=tmp_path)
    warnings: list[str] = []

    caption = _search_map_caption(
        1,
        search_map,
        sample,
        (512, 436),
        [],
        warnings,
        inferred_failed_tilt_count=1,
        inferred_failed_batch_count=1,
    )

    assert caption == (
        "Figure 1. Search map SearchMap_20260320_022428 for data collection 1. vellio. "
        "Overlays show 1 inferred failed exposure area across 1 inferred failed batch position group. "
        "Numeric labels identify available batch positions and exposure areas. "
        "Native size: 512 × 436 px."
    )
    assert warnings == []


def test_search_map_completion_block_keeps_failed_text_on_own_line(tmp_path: Path) -> None:
    search_map = SearchMap(
        id="sm-1",
        name="SearchMap_20260320_022037",
        linked_batch_position_ids=["bp-1"],
    )
    batch = BatchPosition(
        id="bp-1",
        linked_search_map_id=search_map.id,
        linked_tilt_series_ids=["ts-good", "ts-failed"],
        metadata={
            "ExposureTemplateAreaParameters": {},
            "AdditionalExposureTemplateAreas": {"ExposureTemplateAreaParameters": {}},
        },
    )
    sample = Sample(
        id="sample-1",
        name="Sample 1",
        path=tmp_path,
        search_maps=[search_map],
        batch_positions=[batch],
    )
    ctx = SimpleNamespace(
        validations={
            "ts-good": SimpleNamespace(status=STATUS_COMPLETE),
            "ts-failed": SimpleNamespace(status=STATUS_FAILED),
        }
    )

    table = _search_map_completion_block(sample, ctx)
    text_cell = table._cellvalues[0][2]

    assert "<br/>" in text_cell.text
    assert "1 acquired / 2 planned exposure areas" in text_cell.text
    assert "1 failed exposure area" in text_cell.text
    assert table._argW[0] < 2.0 * inch
    assert table._cellStyles[0][1].rightPadding == 0
    assert table._cellStyles[0][2].leftPadding == 8
