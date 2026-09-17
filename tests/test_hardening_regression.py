from __future__ import annotations

from pathlib import Path

from tomography_session_browser.domain.enums import SessionKind
from tomography_session_browser.domain.models import Atlas, MrcMetadata, Overview, Sample, SearchMap, Session
from tomography_session_browser.parsers.mdoc_parser import parse_mdoc
from tomography_session_browser.parsers.session_scanner import SessionScanner
from tomography_session_browser.parsers.tiltseries_parser import (
    find_unpaired_mrcs,
    parse_tilt_series,
    parse_tilt_series_in_folder,
)
from tomography_session_browser.parsers.xml_parser import parse_xml_file
from tomography_session_browser.reports.report_generator import _resolve_overlay_geometry
from tomography_session_browser.reports.warning_summary import summarise_warnings
from tomography_session_browser.ui.session_presenter import grouped_warnings
from tomography_session_browser.ui.session_presenter import session_warnings


def test_unknown_folder_loads_as_warning_instead_of_crashing(tmp_path: Path) -> None:
    empty_folder = tmp_path / "not_a_tomography_session"
    empty_folder.mkdir()

    session = SessionScanner().load(empty_folder)

    assert session.kind == SessionKind.UNKNOWN
    assert session.samples == []
    assert any("known Tomography 5 layout" in warning for warning in session.warnings)


def test_collection_with_orphan_mdoc_keeps_loading_and_warns(tmp_path: Path) -> None:
    (tmp_path / "orphan_stack.mdoc").write_text(
        "[ZValue = 0]\nTiltAngle = 0\n",
        encoding="utf-8",
    )

    session = SessionScanner().load(tmp_path)

    assert session.kind == SessionKind.COLLECTION
    assert len(session.samples) == 1
    assert session.samples[0].tilt_series == []
    assert any(
        "orphan_stack.mdoc has no matching MRC stack" in warning
        for warning in session.samples[0].warnings
    )


def test_collection_with_empty_mrc_keeps_loading_and_warns(tmp_path: Path) -> None:
    (tmp_path / "empty_stack.mrc").write_bytes(b"")

    session = SessionScanner().load(tmp_path)

    assert session.kind == SessionKind.COLLECTION
    assert len(session.samples) == 1
    assert [tilt.name for tilt in session.samples[0].tilt_series] == ["empty_stack"]
    tilt_warnings = session.samples[0].tilt_series[0].warnings
    assert any("MRC header is shorter" in warning for warning in tilt_warnings)
    assert any("Tilt-angle metadata unavailable" in warning for warning in tilt_warnings)


def test_serialem_mrc_mdoc_name_pairs_with_stack(tmp_path: Path) -> None:
    mrc_path = tmp_path / "stack.mrc"
    mdoc_path = tmp_path / "stack.mrc.mdoc"
    mrc_path.write_bytes(b"")
    mdoc_path.write_text("[ZValue = 0]\nTiltAngle = 0\n", encoding="utf-8")

    series = parse_tilt_series_in_folder(tmp_path)

    assert len(series) == 1
    assert series[0].mrc_path == mrc_path
    assert series[0].mdoc_path == mdoc_path
    assert find_unpaired_mrcs(tmp_path) == []


def test_missing_mdoc_binning_uses_cross_checked_mrc_pixel_size(
    tmp_path: Path,
    monkeypatch,
) -> None:
    mrc_path = tmp_path / "stack.mrc"
    mdoc_path = tmp_path / "stack.mdoc"
    mrc_path.write_bytes(b"")
    mdoc_path.write_text(
        "PixelSpacing = 3.39\n[ZValue = 0]\nTiltAngle = 0\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "tomography_session_browser.parsers.tiltseries_parser.read_mrc_metadata",
        lambda path: MrcMetadata(
            path=path,
            size_bytes=0,
            nz=1,
            voxel_size=(6.774, 6.774, 6.774),
            frame_metadata=[{"pixel_size": 6.774}],
        ),
    )

    tilt = parse_tilt_series(mrc_path, mdoc_path)

    assert tilt.original_pixel_size == 3.39
    assert tilt.binning is None
    assert tilt.pixel_size == 6.774
    assert any("MDOC Binning is missing" in warning for warning in tilt.warnings)


def test_mdoc_pixel_size_is_preserved_when_mrc_cross_check_agrees(
    tmp_path: Path,
    monkeypatch,
) -> None:
    mrc_path = tmp_path / "stack.mrc"
    mdoc_path = tmp_path / "stack.mdoc"
    mrc_path.write_bytes(b"")
    mdoc_path.write_text(
        "PixelSpacing = 3.39\nBinning = 2\n[ZValue = 0]\nTiltAngle = 0\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "tomography_session_browser.parsers.tiltseries_parser.read_mrc_metadata",
        lambda path: MrcMetadata(
            path=path,
            size_bytes=0,
            nz=1,
            voxel_size=(6.774, 6.774, 6.774),
            frame_metadata=[{"pixel_size": 6.774}],
        ),
    )

    tilt = parse_tilt_series(mrc_path, mdoc_path)

    assert tilt.pixel_size == 6.78
    assert not any("Pixel size conflict" in warning for warning in tilt.warnings)


def test_malformed_batch_positions_xml_warns_even_without_rows(tmp_path: Path) -> None:
    batch = tmp_path / "Batch"
    batch.mkdir()
    (batch / "BatchPositionsList.xml").write_text("<BatchPositions>", encoding="utf-8")

    session = SessionScanner().load(tmp_path)

    assert session.kind == SessionKind.COLLECTION
    assert session.samples[0].batch_positions == []
    assert any(
        "Could not parse acquisition metadata XML BatchPositionsList.xml" in warning
        for warning in session.samples[0].warnings
    )


def test_malformed_session_xml_is_visible_as_a_session_warning(tmp_path: Path) -> None:
    (tmp_path / "Session.dm").write_text("<Session>", encoding="utf-8")

    session = SessionScanner().load(tmp_path)

    assert any("Could not parse metadata XML Session.dm" in warning for warning in session_warnings(session))


def test_session_warning_collection_includes_root_atlas_and_overviews(tmp_path: Path) -> None:
    overview = Overview(
        id="overview",
        name="Overview 1",
        image_path=tmp_path / "overview.mrc",
        warnings=["Overview metadata could not be read."],
    )
    atlas = Atlas(id="atlas", warnings=["Atlas calibration is unavailable."])
    sample = Sample(id="sample", name="Sample 1", path=tmp_path, overviews=[overview])
    session = Session(id="session", name="Session", path=tmp_path, atlas=atlas, samples=[sample])

    warnings = session_warnings(session)

    assert any("Atlas calibration is unavailable" in warning for warning in warnings)
    assert any("Overview metadata could not be read" in warning for warning in warnings)


def test_malformed_search_map_xml_warns_on_search_map(tmp_path: Path) -> None:
    search_map_dir = tmp_path / "SearchMaps" / "SearchMap_001"
    search_map_dir.mkdir(parents=True)
    (search_map_dir / "SearchMap.xml").write_text("<SearchMap>", encoding="utf-8")
    (search_map_dir / "SearchMap.jpg").write_bytes(b"not really an image")

    session = SessionScanner().load(tmp_path)

    search_map = session.samples[0].search_maps[0]
    assert any(
        "Could not parse search-map metadata XML SearchMap.xml" in warning
        for warning in search_map.warnings
    )


def test_malformed_mdoc_key_value_line_warns_and_continues(tmp_path: Path) -> None:
    mdoc = tmp_path / "tilt.mdoc"
    mdoc.write_text("[ZValue = 0]\nTiltAngle = 0\nBroken metadata line\n", encoding="utf-8")

    _header, sections, warnings = parse_mdoc(mdoc)

    assert len(sections) == 1
    assert any("unrecognised MDOC line ignored" in warning for warning in warnings)


def test_non_finite_xml_numeric_is_omitted(tmp_path: Path) -> None:
    xml = tmp_path / "bad_numeric.xml"
    xml.write_text("<Root><pixelSize><x><numericValue>1e309</numericValue></x></pixelSize></Root>", encoding="utf-8")

    parsed = parse_xml_file(xml)

    assert parsed["pixelSize"]["x"]["numericValue"] is None


def test_report_overlay_geometry_warns_when_raster_size_is_unreadable(tmp_path: Path) -> None:
    corrupt = tmp_path / "SearchMap.jpg"
    corrupt.write_bytes(b"not a jpg")
    search_map = SearchMap(id="sm-1", name="SearchMap_001", image_path=corrupt)
    warnings: list[str] = []

    native, markers = _resolve_overlay_geometry(search_map, (), corrupt, warnings)

    assert native == (1000, 1000)
    assert markers == []
    assert any("Could not read embedded raster image size" in warning for warning in warnings)


def test_tilt_series_file_list_uses_natural_numeric_order(tmp_path: Path) -> None:
    for name in ("Lamella_green_1.mrc", "Lamella_green_10.mrc", "Lamella_green_3.mrc"):
        (tmp_path / name).write_bytes(b"")

    series = parse_tilt_series_in_folder(tmp_path)

    assert [tilt.name for tilt in series] == [
        "Lamella_green_1",
        "Lamella_green_3",
        "Lamella_green_10",
    ]


def test_tilt_angle_warnings_are_grouped_for_ui_and_reports() -> None:
    warnings = [
        "Tilt-angle conflict: MRC extended header and MDOC disagree; using MRC extended header.",
        "No valid per-frame tilt-angle metadata found; displaying frames in stack order without angle labels.",
    ]

    ui_groups = grouped_warnings(warnings)
    report_rows = summarise_warnings(warnings)

    # D2 migrated the dashboard onto the report's classifier. This test used to
    # accept two *different* names for the same finding — "Tilt-angle metadata
    # warnings" on screen, "Tilt-angle metadata fallback" in the PDF — which is
    # the divergence T1 and D2 exist to remove. Now they must agree.
    assert ui_groups["Tilt-angle metadata fallback"] == warnings
    assert any(row.category == "Tilt-angle metadata fallback" and row.count == 2 for row in report_rows)
    assert set(ui_groups) == {row.category for row in report_rows}


def test_the_dashboard_and_the_report_count_the_same_things() -> None:
    """Names agreed; the numbers did not.

    The dashboard counted raw warning strings while the report cover counted
    affected objects — on real data 2,805 against 85 for one finding, which
    made the two surfaces look like they were describing different sessions.
    """

    from tomography_session_browser.ui.session_presenter import (
        _dashboard_warning_groups,
    )

    # Three messages, two distinct affected objects.
    warnings = [
        "vellio_1: NaN values found in frame-dose metadata.",
        "vellio_1: NaN values found in frame-dose metadata.",
        "vellio_2: NaN values found in frame-dose metadata.",
    ]

    rows = _dashboard_warning_groups(warnings)
    report_rows = {row.category: row for row in summarise_warnings(warnings)}

    assert rows
    for row in rows:
        assert row.count == report_rows[row.label].affected_items
        # The raw strings are still available for the expanded view.
        assert len(row.items) == report_rows[row.label].count
        assert row.severity == report_rows[row.label].severity


def test_warning_summary_counts_long_scoped_names_as_one_affected_object() -> None:
    prefix = "Sample / " + "LongAcquisitionName" * 8
    rows = summarise_warnings(
        [
            f"{prefix}: NaN dose field A",
            f"{prefix}: NaN dose field B",
        ]
    )

    assert rows[0].affected_items == 1


def test_warning_classification_ignores_failed_text_in_object_name() -> None:
    rows = summarise_warnings(
        ["Sample / failed_control_1: Could not read MDOC file: permission denied"]
    )

    assert [(row.category, row.severity) for row in rows] == [
        ("Missing MDOC file", "warning")
    ]


def test_orphan_mdoc_warning_is_a_missing_mrc_error() -> None:
    rows = summarise_warnings(
        ["Sample / stack: orphan.mdoc has no matching MRC stack and was skipped."]
    )

    assert [(row.category, row.severity) for row in rows] == [
        ("Missing MRC file", "error")
    ]
