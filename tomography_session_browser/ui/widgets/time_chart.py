"""Shared helpers for dashboard time-based chart widgets."""

from __future__ import annotations

from datetime import datetime, timedelta


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


def time_axis_tick_count(width: float) -> int:
    """Return a compact tick count shared by the timeline and defocus chart."""

    if width < 420:
        return 3
    if width < 760:
        return 4
    return 5


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
