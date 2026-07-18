from __future__ import annotations

import re
from dataclasses import dataclass, replace
from typing import Any, Iterable, Mapping, Sequence

from tomography_session_browser.parsers.xml_parser import as_list, find_all


ACQUISITION_METADATA_KEY = "AcquisitionSettings"
DEFOCUS_GROUPING_TOLERANCE_UM = 0.01
SEARCH_SPOT_LABEL = "Search spot size"
ACQUISITION_SPOT_LABEL = "Acquisition spot size"
LEGACY_SPOT_LABEL = "Spot size"


@dataclass(frozen=True, slots=True)
class AcquisitionSetting:
    label: str
    value: str
    source: str
    field: str
    internal_key: str | None = None
    alternatives: tuple[tuple[str, str, str | None], ...] = ()
    source_path: str | None = None
    source_kind: str | None = None
    field_path: str | None = None
    authority: str | None = None
    fallback: bool = False
    conflicts: tuple[tuple[str, str, str], ...] = ()


def _normalise_key(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", value.casefold())


_FIELD_ALIASES: dict[str, tuple[str, ...]] = {
    "Detector": (
        "Detector",
        "DetectorType",
        "DetectorName",
        "Camera",
        "CameraName",
        "CameraType",
        "AcquisitionCamera",
        "AcquisitionCameraName",
    ),
    "Probe mode": (
        "ProbeMode",
        "Probe",
        "IlluminationProbeMode",
        "IlluminationMode",
        "AcquisitionProbeMode",
    ),
}

_NORMALISED_ALIASES = {
    label: {_normalise_key(alias) for alias in aliases}
    for label, aliases in _FIELD_ALIASES.items()
}


def extract_acquisition_settings(
    data: Mapping[str, Any],
    *,
    source: str,
    acquisition_scope: bool = False,
) -> dict[str, AcquisitionSetting]:
    """Return detector/acquisition settings found anywhere in parsed XML data."""

    found: dict[str, AcquisitionSetting] = {}
    detector = _extract_detector_setting(data, source=source)
    if detector is not None:
        found["Detector"] = detector
    spot_settings = _extract_spot_index_settings(
        data,
        source=source,
        acquisition_scope=acquisition_scope,
    )
    found.update(spot_settings)
    probe_mode = _extract_probe_mode_setting(data, source=source)
    if probe_mode is not None:
        found["Probe mode"] = probe_mode
    for key, value, _field_path in _walk_key_values(data):
        normalised_key = _normalise_key(key)
        for label, aliases in _NORMALISED_ALIASES.items():
            if label in found or label == "Probe mode" or normalised_key not in aliases:
                continue
            text = _display_value(value)
            if text is None:
                continue
            found[label] = AcquisitionSetting(
                label=label,
                value=text,
                source=source,
                field=key,
            )
    return found


def extract_batch_position_settings(
    data: Mapping[str, Any],
    *,
    source: str,
) -> tuple[dict[str, AcquisitionSetting], dict[str, dict[str, AcquisitionSetting]]]:
    """Return ``(global_settings, per_position_settings)`` from batch XML data."""

    global_settings = extract_acquisition_settings(data, source=source)
    per_position: dict[str, dict[str, AcquisitionSetting]] = {}
    raw_positions = _raw_batch_positions(data)
    for index, raw in enumerate(raw_positions):
        if not isinstance(raw, Mapping):
            continue
        name = _display_value(raw.get("Name")) or _display_value(raw.get("BatchPositionName"))
        if not name:
            name = f"BatchPosition{index + 1}"
        settings = extract_acquisition_settings(raw, source=source, acquisition_scope=True)
        if settings:
            per_position[name] = settings
    return global_settings, per_position


def apply_acquisition_settings(
    metadata: dict[str, Any],
    settings: Mapping[str, AcquisitionSetting],
    *,
    overwrite: bool,
) -> None:
    bucket = metadata.setdefault(ACQUISITION_METADATA_KEY, {})
    if not isinstance(bucket, dict):
        bucket = {}
        metadata[ACQUISITION_METADATA_KEY] = bucket
    for label, setting in settings.items():
        if not overwrite and label in bucket:
            continue
        bucket[label] = {
            "value": setting.value,
            "source": setting.source,
            "field": setting.field,
        }
        if setting.internal_key:
            bucket[label]["internal_key"] = setting.internal_key
        if setting.alternatives:
            bucket[label]["alternatives"] = [
                {
                    "value": value,
                    "field": field,
                    **({"internal_key": internal_key} if internal_key else {}),
                }
                for value, field, internal_key in setting.alternatives
            ]
        if setting.source_path:
            bucket[label]["source_path"] = setting.source_path
        if setting.source_kind:
            bucket[label]["source_kind"] = setting.source_kind
        if setting.field_path:
            bucket[label]["field_path"] = setting.field_path
        if setting.authority:
            bucket[label]["authority"] = setting.authority
        if setting.fallback:
            bucket[label]["fallback"] = True
        if setting.conflicts:
            bucket[label]["conflicts"] = [
                {"value": value, "source": source, "field": field}
                for value, source, field in setting.conflicts
            ]


def merge_acquisition_settings_metadata(
    target: dict[str, Any],
    source: Mapping[str, Any],
    *,
    overwrite: bool,
) -> None:
    source_bucket = source.get(ACQUISITION_METADATA_KEY)
    if not isinstance(source_bucket, Mapping):
        return
    target_bucket = target.setdefault(ACQUISITION_METADATA_KEY, {})
    if not isinstance(target_bucket, dict):
        target_bucket = {}
        target[ACQUISITION_METADATA_KEY] = target_bucket
    for label, entry in source_bucket.items():
        if not overwrite and label in target_bucket:
            continue
        target_bucket[label] = entry


def acquisition_setting_value(metadata: Mapping[str, Any], label: str) -> str | None:
    entry = _setting_entry(metadata, label)
    if entry is None:
        return None
    value = entry.get("value")
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def acquisition_setting_source(metadata: Mapping[str, Any], label: str) -> str | None:
    entry = _setting_entry(metadata, label)
    if entry is None:
        return None
    source = entry.get("source")
    field = entry.get("field")
    if isinstance(source, str) and source.strip() and isinstance(field, str) and field.strip():
        return f"{source} {field}"
    if isinstance(source, str) and source.strip():
        return source.strip()
    return None


def summarise_acquisition_setting(
    metadata_values: Iterable[Mapping[str, Any]],
    label: str,
    *,
    missing: str = "n/a",
    max_list: int = 4,
) -> str:
    values = [
        value
        for metadata in metadata_values
        if (value := acquisition_setting_value(metadata, label)) is not None
    ]
    if not values:
        return missing
    unique = sorted(set(values), key=lambda item: item.casefold())
    if len(unique) == 1:
        return unique[0]
    if len(unique) <= max_list:
        return ", ".join(unique)
    return f"Mixed ({len(unique)} values)"


def format_target_defocus_values(
    values: Iterable[float | int | str | None],
    *,
    missing: str = "n/a",
    unit: str = "µm",
    tolerance: float = DEFOCUS_GROUPING_TOLERANCE_UM,
    max_list: int = 4,
) -> str:
    """Summarise target/applied defocus in µm, grouping float noise at 0.01 µm."""

    numeric = sorted(
        numeric_value
        for value in values
        if (numeric_value := _as_float(value)) is not None
    )
    if not numeric:
        return missing
    grouped = _group_numeric_values(numeric, tolerance)
    if len(grouped) == 1:
        return f"{_format_number(grouped[0])} {unit}"
    if len(grouped) <= max_list:
        return ", ".join(f"{_format_number(value)} {unit}" for value in grouped)
    return f"{_format_number(grouped[0])} {unit} – {_format_number(grouped[-1])} {unit}"


def _raw_batch_positions(data: Mapping[str, Any]) -> list[Any]:
    for key in ("BatchPositionParameters", "BatchPosition", "Position"):
        raw = _scoped_batch_position_values(data, key)
        if raw:
            return raw
    return []


def _scoped_batch_position_values(data: Mapping[str, Any], key: str) -> list[Any]:
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
    parent = _normalise_key(path[-2])
    return parent in {
        "batchpositions",
        "batchpositionslist",
        "batchpositionlist",
        "positions",
        "arrayofbatchpositionparameters",
        "root",
    }


def _setting_entry(metadata: Mapping[str, Any], label: str) -> Mapping[str, Any] | None:
    bucket = metadata.get(ACQUISITION_METADATA_KEY)
    if not isinstance(bucket, Mapping):
        return None
    entry = bucket.get(label)
    if isinstance(entry, Mapping):
        return entry
    for alias in _setting_aliases(label):
        entry = bucket.get(alias)
        if isinstance(entry, Mapping):
            return entry
    return None


def _setting_aliases(label: str) -> tuple[str, ...]:
    if label == LEGACY_SPOT_LABEL:
        return (ACQUISITION_SPOT_LABEL,)
    if label == ACQUISITION_SPOT_LABEL:
        return (LEGACY_SPOT_LABEL,)
    return ()


def _extract_detector_setting(data: Mapping[str, Any], *, source: str) -> AcquisitionSetting | None:
    candidates: list[tuple[int, int, AcquisitionSetting]] = []
    order = 0

    for field, value, _field_path in _iter_xml_key_values(data):
        text = _display_value(value)
        if text is None:
            continue
        priority = _detector_key_priority(field)
        if priority is None:
            continue
        candidates.append(
            (
                priority,
                order,
                AcquisitionSetting(
                    label="Detector",
                    value=text,
                    source=source,
                    field=field,
                    internal_key=_detector_internal_key(field),
                ),
            )
        )
        order += 1

    for field, value, _field_path in _walk_key_values(data):
        if field in {"Key", "Value"}:
            continue
        text = _display_value(value)
        if text is None:
            continue
        priority = _detector_key_priority(field)
        if priority is None:
            continue
        candidates.append(
            (
                priority,
                order,
                AcquisitionSetting(
                    label="Detector",
                    value=text,
                    source=source,
                    field=field,
                    internal_key=_detector_internal_key(field),
                ),
            )
        )
        order += 1

    if not candidates:
        return None
    candidates.sort(key=lambda item: (item[0], item[1]))
    selected = candidates[0][2]
    alternatives: list[tuple[str, str, str | None]] = []
    seen = {(selected.value, selected.field)}
    for _, _, setting in candidates[1:]:
        key = (setting.value, setting.field)
        if key in seen or setting.value == selected.value:
            continue
        alternatives.append((setting.value, setting.field, setting.internal_key))
        seen.add(key)
    if alternatives:
        selected = replace(selected, alternatives=tuple(alternatives))
    return selected


def _extract_spot_index_settings(
    data: Mapping[str, Any],
    *,
    source: str,
    acquisition_scope: bool,
) -> dict[str, AcquisitionSetting]:
    candidates: dict[str, list[tuple[int, int, AcquisitionSetting]]] = {
        SEARCH_SPOT_LABEL: [],
        ACQUISITION_SPOT_LABEL: [],
    }
    order = 0
    for field, value, field_path in _iter_xml_key_values(data):
        text = _display_value(value)
        if text is None:
            continue
        context = _spot_index_context_priority(
            field,
            field_path,
            acquisition_scope=acquisition_scope,
        )
        if context is None:
            continue
        label, priority = context
        candidates[label].append(
            _spot_candidate(
                priority,
                order,
                label=label,
                value=text,
                source=source,
                field=field,
                field_path=field_path,
            )
        )
        order += 1
    for field, value, field_path in _walk_key_values(data):
        if field in {"Key", "Value"}:
            continue
        text = _display_value(value)
        if text is None:
            continue
        context = _spot_index_context_priority(
            field,
            field_path,
            acquisition_scope=acquisition_scope,
        )
        if context is None:
            continue
        label, priority = context
        candidates[label].append(
            _spot_candidate(
                priority,
                order,
                label=label,
                value=text,
                source=source,
                field=field,
                field_path=field_path,
            )
        )
        order += 1
    found: dict[str, AcquisitionSetting] = {}
    for label, items in candidates.items():
        if not items:
            continue
        items.sort(key=lambda item: (item[0], item[1]))
        selected = _with_alternatives(items[0][2], [item[2] for item in items[1:]])
        found[label] = selected
    if ACQUISITION_SPOT_LABEL in found:
        found[LEGACY_SPOT_LABEL] = replace(found[ACQUISITION_SPOT_LABEL], label=LEGACY_SPOT_LABEL)
    return found


def _extract_probe_mode_setting(data: Mapping[str, Any], *, source: str) -> AcquisitionSetting | None:
    candidates: list[tuple[int, int, AcquisitionSetting]] = []
    order = 0
    for field, value, _field_path in _iter_xml_key_values(data):
        text = _display_value(value)
        if text is None:
            continue
        priority = _probe_mode_key_priority(field)
        if priority is None:
            continue
        candidates.append(
            (
                priority,
                order,
                AcquisitionSetting(
                    label="Probe mode",
                    value=_format_probe_mode(text),
                    source=source,
                    field=field,
                ),
            )
        )
        order += 1
    for field, value, _field_path in _walk_key_values(data):
        if field in {"Key", "Value"}:
            continue
        text = _display_value(value)
        if text is None:
            continue
        priority = _probe_mode_key_priority(field)
        if priority is None:
            continue
        candidates.append(
            (
                priority,
                order,
                AcquisitionSetting(
                    label="Probe mode",
                    value=_format_probe_mode(text),
                    source=source,
                    field=field,
                ),
            )
        )
        order += 1
    if not candidates:
        return None
    candidates.sort(key=lambda item: (item[0], item[1]))
    return candidates[0][2]


def _iter_xml_key_values(value: Any, path: tuple[str, ...] = ()):
    if isinstance(value, Mapping):
        key = _display_value(value.get("Key"))
        if key:
            payload = value.get("Value")
            text = _display_value(payload)
            if text is not None:
                yield key, text, (*path, key)
            if payload is not None:
                yield from _iter_xml_key_values(payload, (*path, key))
        for item_key, item in value.items():
            if key and item_key in {"Key", "Value"}:
                continue
            next_path = (*path, str(item_key)) if isinstance(item_key, str) else path
            yield from _iter_xml_key_values(item, next_path)
    elif isinstance(value, list):
        for item in value:
            yield from _iter_xml_key_values(item, path)


def _detector_key_priority(field: str) -> int | None:
    normalised = _normalise_key(field)
    if normalised == "detectorcommercialname":
        return 0
    if re.fullmatch(r"Detectors\[[^\]]+\]\.CommercialName", field, flags=re.IGNORECASE):
        return 1
    if _is_detector_display_field(field):
        return 2
    if normalised in _NORMALISED_ALIASES["Detector"]:
        return 4
    return None


def _is_detector_display_field(field: str) -> bool:
    normalised = _normalise_key(field)
    if not any(token in normalised for token in ("detector", "camera")):
        return False
    return any(
        normalised.endswith(suffix)
        for suffix in ("commercialname", "displayname", "name")
    )


def _detector_internal_key(field: str) -> str | None:
    match = re.fullmatch(r"Detectors\[([^\]]+)\]\..+", field, flags=re.IGNORECASE)
    if match:
        return match.group(1)
    return None


def _spot_candidate(
    priority: int,
    order: int,
    *,
    label: str,
    value: str,
    source: str,
    field: str,
    field_path: tuple[str, ...],
) -> tuple[int, int, AcquisitionSetting]:
    return (
        priority,
        order,
        AcquisitionSetting(
            label=label,
            value=value,
            source=source,
            field=field,
            field_path="/".join(field_path) if field_path else field,
            authority="xml_scoped",
        ),
    )


def _with_alternatives(
    selected: AcquisitionSetting,
    alternatives: Sequence[AcquisitionSetting],
) -> AcquisitionSetting:
    values: list[tuple[str, str, str | None]] = []
    conflicts: list[tuple[str, str, str]] = []
    seen = {(selected.value, selected.field)}
    for setting in alternatives:
        key = (setting.value, setting.field)
        if key in seen or setting.value == selected.value:
            continue
        values.append((setting.value, setting.field, setting.internal_key))
        conflicts.append((setting.value, setting.source, setting.field))
        seen.add(key)
    if values or conflicts:
        return replace(
            selected,
            alternatives=tuple(values),
            conflicts=tuple(conflicts),
        )
    return selected


def _spot_index_context_priority(
    field: str,
    path: tuple[str, ...],
    *,
    acquisition_scope: bool,
) -> tuple[str, int] | None:
    normalised = _normalise_key(field)
    if "spotindex" not in normalised:
        return None
    path_normalised = tuple(_normalise_key(part) for part in path)
    acquisition_tokens = ("acquisition", "exposure", "tiltseries", "tomography")
    ignored_tokens = ("tracking", "focus", "atlas", "setup")
    if any(token in normalised for token in ignored_tokens):
        return None
    if normalised == "searchspotindex" or "search" in normalised:
        return SEARCH_SPOT_LABEL, 0
    if "backwardalignmenttransformation" in path_normalised and "optics" in path_normalised:
        return SEARCH_SPOT_LABEL, 1
    # Plain SpotIndex on BatchPositionParameters has proven to be a batch/search
    # planning value in Tomography 5 exports. Keep it out of the acquisition
    # alias so FEI/MDOC acquisition metadata can remain authoritative.
    if acquisition_scope and normalised == "spotindex":
        return SEARCH_SPOT_LABEL, 2
    has_acquisition_context = any(token in normalised for token in acquisition_tokens)
    if has_acquisition_context:
        return ACQUISITION_SPOT_LABEL, 0
    if normalised == "spotindex" and "microscopedata" in path_normalised and "optics" in path_normalised:
        return ACQUISITION_SPOT_LABEL, 1
    return None


def _probe_mode_key_priority(field: str) -> int | None:
    normalised = _normalise_key(field)
    if normalised.endswith("acquisitionprobemode"):
        return 0
    if normalised.endswith("probemode") and "illumination" not in normalised:
        return 0
    if normalised.endswith("illuminationprobesubmode") or normalised.endswith("illuminationprobemode"):
        return 1
    if normalised == "probe" or normalised.endswith("probe"):
        return 2
    if normalised.endswith("illuminationmode"):
        return 3
    return None


def _walk_key_values(value: Any, path: tuple[str, ...] = ()):
    if isinstance(value, Mapping):
        key_label = _display_value(value.get("Key"))
        if key_label and "Value" in value:
            payload = value.get("Value")
            yield key_label, payload, (*path, key_label)
            yield from _walk_key_values(payload, (*path, key_label))
        for key, item in value.items():
            if key_label and key in {"Key", "Value"}:
                continue
            if isinstance(key, str) and not key.startswith("_"):
                current_path = (*path, key)
                yield key, item, current_path
            else:
                current_path = path
            yield from _walk_key_values(item, current_path)
    elif isinstance(value, list):
        for item in value:
            yield from _walk_key_values(item, path)


def _display_value(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, Mapping):
        for key in ("value", "_text", "Name", "Value"):
            if key in value:
                text = _display_value(value[key])
                if text:
                    return text
        attributes = value.get("_attributes")
        if isinstance(attributes, Mapping):
            for key in ("value", "Value", "name", "Name"):
                if key in attributes:
                    text = _display_value(attributes[key])
                    if text:
                        return text
        return None
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return f"{value:g}"
    text = str(value).strip()
    return text or None


def _format_probe_mode(value: str) -> str:
    normalised = _normalise_key(value)
    if normalised == "nanoprobe":
        return "Nanoprobe"
    if normalised == "microprobe":
        return "Microprobe"
    return value.strip()


def _as_float(value: float | int | str | None) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _group_numeric_values(values: Sequence[float], tolerance: float) -> list[float]:
    grouped: list[list[float]] = []
    for value in values:
        if not grouped or abs(value - grouped[-1][-1]) > tolerance:
            grouped.append([value])
        else:
            grouped[-1].append(value)
    return [sum(group) / len(group) for group in grouped]


def _format_number(value: float) -> str:
    rounded = round(value, 2)
    if abs(rounded) < 0.005:
        rounded = 0.0
    return f"{rounded:g}"
