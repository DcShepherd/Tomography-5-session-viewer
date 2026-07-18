from __future__ import annotations

from dataclasses import dataclass
import logging
import math
from pathlib import Path
import struct
import time

import numpy as np
from PIL import Image

from tomography_session_browser.domain.models import MrcMetadata
from tomography_session_browser.parsers.mrc_metadata_parser import MrcFrameMetadata, ParsedMrcMetadata, parse_mrc_metadata
from tomography_session_browser.services.loading_profiler import record_aggregate_phase


LOGGER = logging.getLogger(__name__)

MRC_HEADER_BYTES = 1024
DEFAULT_MAX_PREVIEW_DIMENSION = 2048
NORMALISATION = "area_downsample_percentile_0.5_99.5"


@dataclass(frozen=True, slots=True)
class MrcInspection:
    path: Path
    size_bytes: int
    nx: int
    ny: int
    nz: int
    mode: int
    dtype: np.dtype
    endian: str
    extended_header_bytes: int
    data_kind: str
    frame_count: int
    mapc: int | None = None
    mapr: int | None = None
    maps: int | None = None
    pixel_size: float | None = None
    extended_header_type: str | None = None
    tilt_angles: tuple[float, ...] = ()
    tilt_angle_source: str | None = None
    parsed_metadata: ParsedMrcMetadata | None = None
    warnings: tuple[str, ...] = ()

    @property
    def shape(self) -> tuple[int, int, int]:
        return (self.nz, self.ny, self.nx)

    @property
    def data_offset(self) -> int:
        return MRC_HEADER_BYTES + self.extended_header_bytes

    @property
    def frame_shape(self) -> tuple[int, int]:
        return (self.ny, self.nx)


@dataclass(slots=True)
class MrcSlicePreview:
    path: Path
    image: Image.Image | None
    slice_index: int
    slice_count: int
    warnings: list[str]
    original_shape: tuple[int, int, int] | None = None
    raw_frame_shape: tuple[int, int] | None = None
    preview_shape: tuple[int, int] | None = None
    preview_dtype: str = "uint8"
    used_mmap: bool = False
    estimated_bytes: int = 0
    data_kind: str = "unknown"
    downsample_factor: int = 1
    from_cache: bool = False
    load_seconds: float = 0.0
    normalise_seconds: float = 0.0
    total_seconds: float = 0.0


class MrcPreviewSource:
    def __init__(self, path: Path, inspection: MrcInspection | None = None) -> None:
        self.path = path
        self.inspection = inspection or inspect_mrc(path)

    def frame_count(self) -> int:
        return self.inspection.frame_count

    def get_frame_header(self, index: int) -> MrcInspection:
        self._check_index(index)
        return self.inspection

    def get_frame_preview(self, index: int = 0, max_size: int = DEFAULT_MAX_PREVIEW_DIMENSION) -> MrcSlicePreview:
        start = time.perf_counter()
        self._check_index(index)
        load_start = time.perf_counter()
        frame = self._read_frame(index)
        load_seconds = time.perf_counter() - load_start

        normalise_start = time.perf_counter()
        preview, downsample_factor = _frame_to_preview(frame, max_size)
        normalise_seconds = time.perf_counter() - normalise_start

        contiguous = np.ascontiguousarray(preview, dtype=np.uint8)
        image = Image.fromarray(contiguous, mode="L")
        total_seconds = time.perf_counter() - start
        # MRC slice loads land in the perf report so we can see how long
        # the user waits when they click into the viewer or scrub a tilt.
        record_aggregate_phase("load_mrc_slice", total_seconds)
        result = MrcSlicePreview(
            path=self.path,
            image=image,
            slice_index=index,
            slice_count=self.frame_count(),
            warnings=list(self.inspection.warnings),
            original_shape=self.inspection.shape,
            raw_frame_shape=tuple(frame.shape),
            preview_shape=tuple(contiguous.shape),
            preview_dtype=str(contiguous.dtype),
            used_mmap=True,
            estimated_bytes=int(contiguous.nbytes),
            data_kind=self.inspection.data_kind,
            downsample_factor=downsample_factor,
            load_seconds=load_seconds,
            normalise_seconds=normalise_seconds,
            total_seconds=total_seconds,
        )
        LOGGER.debug(
            "MRC preview path=%s kind=%s dims=%s mapc/mapr/maps=%s/%s/%s frame=%s raw_frame_shape=%s "
            "downsample=%s preview_shape=%s dtype=%s cache=%s load=%.4fs norm=%.4fs total=%.4fs",
            self.path,
            self.inspection.data_kind,
            self.inspection.shape,
            self.inspection.mapc,
            self.inspection.mapr,
            self.inspection.maps,
            index,
            result.raw_frame_shape,
            downsample_factor,
            result.preview_shape,
            result.preview_dtype,
            result.from_cache,
            load_seconds,
            normalise_seconds,
            total_seconds,
        )
        return result

    def clear_cache(self) -> None:
        return

    def _read_frame(self, index: int) -> np.ndarray:
        dtype = self.inspection.dtype
        shape = self.inspection.shape
        mapped = np.memmap(
            self.path,
            dtype=dtype,
            mode="r",
            offset=self.inspection.data_offset,
            shape=shape,
            order="C",
        )
        frame_view = mapped[0] if self.inspection.nz == 1 else mapped[index]
        return frame_view

    def _check_index(self, index: int) -> None:
        if index < 0 or index >= self.frame_count():
            raise IndexError(f"MRC frame index out of range: {index}")


_INSPECT_CACHE: dict[tuple[str, int, int], "MrcInspection"] = {}
_INSPECT_CACHE_MAX = 1024


def _inspect_cache_key(path: Path) -> tuple[str, int, int] | None:
    """Return the cache key ``(path, mtime_ns, size)`` if ``path`` is a real
    file, else ``None`` to bypass caching (caller decides).

    Stat is cheap; doing it on every call is the cost of correctness here.
    Files mutated on disk between two reads will get fresh keys and the
    stale entry stays cached but unreachable until the LRU bound trims it.
    """

    try:
        stat = path.stat()
    except OSError:
        return None
    return (str(path), stat.st_mtime_ns, stat.st_size)


def _inspect_cache_evict_if_needed() -> None:
    if len(_INSPECT_CACHE) <= _INSPECT_CACHE_MAX:
        return
    # Dicts are insertion-ordered: drop the oldest 25% to amortise eviction.
    drop = max(1, _INSPECT_CACHE_MAX // 4)
    for key in list(_INSPECT_CACHE)[:drop]:
        _INSPECT_CACHE.pop(key, None)


def clear_inspect_mrc_cache() -> None:
    """Clear the :func:`inspect_mrc` cache. Useful in tests; the cache is
    keyed by ``(path, mtime_ns, size)`` and is correctness-safe for
    production paths.
    """

    _INSPECT_CACHE.clear()


# Optional hook for the loading profiler. Modules that want cache-hit
# telemetry can set these callables; the parser stays decoupled from the
# profiler module to keep import cost minimal.
inspect_cache_hit_hook = None  # type: ignore[assignment]
inspect_cache_miss_hook = None  # type: ignore[assignment]


def _record_inspect_cache_hit() -> None:
    hook = inspect_cache_hit_hook
    if hook is not None:
        try:
            hook()
        except Exception:  # pragma: no cover - defensive
            LOGGER.debug("inspect_cache_hit_hook raised; ignored", exc_info=True)


def _record_inspect_cache_miss() -> None:
    hook = inspect_cache_miss_hook
    if hook is not None:
        try:
            hook()
        except Exception:  # pragma: no cover - defensive
            LOGGER.debug("inspect_cache_miss_hook raised; ignored", exc_info=True)


def inspect_mrc(path: Path) -> MrcInspection:
    cache_key = _inspect_cache_key(path)
    if cache_key is not None:
        cached = _INSPECT_CACHE.get(cache_key)
        if cached is not None:
            _record_inspect_cache_hit()
            return cached
        _record_inspect_cache_miss()

    parsed = parse_mrc_metadata(path)
    main = parsed.main_header
    warnings = list(parsed.warnings)
    nx, ny, nz, mode = main.nx, main.ny, main.nz, main.mode
    endian = "<"
    dtype = _numpy_dtype(mode, endian)
    if dtype is None:
        raise ValueError(f"MRC mode {mode} is not supported for preview.")
    tilt_angles = [frame.tilt_angle for frame in parsed.frame_metadata if frame.tilt_angle is not None]
    size_bytes = path.stat().st_size
    data_offset = MRC_HEADER_BYTES + main.nsymbt
    expected_data_bytes = max(1, nx) * max(1, ny) * max(1, nz) * dtype.itemsize
    if size_bytes < data_offset + expected_data_bytes:
        warnings.append(
            "MRC data block is incomplete "
            f"({size_bytes:,} bytes on disk; expected at least {data_offset + expected_data_bytes:,})."
        )

    inspection = MrcInspection(
        path=path,
        size_bytes=size_bytes,
        nx=nx,
        ny=ny,
        nz=nz,
        mode=mode,
        dtype=dtype,
        endian=endian,
        extended_header_bytes=main.nsymbt,
        data_kind=_data_kind(nz),
        frame_count=max(1, nz),
        mapc=main.mapc,
        mapr=main.mapr,
        maps=main.maps,
        pixel_size=main.voxel_size[0],
        extended_header_type=main.exttyp,
        tilt_angles=tuple(tilt_angles),
        tilt_angle_source="MRC extended header" if tilt_angles else None,
        parsed_metadata=parsed,
        warnings=tuple(warnings),
    )
    LOGGER.debug(
        "MRC inspect path=%s kind=%s dims=%s dtype=%s mode=%s ext_bytes=%s ext_type=%s "
        "mapc/mapr/maps=%s/%s/%s pixel_size=%s tilt_angles_found=%s angle_count=%s first_angles=%s warnings=%s",
        path,
        inspection.data_kind,
        inspection.shape,
        inspection.dtype,
        mode,
        main.nsymbt,
        main.exttyp,
        main.mapc,
        main.mapr,
        main.maps,
        inspection.pixel_size,
        bool(tilt_angles),
        len(tilt_angles),
        tilt_angles[:5],
        warnings,
    )
    if cache_key is not None:
        _INSPECT_CACHE[cache_key] = inspection
        _inspect_cache_evict_if_needed()
    return inspection


def read_mrc_metadata(path: Path) -> MrcMetadata:
    try:
        inspection = inspect_mrc(path)
    except (OSError, ValueError) as exc:
        try:
            size_bytes = path.stat().st_size
        except OSError:
            size_bytes = 0
        return MrcMetadata(path=path, size_bytes=size_bytes, warnings=[str(exc)])
    return MrcMetadata(
        path=path,
        size_bytes=inspection.size_bytes,
        nx=inspection.nx,
        ny=inspection.ny,
        nz=inspection.nz,
        mode=inspection.mode,
        is_stack=inspection.frame_count > 1,
        extended_header_bytes=inspection.extended_header_bytes,
        extended_header_type=inspection.extended_header_type,
        tilt_angles=list(inspection.tilt_angles),
        tilt_angle_source=inspection.tilt_angle_source,
        voxel_size=inspection.parsed_metadata.main_header.voxel_size if inspection.parsed_metadata is not None else (inspection.pixel_size, None, None),
        frame_metadata=[_frame_metadata_dict(frame) for frame in inspection.parsed_metadata.frame_metadata] if inspection.parsed_metadata is not None else [],
        parser_name=inspection.parsed_metadata.parser_name if inspection.parsed_metadata is not None else None,
        parser_version=inspection.parsed_metadata.parser_version if inspection.parsed_metadata is not None else None,
        warnings=list(inspection.warnings),
    )


def _frame_metadata_dict(frame: MrcFrameMetadata) -> dict[str, object]:
    return {
        "raw_index": frame.raw_index,
        "tilt_angle": frame.tilt_angle,
        "stage_x": frame.stage_x,
        "stage_y": frame.stage_y,
        "stage_z": frame.stage_z,
        "stage_alpha": frame.stage_alpha,
        "stage_beta": frame.stage_beta,
        "image_shift_x": frame.image_shift_x,
        "image_shift_y": frame.image_shift_y,
        "beam_shift_x": frame.beam_shift_x,
        "beam_shift_y": frame.beam_shift_y,
        "pixel_size": frame.pixel_size,
        "magnification": frame.magnification,
        "defocus": frame.defocus,
        "exposure_time": frame.exposure_time,
        "dose": frame.dose,
        "timestamp": frame.timestamp,
        "metadata_source": frame.metadata_source,
        "raw_fields": dict(frame.raw_fields),
        "warnings": list(frame.warnings),
    }


def read_mrc_slice_preview(path: Path, slice_index: int = 0, max_dimension: int = DEFAULT_MAX_PREVIEW_DIMENSION) -> MrcSlicePreview:
    try:
        return MrcPreviewSource(path).get_frame_preview(slice_index, max_dimension)
    except (OSError, ValueError, IndexError) as exc:
        return MrcSlicePreview(path, None, max(0, slice_index), 0, [str(exc)])


def _frame_to_preview(frame: np.ndarray, max_size: int) -> tuple[np.ndarray, int]:
    if frame.ndim != 2:
        raise ValueError(f"MRC frame must be 2D for display, got shape {frame.shape}")
    shrink = _shrink_factor(frame.shape, max_size)
    sampled = _downsample_for_preview(frame, shrink)
    display = _normalise_to_uint8(sampled)
    return display, shrink


def _shrink_factor(shape: tuple[int, int], max_size: int) -> int:
    height, width = shape
    return max(1, math.ceil(max(width / max_size, height / max_size)))


def _downsample_for_preview(frame: np.ndarray, shrink: int) -> np.ndarray:
    if shrink <= 1:
        return np.asarray(frame)
    height, width = frame.shape
    reduced_height = height // shrink
    reduced_width = width // shrink
    if reduced_height <= 0 or reduced_width <= 0:
        return np.asarray(frame)
    trimmed = frame[: reduced_height * shrink, : reduced_width * shrink]
    blocks = trimmed.reshape(reduced_height, shrink, reduced_width, shrink)
    return blocks.mean(axis=(1, 3), dtype=np.float32)


def _strided_shape(shape: tuple[int, int], max_size: int) -> tuple[int, int]:
    shrink = _shrink_factor(shape, max_size)
    height, width = shape
    return (max(1, height // shrink), max(1, width // shrink))


def _normalise_to_uint8(frame: np.ndarray) -> np.ndarray:
    numeric = np.asarray(frame)
    finite = numeric[np.isfinite(numeric)]
    if finite.size == 0:
        return np.zeros(numeric.shape, dtype=np.uint8)
    if finite.size > 1_000_000:
        step = math.ceil(finite.size / 1_000_000)
        finite = finite[::step]
    low, high = np.percentile(finite.astype(np.float32, copy=False), [0.5, 99.5])
    if not np.isfinite(low) or not np.isfinite(high) or high <= low:
        return np.zeros(numeric.shape, dtype=np.uint8)
    scaled = (numeric.astype(np.float32, copy=False) - low) * (255.0 / (high - low))
    return np.ascontiguousarray(np.clip(scaled, 0, 255).astype(np.uint8))


def _unpack_header_shape(header: bytes, warnings: list[str]) -> tuple[int | None, int | None, int | None, int | None]:
    nx, ny, nz, mode, _endian = _unpack_header_shape_and_endian(header, warnings)
    return nx, ny, nz, mode


def _unpack_header_shape_and_endian(
    header: bytes, warnings: list[str]
) -> tuple[int | None, int | None, int | None, int | None, str]:
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


def _extended_header_size(header: bytes, endian: str, warnings: list[str]) -> int:
    try:
        value = struct.unpack(f"{endian}i", header[92:96])[0]
    except struct.error:
        return 0
    if value < 0:
        warnings.append("MRC extended header size is negative; preview starts after the standard header.")
        return 0
    return value


def _extended_header_type(header: bytes) -> str | None:
    if len(header) < 108:
        return None
    raw = header[104:108].rstrip(b"\x00 ")
    if not raw:
        return None
    try:
        return raw.decode("ascii", errors="replace")
    except UnicodeDecodeError:
        return None


def _extended_header_tilt_angles(
    path: Path,
    extended_header_bytes: int,
    frame_count: int,
    endian: str,
    extended_header_type: str | None,
    warnings: list[str],
) -> list[float]:
    if extended_header_bytes <= 0 or frame_count <= 1 or extended_header_bytes % frame_count != 0:
        if extended_header_bytes > 0 and frame_count > 1:
            warnings.append("MRC extended header size is not divisible by frame count; tilt angles unavailable.")
        LOGGER.debug(
            "MRC extended header angles unavailable path=%s frames=%s ext_bytes=%s ext_type=%s reason=%s",
            path,
            frame_count,
            extended_header_bytes,
            extended_header_type,
            "no extended header or incompatible size",
        )
        return []
    record_size = extended_header_bytes // frame_count
    if record_size < 4:
        return []
    try:
        with path.open("rb") as handle:
            handle.seek(MRC_HEADER_BYTES)
            data = handle.read(extended_header_bytes)
    except OSError as exc:
        warnings.append(f"Could not read MRC extended header: {exc}")
        return []
    if len(data) != extended_header_bytes:
        warnings.append("MRC extended header is incomplete; tilt angles unavailable.")
        return []

    candidates: list[tuple[float, int, list[float]]] = []
    for offset in range(0, record_size - 3, 4):
        angles: list[float] = []
        for index in range(frame_count):
            start = (index * record_size) + offset
            value = struct.unpack(f"{endian}f", data[start : start + 4])[0]
            angles.append(float(value))
        if _reliable_stack_order_angles(angles):
            span = max(angles) - min(angles)
            crosses_zero_bonus = 1000.0 if min(angles) <= 0 <= max(angles) else 0.0
            candidates.append((crosses_zero_bonus + span, offset, angles))
    if not candidates:
        LOGGER.debug(
            "MRC extended header tilt angles not found path=%s frames=%s ext_bytes=%s ext_type=%s record_size=%s",
            path,
            frame_count,
            extended_header_bytes,
            extended_header_type,
            record_size,
        )
        return []
    _score, offset, angles = sorted(candidates, key=lambda item: item[0], reverse=True)[0]
    LOGGER.debug(
        "MRC extended header tilt angles found path=%s frames=%s ext_bytes=%s ext_type=%s record_size=%s offset=%s first_angles=%s",
        path,
        frame_count,
        extended_header_bytes,
        extended_header_type,
        record_size,
        offset,
        angles[:5],
    )
    return angles


def _reliable_stack_order_angles(angles: list[float]) -> bool:
    if len(angles) < 2:
        return False
    if not all(np.isfinite(angles)):
        return False
    if any(abs(angle) > 90 for angle in angles):
        return False
    span = max(angles) - min(angles)
    if span < 1:
        return False
    return all(next_angle >= angle for angle, next_angle in zip(angles, angles[1:]))


def _axis_mapping(header: bytes, endian: str) -> tuple[int | None, int | None, int | None]:
    if len(header) < 76:
        return None, None, None
    return struct.unpack(f"{endian}3i", header[64:76])


def _pixel_size(header: bytes, endian: str, nx: int) -> float | None:
    if len(header) < 52 or nx <= 0:
        return None
    cell_x = struct.unpack(f"{endian}f", header[40:44])[0]
    return cell_x / nx if cell_x > 0 else None


def _numpy_dtype(mode: int, endian: str) -> np.dtype | None:
    code_by_mode = {0: "i1", 1: "i2", 2: "f4", 6: "u2"}
    code = code_by_mode.get(mode)
    if code is None:
        return None
    if code == "i1":
        return np.dtype(code)
    return np.dtype(f"{endian}{code}")


def _data_kind(nz: int) -> str:
    if nz <= 1:
        return "2D"
    return "2D stack"
