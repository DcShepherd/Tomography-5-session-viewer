"""Shape metadata, validation and acquisition settings for context descriptions."""
from __future__ import annotations

from collections.abc import Iterable
from statistics import median
from typing import Any
from tomography_session_browser.domain.coerce import float_or_none
from tomography_session_browser.domain.display_names import count_phrase
from tomography_session_browser.domain.models import (
    BatchPosition,
    MrcMetadata,
    Sample,
    TiltSeries,
)
from tomography_session_browser.domain.units import (
    ANGSTROM,
    ANGSTROM_PER_PIXEL,
    DEGREE,
    MICROMETRE,
)
from tomography_session_browser.parsers.xml_parser import find_first
from tomography_session_browser.presentation.formatting import (
    angle_range_text,
    binning_text,
    common_text,
    display_value,
    format_length_from_meters,
)
from tomography_session_browser.services.acquisition_metadata import (
    ACQUISITION_SPOT_LABEL,
    LEGACY_SPOT_LABEL,
    SEARCH_SPOT_LABEL,
    acquisition_setting_source,
    acquisition_setting_value,
    format_target_defocus_values,
    summarise_acquisition_setting,
)
from tomography_session_browser.services.item_status import display_status_label
from tomography_session_browser.services.tilt_series_validation import (
    SOURCE_MRC_EXTENDED,
    SOURCE_SESSION_METADATA,
    TiltSeriesValidation,
    actual_tilt_count,
)
from tomography_session_browser.services.timeline_service import parse_datetime


def microscope_state_lines(metadata: dict[str, Any]) -> list[str]:
    """Surface instrument-identification fields recorded in SearchMap.xml.

    Vacuum mode, projection-chamber pressure, and sample pressure used to
    appear here, but they're not actionable signal for a review session
    (always ``Ready`` / 0 in practice), so the context panel omits them.
    Implicit pauses are inferred separately by the timeline service and
    clearly labelled there.
    """

    lines: list[str] = []
    instrument = find_first(metadata, "InstrumentModel")
    if isinstance(instrument, str) and instrument:
        lines.append(f"Instrument: {instrument}")

    voltage = find_first(metadata, "AccelerationVoltage")
    if voltage is not None:
        try:
            voltage_value = float(voltage)
            lines.append(f"Acceleration voltage: {voltage_value / 1000:.0f} kV")
        except (TypeError, ValueError):
            pass

    return lines


def tilt_series_validation_lines(
    tilt_series: TiltSeries,
    *,
    validation: TiltSeriesValidation | None = None,
) -> list[str]:
    """Validation summary used by the right-hand context panel.

    Surfaces the same data the dashboard tile tooltips show — status,
    actual / expected counts, evidence source, and the human-readable
    reason — so the user can see *why* a tilt series was flagged FAILED or
    INCOMPLETE without leaving the Tilt-series tab.
    """

    validation = validation or standalone_tilt_validation(tilt_series)
    if validation is None:
        return []
    expected_text = str(validation.expected_count) if validation.expected_count is not None else "unknown"
    lines = [
        # The reviewer's word, as the header pill and the batch rows say it
        # ("Complete", not "COMPLETE"): the context panel draws it as the
        # same pill (harmonisation C1).
        f"Status: {display_status_label(validation.status)}",
        f"Tilt images: {validation.actual_count} / {expected_text} expected",
    ]
    if validation.evidence_source and validation.evidence_source != "none":
        lines.append(f"Expected source: {validation.evidence_source.replace('_', ' ')}")
    if validation.min_tilt is not None and validation.max_tilt is not None:
        range_label = (
            "Planned range"
            if validation.evidence_source in {SOURCE_SESSION_METADATA, SOURCE_MRC_EXTENDED}
            else "Observed range"
        )
        if validation.tilt_increment is not None:
            lines.append(
                f"{range_label}: {validation.min_tilt:g}° to {validation.max_tilt:g}° "
                f"at {validation.tilt_increment:g}°"
            )
        else:
            lines.append(
                f"{range_label}: {validation.min_tilt:g}° to {validation.max_tilt:g}°"
            )
    if validation.reason:
        lines.append(f"Reason: {validation.reason}")
    return lines


def tilt_series_quality_lines(
    tilt: TiltSeries,
    *,
    validation: TiltSeriesValidation | None = None,
) -> list[str]:
    """Return human-readable quality / completeness lines for a tilt series.

    Per the disk audit, mdoc files do not record explicit ``FocusError`` or
    ``Failed`` flags. The most useful indicators we *can* compute are:
    section-count vs. expected, defocus distribution stats, and a "no stack"
    flag when the .mrc disappears. Anything missing simply doesn't render.
    """

    lines: list[str] = []

    # Stack presence
    if tilt.mrc_path is None or not tilt.mrc_path.exists():
        lines.append("No stack on disk (mrc missing).")
        return lines  # everything else needs the stack

    # Sections vs. expected
    validation = validation or standalone_tilt_validation(tilt)
    expected = validation.expected_count if validation is not None else None
    actual = validation.actual_count if validation is not None else actual_tilt_count(tilt)
    # The Summary and Validation sections already state the image count and
    # the expected count. This block is about the judgement drawn from them,
    # so it leads with that rather than printing the same ratio a third time
    # under a third noun.
    if expected:
        if actual + 1 < expected:
            lines.append(f"Coverage: partial — {actual} of {expected} expected")
        elif actual >= expected:
            lines.append(f"Coverage: complete — {actual} of {expected} expected")
        else:
            lines.append(f"Coverage: near-complete — {actual} of {expected} expected")
    elif actual:
        lines.append(f"Coverage: {count_phrase(actual, 'tilt image')}, expected count unknown")

    if not actual:
        lines.append("The MDOC file records no tilt images.")
        return lines

    # Defocus stats
    defocus_values = [
        v for section in tilt.sections
        if (v := float_or_none(section.metadata.get("Defocus"))) is not None
    ]
    if defocus_values:
        med = median(defocus_values)
        lo = min(defocus_values)
        hi = max(defocus_values)
        spread = hi - lo
        line = f"Defocus: median {med:+.2f} {MICROMETRE} (range {lo:+.2f} → {hi:+.2f}, spread {spread:.2f})"
        if abs(spread) > 1.5 and len(defocus_values) >= 5:
            line += "  ⚠ wide drift"
        lines.append(line)

    return lines


def standalone_tilt_validation(tilt: TiltSeries) -> TiltSeriesValidation | None:
    """Best-effort fallback for callers that have no shared session context."""

    from tomography_session_browser.services.tilt_series_validation import validate_tilt_series

    try:
        return validate_tilt_series(tilt)
    except Exception:  # pragma: no cover - context-panel rendering must remain recoverable
        return None


def mrc_metadata_lines(metadata: MrcMetadata | None) -> list[str]:
    """Body rows for a ``Metadata source`` section describing one MRC.

    Returns rows WITHOUT the leading section header so the caller can
    place them under whichever section heading is most appropriate
    (typically ``Metadata source`` in the new sectioned context panel).
    Returns an empty list when no metadata is available, so
    :func:`_emit_section` skips the section entirely.
    """

    if metadata is None:
        return []
    rows = [
        f"Dimensions: {dimensions_text(metadata)}",
        f"Frames: {display_value(metadata.nz)}",
        f"Data type: {mrc_mode_text(metadata.mode)}",
        f"Voxel size: {voxel_size_text(metadata.voxel_size)}",
        f"Extended header: {metadata.extended_header_type or 'none'}",
        f"Frames with parsed metadata: {len(metadata.frame_metadata)}",
        f"Tilt angles: {len(metadata.tilt_angles)} ({metadata.tilt_angle_source or 'unavailable'})",
    ]
    if metadata.warnings:
        rows.append(f"Parser warnings: {len(metadata.warnings)}")
    return rows


_MRC_MODE_LABELS = {
    0: "int8",
    1: "int16",
    2: "float32",
    3: "complex int16",
    4: "complex float32",
    6: "uint16",
    12: "float16",
}


def mrc_mode_text(mode: int | None) -> str:
    if mode is None:
        return "unknown"
    label = _MRC_MODE_LABELS.get(mode)
    return f"{mode} ({label})" if label else f"{mode} (unrecognised)"


def dimensions_text(metadata: MrcMetadata) -> str:
    """Format the MRC extents with a real multiplication sign and a unit."""

    parts = [display_value(metadata.nx), display_value(metadata.ny), display_value(metadata.nz)]
    return f"{' × '.join(parts)} px"


def voxel_size_text(value: tuple[float | None, float | None, float | None]) -> str:
    """Format the voxel extents, which MRC records in ångström.

    The unit was previously omitted entirely, so the panel showed the same
    physical quantity twice in different units with only one of them labelled
    (``Pixel size: 466.07 nm`` beside ``Voxel size: 4660.71 x ...``).
    """

    if all(item is None for item in value):
        return "unknown"
    parts = ["unknown" if item is None else f"{item:g}" for item in value]
    return f"{' × '.join(parts)} {ANGSTROM}"


def first_metadata(metadata: dict[str, Any]) -> Any:
    for key in sorted(metadata):
        if key.startswith("Atlas_") and key.endswith(".xml"):
            return metadata[key]
    for key in ("SearchMap.xml", "SearchMap.dm", "Atlas.dm", "Overview.xml"):
        if key in metadata:
            return metadata[key]
    return next(iter(metadata.values()), None) if metadata else None


def metadata_value(metadata: Any, key: str) -> str:
    value = find_first(metadata, key)
    if isinstance(value, dict):
        value = value.get("value")
    return display_value(value)


def common_metadata_value(metadata_values: list[Any], key: str) -> str:
    values = [metadata_value(metadata, key) for metadata in metadata_values]
    return common_text(values)


def xml_pixel_size(metadata: Any) -> str:
    pixel_size = find_first(metadata, "pixelSize")
    value = _nested_numeric(pixel_size, "x")
    if value is None:
        return "unknown"
    return format_length_from_meters(value)


def common_xml_pixel_size(metadata_values: list[Any]) -> str:
    return common_text([xml_pixel_size(metadata) for metadata in metadata_values])


def _nested_numeric(value: Any, key: str) -> float | None:
    if not isinstance(value, dict):
        return None
    nested = value.get(key)
    if isinstance(nested, dict):
        numeric = nested.get("numericValue")
        if isinstance(numeric, int | float):
            return float(numeric)
    return None


def acquisition_setting_lines_for_scope(
    tilt_metadata: Iterable[dict[str, Any]],
    batch_metadata: Iterable[dict[str, Any]],
    *,
    include_unknown: bool = False,
) -> list[str]:
    tilt_metadata_list = list(tilt_metadata)
    batch_metadata_list = list(batch_metadata)
    lines: list[str] = []
    for label in ("Detector", SEARCH_SPOT_LABEL, ACQUISITION_SPOT_LABEL, "Probe mode"):
        value = scope_setting_summary(tilt_metadata_list, batch_metadata_list, label)
        if value == "unknown" and not include_unknown:
            continue
        lines.append(f"{label}: {value}")
    return lines


def acquisition_setting_lines(
    metadata_values: Iterable[dict[str, Any]],
    *,
    include_unknown: bool = False,
) -> list[str]:
    return acquisition_setting_lines_for_scope(metadata_values, (), include_unknown=include_unknown)


def batch_setting_summary(batch_positions: list[BatchPosition], label: str) -> str:
    return summarise_acquisition_setting(
        [batch.metadata for batch in batch_positions],
        label,
        missing="unknown",
    )


def scope_setting_summary(
    tilt_metadata: Iterable[dict[str, Any]],
    batch_metadata: Iterable[dict[str, Any]],
    label: str,
) -> str:
    tilt_metadata_list = list(tilt_metadata)
    batch_metadata_list = list(batch_metadata)
    if label in {LEGACY_SPOT_LABEL, ACQUISITION_SPOT_LABEL}:
        tilt_value = summarise_acquisition_setting(tilt_metadata_list, label, missing="unknown")
        if tilt_value != "unknown":
            return tilt_value
        return summarise_acquisition_setting(batch_metadata_list, label, missing="unknown")
    if label == SEARCH_SPOT_LABEL:
        return summarise_acquisition_setting(batch_metadata_list, label, missing="unknown")
    return summarise_acquisition_setting(
        [*tilt_metadata_list, *batch_metadata_list],
        label,
        missing="unknown",
    )


def acquisition_setting_detail(metadata: dict[str, Any], label: str) -> str:
    value = acquisition_setting_value(metadata, label)
    if value is None:
        return "unknown"
    source = acquisition_setting_source(metadata, label)
    return f"{value} ({source})" if source else value


def tilt_pixel_size_text(tilt_series: TiltSeries) -> str:
    if tilt_series.pixel_size is None:
        return "unknown"
    if tilt_series.original_pixel_size and tilt_series.binning and tilt_series.binning > 1:
        return (
            f"{tilt_series.pixel_size:g} {ANGSTROM_PER_PIXEL} "
            f"(original {tilt_series.original_pixel_size:g} {ANGSTROM_PER_PIXEL}, "
            f"binning {tilt_series.binning}×)"
        )
    return f"{tilt_series.pixel_size:g} {ANGSTROM_PER_PIXEL}"


def section_value_text(tilt_series: TiltSeries, key: str, unit: str | None = None) -> str:
    values = _numeric_section_values(tilt_series, key)
    if not values:
        return "unknown"
    suffix = f" {unit}" if unit else ""
    if len(set(values)) == 1:
        return f"{values[0]:g}{suffix}"
    return f"{min(values):g} to {max(values):g}{suffix}"


def _numeric_section_values(tilt_series: TiltSeries, key: str) -> list[float]:
    values: list[float] = []
    for section in tilt_series.sections:
        value = section.metadata.get(key)
        if isinstance(value, int | float):
            values.append(float(value))
    return values


def tilt_increment_text(tilt_series: TiltSeries) -> str:
    angles = sorted(_numeric_section_values(tilt_series, "TiltAngle"))
    increments = [round(abs(next_angle - angle), 2) for angle, next_angle in zip(angles, angles[1:]) if next_angle != angle]
    if not increments:
        return "unknown"
    return f"{median(increments):g}{DEGREE}"


def combined_tilt_range_text(tilt_series: list[TiltSeries]) -> str:
    ranges = [tilt.tilt_range for tilt in tilt_series if tilt.tilt_range is not None]
    if not ranges:
        return "unknown"
    return angle_range_text(
        (min(item[0] for item in ranges), max(item[1] for item in ranges))
    )


def common_tilt_increment(tilt_series: list[TiltSeries]) -> str:
    return common_text([tilt_increment_text(tilt) for tilt in tilt_series])


def common_tilt_pixel_size(tilt_series: list[TiltSeries]) -> str:
    return common_text([tilt_pixel_size_text(tilt) for tilt in tilt_series])


def common_binning(tilt_series: list[TiltSeries]) -> str:
    return common_text([binning_text(tilt.binning) for tilt in tilt_series])


def common_section_value(tilt_series: list[TiltSeries], key: str, unit: str | None = None) -> str:
    return common_text([section_value_text(tilt, key, unit) for tilt in tilt_series])


def target_defocus_range_text(tilt_series: list[TiltSeries]) -> str:
    values = [tilt.target_defocus for tilt in tilt_series if tilt.target_defocus is not None]
    return format_target_defocus_values(values, missing="unknown")


def sample_collection_start_date(sample: Sample) -> str:
    starts = [parse_datetime(tilt.acquisition_time_start) for tilt in sample.tilt_series]
    starts = [value for value in starts if value is not None]
    if not starts:
        return "unknown"
    return min(starts).date().isoformat()


def sample_collection_duration(sample: Sample) -> float | None:
    starts = [parse_datetime(tilt.acquisition_time_start) for tilt in sample.tilt_series]
    ends = [parse_datetime(tilt.acquisition_time_end) for tilt in sample.tilt_series]
    starts = [value for value in starts if value is not None]
    ends = [value for value in ends if value is not None]
    if not starts or not ends:
        return None
    return max((max(ends) - min(starts)).total_seconds(), 0)


def sample_magnification_text(sample: Sample) -> str:
    values = sorted({value for tilt in sample.tilt_series for value in _numeric_section_values(tilt, "Magnification")})
    if not values:
        return "unknown"
    if len(values) == 1:
        return f"{values[0]:g}"
    return f"{values[0]:g} to {values[-1]:g}"


def sample_original_pixel_size_text(sample: Sample) -> str:
    values = sorted({tilt.original_pixel_size for tilt in sample.tilt_series if tilt.original_pixel_size is not None})
    if not values:
        return "unknown"
    return ", ".join(f"{value:g}" for value in values)
