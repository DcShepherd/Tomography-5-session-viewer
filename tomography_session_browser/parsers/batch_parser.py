from __future__ import annotations

import re
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from tomography_session_browser.domain.models import BatchPosition
from tomography_session_browser.parsers.mrc_parser import read_mrc_metadata
from tomography_session_browser.parsers.path_utils import safe_id, sorted_paths
from tomography_session_browser.parsers.xml_parser import as_list, find_all, parse_xml_file
from tomography_session_browser.services.acquisition_metadata import (
    ACQUISITION_SPOT_LABEL,
    LEGACY_SPOT_LABEL,
    SEARCH_SPOT_LABEL,
    AcquisitionSetting,
    acquisition_setting_source,
    acquisition_setting_value,
    apply_acquisition_settings,
    extract_acquisition_settings,
    extract_batch_position_settings,
)


BATCH_IMAGE_RE = re.compile(r"^(?P<base>.+)_(?P<kind>Search|Tracking|Exposure)\.mrc$", re.IGNORECASE)
BEAM_DIAMETER_LOW_DOSE_CONTEXT = "backward_alignment_optics"
BEAM_DIAMETER_MICROSCOPE_CONTEXT = "microscope_data_optics"

# Canonical source for the tilt-series acquisition beam. The TFS/FEI
# extended header of an exposure MRC records ``illuminated_area`` (offset
# 313, bitmask 2,2) as the beam diameter in meters at the specimen plane,
# captured at the exposure-state optics (high mag, low-dose). It is the
# only source that reflects the beam that actually hits each tilt frame.
BEAM_DIAMETER_EXPOSURE_MRC_CONTEXT = "exposure_mrc_illuminated_area"


@dataclass(frozen=True, slots=True)
class _BeamDiameterCandidate:
    value: float | str
    field: str
    context: str
    priority: int
    order: int


def parse_batch_folder(path: Path, warnings: list[str] | None = None) -> list[BatchPosition]:
    if not path.exists() or not path.is_dir():
        return []

    positions = _positions_from_xml(path / "BatchPositionsList.xml", warnings)
    by_name = {position.name: position for position in positions if position.name}

    # Priority for duplicated acquisition-setting fields is:
    # per-position *_Search.xml > folder search.xml > batchpositions.xml >
    # BatchPositionsList.xml. The first two are Tomography 5 search/acquisition
    # metadata, while the batch-position files are useful fallbacks for
    # beam/spot/probe fields.
    _apply_batch_settings_file(path / "batchpositions.xml", positions, overwrite=True)

    for file_path in sorted_paths(list(path.glob("*.mrc"))):
        match = BATCH_IMAGE_RE.match(file_path.name)
        if not match:
            continue

        base = match.group("base")
        kind = match.group("kind").lower()
        position = by_name.get(base)
        if position is None and kind == "exposure":
            position = by_name.get(_strip_repeated_exposure_index(base))
        if position is None:
            position = BatchPosition(id=safe_id(file_path.parent / base), name=base)
            positions.append(position)
            by_name[base] = position

        if kind == "search":
            position.search_image_path = file_path
            position.search_mrc_metadata = read_mrc_metadata(file_path)
            search_xml = file_path.with_suffix(".xml")
            if search_xml.exists():
                position.search_metadata_path = search_xml
        elif kind == "tracking":
            position.tracking_image_path = file_path
            position.tracking_mrc_metadata = read_mrc_metadata(file_path)
        elif kind == "exposure":
            position.exposure_image_paths.append(file_path)
            position.exposure_image_path = position.exposure_image_path or file_path
            position.exposure_mrc_metadata = position.exposure_mrc_metadata or read_mrc_metadata(file_path)

    _apply_global_settings_file(path / "search.xml", positions, overwrite=True)
    for position in positions:
        if position.search_metadata_path is not None:
            _apply_global_settings_file(position.search_metadata_path, [position], overwrite=True)

    # The exposure MRC's FEI extended header records acquisition-time
    # microscope state for the tilt series. Search/batch XML can describe
    # different low-dose contexts, so keep both contexts instead of letting a
    # generic SpotIndex overwrite the acquisition spot value.
    _apply_mrc_acquisition_metadata(positions)

    for position in positions:
        if not position.search_image_path:
            position.warnings.append("No search image found for batch position.")
        if not position.tracking_image_path:
            position.warnings.append("No tracking image found for batch position.")
        if not position.exposure_image_paths:
            position.warnings.append("No exposure image found for batch position.")

    return positions


def _positions_from_xml(path: Path, warnings: list[str] | None = None) -> list[BatchPosition]:
    if not path.exists():
        return []

    data = parse_xml_file(path)
    message = _xml_warning(path, data)
    if message and warnings is not None:
        warnings.append(message)
    raw_positions = _scoped_batch_position_values(data, "BatchPositionParameters")
    positions: list[BatchPosition] = []
    for index, raw in enumerate(raw_positions):
        if not isinstance(raw, dict):
            continue
        name = _as_str(raw.get("Name")) or f"BatchPosition{index + 1}"
        overview_name = _as_str(raw.get("OverviewImageName"))
        metadata = {key: value for key, value in raw.items() if key not in {"Name", "BatchPositionStatus"}}
        position = BatchPosition(
            id=name,
            name=name,
            status=_as_str(raw.get("BatchPositionStatus")),
            linked_overview_id=overview_name,
            metadata=metadata,
        )
        settings = extract_acquisition_settings(raw, source=path.name, acquisition_scope=True)
        apply_acquisition_settings(position.metadata, settings, overwrite=False)
        positions.append(position)
    return positions


def _apply_global_settings_file(path: Path, positions: list[BatchPosition], *, overwrite: bool) -> None:
    if not path.exists():
        return
    data = parse_xml_file(path)
    _append_xml_warning(path, data, positions)
    _apply_exposure_beam_diameter(data, positions, source=path.name, overwrite=overwrite)
    settings = extract_acquisition_settings(data, source=path.name)
    if not settings:
        return
    for position in positions:
        apply_acquisition_settings(position.metadata, settings, overwrite=overwrite)


def _apply_batch_settings_file(path: Path, positions: list[BatchPosition], *, overwrite: bool) -> None:
    if not path.exists():
        return
    data = parse_xml_file(path)
    _append_xml_warning(path, data, positions)
    _apply_exposure_beam_diameter(data, positions, source=path.name, overwrite=overwrite)
    global_settings, per_position = extract_batch_position_settings(data, source=path.name)
    by_name = {position.name: position for position in positions if position.name}
    if global_settings:
        for position in positions:
            apply_acquisition_settings(position.metadata, global_settings, overwrite=overwrite)
    for name, settings in per_position.items():
        position = by_name.get(name)
        if position is not None:
            apply_acquisition_settings(position.metadata, settings, overwrite=overwrite)


def _apply_exposure_beam_diameter(
    data: dict[str, Any],
    positions: list[BatchPosition],
    *,
    source: str,
    overwrite: bool,
) -> None:
    selected = _beam_diameter_metadata(data)
    if selected is None:
        return
    for position in positions:
        if not overwrite and "BeamDiameter" in position.metadata:
            continue
        position.metadata["BeamDiameter"] = selected.value
        position.metadata["BeamDiameterSource"] = f"{source} {selected.field}"
        position.metadata["BeamDiameterContext"] = selected.context


def _apply_mrc_acquisition_metadata(positions: list[BatchPosition]) -> None:
    _apply_exposure_mrc_beam_diameter(positions)
    _apply_mrc_spot_indices(positions)
    _apply_mrc_detector_and_probe_fallbacks(positions)


def _apply_exposure_mrc_beam_diameter(positions: list[BatchPosition]) -> None:
    """Use the exposure MRC FEI ``illuminated_area`` as the tilt-series beam.

    TFS writes the exposure-state beam diameter (in meters) into every
    Exposure MRC's extended header at acquisition time. That value is the
    physical beam used for every tilt-series frame at this batch position
    and is consistent across the stack. Prefer it over the search/atlas
    XML values, which describe other optics states.
    """

    for position in positions:
        diameter_m = _exposure_mrc_beam_diameter(position)
        if diameter_m is None:
            continue
        position.metadata["BeamDiameter"] = diameter_m
        position.metadata["BeamDiameterSource"] = _exposure_mrc_source_label(position)
        position.metadata["BeamDiameterContext"] = BEAM_DIAMETER_EXPOSURE_MRC_CONTEXT


def _apply_mrc_spot_indices(positions: list[BatchPosition]) -> None:
    for position in positions:
        search_spot = _mrc_spot_index(position.search_mrc_metadata, position.warnings, "search MRC")
        if search_spot is not None:
            setting = AcquisitionSetting(
                label=SEARCH_SPOT_LABEL,
                value=search_spot,
                source=_mrc_spot_source_label(position.search_image_path),
                field="spot_index",
                source_path=str(position.search_image_path) if position.search_image_path else None,
                source_kind="mrc_fei_extended_header",
                field_path="frame_metadata/raw_fields/spot_index",
                authority="fei_extended_header",
                fallback=acquisition_setting_value(position.metadata, SEARCH_SPOT_LABEL) is None,
            )
            _apply_position_setting(position, setting, overwrite=False)

        acquisition_spot = _mrc_spot_index(position.exposure_mrc_metadata, position.warnings, "exposure MRC")
        if acquisition_spot is not None:
            setting = AcquisitionSetting(
                label=ACQUISITION_SPOT_LABEL,
                value=acquisition_spot,
                source=_mrc_spot_source_label(position.exposure_image_path),
                field="spot_index",
                source_path=str(position.exposure_image_path) if position.exposure_image_path else None,
                source_kind="mrc_fei_extended_header",
                field_path="frame_metadata/raw_fields/spot_index",
                authority="fei_extended_header",
            )
            _apply_position_setting(position, setting, overwrite=True)
            _apply_position_setting(position, replace(setting, label=LEGACY_SPOT_LABEL), overwrite=True)


def _apply_mrc_detector_and_probe_fallbacks(positions: list[BatchPosition]) -> None:
    for position in positions:
        metadata = position.exposure_mrc_metadata or position.search_mrc_metadata
        if metadata is None:
            continue
        detector_field = _mrc_raw_text(metadata, ("detector_commercial_name", "camera_name"))
        if detector_field and acquisition_setting_value(position.metadata, "Detector") is None:
            detector, field = detector_field
            path = position.exposure_image_path or position.search_image_path
            _apply_position_setting(
                position,
                AcquisitionSetting(
                    label="Detector",
                    value=detector,
                    source=_mrc_spot_source_label(path),
                    field=field,
                    source_path=str(path) if path else None,
                    source_kind="mrc_fei_extended_header",
                    field_path="frame_metadata/raw_fields",
                    authority="fei_extended_header",
                    fallback=True,
                ),
                overwrite=False,
            )

        probe_field = _mrc_raw_text(metadata, ("probe_mode",))
        if probe_field and acquisition_setting_value(position.metadata, "Probe mode") is None:
            probe_code, field = probe_field
            path = position.exposure_image_path or position.search_image_path
            _apply_position_setting(
                position,
                AcquisitionSetting(
                    label="Probe mode",
                    value=f"FEI code {probe_code}",
                    source=_mrc_spot_source_label(path),
                    field=field,
                    source_path=str(path) if path else None,
                    source_kind="mrc_fei_extended_header",
                    field_path="frame_metadata/raw_fields/probe_mode",
                    authority="fei_extended_header",
                    fallback=True,
                ),
                overwrite=False,
            )


def _apply_position_setting(
    position: BatchPosition,
    setting: AcquisitionSetting,
    *,
    overwrite: bool,
) -> None:
    previous_value = acquisition_setting_value(position.metadata, setting.label)
    previous_source = acquisition_setting_source(position.metadata, setting.label)
    if setting.label != LEGACY_SPOT_LABEL and previous_value is not None and previous_value != setting.value:
        if overwrite:
            message = (
                f"{setting.label} conflict: {setting.source} {setting.field}={setting.value} "
                f"overrides {previous_source or 'existing metadata'}={previous_value}."
            )
        else:
            message = (
                f"{setting.label} conflict: keeping {previous_source or 'existing metadata'}={previous_value}; "
                f"ignored {setting.source} {setting.field}={setting.value}."
            )
        position.warnings.append(message)
    apply_acquisition_settings(position.metadata, {setting.label: setting}, overwrite=overwrite)


def _mrc_spot_index(metadata: Any, warnings: list[str], context: str) -> str | None:
    if metadata is None:
        return None
    values: set[int] = set()
    for frame in getattr(metadata, "frame_metadata", None) or []:
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
        warnings.append(f"{context} FEI extended header has inconsistent spot_index values: {text}.")
        return None
    return str(next(iter(values)))


def _mrc_raw_text(metadata: Any, fields: tuple[str, ...]) -> tuple[str, str] | None:
    for field in fields:
        values: list[str] = []
        for frame in getattr(metadata, "frame_metadata", None) or []:
            raw_fields = frame.get("raw_fields") if isinstance(frame, dict) else None
            if not isinstance(raw_fields, dict):
                continue
            value = raw_fields.get(field)
            if value is not None:
                text = str(value).strip()
                if text:
                    values.append(text)
        unique = sorted(set(values), key=str.casefold)
        if len(unique) == 1:
            return unique[0], field
    return None


def _mrc_spot_source_label(path: Path | None) -> str:
    name = path.name if path is not None else "image.mrc"
    return f"{name} FEI extended header"


def _exposure_mrc_beam_diameter(position: BatchPosition) -> float | None:
    metadata = position.exposure_mrc_metadata
    if metadata is None:
        return None
    frames = getattr(metadata, "frame_metadata", None) or []
    for frame in frames:
        raw_fields = frame.get("raw_fields") if isinstance(frame, dict) else getattr(frame, "raw_fields", None)
        if not raw_fields:
            continue
        value = raw_fields.get("illuminated_area") if isinstance(raw_fields, dict) else None
        try:
            numeric = float(value) if value is not None else None
        except (TypeError, ValueError):
            numeric = None
        if numeric is not None and numeric > 0:
            return numeric
    return None


def _exposure_mrc_source_label(position: BatchPosition) -> str:
    path = position.exposure_image_path
    name = path.name if path is not None else "exposure.mrc"
    return f"{name} FEI extended header illuminated_area"


def _beam_diameter_metadata(data: dict[str, Any]) -> _BeamDiameterCandidate | None:
    candidates = list(_beam_diameter_candidates(data))
    if not candidates:
        return None
    candidates.sort(key=lambda item: (item.priority, item.order))
    return candidates[0]


def _beam_diameter_candidates(data: Any, path: tuple[str, ...] = (), order: list[int] | None = None):
    if order is None:
        order = [0]
    if isinstance(data, dict):
        key_label = _as_nonempty_str(data.get("Key"))
        if key_label and "Value" in data:
            yield from _beam_diameter_candidates(data.get("Value"), (*path, key_label), order)
        for key, item in data.items():
            if key.startswith("_"):
                continue
            current_path = (*path, key)
            if key == "BeamDiameter":
                value = _beam_diameter_value(item)
                if value is not None:
                    priority, context = _beam_diameter_priority(current_path)
                    yield _BeamDiameterCandidate(
                        value=value,
                        field="/".join(current_path),
                        context=context,
                        priority=priority,
                        order=order[0],
                    )
                    order[0] += 1
            else:
                yield from _beam_diameter_candidates(item, current_path, order)
    elif isinstance(data, list):
        for item in data:
            yield from _beam_diameter_candidates(item, path, order)


def _beam_diameter_priority(path: tuple[str, ...]) -> tuple[int, str]:
    normalised = tuple(part.casefold() for part in path)
    if "backwardalignmenttransformation" in normalised and "optics" in normalised:
        return 0, BEAM_DIAMETER_LOW_DOSE_CONTEXT
    if "microscopedata" in normalised and "optics" in normalised:
        return 2, BEAM_DIAMETER_MICROSCOPE_CONTEXT
    if "optics" in normalised:
        return 3, "optics"
    return 4, "unknown"


def _beam_diameter_value(value: Any) -> float | str | None:
    if isinstance(value, dict):
        value = value.get("value")
    if value is None:
        return None
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            return None
        try:
            value = float(stripped)
        except ValueError:
            return stripped
    if isinstance(value, (int, float)) and value <= 0:
        return None
    return value


def _as_nonempty_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _positive_int(value: Any) -> int | None:
    try:
        numeric = int(value)
    except (TypeError, ValueError):
        return None
    return numeric if numeric > 0 else None


def _scoped_batch_position_values(data: dict[str, Any], key: str) -> list[Any]:
    values: list[Any] = []
    for path, value in find_all(data, key):
        if not _valid_batch_position_path(path, key):
            continue
        values.extend(as_list(value))
    return values


def _valid_batch_position_path(path: tuple[str, ...], key: str) -> bool:
    if not path or path[-1] != key:
        return False
    if len(path) == 1:
        return True
    parent = re.sub(r"[^a-z0-9]", "", path[-2].casefold())
    return parent in {
        "batchpositions",
        "batchpositionslist",
        "batchpositionlist",
        "positions",
        "arrayofbatchpositionparameters",
        "root",
    }


def _append_xml_warning(path: Path, data: dict[str, Any], positions: list[BatchPosition]) -> None:
    warning = _xml_warning(path, data)
    if not warning:
        return
    for position in positions:
        position.warnings.append(warning)


def _xml_warning(path: Path, data: dict[str, Any]) -> str | None:
    message = data.get("_parse_error") or data.get("_read_error")
    if not message:
        return None
    return f"Could not parse acquisition metadata XML {path.name}: {message}"


def _strip_repeated_exposure_index(value: str) -> str:
    return re.sub(r"_\d+$", "", value)


def _as_str(value: Any) -> str | None:
    if value is None:
        return None
    return str(value)
