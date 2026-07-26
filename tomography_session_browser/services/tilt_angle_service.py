from __future__ import annotations

import logging
import math
from pathlib import Path
from typing import Any

from tomography_session_browser.domain.models import TiltSeries


LOGGER = logging.getLogger(__name__)


def stack_order_tilt_angles(
    tilt_series: TiltSeries,
    frame_count: int | None = None,
    *,
    allow_inferred: bool = False,
) -> tuple[list[float], str] | None:
    """Return tilt angles in image-stack order.

    Priority is intentionally MRC-first: the extended header is tied directly
    to the stack and is the display-order authority when valid. MDOC is a
    fallback only; it must not reorder a valid MRC stack. Inferred angles are
    disabled by default because frame labels should not present guessed tilt
    angles as measured metadata.
    """

    entries = _stack_order_tilt_angle_entries(tilt_series, frame_count, allow_inferred=allow_inferred)
    if entries is None:
        return None
    angles, _metadata_indices, source = entries
    return angles, source


def explicit_stack_order_tilt_angles(
    tilt_series: TiltSeries,
    frame_count: int | None = None,
) -> tuple[list[float], str] | None:
    """Return only explicit per-frame tilt metadata, never inferred ranges."""

    return stack_order_tilt_angles(tilt_series, frame_count, allow_inferred=False)


def stack_order_mdoc_section_indices(
    tilt_series: TiltSeries,
    frame_count: int | None = None,
) -> list[int | None] | None:
    """Map stack-frame indices to MDOC section indices when that mapping is explicit.

    Tomography 5 writes MDOC sections in dose-symmetric acquisition order while
    its MRC stack is conventionally stored in ascending tilt-angle order.
    Callers that combine per-frame MRC and MDOC metadata must therefore use
    this mapping instead of pairing the two sources by list index.
    """

    frame_count = frame_count or _frame_count_for(tilt_series)
    if frame_count < 1:
        return None
    entries = _mdoc_stack_order_angle_entries(tilt_series, frame_count)
    if entries is None:
        return None
    _angles, metadata_indices = entries
    return metadata_indices


def tilt_angle_metadata_warnings(
    tilt_series: TiltSeries,
    frame_count: int | None = None,
) -> list[str]:
    """Return user-facing warnings about tilt-angle metadata quality.

    The display fallback order is:

    1. Valid MRC extended-header per-frame angles in stack order.
    2. Valid MDOC per-frame angles mapped by complete ZValue when possible.
    3. Valid .rawtlt/.tlt angles in file order.
    4. Frame order with no displayed tilt angles.

    This function does not choose a different source; it explains why weaker
    sources were ignored or why a fallback was needed.
    """

    frame_count = frame_count or _frame_count_for(tilt_series)
    if frame_count < 1:
        return ["Tilt-angle metadata unavailable because the stack frame count is unknown."]

    warnings: list[str] = []
    mrc_angles = _mrc_extended_header_angles(tilt_series, frame_count)
    raw_mrc_angles = list(getattr(tilt_series.mrc_metadata, "tilt_angles", []) or [])
    if raw_mrc_angles and mrc_angles is None:
        if len(raw_mrc_angles) != frame_count:
            warnings.append(
                f"MRC extended-header tilt-angle count ({len(raw_mrc_angles)}) does not match "
                f"stack frame count ({frame_count}); ignoring MRC tilt angles."
            )
        elif not _valid_angles(raw_mrc_angles):
            warnings.append("MRC extended-header tilt angles are invalid or implausible; ignoring them.")

    mdoc_entries = _mdoc_stack_order_angle_entries(tilt_series, frame_count)
    mdoc_angles = mdoc_entries[0] if mdoc_entries is not None else None
    if tilt_series.sections:
        numeric_count = sum(1 for section in tilt_series.sections if _numeric(section.metadata.get("TiltAngle")) is not None)
        if len(tilt_series.sections) != frame_count:
            warnings.append(
                f"MDOC section count ({len(tilt_series.sections)}) does not match stack frame count "
                f"({frame_count}); MDOC tilt angles are fallback-only."
            )
        if numeric_count != len(tilt_series.sections):
            warnings.append(
                f"MDOC contains {numeric_count} numeric TiltAngle values for "
                f"{len(tilt_series.sections)} sections."
            )
        if len(tilt_series.sections) == frame_count and mdoc_entries is None:
            warnings.append("MDOC tilt angles are incomplete or invalid; ignoring MDOC tilt angles.")

    tlt_angles = _tlt_stack_order_angles(tilt_series, frame_count)
    warnings.extend(_tlt_metadata_warnings(tilt_series, frame_count, tlt_angles))

    if mrc_angles is not None:
        if mdoc_angles is not None and not _angles_close(mrc_angles, mdoc_angles):
            if _angle_sets_close(mrc_angles, mdoc_angles):
                warnings.append(
                    "MDOC tilt angles match the MRC extended-header range but are recorded in a different order; "
                    "using MRC extended header for stack-frame labels."
                )
            else:
                warnings.append("Tilt-angle conflict: MRC extended header and MDOC disagree; using MRC extended header.")
        if tlt_angles is not None and not _angles_close(mrc_angles, tlt_angles):
            warnings.append("Tilt-angle conflict: MRC extended header and TLT file disagree; using MRC extended header.")
    elif mdoc_angles is not None and tlt_angles is not None and not _angles_close(mdoc_angles, tlt_angles):
        warnings.append("Tilt-angle conflict: MDOC and TLT file disagree; using MDOC fallback.")

    if stack_order_tilt_angles(tilt_series, frame_count, allow_inferred=False) is None:
        warnings.append("No valid per-frame tilt-angle metadata found; displaying frames in stack order without angle labels.")

    return _dedupe_preserve_order(warnings)


def _stack_order_tilt_angle_entries(
    tilt_series: TiltSeries,
    frame_count: int | None = None,
    *,
    allow_inferred: bool = True,
) -> tuple[list[float], list[int | None], str] | None:
    frame_count = frame_count or _frame_count_for(tilt_series)
    if frame_count < 1:
        return None

    mrc_angles = _mrc_extended_header_angles(tilt_series, frame_count)
    if mrc_angles is not None:
        _log_stack_order(
            tilt_series,
            mrc_angles,
            "MRC extended header",
            fallback=False,
        )
        return mrc_angles, list(range(len(mrc_angles))), "MRC extended header"

    mdoc_entries = _mdoc_stack_order_angle_entries(tilt_series, frame_count)
    if mdoc_entries is not None:
        angles, metadata_indices = mdoc_entries
        LOGGER.debug(
            "Using MDOC fallback tilt angles because MRC extended-header angles are unavailable or invalid path=%s frames=%s order=%s",
            tilt_series.mrc_path,
            frame_count,
            _detected_stack_order(angles),
        )
        _log_stack_order(tilt_series, angles, "MDOC fallback", fallback=True)
        return angles, metadata_indices, "MDOC fallback"

    tlt_angles = _tlt_stack_order_angles(tilt_series, frame_count)
    if tlt_angles is not None:
        LOGGER.debug(
            "Using TLT fallback tilt angles because MRC extended-header/MDOC angles are unavailable path=%s frames=%s order=%s",
            tilt_series.mrc_path,
            frame_count,
            _detected_stack_order(tlt_angles),
        )
        _log_stack_order(tilt_series, tlt_angles, "TLT fallback", fallback=True)
        return tlt_angles, list(range(len(tlt_angles))), "TLT fallback"

    if not allow_inferred:
        return None
    inferred = _inferred_angles(tilt_series, frame_count)
    if inferred is None:
        return None
    angles, source = inferred
    _log_stack_order(tilt_series, angles, source, fallback=True)
    return angles, [None] * len(angles), source


def _frame_count_for(tilt_series: TiltSeries) -> int:
    metadata = tilt_series.mrc_metadata
    if metadata is not None and metadata.nz:
        return metadata.nz
    return len(tilt_series.sections)


def _mdoc_stack_order_angles(tilt_series: TiltSeries, frame_count: int) -> list[float] | None:
    entries = _mdoc_stack_order_angle_entries(tilt_series, frame_count)
    if entries is None:
        return None
    angles, _metadata_indices = entries
    return angles


def _mdoc_stack_order_angle_entries(tilt_series: TiltSeries, frame_count: int) -> tuple[list[float], list[int | None]] | None:
    if len(tilt_series.sections) != frame_count:
        return None
    z_ordered: list[tuple[int, int, float]] = []
    file_order_angles: list[float] = []
    file_order_metadata_indices: list[int | None] = []
    for section_index, section in enumerate(tilt_series.sections):
        angle = _numeric(section.metadata.get("TiltAngle"))
        if angle is None:
            return None
        file_order_angles.append(angle)
        file_order_metadata_indices.append(section_index)
        z_ordered.append((section.z_value, section_index, angle))

    if _is_tomography5_stack_mdoc(tilt_series):
        # Tomography 5 stack mdocs describe the dose-symmetric acquisition
        # order in their ZValue/file order (0, +3, -3...), while the generated
        # .mrc stack is conventionally written in tilt-angle order. This
        # mirrors Scipion's stack-import path, which sorts mdoc tilt metadata
        # by TiltAngle after reading acquisition-order Z sections. Non-Tomo5
        # SerialEM mdocs keep the explicit ZValue-to-stack-index mapping below.
        sorted_entries = sorted(
            ((angle, section_index) for _z_value, section_index, angle in z_ordered),
            key=lambda item: item[0],
        )
        LOGGER.debug(
            "Using Tomography 5 MDOC tilt-angle order path=%s frames=%s order=%s",
            tilt_series.mrc_path,
            frame_count,
            _detected_stack_order([angle for angle, _section_index in sorted_entries]),
        )
        return [angle for angle, _section_index in sorted_entries], [section_index for _angle, section_index in sorted_entries]

    # Prefer explicit ZValue-to-stack-index mapping when it is complete. This
    # uses MDOC as stack metadata, not as the display/review order.
    z_values = [z_value for z_value, _section_index, _angle in z_ordered]
    if sorted(z_values) == list(range(frame_count)) and len(set(z_values)) == frame_count:
        angles = [0.0] * frame_count
        metadata_indices: list[int | None] = [None] * frame_count
        for z_value, section_index, angle in z_ordered:
            angles[z_value] = angle
            metadata_indices[z_value] = section_index
        return angles, metadata_indices
    LOGGER.debug(
        "MDOC tilt angles lack complete ZValue stack mapping; falling back to section file order path=%s frames=%s",
        tilt_series.mrc_path,
        frame_count,
    )
    return file_order_angles, file_order_metadata_indices


def _is_tomography5_stack_mdoc(tilt_series: TiltSeries) -> bool:
    """Return whether the parsed MDOC follows Tomography/Tomography 5 stack conventions.

    Scipion handles these stack mdocs by reading acquisition-order Z sections
    and then sorting the stack metadata by tilt angle. We only apply that
    behaviour when the header identifies Tomography/Tomography 5 and the mdoc
    ImageFile points at the current stack, so plain SerialEM mdocs still use
    their ZValue stack mapping.
    """

    header = tilt_series.metadata or {}
    section_lines = " ".join(str(item) for item in header.get("_sections", []) or ())
    if "tomography" not in section_lines.casefold():
        return False
    image_file = header.get("ImageFile")
    if image_file is None or tilt_series.mrc_path is None:
        return True
    image_name = Path(str(image_file)).name.casefold()
    stack_name = Path(tilt_series.mrc_path).name.casefold()
    return image_name == stack_name


def _mrc_extended_header_angles(tilt_series: TiltSeries, frame_count: int) -> list[float] | None:
    metadata = tilt_series.mrc_metadata
    if (
        metadata is not None
        and metadata.tilt_angle_source == "MRC extended header"
        and len(metadata.tilt_angles) == frame_count
        and _valid_angles(metadata.tilt_angles)
    ):
        return list(metadata.tilt_angles)
    return None


def _tlt_stack_order_angles(tilt_series: TiltSeries, frame_count: int) -> list[float] | None:
    mrc_path = tilt_series.mrc_path
    if mrc_path is None:
        return None
    for suffix in (".rawtlt", ".tlt"):
        path = Path(mrc_path).with_suffix(suffix)
        angles = _read_angle_lines(path)
        if angles is not None and len(angles) == frame_count and _valid_angles(angles):
            return angles
    return None


def _inferred_angles(tilt_series: TiltSeries, frame_count: int) -> tuple[list[float], str] | None:
    if tilt_series.tilt_range is None or frame_count < 2:
        return None
    low, high = tilt_series.tilt_range
    if low >= 0 or high <= 0:
        return None
    step = (high - low) / (frame_count - 1)
    if step <= 0:
        return None
    return [low + (step * index) for index in range(frame_count)], "inferred"


def _numeric(value: Any) -> float | None:
    if isinstance(value, int | float):
        return float(value)
    return None


def _read_angle_lines(path: Path) -> list[float] | None:
    if not path.exists():
        return None
    values: list[float] = []
    try:
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            values.append(float(stripped.split()[0]))
    except (OSError, ValueError):
        return None
    return values


def _tlt_metadata_warnings(tilt_series: TiltSeries, frame_count: int, accepted: list[float] | None) -> list[str]:
    mrc_path = tilt_series.mrc_path
    if mrc_path is None:
        return []
    warnings: list[str] = []
    for suffix in (".rawtlt", ".tlt"):
        path = Path(mrc_path).with_suffix(suffix)
        if not path.exists():
            continue
        values = _read_angle_lines(path)
        if values is None:
            warnings.append(f"{path.name} could not be parsed as numeric tilt angles; ignoring it.")
        elif len(values) != frame_count:
            warnings.append(
                f"{path.name} tilt-angle count ({len(values)}) does not match stack frame count ({frame_count}); ignoring it."
            )
        elif not _valid_angles(values):
            warnings.append(f"{path.name} tilt angles are invalid or implausible; ignoring them.")
        elif accepted is not None and _angles_close(values, accepted):
            break
    return warnings


def _valid_angles(values: list[float] | tuple[float, ...]) -> bool:
    if len(values) < 2:
        return False
    return all(isinstance(value, int | float) and math.isfinite(float(value)) and -90 <= float(value) <= 90 for value in values) and (
        max(values) - min(values) >= 1
    )


def _angles_close(left: list[float], right: list[float], *, tolerance: float = 0.05) -> bool:
    if len(left) != len(right):
        return False
    return all(abs(a - b) <= tolerance for a, b in zip(left, right))


def _angle_sets_close(left: list[float], right: list[float], *, tolerance: float = 0.05) -> bool:
    if len(left) != len(right):
        return False
    return _angles_close(sorted(left), sorted(right), tolerance=tolerance)


def _detected_stack_order(values: list[float]) -> str:
    if len(values) < 2:
        return "single-frame"
    increasing = all(next_value >= value for value, next_value in zip(values, values[1:]))
    decreasing = all(next_value <= value for value, next_value in zip(values, values[1:]))
    crosses_zero = min(values) <= 0 <= max(values)
    if increasing:
        return "negative-to-positive" if crosses_zero else "ascending"
    if decreasing:
        return "positive-to-negative" if crosses_zero else "descending"
    return "non-monotonic"


def _log_stack_order(tilt_series: TiltSeries, angles: list[float], source: str, *, fallback: bool) -> None:
    if not LOGGER.isEnabledFor(logging.DEBUG):
        return
    mapping_preview = [
        {"stack_index": index, "display_index": index, "tilt_angle": round(float(angle), 4)}
        for index, angle in enumerate(angles[:12])
    ]
    LOGGER.debug(
        "Tilt stack order source=%s fallback=%s path=%s frames=%s order=%s first=%.4f last=%.4f mapping_preview=%s",
        source,
        fallback,
        tilt_series.mrc_path,
        len(angles),
        _detected_stack_order(angles),
        float(angles[0]),
        float(angles[-1]),
        mapping_preview,
    )


def _dedupe_preserve_order(values: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        out.append(value)
    return out
