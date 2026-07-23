from __future__ import annotations

import os
from pathlib import Path

import pytest

from tomography_session_browser.domain.models import BatchPosition, MdocSection, MrcMetadata, Sample, TiltSeries
from tomography_session_browser.parsers import batch_parser
from tomography_session_browser.parsers.batch_parser import parse_batch_folder
from tomography_session_browser.parsers.xml_parser import find_first
from tomography_session_browser.reports.report_generator import _acquisition_settings_table
from tomography_session_browser.services.tilt_series_validation import TiltSeriesValidation
from tomography_session_browser.services.acquisition_metadata import (
    ACQUISITION_SPOT_LABEL,
    LEGACY_SPOT_LABEL,
    SEARCH_SPOT_LABEL,
    acquisition_setting_source,
    acquisition_setting_value,
    extract_acquisition_settings,
    format_target_defocus_values,
)
from tomography_session_browser.services.marker_service import _beam_radius_pixels, _mrc_pixel_size
from tomography_session_browser.ui.session_presenter import describe_object


REPO_ROOT = Path(__file__).resolve().parents[1]


def test_batch_parser_extracts_acquisition_settings_with_search_priority(tmp_path: Path) -> None:
    batch = tmp_path / "Batch"
    batch.mkdir()
    (batch / "BatchPositionsList.xml").write_text(
        """
        <Root>
          <BatchPositionParameters>
            <Name>vellio</Name>
            <DetectorType>LegacyCam</DetectorType>
            <SpotIndex>5</SpotIndex>
            <ProbeMode>nanoprobe</ProbeMode>
          </BatchPositionParameters>
          <BatchPositionParameters>
            <Name>Rothia</Name>
            <DetectorType>LegacyCam</DetectorType>
            <SpotIndex>5</SpotIndex>
            <ProbeMode>nanoprobe</ProbeMode>
          </BatchPositionParameters>
        </Root>
        """,
        encoding="utf-8",
    )
    (batch / "batchpositions.xml").write_text(
        """
        <Root>
          <SpotIndex>9</SpotIndex>
          <BatchPosition>
            <Name>Rothia</Name>
            <SpotIndex>7</SpotIndex>
          </BatchPosition>
        </Root>
        """,
        encoding="utf-8",
    )
    (batch / "search.xml").write_text(
        """
        <Search xmlns:a="http://schemas.microsoft.com/2003/10/Serialization/Arrays"
                xmlns:i="http://www.w3.org/2001/XMLSchema-instance">
          <Detector>EF-CCD</Detector>
          <a:KeyValueOfstringanyType>
            <a:Key>DetectorCommercialName</a:Key>
            <a:Value xmlns:b="http://www.w3.org/2001/XMLSchema" i:type="b:string">BioContinuum K3</a:Value>
          </a:KeyValueOfstringanyType>
          <a:KeyValueOfstringanyType>
            <a:Key>Detectors[EF-CCD].CommercialName</a:Key>
            <a:Value xmlns:b="http://www.w3.org/2001/XMLSchema" i:type="b:string">BioContinuum K3</a:Value>
          </a:KeyValueOfstringanyType>
          <a:KeyValueOfstringanyType>
            <a:Key>SearchSpotIndex</a:Key>
            <a:Value xmlns:b="http://www.w3.org/2001/XMLSchema" i:type="b:int">2</a:Value>
          </a:KeyValueOfstringanyType>
          <a:KeyValueOfstringanyType>
            <a:Key>TrackingSpotIndex</a:Key>
            <a:Value xmlns:b="http://www.w3.org/2001/XMLSchema" i:type="b:int">3</a:Value>
          </a:KeyValueOfstringanyType>
          <a:KeyValueOfstringanyType>
            <a:Key>AcquisitionSpotIndex</a:Key>
            <a:Value xmlns:b="http://www.w3.org/2001/XMLSchema" i:type="b:int">6</a:Value>
          </a:KeyValueOfstringanyType>
          <ProbeMode>MicroProbe</ProbeMode>
        </Search>
        """,
        encoding="utf-8",
    )

    positions = parse_batch_folder(batch)
    by_name = {position.name: position for position in positions}

    assert acquisition_setting_value(by_name["vellio"].metadata, "Detector") == "BioContinuum K3"
    assert acquisition_setting_value(by_name["vellio"].metadata, SEARCH_SPOT_LABEL) == "2"
    assert acquisition_setting_value(by_name["vellio"].metadata, ACQUISITION_SPOT_LABEL) == "6"
    assert acquisition_setting_value(by_name["vellio"].metadata, LEGACY_SPOT_LABEL) == "6"
    assert acquisition_setting_value(by_name["vellio"].metadata, "Probe mode") == "Microprobe"
    assert acquisition_setting_source(by_name["vellio"].metadata, "Detector") == "search.xml DetectorCommercialName"
    assert acquisition_setting_value(by_name["Rothia"].metadata, ACQUISITION_SPOT_LABEL) == "6"


def test_batch_parser_ignores_nested_decoy_batch_position_parameters(tmp_path: Path) -> None:
    batch = tmp_path / "Batch"
    batch.mkdir()
    (batch / "BatchPositionsList.xml").write_text(
        """
        <Root>
          <Template>
            <BatchPositionParameters>
              <Name>Decoy</Name>
              <SpotIndex>3</SpotIndex>
            </BatchPositionParameters>
          </Template>
          <BatchPositionParameters>
            <Name>Real</Name>
            <SpotIndex>8</SpotIndex>
          </BatchPositionParameters>
        </Root>
        """,
        encoding="utf-8",
    )

    positions = parse_batch_folder(batch)

    assert [position.name for position in positions] == ["Real"]
    assert acquisition_setting_value(positions[0].metadata, SEARCH_SPOT_LABEL) == "8"


def test_scoped_xml_spot_indices_keep_search_and_acquisition_contexts_separate() -> None:
    settings = extract_acquisition_settings(
        {
            "CustomData": {
                "KeyValueOfstringanyType": {
                    "Key": "BackwardAlignmentTransformation",
                    "Value": {"Optics": {"SpotIndex": 8}},
                }
            },
            "microscopeData": {"optics": {"SpotIndex": 6}},
        },
        source="search.xml",
    )

    assert settings[SEARCH_SPOT_LABEL].value == "8"
    assert settings[ACQUISITION_SPOT_LABEL].value == "6"
    assert settings[LEGACY_SPOT_LABEL].value == "6"


def test_batch_parser_prefers_probe_mode_over_illumination_mode_and_fills_beam_diameter(tmp_path: Path) -> None:
    batch = tmp_path / "Batch"
    batch.mkdir()
    (batch / "BatchPositionsList.xml").write_text(
        """
        <Root>
          <BatchPositionParameters>
            <Name>Lamella_green_1</Name>
          </BatchPositionParameters>
        </Root>
        """,
        encoding="utf-8",
    )
    (batch / "search.xml").write_text(
        """
        <MicroscopeImage>
          <microscopeData>
            <optics>
              <BeamDiameter>1.1347672289078868E-05</BeamDiameter>
              <IlluminationMode>Parallel</IlluminationMode>
              <ProbeMode>NanoProbe</ProbeMode>
            </optics>
          </microscopeData>
        </MicroscopeImage>
        """,
        encoding="utf-8",
    )

    positions = parse_batch_folder(batch)
    metadata = positions[0].metadata

    assert find_first(metadata, "BeamDiameter") == pytest.approx(1.1347672289078868e-05)
    assert metadata["BeamDiameterSource"] == "search.xml microscopeData/optics/BeamDiameter"
    assert metadata["BeamDiameterContext"] == "microscope_data_optics"
    assert _beam_radius_pixels(positions[0], 2.731344928008639e-09) == pytest.approx(415.46097575278407)
    assert acquisition_setting_value(metadata, "Probe mode") == "Nanoprobe"
    assert acquisition_setting_source(metadata, "Probe mode") == "search.xml ProbeMode"


@pytest.mark.parametrize(
    ("probe_code", "expected"),
    [
        (1, "Nanoprobe"),
        (2, "Microprobe"),
        (7, "Unknown (FEI code 7)"),
    ],
)
def test_exposure_mrc_probe_mode_decodes_and_overrides_search_xml(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    probe_code: int,
    expected: str,
) -> None:
    batch = tmp_path / "Batch"
    batch.mkdir()
    (batch / "BatchPositionsList.xml").write_text(
        """
        <Root>
          <BatchPositionParameters>
            <Name>Position_1</Name>
          </BatchPositionParameters>
        </Root>
        """,
        encoding="utf-8",
    )
    (batch / "search.xml").write_text(
        "<MicroscopeImage><ProbeMode>MicroProbe</ProbeMode></MicroscopeImage>",
        encoding="utf-8",
    )
    exposure_path = batch / "Position_1_Exposure.mrc"
    exposure_path.write_bytes(b"")

    monkeypatch.setattr(
        batch_parser,
        "read_mrc_metadata",
        lambda path: MrcMetadata(
            path=path,
            size_bytes=0,
            frame_metadata=[{"raw_fields": {"probe_mode": probe_code}}],
        ),
    )

    position = parse_batch_folder(batch)[0]

    assert acquisition_setting_value(position.metadata, "Probe mode") == expected
    assert acquisition_setting_source(position.metadata, "Probe mode") == (
        "Position_1_Exposure.mrc FEI extended header probe_mode"
    )
    if expected != "Microprobe":
        assert any(
            "Probe mode conflict:" in warning
            and f"probe_mode={expected}" in warning
            and "overrides search.xml ProbeMode=Microprobe" in warning
            for warning in position.warnings
        )


def test_inconsistent_exposure_mrc_probe_modes_keep_xml_fallback_and_warn(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    batch = tmp_path / "Batch"
    batch.mkdir()
    (batch / "BatchPositionsList.xml").write_text(
        """
        <Root>
          <BatchPositionParameters>
            <Name>Position_1</Name>
          </BatchPositionParameters>
        </Root>
        """,
        encoding="utf-8",
    )
    (batch / "search.xml").write_text(
        "<MicroscopeImage><ProbeMode>MicroProbe</ProbeMode></MicroscopeImage>",
        encoding="utf-8",
    )
    exposure_path = batch / "Position_1_Exposure.mrc"
    exposure_path.write_bytes(b"")

    monkeypatch.setattr(
        batch_parser,
        "read_mrc_metadata",
        lambda path: MrcMetadata(
            path=path,
            size_bytes=0,
            frame_metadata=[
                {"raw_fields": {"probe_mode": 1}},
                {"raw_fields": {"probe_mode": 2}},
            ],
        ),
    )

    position = parse_batch_folder(batch)[0]

    assert acquisition_setting_value(position.metadata, "Probe mode") == "Microprobe"
    assert acquisition_setting_source(position.metadata, "Probe mode") == "search.xml ProbeMode"
    assert any(
        "exposure MRC FEI extended header has inconsistent probe_mode values: 1, 2."
        in warning
        for warning in position.warnings
    )


def test_reference_collection_a_exposure_mrc_supplies_tilt_series_beam_diameter() -> None:
    """Lamella tilt-series beam diameter comes from the Exposure MRC FEI header."""

    dataset = Path(os.environ.get("TOMOAPP_REFERENCE_COLLECTION_A", REPO_ROOT / "reference_collection_a"))
    if not (dataset / "Batch").exists():
        pytest.skip("Reference collection A is not available; set TOMOAPP_REFERENCE_COLLECTION_A")

    positions = parse_batch_folder(dataset / "Batch")
    by_name = {position.name: position for position in positions}
    position = by_name["Lamella_green_1"]
    metadata = position.metadata
    pixel_size = _mrc_pixel_size(position.search_mrc_metadata)

    # TFS FEI extended header records the exposure-state beam diameter
    # (in meters) per frame. This is the actual beam used to acquire each
    # tilt-series frame at this batch position.
    assert find_first(metadata, "BeamDiameter") == pytest.approx(2.99102308658651e-06)
    assert metadata["BeamDiameterSource"].startswith("Lamella_green_1")
    assert metadata["BeamDiameterSource"].endswith(
        "_Exposure.mrc FEI extended header illuminated_area"
    )
    assert metadata["BeamDiameterContext"] == "exposure_mrc_illuminated_area"
    # diameter / 2 / search-image pixel size — no empirical scale required.
    expected_radius_px = (2.99102308658651e-06 / 2) / pixel_size
    assert _beam_radius_pixels(position, pixel_size) == pytest.approx(expected_radius_px, rel=1e-6)
    assert acquisition_setting_value(metadata, "Probe mode") == "Nanoprobe"
    assert acquisition_setting_value(metadata, SEARCH_SPOT_LABEL) == "6"
    assert acquisition_setting_value(metadata, ACQUISITION_SPOT_LABEL) == "7"
    assert acquisition_setting_value(metadata, LEGACY_SPOT_LABEL) == "7"


def test_reference_collection_b_exposure_mrc_supplies_tilt_series_beam_diameter() -> None:
    """Reference tilt-series beam diameter also comes from the Exposure MRC FEI header."""

    dataset = Path(os.environ.get("TOMOAPP_REFERENCE_COLLECTION_B", REPO_ROOT / "reference_collection_b"))
    sample = dataset / "Sample1" if (dataset / "Sample1" / "Batch").exists() else dataset
    if not (sample / "Batch").exists():
        pytest.skip("Reference collection B is not available; set TOMOAPP_REFERENCE_COLLECTION_B")

    positions = parse_batch_folder(sample / "Batch")
    by_name = {position.name: position for position in positions}
    position = by_name["vellio_2"]
    metadata = position.metadata
    pixel_size = _mrc_pixel_size(position.search_mrc_metadata)

    assert find_first(metadata, "BeamDiameter") == pytest.approx(4.2e-06)
    assert metadata["BeamDiameterSource"].startswith("vellio_2")
    assert metadata["BeamDiameterSource"].endswith(
        "_Exposure.mrc FEI extended header illuminated_area"
    )
    assert metadata["BeamDiameterContext"] == "exposure_mrc_illuminated_area"
    expected_radius_px = (4.2e-06 / 2) / pixel_size
    assert _beam_radius_pixels(position, pixel_size) == pytest.approx(expected_radius_px, rel=1e-6)
    # The Exposure MRC is the acquisition-time source; FEI probe_mode code 2
    # is MicroProbe according to the Tomography 5.26 MRC header definition.
    assert acquisition_setting_value(metadata, "Probe mode") == "Microprobe"
    assert acquisition_setting_source(metadata, "Probe mode").endswith(
        "_Exposure.mrc FEI extended header probe_mode"
    )
    assert acquisition_setting_value(metadata, SEARCH_SPOT_LABEL) == "8"
    assert acquisition_setting_value(metadata, ACQUISITION_SPOT_LABEL) == "6"
    assert acquisition_setting_value(metadata, LEGACY_SPOT_LABEL) == "6"


def test_reference_collection_c_fei_spot_values_fill_missing_search_and_acquisition_settings() -> None:
    dataset_env = os.environ.get("TOMOAPP_REFERENCE_COLLECTION_C")
    if not dataset_env:
        pytest.skip("Reference collection C is not available; set TOMOAPP_REFERENCE_COLLECTION_C")
    dataset = Path(dataset_env)
    if not (dataset / "Batch").exists():
        pytest.skip("Reference collection C is not available; set TOMOAPP_REFERENCE_COLLECTION_C")

    positions = parse_batch_folder(dataset / "Batch")
    assert positions
    metadata = positions[0].metadata

    assert acquisition_setting_value(metadata, SEARCH_SPOT_LABEL) == "7"
    assert acquisition_setting_value(metadata, ACQUISITION_SPOT_LABEL) == "5"
    assert acquisition_setting_value(metadata, LEGACY_SPOT_LABEL) == "5"


def test_context_panels_show_acquisition_settings_and_summaries() -> None:
    batch = BatchPosition(
        id="bp-1",
        name="Position 1",
        metadata={
            "AcquisitionSettings": {
                "Detector": {"value": "Falcon 4i", "source": "search.xml", "field": "Detector"},
                SEARCH_SPOT_LABEL: {"value": "8", "source": "BatchPositionsList.xml", "field": "SpotIndex"},
                ACQUISITION_SPOT_LABEL: {"value": "6", "source": "FEI", "field": "spot_index"},
                LEGACY_SPOT_LABEL: {"value": "6", "source": "FEI", "field": "spot_index"},
                "Probe mode": {"value": "Nanoprobe", "source": "search.xml", "field": "ProbeMode"},
            }
        },
    )
    tilt = TiltSeries(
        id="tilt-1",
        name="vellio_2_3",
        mrc_path=Path("vellio_2_3.mrc"),
        metadata=batch.metadata,
    )
    sample = Sample(
        id="sample-1",
        name="vellio",
        path=Path("vellio"),
        batch_positions=[batch],
        tilt_series=[tilt],
    )

    batch_text = describe_object(batch)
    assert "Detector: Falcon 4i (search.xml Detector)" in batch_text
    assert "Search spot size: 8 (BatchPositionsList.xml SpotIndex)" in batch_text
    assert "Acquisition spot size: 6 (FEI spot_index)" in batch_text

    tilt_text = describe_object(tilt)
    # The context panel now renders sectioned headings ("Acquisition:")
    # instead of the previous wordier "Acquisition settings:" — matches
    # the section list called out in the UI/UX brief.
    assert "Acquisition:" in tilt_text
    assert "Probe mode: Nanoprobe" in tilt_text

    sample_text = describe_object(sample)
    assert "Detector: Falcon 4i" in sample_text
    assert "Search spot size: 8" in sample_text
    assert "Acquisition spot size: 6" in sample_text


def test_detector_commercial_name_falls_back_to_detector_collection_field() -> None:
    settings = extract_acquisition_settings(
        {
            "KeyValueOfstringanyType": [
                {"Key": "Detector", "Value": "EF-CCD"},
                {"Key": "Detectors[EF-CCD].CommercialName", "Value": "BioContinuum K3"},
            ]
        },
        source="search.xml",
    )

    detector = settings["Detector"]
    assert detector.value == "BioContinuum K3"
    assert detector.field == "Detectors[EF-CCD].CommercialName"
    assert detector.internal_key == "EF-CCD"


def test_detector_prefers_primary_commercial_name_and_preserves_conflict() -> None:
    from tomography_session_browser.services.acquisition_metadata import apply_acquisition_settings, extract_acquisition_settings

    settings = extract_acquisition_settings(
        {
            "KeyValueOfstringanyType": [
                {"Key": "DetectorCommercialName", "Value": "BioContinuum K3"},
                {"Key": "Detectors[EF-CCD].CommercialName", "Value": "Different display name"},
                {"Key": "Detector", "Value": "EF-CCD"},
            ]
        },
        source="search.xml",
    )
    metadata: dict[str, object] = {}
    apply_acquisition_settings(metadata, settings, overwrite=True)
    detector = metadata["AcquisitionSettings"]["Detector"]  # type: ignore[index]

    assert detector["value"] == "BioContinuum K3"
    assert detector["alternatives"][0]["value"] == "Different display name"
    assert detector["alternatives"][1]["value"] == "EF-CCD"


def test_detector_internal_key_is_last_resort() -> None:
    from tomography_session_browser.services.acquisition_metadata import extract_acquisition_settings

    settings = extract_acquisition_settings(
        {"KeyValueOfstringanyType": {"Key": "Detector", "Value": "EF-CCD"}},
        source="search.xml",
    )

    assert settings["Detector"].value == "EF-CCD"


def test_non_acquisition_spot_index_values_are_not_reported_as_acquisition() -> None:
    settings = extract_acquisition_settings(
        {
            "KeyValueOfstringanyType": [
                {"Key": "SearchSpotIndex", "Value": "2"},
                {"Key": "TrackingSpotIndex", "Value": "3"},
                {"Key": "FocusSpotIndex", "Value": "4"},
            ]
        },
        source="search.xml",
    )

    assert settings[SEARCH_SPOT_LABEL].value == "2"
    assert ACQUISITION_SPOT_LABEL not in settings
    assert LEGACY_SPOT_LABEL not in settings


def test_report_acquisition_table_uses_batch_settings_and_defocus_formatter() -> None:
    batch = BatchPosition(
        id="bp-1",
        name="Position 1",
        metadata={
            "AcquisitionSettings": {
                "Detector": {"value": "Falcon 4i", "source": "search.xml", "field": "Detector"},
                SEARCH_SPOT_LABEL: {"value": "8", "source": "BatchPositionsList.xml", "field": "SpotIndex"},
                ACQUISITION_SPOT_LABEL: {"value": "6", "source": "FEI", "field": "spot_index"},
                LEGACY_SPOT_LABEL: {"value": "6", "source": "FEI", "field": "spot_index"},
                "Probe mode": {"value": "Microprobe", "source": "search.xml", "field": "ProbeMode"},
            }
        },
    )
    tilt = TiltSeries(
        id="tilt-1",
        name="vellio_2_3",
        mrc_path=Path("vellio_2_3.mrc"),
        target_defocus=-8.000004,
    )

    table = _acquisition_settings_table([tilt], [batch])
    text = "\n".join(
        cell.getPlainText()
        for row in table._cellvalues
        for cell in row
        if hasattr(cell, "getPlainText")
    )

    assert "Detector" in text
    assert "Falcon 4i" in text
    assert SEARCH_SPOT_LABEL in text
    assert ACQUISITION_SPOT_LABEL in text
    assert "6" in text
    assert "Probe mode" in text
    assert "Microprobe" in text
    assert "-8 µm" in text
    assert "-8 µm – -8 µm" not in text


def test_report_spot_size_prefers_mdoc_over_ambiguous_batch_xml() -> None:
    batch = BatchPosition(
        id="bp-1",
        name="Position 1",
        metadata={
            "AcquisitionSettings": {
                SEARCH_SPOT_LABEL: {"value": "8", "source": "batchpositions.xml", "field": "SpotIndex"},
            }
        },
    )
    tilt = TiltSeries(
        id="tilt-1",
        name="vellio_2_3",
        mrc_path=Path("vellio_2_3.mrc"),
        metadata={
            "AcquisitionSettings": {
                ACQUISITION_SPOT_LABEL: {"value": "6", "source": "MDOC", "field": "SpotSize"},
                LEGACY_SPOT_LABEL: {"value": "6", "source": "MDOC", "field": "SpotSize"},
            }
        },
    )

    table = _acquisition_settings_table([tilt], [batch])
    rows = {
        row[0].getPlainText(): row[1].getPlainText()
        for row in table._cellvalues
        if hasattr(row[0], "getPlainText") and hasattr(row[1], "getPlainText")
    }

    assert rows[SEARCH_SPOT_LABEL] == "8"
    assert rows[ACQUISITION_SPOT_LABEL] == "6"


def test_target_defocus_formatter_groups_noise_and_lists_small_sets() -> None:
    assert format_target_defocus_values([-8, -8.000001], missing="unknown") == "-8 µm"
    assert format_target_defocus_values([-8, -6, -5], missing="unknown") == "-8 µm, -6 µm, -5 µm"
    assert format_target_defocus_values([-8, -7, -6, -5, -4], missing="unknown") == "-8 µm – -4 µm"
    assert format_target_defocus_values([], missing="unknown") == "unknown"


def test_report_expected_tilts_uses_validation_expected_count_not_observed_range() -> None:
    failed = TiltSeries(
        id="failed",
        name="vellio_1_1",
        mrc_path=Path("vellio_1_1.mrc"),
        tilt_count=1,
        sections=[MdocSection(z_value=0)],
    )
    complete = TiltSeries(
        id="complete",
        name="vellio_2_3",
        mrc_path=Path("vellio_2_3.mrc"),
        tilt_count=35,
        sections=[MdocSection(z_value=index) for index in range(35)],
    )
    validations = {
        "failed": TiltSeriesValidation(
            tilt_series_id="failed",
            name="vellio_1_1",
            status="failed",
            actual_count=1,
            expected_count=35,
            min_tilt=None,
            max_tilt=None,
            tilt_increment=None,
            reason="failed",
            evidence_source="inferred_session_majority",
        ),
        "complete": TiltSeriesValidation(
            tilt_series_id="complete",
            name="vellio_2_3",
            status="complete",
            actual_count=35,
            expected_count=35,
            min_tilt=None,
            max_tilt=None,
            tilt_increment=None,
            reason="complete",
            evidence_source="inferred_session_majority",
        ),
    }

    table = _acquisition_settings_table([failed, complete], [], validations)
    text = "\n".join(
        cell.getPlainText()
        for row in table._cellvalues
        for cell in row
        if hasattr(cell, "getPlainText")
    )

    assert "Expected images" in text
    assert "35" in text
    assert "1 – 35" not in text
    assert "Observed images" in text
    assert "1, 35" in text
