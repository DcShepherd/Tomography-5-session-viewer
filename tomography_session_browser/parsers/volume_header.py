"""Strict reconstruction headers, without changing acquisition MRC policy."""
from __future__ import annotations

import math
from pathlib import Path
import struct
import numpy as np

from tomography_session_browser.domain.identity import path_identity_key
from tomography_session_browser.domain.models import MrcMetadata
from tomography_session_browser.domain.tomograms import (SUSPICIOUS_SPACING_WARNING, UNRESOLVED_SPACING_WARNING, Volume,
                                                         VolumeFingerprint, VolumeGrid)


def fingerprint(path: Path) -> VolumeFingerprint:
    stat = path.stat()
    return VolumeFingerprint(path.resolve(), stat.st_size, stat.st_mtime_ns)


def is_cloud_placeholder(path: Path) -> bool:
    # Attribute queries do not open/dehydrate the file's contents.
    return bool(getattr(path.stat(), "st_file_attributes", 0) & (0x1000 | 0x40000 | 0x400000))


def records_volume(header: bytes) -> bool:
    """The MRC format's own record of one volume: space group 1, not 0 for an image
    stack, with every section in the one map and no tilt-stack extended header."""
    endian = ">" if header[212:214] == b"\x11\x11" else "<"
    nz = struct.unpack_from(endian + "i", header, 8)[0]
    sections = struct.unpack_from(endian + "i", header, 36)[0]
    space_group, extended = struct.unpack_from(endian + "2i", header, 88)
    return space_group == 1 and extended == 0 and nz > 1 and sections == nz


def file_records_volume(path: Path) -> bool:
    with path.open("rb") as handle:
        header = handle.read(1024)
    return len(header) == 1024 and records_volume(header)


def read_volume(path: Path, version: str) -> Volume:
    stamp = fingerprint(path)
    key = path_identity_key(path)
    if is_cloud_placeholder(path):
        return Volume(key, stamp.path, version, stamp, None,
                      MrcMetadata(path, stamp.size), warnings=(
                          "Cloud-only volume: make this file available locally, then reload the folder.",))
    try:
        with path.open("rb") as handle:
            header = handle.read(1024)
        if len(header) != 1024:
            raise ValueError("The reconstruction header is truncated.")
        formats = {0: "i1", 1: "i2", 2: "f4", 6: "u2", 12: "f2"}
        candidates: list[str] = []
        for order in ("<", ">"):
            nx, ny, nz, mode = struct.unpack_from(order + "4i", header)
            axes = struct.unpack_from(order + "3i", header, 64)
            if mode in formats and all(0 < value <= 1_000_000 for value in (nx, ny, nz)) and sorted(axes) == [1, 2, 3]:
                candidates.append(order)
        preferred = "<" if header[212:214] in (b"DD", b"DA") else ">" if header[212:214] == b"\x11\x11" else None
        endian = preferred if preferred in candidates else candidates[0] if len(candidates) == 1 else None
        if endian is None:
            raise ValueError("Unsupported MRC mode, invalid axes, or ambiguous reconstruction byte order.")
        nx, ny, nz, mode = struct.unpack_from(endian + "4i", header)
        mapping = struct.unpack_from(endian + "3i", header, 64)
        nsymbt = struct.unpack_from(endian + "i", header, 92)[0]
        if nsymbt < 0:
            raise ValueError("Extended-header size is negative.")
        dtype = np.dtype(endian + formats[mode])
        offset = 1024 + nsymbt
        expected = offset + nx * ny * nz * dtype.itemsize
        counts = struct.unpack_from(endian + "3i", header, 28)
        lengths = struct.unpack_from(endian + "3f", header, 40)
        spacing = tuple(length / count if count > 0 and math.isfinite(length) and length > 0 else None
                        for length, count in zip(lengths, counts, strict=True))
        metadata = MrcMetadata(path, stamp.size, nx=nx, ny=ny, nz=nz, mode=mode, is_stack=True,
                               extended_header_bytes=nsymbt, voxel_size=(spacing[0], spacing[1], spacing[2]),
                               data_complete=stamp.size >= expected)
        angles = struct.unpack_from(endian + "3f", header, 52)
        if any(not math.isfinite(angle) or abs(angle - 90) > 0.01 for angle in angles):
            raise ValueError("Non-orthogonal or missing cell angles are not supported for volume viewing.")
        origin = struct.unpack_from(endian + "3f", header, 196)
        if not all(math.isfinite(value) for value in origin):
            raise ValueError("Volume origin is not finite.")
        starts = struct.unpack_from(endian + "3i", header, 16)
        grid = VolumeGrid((nz, ny, nx), mapping, metadata.voxel_size, origin, starts)
        warnings: list[str] = []
        if any(value is None or not math.isfinite(value) or value <= 0 for value in grid.spacing_xyz):
            warnings.append(UNRESOLVED_SPACING_WARNING)
        elif any(abs(float(value) - 1) < 1e-6 for value in grid.spacing_xyz if value is not None):
            warnings.append(SUSPICIOUS_SPACING_WARNING)
        if metadata.data_complete is not True:
            raise ValueError("The reconstruction data block is incomplete.")
        if fingerprint(path) != stamp:
            raise ValueError("The volume changed while its header was being read. Reload the folder.")
        return Volume(key, stamp.path, version, stamp, grid, metadata,
                      dtype.str, offset, tuple(warnings))
    except (OSError, ValueError, struct.error) as exc:
        return Volume(key, stamp.path, version, stamp, None,
                      MrcMetadata(path, stamp.size, data_complete=False), warnings=(str(exc),))
