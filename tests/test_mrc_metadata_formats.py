from __future__ import annotations

import struct
from pathlib import Path

import numpy as np
import pytest

from tomography_session_browser.domain.models import MdocSection, MrcMetadata, TiltSeries
from tomography_session_browser.parsers.mrc_metadata_parser import MRC_HEADER_BYTES, parse_mrc_metadata
from tomography_session_browser.parsers.mrc_parser import read_mrc_metadata
from tomography_session_browser.services.tilt_angle_service import stack_order_tilt_angles, tilt_angle_metadata_warnings


def test_old_fei1_float64_tilt_field_is_preferred_over_float32_decoy(tmp_path: Path) -> None:
    path = tmp_path / "old_fei1.mrc"
    angles = [-40.0, -20.0, 0.0, 20.0, 40.0]
    _write_mrc(path, angles, exttyp=b"FEI1", record_size=768, angle_dtype="float64", angle_offset=100)

    metadata = parse_mrc_metadata(path)

    parsed = [frame.tilt_angle for frame in metadata.frame_metadata]
    assert parsed == pytest.approx(angles)
    assert metadata.parser_name == "FEI/Thermo Fisher extended header"
    assert metadata.parser_version == "0.3"
    assert metadata.frame_metadata[0].raw_fields["tilt_angle_offset"] == 100
    assert metadata.frame_metadata[0].raw_fields["tilt_angle_dtype"] == "float64"


def test_newer_fei2_metadata_size_header_is_detected(tmp_path: Path) -> None:
    path = tmp_path / "new_fei2.mrc"
    angles = [-2.0, 0.0, 2.0]
    _write_fei2_mrc(path, angles, metadata_size=512)

    metadata = parse_mrc_metadata(path)

    parsed = [frame.tilt_angle for frame in metadata.frame_metadata]
    assert parsed == pytest.approx(angles)
    assert metadata.parser_name == "FEI/Thermo Fisher extended header"
    assert metadata.frame_metadata[0].raw_fields["metadata_size"] == 512
    assert metadata.frame_metadata[0].raw_fields["alpha_tilt"] == pytest.approx(-2.0)


def test_fei2_documented_offsets_and_bitmasks_are_parsed(tmp_path: Path) -> None:
    path = tmp_path / "fei2_documented_offsets.mrc"
    _write_fei2_mrc(path, [-1.5, 1.5], metadata_size=784, documented_fields=True)

    metadata = parse_mrc_metadata(path)
    frame = metadata.frame_metadata[0]
    raw = frame.raw_fields

    assert frame.tilt_angle == pytest.approx(-1.5)
    assert frame.stage_x == pytest.approx(1.2e-6)
    assert frame.stage_y == pytest.approx(-2.3e-6)
    assert frame.stage_z == pytest.approx(3.4e-6)
    assert frame.pixel_size == pytest.approx(1.5)
    assert frame.magnification == pytest.approx(42000.0)
    assert frame.exposure_time == pytest.approx(2.5)
    assert frame.dose == pytest.approx(1.225e21)
    assert raw["high_tension"] == pytest.approx(300000.0)
    assert raw["dose_e_per_angstrom2"] == pytest.approx(12.25)
    assert raw["camera_name"] == "Falcon4"
    assert raw["binning_width"] == 2
    assert raw["binning_height"] == 2
    assert raw["slit_width"] == pytest.approx(20.0)
    assert raw["readout_area"] == {"left": 10, "top": 20, "right": 4106, "bottom": 4116}
    assert raw["alpha_tilt_min"] == pytest.approx(-60.0)
    assert raw["alpha_tilt_max"] == pytest.approx(60.0)
    assert raw["scan_rotation"] == pytest.approx(0.125)
    assert raw["diffraction_pattern_rotation"] == pytest.approx(0.25)


def test_fei2_extension_rotation_planned_tilt_and_detector_fields_are_parsed(tmp_path: Path) -> None:
    path = tmp_path / "fei2_extension_fields.mrc"
    _write_fei2_mrc(path, [-1.5, 1.5], metadata_size=864, documented_fields=True)

    metadata = parse_mrc_metadata(path)
    raw = metadata.frame_metadata[0].raw_fields

    assert raw["image_rotation"] == pytest.approx(0.375)
    assert raw["detector_commercial_name"] == "BioContinuum K3"
    assert raw["start_tilt_angle"] == pytest.approx(-60.0)
    assert raw["end_tilt_angle"] == pytest.approx(60.0)
    assert raw["tilt_per_image"] == pytest.approx(3.0)
    assert raw["beam_center_x"] == 1440
    assert raw["beam_center_y"] == 1023
    assert raw["field_presence"]["image_rotation"] == "bitmask4:4"
    assert raw["field_presence"]["spot_index"] == "bitmask2:1"


def test_fei1_bitmask4_does_not_authorise_fei2_extension_fields(tmp_path: Path) -> None:
    path = tmp_path / "fei1_fixed_768_record.mrc"
    _write_fei1_bitmask4_mrc(path)

    metadata = parse_mrc_metadata(path)
    raw = metadata.frame_metadata[0].raw_fields

    assert raw["alpha_tilt_min"] == pytest.approx(-40.0)
    assert raw["alpha_tilt_max"] == pytest.approx(40.0)
    assert raw["field_presence"]["alpha_tilt_min"] == "bitmask4:0"
    assert raw["field_presence"]["alpha_tilt_max"] == "bitmask4:1"
    assert "detector_commercial_name" not in raw
    assert "detector_commercial_name" not in raw["field_presence"]
    assert raw["field_presence_ignored"]["detector_commercial_name"] == (
        "bitmask4:7 beyond 768-byte FEI record"
    )


def test_fei2_absent_bitmask_fields_are_not_reported(tmp_path: Path) -> None:
    path = tmp_path / "fei2_absent_fields.mrc"
    _write_fei2_mrc(path, [0.0, 2.0], metadata_size=512)

    metadata = parse_mrc_metadata(path)
    raw = metadata.frame_metadata[0].raw_fields

    assert raw["alpha_tilt"] == pytest.approx(0.0)
    assert "slit_width" not in raw
    assert "camera_name" not in raw
    assert "readout_area" not in raw
    assert "image_rotation" not in raw
    assert "detector_commercial_name" not in raw


def test_mrc_preview_preserves_top_left_pixel_origin(tmp_path: Path) -> None:
    from tomography_session_browser.parsers.mrc_parser import MrcPreviewSource

    path = tmp_path / "asymmetric_origin.mrc"
    header = bytearray(MRC_HEADER_BYTES)
    nx, ny, nz, mode = 3, 2, 1, 2
    struct.pack_into("<4i", header, 0, nx, ny, nz, mode)
    struct.pack_into("<3f", header, 40, float(nx), float(ny), float(nz))
    struct.pack_into("<3i", header, 64, 1, 2, 3)
    data = np.array([[1000.0, 0.0, 0.0], [0.0, 0.0, 0.0]], dtype="<f4")
    path.write_bytes(bytes(header) + data.tobytes(order="C"))

    preview = MrcPreviewSource(path).get_frame_preview(0, max_size=16)
    pixels = np.asarray(preview.image)

    assert pixels[0, 0] == pixels.max()
    assert pixels[-1, 0] < pixels[0, 0]


def test_mrc_preview_downsampling_preserves_small_features() -> None:
    from tomography_session_browser.parsers.mrc_parser import _frame_to_preview

    frame = np.zeros((4, 4), dtype=np.float32)
    frame[1, 1] = 1000.0

    preview, shrink = _frame_to_preview(frame, max_size=2)

    assert shrink == 2
    assert preview.shape == (2, 2)
    assert preview[0, 0] == preview.max()
    assert preview[0, 0] > 0


def test_extended_header_can_report_positive_to_negative_stack_order(tmp_path: Path) -> None:
    path = tmp_path / "reverse_stack.mrc"
    angles = [40.0, 20.0, 0.0, -20.0, -40.0]
    _write_mrc(path, angles, exttyp=b"FEI1", record_size=768, angle_dtype="float64", angle_offset=100)

    metadata = parse_mrc_metadata(path)

    parsed = [frame.tilt_angle for frame in metadata.frame_metadata]
    assert parsed == pytest.approx(angles)


def test_tilt_angle_service_keeps_valid_mrc_stack_order_over_mdoc() -> None:
    tilt = _tilt_series(
        mrc_angles=[-4.0, -2.0, 0.0, 2.0, 4.0],
        mdoc_angles=[0.0, 2.0, -2.0, 4.0, -4.0],
    )

    angles, source = stack_order_tilt_angles(tilt, 5)

    assert source == "MRC extended header"
    assert angles == pytest.approx([-4.0, -2.0, 0.0, 2.0, 4.0])


def test_positive_to_negative_mrc_stack_order_is_not_remapped_by_mdoc() -> None:
    tilt = _tilt_series(
        mrc_angles=[4.0, 2.0, 0.0, -2.0, -4.0],
        mdoc_angles=[0.0, 2.0, -2.0, 4.0, -4.0],
    )

    angles, source = stack_order_tilt_angles(tilt, 5)

    assert source == "MRC extended header"
    assert angles == pytest.approx([4.0, 2.0, 0.0, -2.0, -4.0])


def test_tilt_angle_service_uses_mdoc_only_when_mrc_is_unavailable() -> None:
    tilt = _tilt_series(
        mrc_angles=[],
        mdoc_angles=[0.0, 2.0, -2.0, 4.0, -4.0],
    )

    angles, source = stack_order_tilt_angles(tilt, 5)

    assert source == "MDOC fallback"
    assert angles == pytest.approx([0.0, 2.0, -2.0, 4.0, -4.0])


def test_tomography5_mdoc_fallback_sorts_stack_angles_like_scipion() -> None:
    tilt = _tilt_series(
        mrc_angles=[],
        mdoc_angles=[0.0, 3.0, -3.0, -6.0, 6.0],
        mdoc_header={
            "ImageFile": "tilt.mrc",
            "_sections": ["[T = Tomography_5.23.0.10273: TITAN 01-Jan-26  00:00:00]"],
        },
    )

    angles, source = stack_order_tilt_angles(tilt, 5)

    assert source == "MDOC fallback"
    assert angles == pytest.approx([-6.0, -3.0, 0.0, 3.0, 6.0])


def test_mdoc_fallback_uses_zvalue_stack_mapping_not_section_file_order() -> None:
    tilt = _tilt_series(
        mrc_angles=[],
        mdoc_angles=[-2.0, 0.0, 2.0],
        mdoc_z_values=[2, 0, 1],
    )

    angles, source = stack_order_tilt_angles(tilt, 3)

    assert source == "MDOC fallback"
    assert angles == pytest.approx([0.0, 2.0, -2.0])


def test_mrc_mdoc_conflict_warns_but_keeps_mrc_stack_order() -> None:
    tilt = _tilt_series(
        mrc_angles=[-4.0, -2.0, 0.0, 2.0, 4.0],
        mdoc_angles=[0.0, 2.0, -2.0, 4.0, -4.0],
    )

    angles, source = stack_order_tilt_angles(tilt, 5)
    warnings = tilt_angle_metadata_warnings(tilt, 5)

    assert source == "MRC extended header"
    assert angles == pytest.approx([-4.0, -2.0, 0.0, 2.0, 4.0])
    assert any("recorded in a different order" in warning for warning in warnings)


def test_tomography5_mrc_mdoc_order_difference_is_harmonized_without_conflict() -> None:
    tilt = _tilt_series(
        mrc_angles=[-6.0, -3.0, 0.0, 3.0, 6.0],
        mdoc_angles=[0.0, 3.0, -3.0, -6.0, 6.0],
        mdoc_header={
            "ImageFile": "tilt.mrc",
            "_sections": ["[T = Tomography: TITAN 01-Jan-26  00:00:00]"],
        },
    )

    angles, source = stack_order_tilt_angles(tilt, 5)
    warnings = tilt_angle_metadata_warnings(tilt, 5)

    assert source == "MRC extended header"
    assert angles == pytest.approx([-6.0, -3.0, 0.0, 3.0, 6.0])
    assert not any("MRC extended header and MDOC disagree" in warning for warning in warnings)


def test_mrc_count_mismatch_is_ignored_with_mdoc_fallback_warning() -> None:
    tilt = _tilt_series(
        mrc_angles=[-2.0, 0.0],
        mdoc_angles=[-2.0, 0.0, 2.0],
        frame_count=3,
    )

    angles, source = stack_order_tilt_angles(tilt, 3)
    warnings = tilt_angle_metadata_warnings(tilt, 3)

    assert source == "MDOC fallback"
    assert angles == pytest.approx([-2.0, 0.0, 2.0])
    assert any("MRC extended-header tilt-angle count (2) does not match stack frame count (3)" in warning for warning in warnings)


def test_tlt_count_mismatch_warns_and_falls_back_to_frame_order(tmp_path: Path) -> None:
    mrc_path = tmp_path / "tilt.mrc"
    mrc_path.write_bytes(b"")
    mrc_path.with_suffix(".tlt").write_text("-2\n0\n", encoding="utf-8")
    tilt = TiltSeries(
        id="tilt",
        name="tilt",
        mrc_path=mrc_path,
        mrc_metadata=MrcMetadata(path=mrc_path, size_bytes=0, nz=3),
        sections=[],
        tilt_range=(-2.0, 2.0),
    )

    assert stack_order_tilt_angles(tilt, 3) is None
    warnings = tilt_angle_metadata_warnings(tilt, 3)

    assert any("tilt-angle count (2) does not match stack frame count (3)" in warning for warning in warnings)
    assert any("displaying frames in stack order without angle labels" in warning for warning in warnings)


def test_inferred_tilt_range_is_not_used_as_default_angle_metadata() -> None:
    tilt = TiltSeries(
        id="tilt",
        name="tilt",
        mrc_path=Path("tilt.mrc"),
        mrc_metadata=MrcMetadata(path=Path("tilt.mrc"), size_bytes=0, nz=5),
        sections=[],
        tilt_range=(-4.0, 4.0),
    )

    assert stack_order_tilt_angles(tilt, 5) is None
    assert stack_order_tilt_angles(tilt, 5, allow_inferred=True) is not None


def test_truncated_mrc_reports_incomplete_data_warning(tmp_path: Path) -> None:
    path = tmp_path / "truncated.mrc"
    _write_mrc(path, [0.0, 2.0], exttyp=b"FEI1", record_size=128, angle_dtype="float32", angle_offset=16)

    metadata = read_mrc_metadata(path)

    assert any("MRC data block is incomplete" in warning for warning in metadata.warnings)


def test_mrc_stack_order_angles_do_not_sort_image_stack_order() -> None:
    tilt = _tilt_series(
        mrc_angles=[-3.0, -1.0, 1.0, 3.0],
        mdoc_angles=[],
        frame_count=4,
    )

    angles, source = stack_order_tilt_angles(tilt, 4)

    assert source == "MRC extended header"
    assert angles == pytest.approx([-3.0, -1.0, 1.0, 3.0])


def _write_mrc(
    path: Path,
    angles: list[float],
    *,
    exttyp: bytes,
    record_size: int,
    angle_dtype: str,
    angle_offset: int,
) -> None:
    header = bytearray(MRC_HEADER_BYTES)
    nx, ny, nz, mode = 4, 4, len(angles), 2
    struct.pack_into("<4i", header, 0, nx, ny, nz, mode)
    struct.pack_into("<3f", header, 40, float(nx), float(ny), float(nz))
    struct.pack_into("<3i", header, 64, 1, 2, 3)
    struct.pack_into("<i", header, 92, record_size * nz)
    header[104:108] = exttyp.ljust(4, b"\x00")[:4]

    extended = bytearray(record_size * nz)
    fmt = "<d" if angle_dtype == "float64" else "<f"
    for index, angle in enumerate(angles):
        struct.pack_into(fmt, extended, (index * record_size) + angle_offset, angle)
    path.write_bytes(bytes(header) + bytes(extended))


def _write_fei1_bitmask4_mrc(path: Path) -> None:
    header = bytearray(MRC_HEADER_BYTES)
    nx, ny, nz, mode = 4, 4, 1, 2
    struct.pack_into("<4i", header, 0, nx, ny, nz, mode)
    struct.pack_into("<3f", header, 40, float(nx), float(ny), float(nz))
    struct.pack_into("<3i", header, 64, 1, 2, 3)
    struct.pack_into("<i", header, 92, 768)
    header[104:108] = b"FEI1"

    extended = bytearray(768)
    struct.pack_into("<ii", extended, 0, 768, 0)
    struct.pack_into("<I", extended, 8, 1 << 7)
    struct.pack_into("<d", extended, 100, 0.0)
    struct.pack_into("<I", extended, 748, (1 << 0) | (1 << 1) | (1 << 7))
    struct.pack_into("<d", extended, 752, -40.0)
    struct.pack_into("<d", extended, 760, 40.0)
    path.write_bytes(bytes(header) + bytes(extended))


def _write_fei2_mrc(
    path: Path,
    angles: list[float],
    *,
    metadata_size: int,
    documented_fields: bool = False,
) -> None:
    header = bytearray(MRC_HEADER_BYTES)
    nx, ny, nz, mode = 4, 4, len(angles), 2
    struct.pack_into("<4i", header, 0, nx, ny, nz, mode)
    struct.pack_into("<3f", header, 40, float(nx), float(ny), float(nz))
    struct.pack_into("<3i", header, 64, 1, 2, 3)
    struct.pack_into("<i", header, 92, metadata_size * nz)
    header[104:108] = b"FEI2"

    extended = bytearray(metadata_size * nz)
    for index, angle in enumerate(angles):
        base = index * metadata_size
        struct.pack_into("<ii", extended, base, metadata_size, 1)
        if documented_fields:
            bitmask1 = (
                (1 << 1)
                | (1 << 3)
                | (1 << 4)
                | (1 << 5)
                | (1 << 6)
                | (1 << 7)
                | (1 << 8)
                | (1 << 9)
                | (1 << 10)
                | (1 << 11)
                | (1 << 12)
                | (1 << 14)
                | (1 << 15)
                | (1 << 22)
                | (1 << 24)
                | (1 << 29)
                | (1 << 31)
            )
            bitmask2 = (
                (1 << 1)
                | (1 << 2)
                | (1 << 7)
                | (1 << 8)
                | (1 << 14)
                | (1 << 15)
                | (1 << 16)
                | (1 << 17)
                | (1 << 18)
                | (1 << 19)
                | (1 << 20)
                | (1 << 21)
                | (1 << 22)
                | (1 << 23)
            )
            bitmask3 = (1 << 6) | (1 << 15) | (1 << 21) | (1 << 22) | (1 << 27) | (1 << 28) | (1 << 29) | (1 << 30)
            bitmask4 = (1 << 0) | (1 << 1) | (1 << 2) | (1 << 3)
            if metadata_size >= 864:
                for bit in range(4, 14):
                    bitmask4 |= 1 << bit
            struct.pack_into("<I", extended, base + 8, bitmask1)
            struct.pack_into("<I", extended, base + 297, bitmask2)
            struct.pack_into("<I", extended, base + 490, bitmask3)
            struct.pack_into("<I", extended, base + 748, bitmask4)
            _pack_ascii(extended, base + 20, 16, "Krios")
            _pack_ascii(extended, base + 52, 16, "Tomography")
            _pack_ascii(extended, base + 68, 16, "5.26")
            struct.pack_into("<d", extended, base + 84, 300000.0)
            struct.pack_into("<d", extended, base + 92, 1.225e21)
        struct.pack_into("<d", extended, base + 100, angle)
        struct.pack_into("<d", extended, base + 108, 0.25)
        if documented_fields:
            struct.pack_into("<d", extended, base + 116, 1.2e-6)
            struct.pack_into("<d", extended, base + 124, -2.3e-6)
            struct.pack_into("<d", extended, base + 132, 3.4e-6)
            struct.pack_into("<d", extended, base + 140, 12.0)
        struct.pack_into("<d", extended, base + 156, 1.5e-10 if documented_fields else 1.0e-10)
        if documented_fields:
            struct.pack_into("<d", extended, base + 164, 1.5e-10)
            struct.pack_into("<d", extended, base + 220, -2.0e-6)
            struct.pack_into("<d", extended, base + 236, -1.5e-6)
            struct.pack_into("<i", extended, base + 284, 1)
            struct.pack_into("<d", extended, base + 289, 42000.0)
            struct.pack_into("<i", extended, base + 309, 6)
            struct.pack_into("<d", extended, base + 313, 4.2e-6)
            extended[base + 354] = 1
            struct.pack_into("<d", extended, base + 355, 20.0)
            struct.pack_into("<d", extended, base + 403, 1.0e-7)
            struct.pack_into("<d", extended, base + 411, -1.0e-7)
            struct.pack_into("<d", extended, base + 419, 2.5)
            struct.pack_into("<i", extended, base + 427, 2)
            struct.pack_into("<i", extended, base + 431, 2)
            _pack_ascii(extended, base + 435, 16, "Falcon4")
            struct.pack_into("<4i", extended, base + 451, 10, 20, 4106, 4116)
            extended[base + 518] = 1
            struct.pack_into("<d", extended, base + 571, 1.0e-6)
            struct.pack_into("<d", extended, base + 603, 4.0e-6)
            struct.pack_into("<d", extended, base + 611, 5.0e-6)
            extended[base + 655] = 1
            struct.pack_into("<iii", extended, base + 656, index + 1, index, index + 3)
            struct.pack_into("<d", extended, base + 752, -60.0)
            struct.pack_into("<d", extended, base + 760, 60.0)
            struct.pack_into("<d", extended, base + 768, 0.125)
            struct.pack_into("<d", extended, base + 776, 0.25)
            if metadata_size >= 864:
                struct.pack_into("<d", extended, base + 784, 0.375)
                struct.pack_into("<i", extended, base + 792, 2)
                struct.pack_into("<q", extended, base + 796, 123456789)
                _pack_ascii(extended, base + 804, 16, "BioContinuum K3")
                struct.pack_into("<d", extended, base + 820, -60.0)
                struct.pack_into("<d", extended, base + 828, 60.0)
                struct.pack_into("<d", extended, base + 836, 3.0)
                struct.pack_into("<d", extended, base + 844, 1.5)
                struct.pack_into("<i", extended, base + 852, 1440)
                struct.pack_into("<i", extended, base + 856, 1023)
    path.write_bytes(bytes(header) + bytes(extended))


def _pack_ascii(buffer: bytearray, offset: int, size: int, text: str) -> None:
    encoded = text.encode("ascii")[:size]
    buffer[offset : offset + size] = encoded.ljust(size, b"\x00")


def _tilt_series(
    *,
    mrc_angles: list[float],
    mdoc_angles: list[float],
    mdoc_z_values: list[int] | None = None,
    mdoc_header: dict[str, object] | None = None,
    frame_count: int | None = None,
) -> TiltSeries:
    frame_count = frame_count if frame_count is not None else max(len(mrc_angles), len(mdoc_angles))
    if mdoc_z_values is None:
        mdoc_z_values = list(range(len(mdoc_angles)))
    return TiltSeries(
        id="tilt",
        name="tilt",
        mrc_path=Path("tilt.mrc"),
        mrc_metadata=MrcMetadata(
            path=Path("tilt.mrc"),
            size_bytes=0,
            nz=frame_count,
            tilt_angles=mrc_angles,
            tilt_angle_source="MRC extended header" if mrc_angles else None,
        ),
        sections=[
            MdocSection(z_value=z_value, metadata={"TiltAngle": angle})
            for z_value, angle in zip(mdoc_z_values, mdoc_angles)
        ],
        metadata=dict(mdoc_header or {}),
        tilt_range=(min(mdoc_angles or mrc_angles), max(mdoc_angles or mrc_angles)),
    )
