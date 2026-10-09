"""Shared display formats with distinct compact and long duration policies."""
from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path
from typing import Any
from tomography_session_browser.domain.models import TiltSeries
from tomography_session_browser.domain.units import ANGSTROM_PER_PIXEL, DEGREE, NANOMETRE_PER_PIXEL
from tomography_session_browser.services.timeline_service import parse_datetime


def path_text(path: Path | None) -> str:
    return str(path) if path is not None else "not found"


def format_length_from_meters(value: float) -> str:
    angstrom = value * 1e10
    if angstrom < 100:
        return f"{angstrom:.2f} {ANGSTROM_PER_PIXEL}"
    nm = value * 1e9
    if nm < 1000:
        return f"{nm:.2f} {NANOMETRE_PER_PIXEL}"
    return f"{value:.3g} m"


def display_value(value: Any) -> str:
    if value is None:
        return "unknown"
    if isinstance(value, float):
        return f"{value:g}"
    return str(value)


def number_text(value: float | None, unit: str | None = None) -> str:
    if value is None:
        return "unknown"
    suffix = f" {unit}" if unit else ""
    return f"{value:g}{suffix}"


def angle_range_text(value: tuple[float, float] | None) -> str:
    """Format a tilt range the way the validation section already does.

    The degree sign attaches to both endpoints (``-60° to 60°``) rather than
    trailing the pair, so the Summary and Validation blocks of the same
    context panel read identically.
    """

    if value is None:
        return "unknown"
    return f"{value[0]:g}{DEGREE} to {value[1]:g}{DEGREE}"


def binning_text(value: int | None) -> str:
    return f"{value}×" if value is not None else "unknown"


def common_text(values: list[str]) -> str:
    useful = [value for value in values if value != "unknown"]
    if not useful:
        return "unknown"
    unique = set(useful)
    numeric_values = [
        (number, value)
        for value in unique
        if (number := _leading_number(value)) is not None
    ]
    if len(numeric_values) == len(unique):
        ordered = [value for _, value in sorted(numeric_values, key=lambda item: (item[0], item[1]))]
    else:
        ordered = sorted(unique, key=str.casefold)
    if len(ordered) == 1:
        return ordered[0]
    return f"{ordered[0]} to {ordered[-1]}"


def _leading_number(value: str) -> float | None:
    match = re.match(
        r"^\s*([+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?)",
        value.replace(",", ""),
    )
    if match is None:
        return None
    try:
        return float(match.group(1))
    except ValueError:
        return None


_DISPLAY_TIMESTAMP_FORMAT = "%Y-%m-%d %H:%M:%S"


_FRACTIONAL_SECONDS_RE = re.compile(r"\.(\d{7,})")


def format_timestamp(value: Any) -> str:
    """Render a parsed or raw acquisition timestamp consistently.

    Tomography 5 writes full-precision ISO strings such as
    ``2026-03-19T13:52:11.3176724+11:00``. Shown verbatim they were the only
    place in the app using ``T`` separators, seven fractional digits, and a
    timezone offset that the panel then truncated mid-value. Anything that
    will not parse is passed through unchanged rather than hidden.
    """

    if value is None or value == "":
        return "unknown"
    if isinstance(value, datetime):
        return value.strftime(_DISPLAY_TIMESTAMP_FORMAT)

    text = str(value).strip()
    # Deliberately *not* ``parse_datetime``: that normalises to UTC for
    # sorting, which would display a 13:52 local acquisition as 02:52. The
    # wall-clock time the microscope recorded is what the reviewer wants.
    candidate = text.replace("T", " ", 1)
    match = _FRACTIONAL_SECONDS_RE.search(candidate)
    if match is not None:
        # ``fromisoformat`` accepts 3 or 6 fractional digits; Tomography 5
        # writes 7.
        candidate = candidate[: match.start(1) + 6] + candidate[match.end(1) :]
    try:
        parsed = datetime.fromisoformat(candidate)
    except ValueError:
        return text
    return parsed.strftime(_DISPLAY_TIMESTAMP_FORMAT)


def series_duration_seconds(tilt_series: TiltSeries) -> float | None:
    start = parse_datetime(tilt_series.acquisition_time_start)
    end = parse_datetime(tilt_series.acquisition_time_end)
    if start is None or end is None:
        return None
    return max((end - start).total_seconds(), 0)


def format_duration_long(seconds: float | None) -> str:
    if seconds is None:
        return "unknown"
    minutes, second = divmod(int(round(seconds)), 60)
    hours, minute = divmod(minutes, 60)
    if hours:
        return f"{hours}h {minute}m {second}s"
    if minute:
        return f"{minute}m {second}s"
    return f"{second}s"


def format_duration_between(start: datetime, end: datetime) -> str | None:
    """Human-readable ``Δ days, hours, minutes`` between two timestamps.

    Negative or zero deltas return ``None`` so the caller can omit the
    suffix entirely. Days / hours / minutes that are zero are dropped from
    the output, except the smallest non-zero unit always renders. For very
    short windows we fall back to "less than a minute".
    """

    if end <= start:
        return None
    total_seconds = int((end - start).total_seconds())
    days, remainder = divmod(total_seconds, 86_400)
    hours, remainder = divmod(remainder, 3_600)
    minutes = remainder // 60
    parts: list[str] = []
    if days:
        parts.append(f"{days} day" + ("s" if days != 1 else ""))
    if hours:
        parts.append(f"{hours} hour" + ("s" if hours != 1 else ""))
    if minutes:
        parts.append(f"{minutes} minute" + ("s" if minutes != 1 else ""))
    if not parts:
        return "less than a minute"
    if len(parts) == 1:
        return parts[0]
    if len(parts) == 2:
        return f"{parts[0]} and {parts[1]}"
    return f"{parts[0]}, {parts[1]} and {parts[2]}"


def format_duration_compact(seconds: float) -> str:
    total_seconds = int(round(seconds))
    minutes, secs = divmod(total_seconds, 60)
    hours, mins = divmod(minutes, 60)
    if hours:
        return f"{hours}h {mins:02d}m"
    if mins:
        return f"{mins}m {secs:02d}s"
    return f"{secs}s"
