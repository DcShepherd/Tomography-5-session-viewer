from __future__ import annotations

import logging
import math
from dataclasses import replace
from pathlib import Path
from typing import Any

from tomography_session_browser.domain.models import TiltSeries
from tomography_session_browser.parsers.mdoc_parser import numeric_values, parse_mdoc
from tomography_session_browser.parsers.mrc_parser import read_mrc_metadata
from tomography_session_browser.parsers.path_utils import natural_key, safe_id, sorted_paths
from tomography_session_browser.services.acquisition_metadata import (
    ACQUISITION_SPOT_LABEL,
    LEGACY_SPOT_LABEL,
    AcquisitionSetting,
    acquisition_setting_source,
    acquisition_setting_value,
    apply_acquisition_settings,
)
from tomography_session_browser.services.tilt_angle_service import plausible_tilt_angles, tilt_angle_metadata_warnings

LOGGER = logging.getLogger(__name__)


def parse_tilt_series_in_folder(path: Path, warnings: list[str] | None = None) -> list[TiltSeries]:
    series: list[TiltSeries] = []
    try:
        mdoc_paths = {
            _mdoc_stack_stem(item): item
            for item in sorted_paths(list(path.glob("*.mdoc")))
        }
        mrc_paths = {item.stem: item for item in path.glob("*.mrc")}
    except OSError as exc:
        message = f"Could not list tilt-series folder: {exc}"
        if warnings is not None:
            warnings.append(message)
        LOGGER.warning("Could not list tilt-series folder %s: %s", path, exc)
        return []
    stems = sorted(mdoc_paths.keys() | mrc_paths.keys(), key=natural_key)

    for stem in stems:
        mrc_path = mrc_paths.get(stem)
        if mrc_path is None:
            if warnings is not None:
                warnings.append(
                    f"{mdoc_paths[stem].name} has no matching MRC stack and was skipped."
                )
            continue
        try:
            series.append(parse_tilt_series(mrc_path, mdoc_paths.get(stem)))
        except Exception as exc:  # pragma: no cover - defensive boundary for malformed user data
            message = f"{mrc_path.name} could not be parsed and was skipped: {exc}"
            if warnings is not None:
                warnings.append(message)
            LOGGER.warning(message, exc_info=True)

    return sorted(series, key=lambda item: natural_key(item.name))


def parse_tilt_series(mrc_path: Path, mdoc_path: Path | None = None) -> TiltSeries:
    warnings: list[str] = []
    header = {}
    sections = []
    if mdoc_path is not None and mdoc_path.exists():
        header, sections, mdoc_warnings = parse_mdoc(mdoc_path)
        warnings.extend(mdoc_warnings)
        for section in sections:
            warnings.extend(section.warnings)
    else:
        warnings.append("No matching MDOC file found.")

    tilt_angles = numeric_values(sections, "TiltAngle")
    if len(tilt_angles) != len(sections) or not plausible_tilt_angles(tilt_angles):
        tilt_angles = []
    defocus_values = numeric_values(sections, "Defocus")
    target_defocus_values = numeric_values(sections, "TargetDefocus")
    dates = [str(section.metadata["DateTime"]) for section in sections if section.metadata.get("DateTime")]
    spot_size_setting = _mdoc_spot_size_setting(header, sections)
    if spot_size_setting is not None:
        apply_acquisition_settings(
            header,
            {
                ACQUISITION_SPOT_LABEL: spot_size_setting,
                LEGACY_SPOT_LABEL: replace(spot_size_setting, label=LEGACY_SPOT_LABEL),
            },
            overwrite=True,
        )

    original_pixel_size = _first_numeric(header.get("PixelSpacing"))
    if original_pixel_size is None and sections:
        original_pixel_size = _first_numeric(sections[0].metadata.get("PixelSpacing"))
    binning = _positive_int(header.get("Binning"))
    if binning is None and sections:
        binning = _positive_int(sections[0].metadata.get("Binning"))
    mrc_metadata = read_mrc_metadata(mrc_path)
    warnings.extend(mrc_metadata.warnings)
    mrc_pixel_size = _mrc_pixel_size_angstrom(mrc_metadata, warnings)
    mdoc_pixel_size = (
        original_pixel_size * (binning or 1)
        if original_pixel_size is not None
        else None
    )
    pixel_size = _resolved_tilt_pixel_size(
        mdoc_pixel_size=mdoc_pixel_size,
        original_pixel_size=original_pixel_size,
        binning=binning,
        mrc_pixel_size=mrc_pixel_size,
        mrc_path=mrc_path,
        warnings=warnings,
    )
    if sections and mrc_metadata.nz and len(sections) != mrc_metadata.nz:
        warnings.append(
            f"MDOC section count ({len(sections)}) disagrees with MRC nz ({mrc_metadata.nz}); "
            "validation uses the shared tilt-series validator."
        )
    _apply_mrc_acquisition_spot(header, mrc_metadata, mrc_path, warnings)
    image_count = mrc_metadata.available_frames
    if image_count is None:
        image_count = mrc_metadata.nz if mrc_metadata.nz is not None else len(sections) or None

    tilt_series = TiltSeries(
        id=safe_id(mrc_path),
        name=mrc_path.stem,
        mrc_path=mrc_path,
        mdoc_path=mdoc_path if mdoc_path and mdoc_path.exists() else None,
        tilt_count=image_count,
        tilt_range=(min(tilt_angles), max(tilt_angles)) if tilt_angles else None,
        pixel_size=pixel_size,
        original_pixel_size=original_pixel_size,
        binning=binning,
        defocus=sum(defocus_values) / len(defocus_values) if defocus_values else None,
        target_defocus=sum(target_defocus_values) / len(target_defocus_values) if target_defocus_values else None,
        number_of_frames=image_count,
        acquisition_time_start=dates[0] if dates else None,
        acquisition_time_end=dates[-1] if dates else None,
        metadata=header,
        mrc_metadata=mrc_metadata,
        sections=sections,
        warnings=warnings,
    )
    tilt_series.warnings.extend(tilt_angle_metadata_warnings(tilt_series))
    return tilt_series


def find_unpaired_mrcs(path: Path) -> list[Path]:
    return sorted_paths(
        [
            item
            for item in path.glob("*.mrc")
            if not any(candidate.exists() for candidate in _mdoc_candidates(item))
        ]
    )


def _mdoc_stack_stem(path: Path) -> str:
    name = path.name
    suffix = ".mrc.mdoc"
    if name.casefold().endswith(suffix):
        return name[: -len(suffix)]
    return path.stem


def _mdoc_candidates(mrc_path: Path) -> tuple[Path, Path]:
    return mrc_path.with_suffix(".mdoc"), Path(f"{mrc_path}.mdoc")


def _mrc_pixel_size_angstrom(metadata: Any, warnings: list[str]) -> float | None:
    frame_values: list[float] = []
    for frame in getattr(metadata, "frame_metadata", None) or []:
        value = frame.get("pixel_size") if isinstance(frame, dict) else None
        try:
            numeric = float(value) if value is not None else None
        except (TypeError, ValueError):
            numeric = None
        if numeric is not None and math.isfinite(numeric) and numeric > 0:
            frame_values.append(numeric)
    if frame_values:
        reference = frame_values[0]
        if any(
            not math.isclose(value, reference, rel_tol=1e-6, abs_tol=1e-6)
            for value in frame_values[1:]
        ):
            text = ", ".join(f"{value:g}" for value in sorted(set(frame_values)))
            warnings.append(
                "MRC extended header has inconsistent pixel_size values: "
                f"{text}; pixel-size cross-check unavailable."
            )
            return None
        return reference
    voxel_size = getattr(metadata, "voxel_size", (None, None, None))
    value = voxel_size[0] if voxel_size else None
    try:
        numeric = float(value) if value is not None else None
    except (TypeError, ValueError):
        return None
    return numeric if numeric is not None and math.isfinite(numeric) and numeric > 0 else None


def _resolved_tilt_pixel_size(
    *,
    mdoc_pixel_size: float | None,
    original_pixel_size: float | None,
    binning: int | None,
    mrc_pixel_size: float | None,
    mrc_path: Path,
    warnings: list[str],
) -> float | None:
    if mrc_pixel_size is None:
        return mdoc_pixel_size
    if mdoc_pixel_size is None:
        return mrc_pixel_size
    if math.isclose(mdoc_pixel_size, mrc_pixel_size, rel_tol=0.01, abs_tol=0.01):
        return mdoc_pixel_size
    if original_pixel_size is not None and binning is None:
        warnings.append(
            "MDOC Binning is missing; using "
            f"{mrc_path.name} pixel size {mrc_pixel_size:g} Å/pixel instead of "
            f"unbinned PixelSpacing {original_pixel_size:g} Å/pixel."
        )
    else:
        warnings.append(
            "Pixel size conflict: MDOC PixelSpacing × Binning gives "
            f"{mdoc_pixel_size:g} Å/pixel; {mrc_path.name} reports "
            f"{mrc_pixel_size:g} Å/pixel; using MRC metadata."
        )
    return mrc_pixel_size


def _first_numeric(value: object) -> float | None:
    if isinstance(value, int | float):
        return float(value)
    if isinstance(value, list):
        for item in value:
            if isinstance(item, int | float):
                return float(item)
    return None


def _positive_int(value: object) -> int | None:
    if isinstance(value, int) and value > 0:
        return value
    if isinstance(value, float) and value.is_integer() and value > 0:
        return int(value)
    if isinstance(value, list):
        for item in value:
            parsed = _positive_int(item)
            if parsed is not None:
                return parsed
    return None


def _mdoc_spot_size_setting(header: dict[str, object], sections: list) -> AcquisitionSetting | None:
    for key in ("SpotSize", "SpotIndex"):
        value = _positive_int(header.get(key))
        if value is not None:
            return AcquisitionSetting(ACQUISITION_SPOT_LABEL, f"{value:g}", "MDOC", key)
        values = sorted(
            {
                parsed
                for section in sections
                if (parsed := _positive_int(section.metadata.get(key))) is not None
            }
        )
        if len(values) == 1:
            return AcquisitionSetting(ACQUISITION_SPOT_LABEL, f"{values[0]:g}", "MDOC", key)
        if len(values) > 1:
            text = ", ".join(f"{value:g}" for value in values[:4])
            if len(values) > 4:
                text = f"Mixed ({len(values)} values)"
            return AcquisitionSetting(ACQUISITION_SPOT_LABEL, text, "MDOC", key)
    return None


def _apply_mrc_acquisition_spot(
    header: dict[str, object],
    mrc_metadata: Any,
    mrc_path: Path,
    warnings: list[str],
) -> None:
    value = _mrc_spot_index(mrc_metadata, warnings)
    if value is None:
        return
    setting = AcquisitionSetting(
        label=ACQUISITION_SPOT_LABEL,
        value=value,
        source=f"{mrc_path.name} FEI extended header",
        field="spot_index",
        source_path=str(mrc_path),
        source_kind="mrc_fei_extended_header",
        field_path="frame_metadata/raw_fields/spot_index",
        authority="fei_extended_header",
    )
    _apply_header_setting(header, setting, overwrite=True, warnings=warnings)
    _apply_header_setting(header, replace(setting, label=LEGACY_SPOT_LABEL), overwrite=True, warnings=warnings)


def _apply_header_setting(
    header: dict[str, object],
    setting: AcquisitionSetting,
    *,
    overwrite: bool,
    warnings: list[str],
) -> None:
    previous_value = acquisition_setting_value(header, setting.label)
    previous_source = acquisition_setting_source(header, setting.label)
    if setting.label != LEGACY_SPOT_LABEL and previous_value is not None and previous_value != setting.value:
        warnings.append(
            f"{setting.label} conflict: {setting.source} {setting.field}={setting.value} "
            f"overrides {previous_source or 'existing metadata'}={previous_value}."
        )
    apply_acquisition_settings(header, {setting.label: setting}, overwrite=overwrite)


def _mrc_spot_index(mrc_metadata: Any, warnings: list[str]) -> str | None:
    values: set[int] = set()
    for frame in getattr(mrc_metadata, "frame_metadata", None) or []:
        raw_fields = frame.get("raw_fields") if isinstance(frame, dict) else None
        if not isinstance(raw_fields, dict):
            continue
        parsed = _positive_int(raw_fields.get("spot_index"))
        if parsed is not None:
            values.add(parsed)
    if not values:
        return None
    if len(values) > 1:
        text = ", ".join(str(value) for value in sorted(values))
        warnings.append(f"Tilt-series FEI extended header has inconsistent spot_index values: {text}.")
        return None
    return str(next(iter(values)))
