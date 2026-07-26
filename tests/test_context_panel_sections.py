"""Regression tests for the sectioned context-panel output.

The :class:`MetadataPanel` parses ``Title:`` lines (no value after the
colon) into section headers and indented ``key: value`` lines below as
section rows. The brief asks for a stable section list — Summary /
Acquisition / Atlas linkage / Files / Metadata source / Warnings — so
the user can predict where each kind of fact lives.

These tests pin the section names emitted by the description helpers
in :mod:`tomography_session_browser.ui.session_presenter` so a future
refactor cannot quietly regress to the flat dump the panel had before.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import QApplication, QLabel

from tomography_session_browser.domain.models import (
    BatchPosition,
    MdocSection,
    MrcMetadata,
    Sample,
    SearchMap,
    SearchTile,
    TiltSeries,
)
from tomography_session_browser.services.acquisition_metadata import (
    ACQUISITION_SPOT_LABEL,
    LEGACY_SPOT_LABEL,
    SEARCH_SPOT_LABEL,
)
from tomography_session_browser.services.tilt_series_validation import (
    validate_session_tilt_series,
)
from tomography_session_browser.ui.main_window import MainWindow
from tomography_session_browser.ui.session_presenter import (
    _common_text,
    _is_successful_tilt_series,
    describe_object,
)
from tomography_session_browser.ui.widgets.metadata_panel import MetadataPanel


def _app() -> QApplication:
    app = QApplication.instance()
    return app or QApplication([])


def _section_titles(text: str) -> list[str]:
    """Return the section headers (lines of the form ``Title:`` with no
    value) in render order. Mirrors the :class:`MetadataPanel` parser:
    a line is a section header when its stripped form ends with ``:``
    AND has no content after the colon.
    """

    titles: list[str] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or ":" not in line:
            continue
        key, _, value = line.partition(":")
        if value.strip() == "":
            titles.append(key.strip())
    return titles


def _value_rows(panel: MetadataPanel) -> dict[str, list[tuple[str, str]]]:
    rows: dict[str, list[tuple[str, str]]] = {}
    for label in panel.findChildren(QLabel, "metaValue"):
        parent = label.parent()
        key = getattr(parent, "key", None)
        if isinstance(key, str):
            rows.setdefault(key, []).append((label.text(), label.toolTip()))
    return rows


def test_sample_description_uses_sectioned_layout() -> None:
    sample = Sample(id="s", name="vellio", path=Path("vellio"))

    text = describe_object(sample)
    titles = _section_titles(text)

    # Summary must come before Warnings; both must exist for every sample.
    assert "Summary" in titles
    assert "Warnings" in titles
    assert titles.index("Summary") < titles.index("Warnings")
    # Identifying kvs still emitted at the top for backwards compat with
    # ``_context_description``'s atlas-linkage post-processor and existing
    # tests that grep for ``Sample:`` / ``Path:``.
    assert "Sample: vellio" in text
    assert "Path:" in text


def test_tilt_series_description_uses_full_section_list() -> None:
    tilt = TiltSeries(
        id="t",
        name="vellio_1",
        mrc_path=Path("vellio_1.mrc"),
        mdoc_path=Path("vellio_1.mdoc"),
        tilt_count=41,
        sections=[MdocSection(z_value=index) for index in range(41)],
        metadata={"Detector": "Falcon 4i"},
    )

    titles = _section_titles(describe_object(tilt))

    assert "Summary" in titles
    assert "Files" in titles
    assert "Warnings" in titles
    # Acquisition is conditional on detected settings — the simple fixture
    # above does not provide enough metadata, so it's allowed to be
    # absent. Validation / Quality similarly depend on the validator.


def test_failed_tilt_quality_uses_validator_expected_count_not_actual_tilt_count(
    tmp_path: Path,
) -> None:
    mrc_path = tmp_path / "vellio_1.mrc"
    mrc_path.write_bytes(b"stub")
    tilt = TiltSeries(
        id="t",
        name="vellio_1",
        mrc_path=mrc_path,
        mdoc_path=tmp_path / "vellio_1.mdoc",
        tilt_count=1,
        sections=[MdocSection(z_value=0, metadata={"TiltAngle": -51.0})],
        mrc_metadata=MrcMetadata(
            path=mrc_path,
            size_bytes=4,
            nz=1,
            frame_metadata=[
                {
                    "raw_fields": {
                        "start_tilt_angle": -51.0,
                        "end_tilt_angle": 51.0,
                        "tilt_per_image": 3.0,
                    }
                }
            ],
        ),
    )

    text = describe_object(tilt)

    assert "Status: FAILED" in text
    assert "Tilt images: 1 / 35 expected" in text
    assert "Sections: 1 of 35 expected (partial)" in text
    assert "Sections: 1 (complete)" not in text


def test_context_panel_uses_same_session_majority_expected_count_as_list_row(
    tmp_path: Path,
) -> None:
    _app()
    failed_path = tmp_path / "vellio_1.mrc"
    failed_path.write_bytes(b"stub")
    failed = TiltSeries(
        id="failed",
        name="vellio_1",
        mrc_path=failed_path,
        tilt_count=1,
        sections=[MdocSection(z_value=0, metadata={"TiltAngle": -51.0})],
    )
    complete = TiltSeries(
        id="complete",
        name="vellio_2",
        mrc_path=tmp_path / "vellio_2.mrc",
        tilt_count=35,
        sections=[MdocSection(z_value=index) for index in range(35)],
    )
    validations = {
        result.tilt_series_id: result
        for result in validate_session_tilt_series([failed, complete])
    }
    window = MainWindow()
    window._viewer_tilt_validations = validations

    text = window._context_description(failed)
    window.close()

    assert "Tilt images: 1 / 35 expected" in text
    assert "Expected source: inferred session majority" in text
    assert "Sections: 1 of 35 expected (partial)" in text


def test_common_numeric_summary_orders_values_by_number() -> None:
    assert _common_text(["3 deg", "10 deg"]) == "3 deg to 10 deg"
    assert _common_text(["6.78 A", "10.4 A"]) == "6.78 A to 10.4 A"
    assert _common_text(["2", "10"]) == "2 to 10"
    assert _common_text(["0.5 s", "2 s"]) == "0.5 s to 2 s"


def test_successful_tilt_series_uses_validator_hard_failure_floor(tmp_path: Path) -> None:
    two_images = TiltSeries(
        id="two",
        name="two",
        mrc_path=tmp_path / "two.mrc",
        sections=[MdocSection(z_value=index) for index in range(2)],
    )
    five_images = TiltSeries(
        id="five",
        name="five",
        mrc_path=tmp_path / "five.mrc",
        sections=[MdocSection(z_value=index) for index in range(5)],
    )

    assert _is_successful_tilt_series(two_images) is False
    assert _is_successful_tilt_series(five_images) is True


def test_search_map_description_uses_summary_files_sections() -> None:
    search_map = SearchMap(id="sm", name="SearchMap_test")

    titles = _section_titles(describe_object(search_map))

    assert "Summary" in titles
    assert "Files" in titles
    assert "Warnings" in titles
    assert titles.index("Summary") < titles.index("Files")


def test_search_tile_description_uses_summary_files_sections() -> None:
    tile = SearchTile(id="st", name="tile_1")

    titles = _section_titles(describe_object(tile))

    assert "Summary" in titles
    assert "Files" in titles
    assert "Warnings" in titles


def test_batch_position_description_groups_acquisition_separately() -> None:
    batch = BatchPosition(
        id="b", name="batch", metadata={"Detector": "Falcon 4i"}
    )

    text = describe_object(batch)
    titles = _section_titles(text)

    assert "Summary" in titles
    # The Acquisition section is the one place the brief explicitly named
    # for the detector / spot size / probe mode triplet — keep it
    # separately headed even when values are unknown.
    assert "Acquisition" in titles
    assert "Files" in titles
    assert "Warnings" in titles


def test_warnings_section_is_always_emitted_even_when_empty() -> None:
    """The brief asks for a stable section list. ``Warnings`` must
    appear even with zero warnings (rendered as ``  None``) so the
    user always knows where to look."""

    sample = Sample(id="s", name="alpha", path=Path("alpha"))

    text = describe_object(sample)

    assert "\nWarnings:\n  None" in text


def test_mrc_metadata_lives_under_metadata_source_section() -> None:
    """MRC parser fields (dimensions / extended header / parsed frames)
    used to render inline; the sectioned panel groups them under
    ``Metadata source``."""

    tile = SearchTile(
        id="st",
        name="tile_with_mrc",
        mrc_metadata=MrcMetadata(path=Path("tile.mrc"), size_bytes=4096, nx=4096, ny=4096, nz=1),
    )

    titles = _section_titles(describe_object(tile))
    assert "Metadata source" in titles


def test_metadata_panel_renders_path_rows_without_acquisition_tooltip_state() -> None:
    _app()
    panel = MetadataPanel()

    panel.set_text("Summary:\n  Path: C:\\data\\Rothia\\Session.dm")

    path_labels = panel.findChildren(QLabel, "metaValuePath")
    assert len(path_labels) == 1
    assert path_labels[0].text_full() == "C:\\data\\Rothia\\Session.dm"


def test_acquisition_provenance_renders_as_tooltips_not_inline_text() -> None:
    _app()
    batch = BatchPosition(
        id="b",
        name="batch",
        metadata={
            "AcquisitionSettings": {
                "Detector": {
                    "value": "BioContinuum K3",
                    "source": "Rothia_1_Search.xml",
                    "field": "DetectorCommercialName",
                },
                SEARCH_SPOT_LABEL: {
                    "value": "8",
                    "source": "BatchPositionsList.xml",
                    "field": "SpotIndex",
                },
                ACQUISITION_SPOT_LABEL: {
                    "value": "6",
                    "source": "FEI",
                    "field": "spot_index",
                },
                LEGACY_SPOT_LABEL: {
                    "value": "6",
                    "source": "FEI",
                    "field": "spot_index",
                },
                "Probe mode": {
                    "value": "Nanoprobe",
                    "source": "Rothia_1_Search.xml",
                    "field": "ProbeMode",
                },
            }
        },
    )
    panel = MetadataPanel()
    panel.set_text(describe_object(batch))

    rows = _value_rows(panel)

    assert rows["Detector"][0] == (
        "BioContinuum K3",
        "Source: Rothia_1_Search.xml DetectorCommercialName",
    )
    assert rows[SEARCH_SPOT_LABEL][0] == (
        "8",
        "Source: BatchPositionsList.xml SpotIndex",
    )
    assert rows[ACQUISITION_SPOT_LABEL][0] == (
        "6",
        "Source: FEI spot_index",
    )
    assert rows["Probe mode"][0] == (
        "Nanoprobe",
        "Source: Rothia_1_Search.xml ProbeMode",
    )
    for key in ("Detector", SEARCH_SPOT_LABEL, ACQUISITION_SPOT_LABEL, "Probe mode"):
        displayed, tooltip = rows[key][0]
        assert "(" not in displayed
        assert ")" not in displayed
        assert tooltip.startswith("Source: ")
