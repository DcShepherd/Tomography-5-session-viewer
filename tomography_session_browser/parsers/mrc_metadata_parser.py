from __future__ import annotations

from datetime import datetime, timedelta
from dataclasses import dataclass, field
import json
import logging
import math
from pathlib import Path
import struct
import time
from typing import Any

import numpy as np

from tomography_session_browser.services.loading_profiler import record_aggregate_phase


LOGGER = logging.getLogger(__name__)
MRC_HEADER_BYTES = 1024
PARSER_NAME = "tomography_session_browser.mrc_metadata"
PARSER_VERSION = "0.3"
OLE_DATE_EPOCH = datetime(1899, 12, 30)


@dataclass(slots=True)
class MrcMainHeaderInfo:
    path: str
    nx: int
    ny: int
    nz: int
    mode: int
    nsymbt: int
    exttyp: str | None
    mapc: int | None
    mapr: int | None
    maps: int | None
    voxel_size: tuple[float | None, float | None, float | None]
    is_stack: bool
    is_volume: bool
    frame_count: int
    dtype: str | None
    warnings: list[str] = field(default_factory=list)


@dataclass(slots=True)
class MrcFrameMetadata:
    raw_index: int
    tilt_angle: float | None = None
    stage_x: float | None = None
    stage_y: float | None = None
    stage_z: float | None = None
    stage_alpha: float | None = None
    stage_beta: float | None = None
    image_shift_x: float | None = None
    image_shift_y: float | None = None
    beam_shift_x: float | None = None
    beam_shift_y: float | None = None
    pixel_size: float | None = None
    magnification: float | None = None
    defocus: float | None = None
    exposure_time: float | None = None
    dose: float | None = None
    timestamp: str | None = None
    metadata_source: str | None = None
    raw_fields: dict[str, Any] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)


@dataclass(slots=True)
class ParsedMrcMetadata:
    main_header: MrcMainHeaderInfo
    frame_metadata: list[MrcFrameMetadata]
    extended_header_type: str | None
    extended_header_size: int
    parser_name: str
    parser_version: str
    warnings: list[str] = field(default_factory=list)


def parse_mrc_metadata(path: Path) -> ParsedMrcMetadata:
    header_started = time.perf_counter()
    warnings: list[str] = []
    with path.open("rb") as handle:
        header = handle.read(MRC_HEADER_BYTES)
    if len(header) < 16:
        record_aggregate_phase("parse_mrc_header", time.perf_counter() - header_started)
        raise ValueError("MRC header is shorter than 16 bytes.")
    nx, ny, nz, mode, endian = _unpack_header_shape_and_endian(header, warnings)
    if nx is None or ny is None or nz is None or mode is None:
        record_aggregate_phase("parse_mrc_header", time.perf_counter() - header_started)
        raise ValueError("; ".join(warnings) or "MRC header dimensions are not plausible.")
    dtype = _numpy_dtype(mode, endian)
    nsymbt = _int_at(header, 92, endian, warnings)
    exttyp = _extended_header_type(header)
    mapc, mapr, maps = _axis_mapping(header, endian)
    voxel_size = _voxel_size(header, endian, nx, ny, nz)
    frame_count = max(1, nz)
    main = MrcMainHeaderInfo(
        path=str(path),
        nx=nx,
        ny=ny,
        nz=nz,
        mode=mode,
        nsymbt=max(0, nsymbt or 0),
        exttyp=exttyp,
        mapc=mapc,
        mapr=mapr,
        maps=maps,
        voxel_size=voxel_size,
        is_stack=nz > 1,
        is_volume=False,
        frame_count=frame_count,
        dtype=str(dtype) if dtype is not None else None,
        warnings=[],
    )
    record_aggregate_phase("parse_mrc_header", time.perf_counter() - header_started)
    if dtype is None:
        warnings.append(f"MRC mode {mode} is not supported for pixel preview.")
    ext_started = time.perf_counter()
    frame_metadata = _parse_extended_header(path, main, endian, warnings)
    record_aggregate_phase("parse_mrc_extended_header", time.perf_counter() - ext_started)
    main.voxel_size = _resolved_voxel_size(main.voxel_size, frame_metadata)
    parsed = ParsedMrcMetadata(
        main_header=main,
        frame_metadata=frame_metadata,
        extended_header_type=exttyp,
        extended_header_size=main.nsymbt,
        parser_name=_parser_name_for_exttyp(exttyp, any(frame.tilt_angle is not None for frame in frame_metadata)),
        parser_version=PARSER_VERSION,
        warnings=warnings + main.warnings,
    )
    tilt_angles = [frame.tilt_angle for frame in frame_metadata if frame.tilt_angle is not None]
    LOGGER.debug(
        "MRC metadata parsed path=%s dims=%sx%sx%s mode=%s nsymbt=%s exttyp=%s parser=%s frames=%s "
        "metadata_blocks=%s tilt_angles=%s first_angles=%s warnings=%s",
        path,
        nx,
        ny,
        nz,
        mode,
        main.nsymbt,
        exttyp,
        _parser_name_for_exttyp(exttyp, bool(tilt_angles)),
        frame_count,
        len(frame_metadata),
        len(tilt_angles),
        tilt_angles[:5],
        parsed.warnings,
    )
    return parsed


def format_mrc_metadata_report(metadata_or_path: ParsedMrcMetadata | Path | str, frame_index: int | None = None, all_frames: bool = False) -> str:
    metadata = parse_mrc_metadata(Path(metadata_or_path)) if isinstance(metadata_or_path, str | Path) else metadata_or_path
    main = metadata.main_header
    tilt_angles = [frame.tilt_angle for frame in metadata.frame_metadata if frame.tilt_angle is not None]
    lines = [
        f"MRC metadata: {main.path}",
        f"Dimensions: nx={main.nx}, ny={main.ny}, nz={main.nz}",
        f"Mode/dtype: {main.mode} / {main.dtype}",
        f"Map axes: mapc={main.mapc}, mapr={main.mapr}, maps={main.maps}",
        f"Frames: {main.frame_count}",
        f"Extended header: {metadata.extended_header_type or 'none'} ({metadata.extended_header_size} bytes)",
        f"Frame metadata records: {len(metadata.frame_metadata)}",
        f"Tilt angles found: {len(tilt_angles)}",
        f"First tilt angles: {_format_angle_list(tilt_angles[:8])}",
        f"Voxel size: {main.voxel_size}",
        f"Parser: {metadata.parser_name} {metadata.parser_version}",
    ]
    if metadata.warnings:
        lines.append("Warnings: " + "; ".join(metadata.warnings))
    frames = metadata.frame_metadata if all_frames else []
    if frame_index is not None:
        frames = [metadata.frame_metadata[frame_index]] if 0 <= frame_index < len(metadata.frame_metadata) else []
    for frame in frames:
        fields = {
            "tilt_angle": frame.tilt_angle,
            "stage_alpha": frame.stage_alpha,
            "stage_beta": frame.stage_beta,
            "stage": (frame.stage_x, frame.stage_y, frame.stage_z),
            "image_shift": (frame.image_shift_x, frame.image_shift_y),
            "beam_shift": (frame.beam_shift_x, frame.beam_shift_y),
            "pixel_size": frame.pixel_size,
            "magnification": frame.magnification,
            "defocus": frame.defocus,
            "exposure_time": frame.exposure_time,
            "dose": frame.dose,
            "timestamp": frame.timestamp,
            "source": frame.metadata_source,
            "raw_fields": frame.raw_fields,
        }
        lines.append(f"Frame {frame.raw_index}: " + json.dumps(fields, default=str))
    return "\n".join(lines)


def _parse_extended_header(path: Path, main: MrcMainHeaderInfo, endian: str, warnings: list[str]) -> list[MrcFrameMetadata]:
    if main.nsymbt <= 0:
        return [MrcFrameMetadata(raw_index=index, metadata_source="none") for index in range(main.frame_count)]
    with path.open("rb") as handle:
        handle.seek(MRC_HEADER_BYTES)
        data = handle.read(main.nsymbt)
    if len(data) != main.nsymbt:
        warnings.append("MRC extended header is incomplete.")
        return [MrcFrameMetadata(raw_index=index, metadata_source="unknown", warnings=["Extended header is incomplete."]) for index in range(main.frame_count)]
    if main.exttyp in {"FEI1", "FEI2"}:
        frames = _parse_fei_extended_header(data, main, endian, warnings)
        if frames is not None:
            return frames
    if main.nsymbt % main.frame_count != 0:
        warnings.append("MRC extended header size is not divisible by frame count for generic parser.")
        return [MrcFrameMetadata(raw_index=index, metadata_source="unknown", warnings=["Extended header size does not match frame count."]) for index in range(main.frame_count)]
    record_size = main.nsymbt // main.frame_count
    parser_name = _parser_name_for_exttyp(main.exttyp, False)
    if main.exttyp in {"FEI1", "FEI2", "TOMO", "AGAR"} or record_size >= 4:
        angles = _find_stack_order_tilt_angles(data, main.frame_count, record_size, endian)
        frames = []
        for index in range(main.frame_count):
            record = data[index * record_size : (index + 1) * record_size]
            raw_fields: dict[str, Any] = {"record_size": record_size, "raw_preview_hex": record[:32].hex()}
            if angles is not None:
                raw_fields["tilt_angle_offset"] = angles[1]
                raw_fields["tilt_angle_dtype"] = angles[2]
            frames.append(
                MrcFrameMetadata(
                    raw_index=index,
                    tilt_angle=angles[0][index] if angles is not None else None,
                    metadata_source="MRC extended header" if angles is not None else parser_name,
                    raw_fields=raw_fields,
                )
            )
        if angles is None:
            warnings.append(f"Extended header type {main.exttyp or 'unknown'} parsed without recognised tilt angles.")
        return frames
    warnings.append(f"Unsupported MRC extended header type: {main.exttyp or 'unknown'}.")
    return [
        MrcFrameMetadata(
            raw_index=index,
            metadata_source="unknown",
            raw_fields={"record_size": record_size},
            warnings=["Unsupported extended header format."],
        )
        for index in range(main.frame_count)
    ]


def _parse_fei_extended_header(data: bytes, main: MrcMainHeaderInfo, endian: str, warnings: list[str]) -> list[MrcFrameMetadata] | None:
    record_size = _fei_metadata_size(data, main, endian, warnings)
    if record_size is None:
        return None
    if record_size * main.frame_count > len(data):
        warnings.append(f"{main.exttyp} extended header is smaller than the expected frame metadata block count.")
        return None
    metadata_version = _safe_int32(data, 4, endian)
    LOGGER.debug(
        "%s extended header detected path=%s metadata_size=%s metadata_version=%s frames=%s ext_bytes=%s",
        main.exttyp,
        main.path,
        record_size,
        metadata_version,
        main.frame_count,
        main.nsymbt,
    )
    frames = [
        _parse_fei_metadata_record(
            data[index * record_size : (index + 1) * record_size],
            index,
            record_size,
            metadata_version,
            endian,
        )
        for index in range(main.frame_count)
    ]
    tilt_count = sum(1 for frame in frames if frame.tilt_angle is not None)
    if tilt_count == 0:
        warnings.append(f"{main.exttyp} parser did not find alpha_tilt values.")
    return frames


def _fei_metadata_size(data: bytes, main: MrcMainHeaderInfo, endian: str, warnings: list[str]) -> int | None:
    declared_size = _safe_int32(data, 0, endian)
    if main.exttyp == "FEI1":
        if declared_size == 768 or (declared_size in (0, None) and main.nsymbt >= 768 * main.frame_count):
            return 768
        if main.nsymbt % main.frame_count == 0 and main.nsymbt // main.frame_count == 768:
            return 768
        warnings.append("FEI1 extended header metadata block size was not recognised; using generic parser.")
        return None
    if declared_size is None or not (128 <= declared_size <= 4096):
        warnings.append("FEI2 extended header metadata block size was not recognised; using generic parser.")
        return None
    return declared_size


def _parse_fei_metadata_record(
    record: bytes,
    index: int,
    record_size: int,
    metadata_version: int | None,
    endian: str,
) -> MrcFrameMetadata:
    bitmasks = {
        1: _read_uint32(record, 8, endian),
        2: _read_uint32(record, 297, endian),
        3: _read_uint32(record, 490, endian),
        4: _read_uint32(record, 748, endian),
    }
    alpha_tilt = _read_fei_double(record, 100, bitmasks, 1, 7, fallback=True, low=-90, high=90)
    beta_tilt = _read_fei_double(record, 108, bitmasks, 1, 8, fallback=True, low=-90, high=90)
    pixel_size_x = _read_fei_double(record, 156, bitmasks, 1, 14, fallback=True, low=0, high=1e-3)
    pixel_size_y = _read_fei_double(record, 164, bitmasks, 1, 15, fallback=True, low=0, high=1e-3)
    timestamp_raw = _read_fei_double(record, 12, bitmasks, 1, 0, low=1, high=100_000)
    dose = _read_fei_double(record, 92, bitmasks, 1, 6, low=0, high=1e25)
    shift_x = _read_fei_double(record, 403, bitmasks, 2, 14, low=-1, high=1)
    shift_y = _read_fei_double(record, 411, bitmasks, 2, 15, low=-1, high=1)
    raw_fields = {
        "metadata_size": record_size,
        "metadata_version": metadata_version,
        "bitmask1": bitmasks[1],
        "bitmask2": bitmasks[2],
        "bitmask3": bitmasks[3],
        "bitmask4": bitmasks[4],
        "timestamp_ole": timestamp_raw,
        "microscope_type": _read_fei_ascii(record, 20, 16, bitmasks, 1, 1),
        "microscope_id": _read_fei_ascii(record, 36, 16, bitmasks, 1, 2),
        "application": _read_fei_ascii(record, 52, 16, bitmasks, 1, 3),
        "application_version": _read_fei_ascii(record, 68, 16, bitmasks, 1, 4),
        "high_tension": _read_fei_double(record, 84, bitmasks, 1, 5, low=0, high=2_000_000),
        "dose": dose,
        "dose_e_per_angstrom2": _dose_e_per_angstrom2(dose),
        "alpha_tilt": alpha_tilt,
        "beta_tilt": beta_tilt,
        "tilt_axis_angle": _read_fei_double(record, 140, bitmasks, 1, 12, low=-360, high=360),
        "dual_axis_rotation": _read_fei_double(record, 148, bitmasks, 1, 13, low=-360, high=360),
        "x_stage": _read_fei_double(record, 116, bitmasks, 1, 9, fallback=True, low=-1, high=1),
        "y_stage": _read_fei_double(record, 124, bitmasks, 1, 10, fallback=True, low=-1, high=1),
        "z_stage": _read_fei_double(record, 132, bitmasks, 1, 11, fallback=True, low=-1, high=1),
        "pixel_size_x": pixel_size_x,
        "pixel_size_y": pixel_size_y,
        "defocus": _read_fei_double(record, 220, bitmasks, 1, 22, low=-1, high=1),
        "stem_defocus": _read_fei_double(record, 228, bitmasks, 1, 23, low=-1, high=1),
        "applied_defocus": _read_fei_double(record, 236, bitmasks, 1, 24, low=-1, high=1),
        "instrument_mode": _read_fei_int32(record, 244, bitmasks, 1, 25),
        "projection_mode": _read_fei_int32(record, 248, bitmasks, 1, 26),
        "objective_lens_mode": _read_fei_ascii(record, 252, 16, bitmasks, 1, 27),
        "high_magnification_mode": _read_fei_ascii(record, 268, 16, bitmasks, 1, 28),
        "probe_mode": _read_fei_int32(record, 284, bitmasks, 1, 29),
        "eftem_on": _read_fei_bool(record, 288, bitmasks, 1, 30),
        "magnification": _read_fei_double(record, 289, bitmasks, 1, 31, fallback=True, low=0, high=100_000_000),
        "camera_length": _read_fei_double(record, 301, bitmasks, 2, 0, low=0, high=10_000),
        "spot_index": _read_fei_int32(record, 309, bitmasks, 2, 1),
        "illuminated_area": _read_fei_double(record, 313, bitmasks, 2, 2, low=0, high=1),
        "intensity": _read_fei_double(record, 321, bitmasks, 2, 3),
        "convergence_angle": _read_fei_double(record, 329, bitmasks, 2, 4, low=0, high=180),
        "illumination_mode": _read_fei_ascii(record, 337, 16, bitmasks, 2, 5),
        "wide_convergence_angle_range": _read_fei_bool(record, 353, bitmasks, 2, 6),
        "slit_inserted": _read_fei_bool(record, 354, bitmasks, 2, 7),
        "slit_width": _read_fei_double(record, 355, bitmasks, 2, 8, low=0, high=10_000),
        "acceleration_voltage_offset": _read_fei_double(record, 363, bitmasks, 2, 9),
        "drift_tube_voltage": _read_fei_double(record, 371, bitmasks, 2, 10),
        "energy_shift": _read_fei_double(record, 379, bitmasks, 2, 11),
        "shift_offset_x": _read_fei_double(record, 387, bitmasks, 2, 12, low=-1, high=1),
        "shift_offset_y": _read_fei_double(record, 395, bitmasks, 2, 13, low=-1, high=1),
        "shift_x": shift_x,
        "shift_y": shift_y,
        "integration_time": _read_fei_double(record, 419, bitmasks, 2, 16, fallback=True, low=1e-9, high=3600),
        "binning_width": _read_fei_int32(record, 427, bitmasks, 2, 17),
        "binning_height": _read_fei_int32(record, 431, bitmasks, 2, 18),
        "camera_name": _read_fei_ascii(record, 435, 16, bitmasks, 2, 19),
        "readout_area": _readout_area(record, bitmasks, endian),
        "ceta_noise_reduction": _read_fei_bool(record, 467, bitmasks, 2, 24),
        "ceta_frames_summed": _read_fei_int32(record, 468, bitmasks, 2, 25),
        "direct_detector_electron_counting": _read_fei_bool(record, 472, bitmasks, 2, 26),
        "direct_detector_align_frames": _read_fei_bool(record, 473, bitmasks, 2, 27),
        "phase_plate": _read_fei_bool(record, 518, bitmasks, 3, 6),
        "stem_detector_name": _read_fei_ascii(record, 519, 16, bitmasks, 3, 7),
        "stem_detector_gain": _read_fei_double(record, 535, bitmasks, 3, 8),
        "stem_detector_offset": _read_fei_double(record, 543, bitmasks, 3, 9),
        "dwell_time": _read_fei_double(record, 571, bitmasks, 3, 15, low=0, high=3600),
        "frame_time": _read_fei_double(record, 579, bitmasks, 3, 16, low=0, high=3600),
        "scan_size": _scan_size(record, bitmasks, endian),
        "full_scan_fov_x": _read_fei_double(record, 603, bitmasks, 3, 21, low=0, high=1),
        "full_scan_fov_y": _read_fei_double(record, 611, bitmasks, 3, 22, low=0, high=1),
        "edx_element": _read_fei_ascii(record, 619, 16, bitmasks, 3, 23),
        "is_dose_fraction": _read_fei_bool(record, 655, bitmasks, 3, 27),
        "dose_fraction_number": _read_fei_int32(record, 656, bitmasks, 3, 28),
        "dose_fraction_start_frame": _read_fei_int32(record, 660, bitmasks, 3, 29),
        "dose_fraction_end_frame": _read_fei_int32(record, 664, bitmasks, 3, 30),
        "input_stack_filename": _read_fei_ascii(record, 668, 80, bitmasks, 3, 31),
        "alpha_tilt_min": _read_fei_double(record, 752, bitmasks, 4, 0, low=-90, high=90),
        "alpha_tilt_max": _read_fei_double(record, 760, bitmasks, 4, 1, low=-90, high=90),
        "scan_rotation": _read_fei_double(record, 768, bitmasks, 4, 2, low=-math.tau, high=math.tau),
        "diffraction_pattern_rotation": _read_fei_double(record, 776, bitmasks, 4, 3, low=-math.tau, high=math.tau),
        "image_rotation": _read_fei_double(record, 784, bitmasks, 4, 4, low=-math.tau, high=math.tau),
        "scan_mode": _read_fei_int32(record, 792, bitmasks, 4, 5),
        "acquisition_timestamp_us": _read_fei_int64(record, 796, bitmasks, 4, 6),
        "detector_commercial_name": _read_fei_ascii(record, 804, 16, bitmasks, 4, 7),
        "start_tilt_angle": _read_fei_double(record, 820, bitmasks, 4, 8, low=-90, high=90),
        "end_tilt_angle": _read_fei_double(record, 828, bitmasks, 4, 9, low=-90, high=90),
        "tilt_per_image": _read_fei_double(record, 836, bitmasks, 4, 10, low=0, high=90),
        "tilt_speed": _read_fei_double(record, 844, bitmasks, 4, 11, low=0, high=1000),
        "beam_center_x": _read_fei_int32(record, 852, bitmasks, 4, 12),
        "beam_center_y": _read_fei_int32(record, 856, bitmasks, 4, 13),
        "field_presence": _fei_field_presence(bitmasks, len(record)),
        "field_presence_ignored": _fei_field_presence_ignored(bitmasks, len(record)),
        "raw_preview_hex": record[:32].hex(),
    }
    raw_fields = {
        key: value
        for key, value in raw_fields.items()
        if value is not None and value != {}
    }
    if alpha_tilt is not None:
        raw_fields["tilt_angle_offset"] = 100
        raw_fields["tilt_angle_dtype"] = "float64"
    return MrcFrameMetadata(
        raw_index=index,
        tilt_angle=alpha_tilt,
        stage_x=raw_fields.get("x_stage"),
        stage_y=raw_fields.get("y_stage"),
        stage_z=raw_fields.get("z_stage"),
        stage_alpha=alpha_tilt,
        stage_beta=beta_tilt,
        image_shift_x=shift_x,
        image_shift_y=shift_y,
        pixel_size=_meters_to_angstrom(pixel_size_x) or _meters_to_angstrom(pixel_size_y),
        magnification=raw_fields.get("magnification"),
        defocus=raw_fields.get("defocus"),
        exposure_time=raw_fields.get("integration_time"),
        dose=dose,
        timestamp=_ole_date_to_iso(timestamp_raw),
        metadata_source="MRC extended header",
        raw_fields=raw_fields,
    )


def _find_stack_order_tilt_angles(data: bytes, frame_count: int, record_size: int, endian: str) -> tuple[list[float], int, str] | None:
    candidates: list[tuple[float, int, str, list[float]]] = []
    for offset in range(0, record_size - 3, 4):
        values = [float(struct.unpack(f"{endian}f", data[(index * record_size) + offset : (index * record_size) + offset + 4])[0]) for index in range(frame_count)]
        if _reliable_stack_order_angles(values):
            span = max(values) - min(values)
            crosses_zero_bonus = 1000.0 if min(values) <= 0 <= max(values) else 0.0
            candidates.append((crosses_zero_bonus + span, offset, "float32", values))
    # Older FEI1/Tomography extended headers store the actual tilt-series
    # angle as float64 records (observed at offset 100 in 768-byte FEI1
    # records). The float32 scan above can find nearby stage/shift fields with
    # plausible but much smaller ranges, so include doubles and let the span
    # score pick the true tilt-angle range.
    for offset in range(0, record_size - 7, 4):
        values = [float(struct.unpack(f"{endian}d", data[(index * record_size) + offset : (index * record_size) + offset + 8])[0]) for index in range(frame_count)]
        if _reliable_stack_order_angles(values):
            span = max(values) - min(values)
            crosses_zero_bonus = 1000.0 if min(values) <= 0 <= max(values) else 0.0
            candidates.append((crosses_zero_bonus + span, offset, "float64", values))
    if not candidates:
        return None
    _score, offset, dtype, values = sorted(candidates, key=lambda item: item[0], reverse=True)[0]
    LOGGER.debug(
        "MRC extended-header tilt field selected frames=%s record_size=%s offset=%s dtype=%s range=(%.3f, %.3f) candidates=%s",
        frame_count,
        record_size,
        offset,
        dtype,
        min(values),
        max(values),
        len(candidates),
    )
    return values, offset, dtype


def _safe_int32(data: bytes, offset: int, endian: str) -> int | None:
    if offset < 0 or offset + 4 > len(data):
        return None
    try:
        return struct.unpack(f"{endian}i", data[offset : offset + 4])[0]
    except struct.error:
        return None


def _safe_uint32(data: bytes, offset: int, endian: str) -> int | None:
    if offset < 0 or offset + 4 > len(data):
        return None
    try:
        return struct.unpack(f"{endian}I", data[offset : offset + 4])[0]
    except struct.error:
        return None


def _safe_int64(data: bytes, offset: int, endian: str) -> int | None:
    if offset < 0 or offset + 8 > len(data):
        return None
    try:
        return struct.unpack(f"{endian}q", data[offset : offset + 8])[0]
    except struct.error:
        return None


def _read_uint32(record: bytes, offset: int, endian: str) -> int | None:
    return _safe_uint32(record, offset, endian)


def _read_fei_double(
    record: bytes,
    offset: int,
    bitmasks: dict[int, int | None],
    bitmask_index: int,
    bit_index: int,
    *,
    fallback: bool = False,
    low: float | None = None,
    high: float | None = None,
) -> float | None:
    if not _field_present(bitmasks, bitmask_index, bit_index) and not _fallback_allowed(bitmasks, fallback):
        return None
    if offset < 0 or offset + 8 > len(record):
        return None
    try:
        value = struct.unpack("<d", record[offset : offset + 8])[0]
    except struct.error:
        return None
    if not math.isfinite(value):
        return None
    if low is not None and value < low:
        return None
    if high is not None and value > high:
        return None
    return float(value)


def _read_fei_int32(
    record: bytes,
    offset: int,
    bitmasks: dict[int, int | None],
    bitmask_index: int,
    bit_index: int,
) -> int | None:
    if not _field_present(bitmasks, bitmask_index, bit_index):
        return None
    return _safe_int32(record, offset, "<")


def _read_fei_int64(
    record: bytes,
    offset: int,
    bitmasks: dict[int, int | None],
    bitmask_index: int,
    bit_index: int,
) -> int | None:
    if not _field_present(bitmasks, bitmask_index, bit_index):
        return None
    return _safe_int64(record, offset, "<")


def _read_fei_ascii(
    record: bytes,
    offset: int,
    size: int,
    bitmasks: dict[int, int | None],
    bitmask_index: int,
    bit_index: int,
) -> str | None:
    if not _field_present(bitmasks, bitmask_index, bit_index):
        return None
    if offset < 0 or offset >= len(record):
        return None
    raw = record[offset : min(len(record), offset + size)].split(b"\x00", 1)[0].strip()
    if not raw:
        return None
    return raw.decode("ascii", errors="replace").strip() or None


def _read_fei_bool(
    record: bytes,
    offset: int,
    bitmasks: dict[int, int | None],
    bitmask_index: int,
    bit_index: int,
) -> bool | None:
    if not _field_present(bitmasks, bitmask_index, bit_index):
        return None
    if offset < 0 or offset >= len(record):
        return None
    return bool(record[offset])


def _field_present(bitmasks: dict[int, int | None], bitmask_index: int, bit_index: int) -> bool:
    bitmask = bitmasks.get(bitmask_index)
    return bitmask is not None and bool(bitmask & (1 << bit_index))


def _fallback_allowed(bitmasks: dict[int, int | None], fallback: bool) -> bool:
    if not fallback:
        return False
    return not any(value for value in bitmasks.values() if value is not None)


def _fei_field_presence(bitmasks: dict[int, int | None], record_size: int) -> dict[str, str]:
    presence: dict[str, str] = {}
    fallback = _fallback_allowed(bitmasks, True)
    for field, mask_index, bit_index, allows_fallback, offset, size in _FEI_FIELD_PRESENCE_SPECS:
        if _field_present(bitmasks, mask_index, bit_index) and _fei_field_fits(record_size, offset, size):
            presence[field] = f"bitmask{mask_index}:{bit_index}"
        elif allows_fallback and fallback and _fei_field_fits(record_size, offset, size):
            presence[field] = "legacy_no_bitmask_fallback"
    return presence


def _fei_field_presence_ignored(bitmasks: dict[int, int | None], record_size: int) -> dict[str, str]:
    ignored: dict[str, str] = {}
    for field, mask_index, bit_index, _allows_fallback, offset, size in _FEI_FIELD_PRESENCE_SPECS:
        if _field_present(bitmasks, mask_index, bit_index) and not _fei_field_fits(record_size, offset, size):
            ignored[field] = f"bitmask{mask_index}:{bit_index} beyond {record_size}-byte FEI record"
    return ignored


def _fei_field_fits(record_size: int, offset: int, size: int) -> bool:
    return 0 <= offset and offset + size <= record_size


_FEI_FIELD_PRESENCE_SPECS: tuple[tuple[str, int, int, bool, int, int], ...] = (
    ("alpha_tilt", 1, 7, True, 100, 8),
    ("beta_tilt", 1, 8, True, 108, 8),
    ("x_stage", 1, 9, True, 116, 8),
    ("y_stage", 1, 10, True, 124, 8),
    ("z_stage", 1, 11, True, 132, 8),
    ("pixel_size_x", 1, 14, True, 156, 8),
    ("pixel_size_y", 1, 15, True, 164, 8),
    ("probe_mode", 1, 29, False, 284, 4),
    ("magnification", 1, 31, True, 289, 8),
    ("spot_index", 2, 1, False, 309, 4),
    ("illuminated_area", 2, 2, False, 313, 8),
    ("camera_name", 2, 19, False, 435, 16),
    ("alpha_tilt_min", 4, 0, False, 752, 8),
    ("alpha_tilt_max", 4, 1, False, 760, 8),
    ("scan_rotation", 4, 2, False, 768, 8),
    ("diffraction_pattern_rotation", 4, 3, False, 776, 8),
    ("image_rotation", 4, 4, False, 784, 8),
    ("scan_mode", 4, 5, False, 792, 4),
    ("acquisition_timestamp_us", 4, 6, False, 796, 8),
    ("detector_commercial_name", 4, 7, False, 804, 16),
    ("start_tilt_angle", 4, 8, False, 820, 8),
    ("end_tilt_angle", 4, 9, False, 828, 8),
    ("tilt_per_image", 4, 10, False, 836, 8),
    ("tilt_speed", 4, 11, False, 844, 8),
    ("beam_center_x", 4, 12, False, 852, 4),
    ("beam_center_y", 4, 13, False, 856, 4),
)


def _readout_area(record: bytes, bitmasks: dict[int, int | None], endian: str) -> dict[str, int] | None:
    fields = {
        "left": _read_fei_int32(record, 451, bitmasks, 2, 20),
        "top": _read_fei_int32(record, 455, bitmasks, 2, 21),
        "right": _read_fei_int32(record, 459, bitmasks, 2, 22),
        "bottom": _read_fei_int32(record, 463, bitmasks, 2, 23),
    }
    fields = {key: value for key, value in fields.items() if value is not None}
    return fields or None


def _scan_size(record: bytes, bitmasks: dict[int, int | None], endian: str) -> dict[str, int] | None:
    fields = {
        "left": _read_fei_int32(record, 587, bitmasks, 3, 17),
        "top": _read_fei_int32(record, 591, bitmasks, 3, 18),
        "right": _read_fei_int32(record, 595, bitmasks, 3, 19),
        "bottom": _read_fei_int32(record, 599, bitmasks, 3, 20),
    }
    fields = {key: value for key, value in fields.items() if value is not None}
    return fields or None


def _ole_date_to_iso(value: float | None) -> str | None:
    if value is None:
        return None
    try:
        parsed = OLE_DATE_EPOCH + timedelta(days=float(value))
    except (OverflowError, ValueError):
        return None
    if not (1900 <= parsed.year <= 2200):
        return None
    return parsed.isoformat(sep=" ", timespec="seconds")


def _dose_e_per_angstrom2(value: float | None) -> float | None:
    if value is None:
        return None
    # FEI/Thermo records dose as electrons per square metre. 1 A^2 = 1e-20 m^2.
    return value * 1e-20


def _meters_to_angstrom(value: float | None) -> float | None:
    if value is None:
        return None
    if 0 < abs(value) < 1e-6:
        return value * 1e10
    return value


def _plausible(value: float | None, low: float, high: float) -> float | None:
    if value is None or not (low <= value <= high):
        return None
    return value


def _resolved_voxel_size(
    value: tuple[float | None, float | None, float | None],
    frames: list[MrcFrameMetadata],
) -> tuple[float | None, float | None, float | None]:
    if all(item is not None for item in value):
        return value
    pixel_size = next((frame.pixel_size for frame in frames if frame.pixel_size is not None), None)
    if pixel_size is None:
        return value
    x = value[0] if value[0] is not None else pixel_size
    y = value[1] if value[1] is not None else pixel_size
    z = value[2] if value[2] is not None else pixel_size
    return (x, y, z)


def _format_angle_list(values: list[float]) -> str:
    if not values:
        return "none"
    return ", ".join(f"{value:.2f}" for value in values)


def _reliable_stack_order_angles(angles: list[float]) -> bool:
    if len(angles) < 2 or not all(np.isfinite(angles)):
        return False
    if any(abs(angle) > 90 for angle in angles):
        return False
    if max(angles) - min(angles) < 1:
        return False
    increasing = all(next_angle >= angle for angle, next_angle in zip(angles, angles[1:]))
    decreasing = all(next_angle <= angle for angle, next_angle in zip(angles, angles[1:]))
    return increasing or decreasing


def _unpack_header_shape_and_endian(header: bytes, warnings: list[str]) -> tuple[int | None, int | None, int | None, int | None, str]:
    little = struct.unpack("<4i", header[:16])
    if _looks_plausible(little):
        return little[0], little[1], little[2], little[3], "<"
    big = struct.unpack(">4i", header[:16])
    if _looks_plausible(big):
        warnings.append("MRC header appears to use big-endian byte order.")
        return big[0], big[1], big[2], big[3], ">"
    warnings.append("MRC header dimensions are not plausible.")
    return None, None, None, None, "<"


def _looks_plausible(values: tuple[int, int, int, int]) -> bool:
    nx, ny, nz, mode = values
    return 0 < nx < 1_000_000 and 0 < ny < 1_000_000 and 0 < nz < 1_000_000 and -10 <= mode <= 100


def _int_at(header: bytes, offset: int, endian: str, warnings: list[str]) -> int | None:
    try:
        value = struct.unpack(f"{endian}i", header[offset : offset + 4])[0]
    except struct.error:
        return None
    if value < 0:
        warnings.append("MRC extended header size is negative.")
        return 0
    return value


def _extended_header_type(header: bytes) -> str | None:
    raw = header[104:108].rstrip(b"\x00 ") if len(header) >= 108 else b""
    return raw.decode("ascii", errors="replace") if raw else None


def _axis_mapping(header: bytes, endian: str) -> tuple[int | None, int | None, int | None]:
    return struct.unpack(f"{endian}3i", header[64:76]) if len(header) >= 76 else (None, None, None)


def _voxel_size(header: bytes, endian: str, nx: int, ny: int, nz: int) -> tuple[float | None, float | None, float | None]:
    if len(header) < 52:
        return (None, None, None)
    cell_x, cell_y, cell_z = struct.unpack(f"{endian}3f", header[40:52])
    return (
        cell_x / nx if nx > 0 and cell_x > 0 else None,
        cell_y / ny if ny > 0 and cell_y > 0 else None,
        cell_z / nz if nz > 0 and cell_z > 0 else None,
    )


def _numpy_dtype(mode: int, endian: str) -> np.dtype | None:
    code_by_mode = {0: "i1", 1: "i2", 2: "f4", 6: "u2"}
    code = code_by_mode.get(mode)
    if code is None:
        return None
    return np.dtype(code if code == "i1" else f"{endian}{code}")


def _parser_name_for_exttyp(exttyp: str | None, has_angles: bool) -> str:
    if exttyp in {"FEI1", "FEI2"}:
        return "FEI/Thermo Fisher extended header"
    if exttyp in {"TOMO", "AGAR"}:
        return "Tomography extended header"
    if has_angles:
        return "Generic stack-order angle parser"
    return "Unknown extended header"
