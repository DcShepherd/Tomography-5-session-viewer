"""Immutable indexed series and distributions with exact row membership."""
from __future__ import annotations

from dataclasses import dataclass, replace
import math


@dataclass(frozen=True, slots=True)
class ChartBin:
    lower: float
    upper: float
    row_keys: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class IndexSeries:
    key: str
    label: str
    unit: str
    values: tuple[float | None, ...]
    missing: tuple[str, ...]
    shape: str = "dot"
    median: float | None = None
    bins: tuple[ChartBin, ...] = ()
    tone: str = "blue"
    missing_lane: str = ""


@dataclass(frozen=True, slots=True)
class SlabReference:
    value: float | None = None
    sources: tuple[str, ...] = ()
    note: str = "No resolved reconstruction slab."


@dataclass(frozen=True, slots=True)
class IndexChartModel:
    key: str
    title: str
    unit: str
    description: str
    row_keys: tuple[str, ...]
    names: tuple[str, ...]
    series: tuple[IndexSeries, ...]
    y_range: tuple[float, float]
    y_ticks: tuple[float, ...]
    x_ticks: tuple[int, ...]
    slabs: tuple[SlabReference, ...] = ()


def distribution(values: tuple[float | None, ...], row_keys: tuple[str, ...]) -> tuple[ChartBin, ...]:
    """The same bounded count-bin contract serves numeric metrics and future series."""
    numeric = [(value, key) for value, key in zip(values, row_keys, strict=True) if value is not None]
    if any(not math.isfinite(value) for value, _ in numeric):
        raise ValueError("Distribution values must be finite or explicitly missing.")
    if len(numeric) <= 24:
        return ()
    low, high = min(value for value, _ in numeric), max(value for value, _ in numeric)
    if low == high:
        return (ChartBin(low, high, tuple(key for _, key in numeric)),)
    count = min(12, max(2, math.ceil(math.sqrt(len(numeric)))))
    scale = max(abs(low), abs(high))
    normalized_low, normalized_high = low / scale, high / scale
    members: list[list[str]] = [[] for _ in range(count)]
    for value, key in numeric:
        index = min(count - 1, int((value / scale - normalized_low) / (normalized_high - normalized_low) * count))
        members[index].append(key)
    return tuple(ChartBin(low * (1 - index/count) + high * (index/count),
                         low * (1 - (index + 1)/count) + high * ((index + 1)/count), tuple(keys))
                 for index, keys in enumerate(members) if keys)


def value_axis(extent: list[float]) -> tuple[tuple[float, float], tuple[float, ...]]:
    """Prefer readable ticks; retain finite endpoints when rounding would overflow."""
    raw_low, raw_high = (min(extent), max(extent)) if extent else (0., 1.)
    low, high = raw_low, raw_high
    if low == high:
        margin = abs(low) * .1 if low else 1.
        low, high = low - margin, high + margin
    try:
        rough = (high - low) / 4
        base = 10 ** math.floor(math.log10(rough))
        step = next(value * base for value in (1, 2, 5, 10) if value * base >= rough)
        bottom, top = math.floor(low / step) * step, math.ceil(high / step) * step
        if not math.isfinite(bottom) or not math.isfinite(top) or bottom == top:
            raise ValueError("Rounded bounds are not a finite interval.")
        ticks = tuple(bottom + index * step for index in range(round((top - bottom) / step) + 1))
        return (bottom, top), ticks
    except (ValueError, OverflowError, ZeroDivisionError, StopIteration):
        low, high = raw_low, raw_high
        if low == high:
            low, high = (0., high) if high > 0 else (low, 0.) if low < 0 else (-1., 1.)
        ticks = tuple(dict.fromkeys(low * (1 - fraction) + high * fraction for fraction in (0., .25, .5, .75, 1.)))
        return (low, high), ticks


def chart_model(key: str, title: str, unit: str, description: str, row_keys: tuple[str, ...],
                names: tuple[str, ...], series: tuple[IndexSeries, ...], *,
                slabs: tuple[SlabReference, ...] = ()) -> IndexChartModel:
    if len(names) != len(row_keys) or len(set(row_keys)) != len(row_keys) or (slabs and len(slabs) != len(row_keys)):
        raise ValueError("Chart slots require unique canonical rows and matching names/slabs.")
    completed = []
    extent = [slab.value for slab in slabs if slab.value is not None]
    if any(not math.isfinite(value) for value in extent):
        raise ValueError("Slab references must be finite or explicitly missing.")
    for item in series:
        if item.unit != unit or len(item.values) != len(row_keys) or len(item.missing) != len(row_keys):
            raise ValueError("Chart series must share the slot vector and display unit.")
        numeric = [value for value in item.values if value is not None]
        if any(not math.isfinite(value) for value in numeric):
            raise ValueError("Chart values must be finite or explicitly missing.")
        if any(value is None and not reason for value, reason in zip(item.values, item.missing, strict=True)):
            raise ValueError("Missing chart values need an explanation.")
        numeric.sort()
        middle = len(numeric) // 2
        centre = (numeric[middle] if len(numeric) % 2 else numeric[middle - 1] * .5 + numeric[middle] * .5) if numeric else None
        completed.append(replace(item, median=centre, bins=distribution(item.values, row_keys)))
        extent.extend(numeric)
    bounds, ticks = value_axis(extent)
    n = len(row_keys)
    x_ticks = tuple(range(1, n + 1)) if n <= 24 else tuple(sorted({1, n, *range(50, n, 50)}))
    return IndexChartModel(key, title, unit, description, row_keys, names, tuple(completed), bounds, ticks, x_ticks, slabs)
