"""Formatting report components."""
from __future__ import annotations
from datetime import datetime
from statistics import median
from typing import Mapping, Sequence
from tomography_session_browser.domain.enums import SessionKind
from tomography_session_browser.domain.models import BatchPosition, Session, TiltSeries
from tomography_session_browser.reports import styles
from tomography_session_browser.services.warning_classification import WarningSummary
from tomography_session_browser.services.timeline_service import parse_datetime
from tomography_session_browser.services.acquisition_metadata import ACQUISITION_SPOT_LABEL, LEGACY_SPOT_LABEL, summarise_acquisition_setting
from tomography_session_browser.services.tilt_series_validation import STATUS_COMPLETE, STATUS_FAILED, STATUS_INCOMPLETE, STATUS_UNKNOWN, TiltSeriesValidation
from tomography_session_browser.presentation.dashboard.models import DashboardModel


def arrow() -> str:
    return " → " if styles.has_unicode_fonts() else " to "


def acquisition_window_text(dashboard: DashboardModel) -> str | None:
    start, end = dashboard.acquisition_start, dashboard.acquisition_end
    if start and end:
        text = f"{start}{arrow()}{end}"
        if dashboard.acquisition_duration:
            text += f" ({dashboard.acquisition_duration})"
        return text
    if start:
        return f"from {start}"
    return None


def session_kind_label(kind: SessionKind) -> str:
    if kind == SessionKind.ATLAS_SCREENING:
        return "atlas screening"
    if kind in {SessionKind.COLLECTION, SessionKind.SINGLE_COLLECTION}:
        return "data collection"
    return kind.value.replace("_", " ")


def tilt_outcome_sub_label(summary: Mapping[str, int]) -> str:
    """Compact, glyph-safe outcome copy that fits the count card."""

    parts = [f"{summary.get(STATUS_COMPLETE, 0)} complete"]
    for status, label in (
        (STATUS_INCOMPLETE, "incomplete"),
        (STATUS_FAILED, "failed"),
        (STATUS_UNKNOWN, "unknown"),
    ):
        count = summary.get(status, 0)
        if count:
            parts.append(f"{count} {label}")
    return ", ".join(parts)


def short_warning_summary(rows: Sequence[WarningSummary]) -> str:
    parts = [f"{r.count}× {r.category}" for r in rows[:3]]
    if len(rows) > 3:
        parts.append(f"+{len(rows) - 3} more")
    return ", ".join(parts) if parts else "none"


def report_acquisition_setting(
    batch_positions: Sequence[BatchPosition],
    label: str,
    *,
    fallback: Sequence[str] = (),
) -> str:
    text = summarise_acquisition_setting(
        [batch.metadata for batch in batch_positions],
        label,
        missing="n/a",
    )
    if text != "n/a":
        return text
    return format_set([str(value) for value in fallback]) if fallback else "n/a"


def report_spot_size(
    tilt_series: Sequence[TiltSeries],
    batch_positions: Sequence[BatchPosition],
) -> str:
    # MDOC records the spot size used for the actual tilt-series acquisition.
    # Prefer it whenever present; Batch/search.xml can also contain search,
    # tracking, or focus spot indices that should not override MDOC.
    tilt_text = summarise_acquisition_setting(
        [tilt.metadata for tilt in tilt_series],
        ACQUISITION_SPOT_LABEL,
        missing="n/a",
    )
    if tilt_text != "n/a":
        return tilt_text
    legacy_tilt_text = summarise_acquisition_setting(
        [tilt.metadata for tilt in tilt_series],
        LEGACY_SPOT_LABEL,
        missing="n/a",
    )
    if legacy_tilt_text != "n/a":
        return legacy_tilt_text
    return report_acquisition_setting(batch_positions, ACQUISITION_SPOT_LABEL)


def format_set(values: Sequence, *, suffix: str = "") -> str:
    if not values:
        return "n/a"
    if len(values) == 1:
        return f"{values[0]}{suffix}"
    return f"{values[0]}{suffix} – {values[-1]}{suffix}"


def format_count_set(values: Sequence[int]) -> str:
    if not values:
        return "n/a"
    unique = sorted(set(values))
    if len(unique) == 1:
        return str(unique[0])
    if len(unique) <= 4:
        return ", ".join(str(value) for value in unique)
    return f"Mixed ({len(unique)} counts)"


def format_pixel_size(values: Sequence[float], unit: str) -> str:
    if not values:
        return "n/a"
    if len(values) == 1:
        return f"{values[0]:g} {unit}"
    return f"{values[0]:g} – {values[-1]:g} {unit}"


def section_floats(tilt: TiltSeries, key: str) -> list[float]:
    out: list[float] = []
    for section in tilt.sections:
        v = section.metadata.get(key)
        if isinstance(v, (int, float)):
            out.append(float(v))
    return out


def section_strings(tilt: TiltSeries, key: str) -> list[str]:
    out: list[str] = []
    for section in tilt.sections:
        v = section.metadata.get(key)
        if isinstance(v, str) and v.strip():
            out.append(v.strip())
    return out


def collect_increments(tilt_series: Sequence[TiltSeries]) -> list[float]:
    seen: set[float] = set()
    for tilt in tilt_series:
        increments = []
        angles = sorted(section_floats(tilt, "TiltAngle"))
        for a, b in zip(angles, angles[1:]):
            delta = round(abs(b - a), 2)
            if delta > 0:
                increments.append(delta)
        if increments:
            seen.add(round(median(increments), 2))
    return sorted(seen)


def collect_expected_counts(
    tilt_series: Sequence[TiltSeries],
    validations: Mapping[str, TiltSeriesValidation],
) -> list[int]:
    seen: set[int] = set()
    for tilt in tilt_series:
        validation = validations.get(tilt.id)
        if validation is not None and validation.expected_count is not None and validation.expected_count > 0:
            seen.add(int(validation.expected_count))
    return sorted(seen)


def collect_observed_counts(
    tilt_series: Sequence[TiltSeries],
    validations: Mapping[str, TiltSeriesValidation],
) -> list[int]:
    seen: set[int] = set()
    for tilt in tilt_series:
        validation = validations.get(tilt.id)
        if validation is not None:
            seen.add(int(validation.actual_count))
        elif tilt.sections:
            seen.add(len(tilt.sections))
        elif tilt.tilt_count and tilt.tilt_count > 0:
            seen.add(int(tilt.tilt_count))
    return sorted(seen)


def total_acquisition_duration(tilt_series: Sequence[TiltSeries]) -> str:
    starts: list[datetime] = []
    ends: list[datetime] = []
    for tilt in tilt_series:
        s = parse_datetime(tilt.acquisition_time_start)
        e = parse_datetime(tilt.acquisition_time_end)
        if s is not None:
            starts.append(s)
        if e is not None:
            ends.append(e)
    if not starts or not ends:
        return "n/a"
    seconds = max((max(ends) - min(starts)).total_seconds(), 0)
    return format_duration(seconds)


def format_duration(seconds: float) -> str:
    seconds = int(round(seconds))
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}h {m}m"
    if m:
        return f"{m}m {s}s"
    return f"{s}s"


def report_title(sessions: Sequence[Session]) -> str:
    if len(sessions) == 1:
        return sessions[0].name or "Tomography Session Report"
    names = ", ".join(s.name for s in sessions if s.name)
    return f"Tomography Session Report ({names})" if names else "Tomography Session Report"


def footer_label(
    sessions: Sequence[Session],
    *,
    project_title: str | None = None,
    generated_at: datetime | None = None,
) -> str:
    """Footer used on every page: the project label and when it was generated.

    The app's name is in the running header, so the footer carries only what
    identifies this report.
    """

    if project_title:
        label = project_title
    elif not sessions:
        label = "Tomography session report"
    else:
        first = sessions[0].name or "Tomography Session"
        label = first if len(sessions) == 1 else f"{first} (+{len(sessions) - 1} more)"
    if generated_at is not None:
        label += f" · Generated {generated_at.strftime('%Y-%m-%d %H:%M')}"
    return label

