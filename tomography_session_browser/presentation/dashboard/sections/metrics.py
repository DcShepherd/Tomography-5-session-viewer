"""Build defocus, dose and exposure-thumbnail display models."""
from __future__ import annotations

from datetime import datetime
from math import isclose, isfinite
from statistics import median
from typing import Any
from tomography_session_browser.domain.coerce import float_or_none
from tomography_session_browser.domain.models import MdocSection, MrcMetadata, TiltSeries
from tomography_session_browser.presentation.dashboard.models import (
    DashboardScope,
    DefocusPlotModel,
    DefocusPointModel,
    DoseInformationPlotModel,
    DoseInformationPointModel,
    ExposureThumbnailModel,
)
from tomography_session_browser.presentation.dashboard.scopes import clean_sample_label
from tomography_session_browser.presentation.formatting import format_timestamp
from tomography_session_browser.services.tilt_angle_service import stack_order_mdoc_section_indices
from tomography_session_browser.services.tilt_series_validation import actual_tilt_count
from tomography_session_browser.services.tilt_thumbnails import exposure_frame, starting_tilt_angle
from tomography_session_browser.services.timeline_service import parse_datetime, parse_section_datetime


def defocus_plot_model(scope: DashboardScope) -> DefocusPlotModel:
    """Build chart-ready per-image ``Defocus`` points for a dashboard scope.

    The FEI MRC extended-header ``Defocus`` value is tied directly to a stack
    frame and is therefore preferred. The MDOC section ``Defocus`` value is
    angle-aligned to stack order and used as a fallback. ``TargetDefocus`` and
    MRC ``AppliedDefocus`` remain tooltip context and are never substituted for
    the plotted value.
    """

    contexts = _tilt_series_label_contexts(scope)
    raw_points: list[DefocusPointModel] = []
    defocus_tilt_ids: set[str] = set()
    timestamp_tilt_ids: set[str] = set()
    frame_order = 0

    for tilt in scope.tilt_series:
        sample_name, linked_group = contexts.get(id(tilt), (scope.title or "Selection", None))
        frame_count = _frame_count_for_per_image_metadata(tilt)
        frame_metadata = tilt.mrc_metadata.frame_metadata if tilt.mrc_metadata is not None else []
        section_indices = stack_order_mdoc_section_indices(tilt, frame_count)
        for zero_based_index in range(frame_count):
            metadata = frame_metadata[zero_based_index] if zero_based_index < len(frame_metadata) else {}
            section = _stack_order_mdoc_section(tilt, section_indices, zero_based_index)
            mrc_defocus_um = _mrc_defocus_um(metadata)
            mdoc_defocus_um = (
                _finite_number(section.metadata.get("Defocus")) if section is not None else None
            )
            if mrc_defocus_um is not None:
                value = mrc_defocus_um
                metadata_source = "MRC FEI extended header Defocus"
            elif mdoc_defocus_um is not None:
                value = mdoc_defocus_um
                metadata_source = "MDOC Defocus fallback"
            else:
                continue

            frame_order += 1
            frame_index = zero_based_index + 1
            timestamp = _frame_metadata_datetime(metadata)
            if timestamp is None and section is not None:
                timestamp = parse_section_datetime(section.metadata)
            tilt_angle = _finite_number(metadata.get("tilt_angle"))
            if tilt_angle is None and section is not None:
                tilt_angle = _finite_number(section.metadata.get("TiltAngle"))
            target_defocus_um = (
                _finite_number(section.metadata.get("TargetDefocus")) if section is not None else None
            )
            application_applied_defocus_um = _mrc_applied_defocus_um(metadata)
            sources_agree = None
            if mrc_defocus_um is not None and mdoc_defocus_um is not None:
                sources_agree = isclose(
                    mrc_defocus_um,
                    mdoc_defocus_um,
                    rel_tol=1e-3,
                    abs_tol=0.01,
                )

            defocus_tilt_ids.add(tilt.id)
            if timestamp is not None:
                timestamp_tilt_ids.add(tilt.id)
            raw_points.append(
                DefocusPointModel(
                    sample_name=sample_name,
                    data_collection_name=sample_name,
                    linked_group_label=linked_group,
                    tilt_series_id=tilt.id,
                    tilt_series_name=tilt.name or tilt.id,
                    frame_index=frame_index,
                    frame_count=frame_count,
                    tilt_angle=tilt_angle,
                    acquisition_time=timestamp,
                    frame_order=frame_order,
                    defocus_um=value,
                    metadata_source=metadata_source,
                    has_absolute_time=timestamp is not None,
                    has_relative_time=False,
                    mrc_defocus_um=mrc_defocus_um,
                    mdoc_defocus_um=mdoc_defocus_um,
                    target_defocus_um=target_defocus_um,
                    application_applied_defocus_um=application_applied_defocus_um,
                    sources_agree=sources_agree,
                )
            )

    if not raw_points:
        return DefocusPlotModel(
            points=[],
            total_tilt_series=len(scope.tilt_series),
            defocus_tilt_series=0,
            timestamp_tilt_series=0,
            x_mode="frame_order",
            x_axis_label="Frame order",
        )

    timestamped_points = [point for point in raw_points if point.acquisition_time is not None]
    if timestamped_points:
        points = timestamped_points
        x_mode = "absolute_time"
        x_axis_label = "Acquisition time"
        note = None
        if len(timestamp_tilt_ids) < len(defocus_tilt_ids):
            note = (
                f"Timestamps available for {len(timestamp_tilt_ids):,} / "
                f"{len(defocus_tilt_ids):,} tilt series with Defocus metadata."
            )
    else:
        points = raw_points
        x_mode = "frame_order"
        x_axis_label = "Frame order"
        note = "Acquisition timestamps unavailable; points are shown in frame order."

    return DefocusPlotModel(
        points=points,
        total_tilt_series=len(scope.tilt_series),
        defocus_tilt_series=len(defocus_tilt_ids),
        timestamp_tilt_series=len(timestamp_tilt_ids),
        x_mode=x_mode,
        x_axis_label=x_axis_label,
        note=note,
    )


def dose_information_plot_model(scope: DashboardScope) -> DoseInformationPlotModel:
    """Build camera-dose-per-image points from MRC per-frame metadata.

    Only explicit dose fields parsed from the MRC frame metadata are used.
    Dose-fraction bookkeeping fields are deliberately ignored because they do
    not represent camera dose per tilt image.
    """

    contexts = _tilt_series_label_contexts(scope)
    points: list[DoseInformationPointModel] = []
    dose_tilt_ids: set[str] = set()
    timestamp_tilt_ids: set[str] = set()
    source_labels: set[str] = set()
    frame_order = 0

    for tilt in scope.tilt_series:
        frame_metadata = tilt.mrc_metadata.frame_metadata if tilt.mrc_metadata is not None else []
        if not frame_metadata:
            continue
        sample_name, linked_group = contexts.get(id(tilt), (scope.title or "Selection", None))
        frame_count = _frame_count_for_per_image_metadata(tilt)
        for frame_index, metadata in enumerate(frame_metadata, start=1):
            dose, source = _camera_dose_from_frame_metadata(metadata)
            if dose is None:
                continue
            frame_order += 1
            timestamp = _frame_metadata_datetime(metadata)
            if timestamp is not None:
                timestamp_tilt_ids.add(tilt.id)
            tilt_angle = float_or_none(metadata.get("tilt_angle"))
            dose_tilt_ids.add(tilt.id)
            source_label = source or "MRC frame metadata"
            source_labels.add(source_label)
            point = DoseInformationPointModel(
                sample_name=sample_name,
                data_collection_name=sample_name,
                linked_group_label=linked_group,
                tilt_series_id=tilt.id,
                tilt_series_name=tilt.name or tilt.id,
                frame_index=frame_index,
                frame_count=frame_count,
                tilt_angle=tilt_angle,
                acquisition_time=timestamp,
                frame_order=frame_order,
                dose_e_per_angstrom2=dose,
                metadata_source=source_label,
                has_absolute_time=timestamp is not None,
            )
            points.append(point)

    total_tilt_series = len(scope.tilt_series)
    if not points:
        return DoseInformationPlotModel(
            points=[],
            total_tilt_series=total_tilt_series,
            dose_tilt_series=0,
            timestamp_tilt_series=0,
            x_mode="frame_order",
            x_axis_label="Frame order",
        )

    if timestamp_tilt_ids and len(timestamp_tilt_ids) == len(dose_tilt_ids):
        x_mode = "absolute_time"
        x_axis_label = "Acquisition time"
        note = None
    else:
        x_mode = "frame_order"
        x_axis_label = "Frame order"
        if timestamp_tilt_ids:
            note = (
                f"Acquisition timestamps available for {len(timestamp_tilt_ids):,}/"
                f"{len(dose_tilt_ids):,} tilt series with dose metadata; "
                "points are shown in frame order."
            )
        else:
            note = "Acquisition timestamps unavailable; points are shown in frame order."

    if total_tilt_series and len(dose_tilt_ids) < total_tilt_series:
        partial_note = f"Dose metadata available for {len(dose_tilt_ids):,}/{total_tilt_series:,} tilt series."
        note = f"{partial_note} {note}" if note else partial_note

    values = [point.dose_e_per_angstrom2 for point in points]
    median_dose = median(values)
    min_dose = min(values)
    max_dose = max(values)
    status, status_reason = _dose_information_status(
        values,
        partial=bool(total_tilt_series and len(dose_tilt_ids) < total_tilt_series),
    )

    return DoseInformationPlotModel(
        points=points,
        total_tilt_series=total_tilt_series,
        dose_tilt_series=len(dose_tilt_ids),
        timestamp_tilt_series=len(timestamp_tilt_ids),
        x_mode=x_mode,
        x_axis_label=x_axis_label,
        median_dose_e_per_angstrom2=median_dose,
        min_dose_e_per_angstrom2=min_dose,
        max_dose_e_per_angstrom2=max_dose,
        status=status,
        status_reason=status_reason,
        source_label=_dose_source_label(source_labels),
        note=note,
    )


def _tilt_series_label_contexts(scope: DashboardScope) -> dict[int, tuple[str, str | None]]:
    contexts: dict[int, tuple[str, str | None]] = {}
    linked_scope = scope.kind in {"linked sample group", "linked sessions"}
    for group_label, samples in scope.sample_groups:
        linked_label = scope.title if scope.kind == "linked sample group" else group_label if linked_scope else None
        for sample in samples:
            sample_label = clean_sample_label(sample.name)
            for tilt in sample.tilt_series:
                contexts[id(tilt)] = (sample_label, linked_label)
    for tilt in scope.tilt_series:
        contexts.setdefault(id(tilt), (scope.title or tilt.name or tilt.id, None))
    return contexts


def _mrc_defocus_um(metadata: dict[str, Any]) -> float | None:
    raw_fields = metadata.get("raw_fields")
    raw = raw_fields if isinstance(raw_fields, dict) else {}
    value_m = _finite_number(raw.get("defocus"))
    if value_m is None:
        value_m = _finite_number(metadata.get("defocus"))
    return value_m * 1_000_000.0 if value_m is not None else None


def _mrc_applied_defocus_um(metadata: dict[str, Any]) -> float | None:
    raw_fields = metadata.get("raw_fields")
    if not isinstance(raw_fields, dict):
        return None
    value_m = _finite_number(raw_fields.get("applied_defocus"))
    return value_m * 1_000_000.0 if value_m is not None else None


def _frame_count_for_per_image_metadata(tilt: TiltSeries) -> int:
    if tilt.mrc_metadata is not None and tilt.mrc_metadata.nz is not None:
        return actual_tilt_count(tilt)
    if tilt.number_of_frames and tilt.number_of_frames > 0:
        return int(tilt.number_of_frames)
    if tilt.tilt_count and tilt.tilt_count > 0:
        return int(tilt.tilt_count)
    if tilt.sections:
        return len(tilt.sections)
    if tilt.mrc_metadata is not None and tilt.mrc_metadata.nz:
        return int(tilt.mrc_metadata.nz)
    if tilt.mrc_metadata is not None and tilt.mrc_metadata.frame_metadata:
        return len(tilt.mrc_metadata.frame_metadata)
    return 0


def _stack_order_mdoc_section(
    tilt: TiltSeries,
    section_indices: list[int | None] | None,
    frame_index: int,
) -> MdocSection | None:
    if section_indices is None or frame_index >= len(section_indices):
        return None
    section_index = section_indices[frame_index]
    if section_index is None or not 0 <= section_index < len(tilt.sections):
        return None
    return tilt.sections[section_index]


def _finite_number(value: Any) -> float | None:
    number = float_or_none(value)
    return number if number is not None and isfinite(number) else None


def _per_frame_metric_tooltip_lines(
    *,
    sample_name: str,
    tilt_series_name: str,
    frame_index: int,
    frame_count: int,
    tilt_angle: float | None,
    acquisition_time: datetime | None,
    frame_order: int,
) -> list[str]:
    """Build the 5 shared identification lines for a per-frame metric tooltip.

    Both the defocus-readout and the camera-dose tooltips lead with the
    same Sample / Tilt series / Frame / Tilt angle / Acquisition time
    block; only the metric line and the caveat differ. Centralising the
    shared portion keeps the two callers honest if e.g. the frame
    counter notation changes.
    """

    angle_text = f"{tilt_angle:g}\N{DEGREE SIGN}" if tilt_angle is not None else "n/a"
    if acquisition_time is not None:
        time_text = acquisition_time.isoformat(sep=" ", timespec="seconds")
    else:
        time_text = f"Frame order {frame_order}"
    return [
        f"Sample: {sample_name}",
        f"Tilt series: {tilt_series_name}",
        f"Frame: {frame_index} / {frame_count or '?'}",
        f"Tilt angle: {angle_text}",
        f"Acquisition time: {time_text}",
    ]


def _defocus_tooltip(
    *,
    sample_name: str,
    tilt_series_name: str,
    frame_index: int,
    frame_count: int,
    tilt_angle: float | None,
    acquisition_time: datetime | None,
    frame_order: int,
    defocus_um: float,
    metadata_source: str,
    mrc_defocus_um: float | None,
    mdoc_defocus_um: float | None,
    target_defocus_um: float | None,
    application_applied_defocus_um: float | None,
    sources_agree: bool | None,
) -> str:
    lines = _per_frame_metric_tooltip_lines(
        sample_name=sample_name,
        tilt_series_name=tilt_series_name,
        frame_index=frame_index,
        frame_count=frame_count,
        tilt_angle=tilt_angle,
        acquisition_time=acquisition_time,
        frame_order=frame_order,
    )
    lines.extend([f"Defocus readout: {defocus_um:g} \N{MICRO SIGN}m", f"Source: {metadata_source}"])
    if mrc_defocus_um is not None:
        lines.append(f"MRC Defocus: {mrc_defocus_um:g} \N{MICRO SIGN}m")
    if mdoc_defocus_um is not None:
        lines.append(f"MDOC Defocus: {mdoc_defocus_um:g} \N{MICRO SIGN}m")
    if sources_agree is True:
        lines.append("MRC and MDOC Defocus agree within 0.01 µm.")
    elif sources_agree is False and mrc_defocus_um is not None and mdoc_defocus_um is not None:
        lines.append(
            "MRC and MDOC Defocus differ by "
            f"{abs(mrc_defocus_um - mdoc_defocus_um):g} \N{MICRO SIGN}m; "
            "the MRC value is plotted."
        )
    if target_defocus_um is not None:
        lines.append(f"TargetDefocus: {target_defocus_um:g} \N{MICRO SIGN}m")
    if application_applied_defocus_um is not None:
        lines.append(
            "Application AppliedDefocus: "
            f"{application_applied_defocus_um:g} \N{MICRO SIGN}m"
        )
    lines.append(
        "The plotted value is the per-image Defocus metadata readout. "
        "TargetDefocus, application AppliedDefocus, and measured CTF defocus "
        "are shown only as distinct context when available."
    )
    return "\n".join(lines)


def defocus_point_tooltip(point: DefocusPointModel) -> str:
    """Build the hover tooltip for one per-image Defocus point on demand."""

    if point.tooltip:
        return point.tooltip
    return _defocus_tooltip(
        sample_name=point.sample_name,
        tilt_series_name=point.tilt_series_name,
        frame_index=point.frame_index,
        frame_count=point.frame_count,
        tilt_angle=point.tilt_angle,
        acquisition_time=point.acquisition_time,
        frame_order=point.frame_order,
        defocus_um=point.defocus_um,
        metadata_source=point.metadata_source,
        mrc_defocus_um=point.mrc_defocus_um,
        mdoc_defocus_um=point.mdoc_defocus_um,
        target_defocus_um=point.target_defocus_um,
        application_applied_defocus_um=point.application_applied_defocus_um,
        sources_agree=point.sources_agree,
    )


def _camera_dose_from_frame_metadata(metadata: dict[str, Any]) -> tuple[float | None, str | None]:
    raw_fields = metadata.get("raw_fields")
    raw = raw_fields if isinstance(raw_fields, dict) else {}
    source = _mrc_metadata_source_label(metadata)

    for candidate in (
        raw.get("dose_e_per_angstrom2"),
        metadata.get("dose_e_per_angstrom2"),
    ):
        value = float_or_none(candidate)
        if _valid_camera_dose_e_per_angstrom2(value):
            return value, source

    # Thermo FEI/Tomo5 stores this dose field as electrons per square metre.
    # Convert only this explicit dose field; dose-fraction fields are not
    # camera dose per image and are intentionally ignored.
    for candidate in (metadata.get("dose"), raw.get("dose")):
        value = float_or_none(candidate)
        if value is None:
            continue
        converted = value * 1e-20
        if _valid_camera_dose_e_per_angstrom2(converted):
            return converted, source
    return None, None


def _mrc_metadata_source_label(metadata: dict[str, Any]) -> str:
    for key in ("metadata_source", "source"):
        value = metadata.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return "MRC frame metadata"


def _valid_camera_dose_e_per_angstrom2(value: float | None) -> bool:
    if value is None:
        return False
    return value > 0 and value < 1_000_000 and value == value and value not in (float("inf"), float("-inf"))


def _frame_metadata_datetime(metadata: dict[str, Any]) -> datetime | None:
    timestamp = metadata.get("timestamp")
    if isinstance(timestamp, datetime):
        return timestamp
    if isinstance(timestamp, str) and timestamp.strip():
        return parse_datetime(timestamp)
    return None


def exposure_thumbnail_model(
    tilt: TiltSeries,
    thumbnail: MrcMetadata,
    *,
    lamella: bool = False,
) -> ExposureThumbnailModel | None:
    """``tilt``'s exposure image in ``thumbnail``, or ``None`` if no angle says which frame.

    The image is the one nearest 0°; for a lamella (``is_lamella_session``)
    the one nearest its pretilt, the angle its tilt series started from.
    Camera dose and acquisition time follow the dashboard's dose plot (the
    explicit camera-dose field, the frame's wall-clock timestamp). Stage X/Y
    are as the microscope recorded them, in µm.
    """

    pretilt = starting_tilt_angle(tilt) if lamella else None
    reference = pretilt if pretilt is not None else 0.0
    frame = exposure_frame(thumbnail, tilt, reference)
    if frame is None:
        return None
    record = dict(frame.record) if frame.record is not None else {}
    dose, _source = _camera_dose_from_frame_metadata(record)
    timestamp = record.get("timestamp")
    stage_x, stage_y = float_or_none(record.get("stage_x")), float_or_none(record.get("stage_y"))
    stage = (
        (stage_x * 1e6, stage_y * 1e6)
        if stage_x is not None and stage_y is not None and isfinite(stage_x) and isfinite(stage_y)
        else None
    )
    return ExposureThumbnailModel(
        tilt_series_id=tilt.id,
        tilt_series_name=tilt.name or tilt.id,
        frame_index=frame.index,
        frame_count=frame.frame_count,
        tilt_angle=frame.tilt_angle,
        reference_angle=frame.reference_angle,
        lamella=lamella,
        pretilt_unknown=lamella and pretilt is None,
        misses_reference=frame.misses_reference,
        angle_source=frame.angle_source,
        acquisition_time=format_timestamp(timestamp) if timestamp not in (None, "") else None,
        camera_dose_e_per_angstrom2=dose,
        stage_um=stage,
        image_size=(int(thumbnail.nx), int(thumbnail.ny)) if thumbnail.nx and thumbnail.ny else None,
    )


def _dose_source_label(source_labels: set[str]) -> str | None:
    labels = sorted(label for label in source_labels if label)
    if not labels:
        return None
    if len(labels) == 1:
        return labels[0]
    if all("mrc" in label.lower() for label in labels):
        return "MRC frame metadata"
    return "Multiple metadata sources"


def _dose_information_status(values: list[float], *, partial: bool) -> tuple[str, str | None]:
    if partial:
        return "warning", "partial data"
    if len(values) < 2:
        return "neutral", None
    med = median(values)
    spread = max(values) - min(values)
    if spread > max(0.5, abs(med) * 0.25):
        return "warning", "variable dose"
    return "neutral", None


def _dose_information_tooltip(
    *,
    sample_name: str,
    tilt_series_name: str,
    frame_index: int,
    frame_count: int,
    tilt_angle: float | None,
    acquisition_time: datetime | None,
    frame_order: int,
    dose_e_per_angstrom2: float,
    metadata_source: str,
) -> str:
    lines = _per_frame_metric_tooltip_lines(
        sample_name=sample_name,
        tilt_series_name=tilt_series_name,
        frame_index=frame_index,
        frame_count=frame_count,
        tilt_angle=tilt_angle,
        acquisition_time=acquisition_time,
        frame_order=frame_order,
    )
    lines.extend(
        [
            f"Camera dose per image: {dose_e_per_angstrom2:.3g} e⁻/Å²",
            f"Source: {metadata_source}",
            "This is camera dose per image, not cumulative tilt series dose.",
        ]
    )
    return "\n".join(lines)


def dose_information_point_tooltip(point: DoseInformationPointModel) -> str:
    """Build the hover/detail tooltip for one camera-dose point on demand."""

    if point.tooltip:
        return point.tooltip
    return _dose_information_tooltip(
        sample_name=point.sample_name,
        tilt_series_name=point.tilt_series_name,
        frame_index=point.frame_index,
        frame_count=point.frame_count,
        tilt_angle=point.tilt_angle,
        acquisition_time=point.acquisition_time,
        frame_order=point.frame_order,
        dose_e_per_angstrom2=point.dose_e_per_angstrom2,
        metadata_source=point.metadata_source,
    )
