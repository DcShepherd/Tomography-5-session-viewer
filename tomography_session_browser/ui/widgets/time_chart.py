"""Shared helpers for dashboard time-based chart widgets."""

from __future__ import annotations

from datetime import datetime, timedelta
from math import ceil, floor, isfinite, log10


TIME_CHART_LEFT_GUTTER = 108
TIME_CHART_RIGHT_GUTTER = 12
TIME_CHART_TOP = 8
TIME_CHART_BOTTOM_AXIS_SPACE = 54
TIME_CHART_AXIS_LABEL_GAP = 7
TIME_CHART_TIMELINE_MIN_HEIGHT = 220
TIME_CHART_DEFOCUS_MIN_HEIGHT = 300
TIME_CHART_CARD_PADDING = (12, 9, 12, 10)
TIME_CHART_CARD_SPACING = 6
TIME_CHART_ELAPSED_AXIS_LABEL = "Time from start"


#: Tick spacings that read as round numbers once scaled by a power of ten.
_NICE_STEP_MANTISSAS = (1.0, 2.0, 2.5, 5.0)


def time_axis_tick_count(width: float) -> int:
    """Return a compact tick count shared by the timeline and defocus chart."""

    if width < 420:
        return 3
    if width < 760:
        return 4
    return 5


#: Comfortable vertical rhythm between value-axis gridlines, in pixels. A
#: label is ~14px tall, so this leaves generous breathing room while still
#: giving :func:`nice_axis_ticks` enough of a budget to choose a tight step.
#: Too small a budget forces a coarse step and a slack axis: the defocus plot
#: with a budget of 4 could only reach ±20 for data spanning ±11.
_VALUE_AXIS_TICK_SPACING = 34
_VALUE_AXIS_MAX_TICKS = 8


def value_axis_tick_count(height: float) -> int:
    """Return how many gridlines a numeric (vertical) axis of ``height`` fits.

    The value axis used to borrow :func:`time_axis_tick_count`, which is
    derived from *width*. Sizing it from the axis it actually runs along
    keeps charts from being restricted to a handful of labels.
    """

    fitted = int(max(height, 0) // _VALUE_AXIS_TICK_SPACING) + 1
    return max(3, min(_VALUE_AXIS_MAX_TICKS, fitted))


def nice_axis_ticks(
    low: float,
    high: float,
    *,
    max_ticks: int,
) -> tuple[float, float, list[float]]:
    """Snap ``[low, high]`` outward onto a round-number grid.

    Returns ``(axis_min, axis_max, ticks)``. Previously the charts divided
    the raw data range into equal fractions, which produced labels such as
    ``+10.9 / +5.3 / -0.2 / -5.8`` on the defocus plot — technically correct
    but impossible to read a value off. Ticks land on multiples of
    1, 2, 2.5, or 5 times a power of ten, and the returned bounds are the
    first and last tick so the gridlines meet the plot edges exactly.

    ``max_ticks`` is an upper bound: the smallest qualifying step that keeps
    the tick count within budget wins, so the axis stays as tight around the
    data as the round-number constraint allows.
    """

    max_ticks = max(2, int(max_ticks))
    if not isfinite(low) or not isfinite(high):
        return low, high, [low, high]
    if high < low:
        low, high = high, low

    span = high - low
    if span <= 0:
        pad = abs(low) * 0.1 or 0.5
        low, high = low - pad, high + pad
        span = high - low

    # Start one decade below the smallest step that could possibly work, then
    # walk upward until the tick budget is satisfied.
    exponent = floor(log10(span / (max_ticks - 1))) - 1
    for offset in range(0, 12):
        base = 10.0 ** (exponent + offset)
        for mantissa in _NICE_STEP_MANTISSAS:
            step = mantissa * base
            if step <= 0:
                continue
            first = floor(low / step)
            last = ceil(high / step)
            if last == first:
                last = first + 1
            if (last - first) + 1 <= max_ticks:
                ticks = [round((first + index) * step, 10) for index in range(int(last - first) + 1)]
                return ticks[0], ticks[-1], ticks

    return low, high, [low, high]


def format_absolute_time(value: datetime) -> str:
    return value.strftime("%Y-%m-%d %H:%M")


def format_elapsed(delta: timedelta) -> str:
    seconds = max(int(delta.total_seconds()), 0)
    hours, remainder = divmod(seconds, 3600)
    minutes = remainder // 60
    if hours:
        return f"{hours}h {minutes:02d}m elapsed"
    return f"{minutes}m elapsed"


def format_elapsed_offset(delta: timedelta) -> str:
    seconds = max(int(delta.total_seconds()), 0)
    hours, remainder = divmod(seconds, 3600)
    minutes = remainder // 60
    return f"{hours:02d}:{minutes:02d}"


def format_time_axis_tick(value: datetime, start: datetime, end: datetime) -> str:
    """Format a time-axis tick using the dashboard timeline convention."""

    _ = end
    return format_elapsed_offset(value - start)


def format_density_bin_duration(delta: timedelta) -> str:
    seconds = max(int(delta.total_seconds()), 1)
    if seconds < 90:
        return f"{seconds} sec"
    minutes = max(1, round(seconds / 60))
    if minutes < 90:
        return f"{minutes} min"
    hours = minutes / 60
    if hours < 24:
        if abs(hours - round(hours)) < 0.05:
            return f"{round(hours)} h"
        return f"{hours:.1f} h"
    days = hours / 24
    if abs(days - round(days)) < 0.05:
        return f"{round(days)} d"
    return f"{days:.1f} d"
