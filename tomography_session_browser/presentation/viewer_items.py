"""Qt-free text for viewer titles, chips, summaries and fallback status."""
from __future__ import annotations

from typing import Any
from tomography_session_browser.domain.display_names import count_phrase, format_overview_display_name
from tomography_session_browser.domain.models import (
    Atlas,
    BatchPosition,
    Overview,
    SearchMap,
    SearchTile,
    TiltSeries,
)
from tomography_session_browser.domain.units import ANGSTROM_PER_PIXEL, DEGREE
from tomography_session_browser.presentation.formatting import format_duration_compact
from tomography_session_browser.services.item_status import atlas_tile_count
from tomography_session_browser.services.timeline_service import build_acquisition_timeline


STATUS_ROW_TYPES = (SearchMap, SearchTile, BatchPosition, TiltSeries)


def panel_title_for_items(items: list[Any]) -> str:
    if not items:
        return "Items"
    first = items[0]
    if isinstance(first, Overview):
        return "OVERVIEWS"
    if isinstance(first, SearchMap):
        return "SEARCH MAPS"
    if isinstance(first, SearchTile):
        return "SEARCH"
    if isinstance(first, BatchPosition):
        return "BATCH POSITIONS"
    if isinstance(first, TiltSeries):
        return "TILT SERIES"
    if isinstance(first, Atlas):
        return "ATLASES"
    return "ITEMS"


def title_for_item(value: Any) -> str:
    if isinstance(value, Atlas):
        return "Atlas"
    if isinstance(value, Overview):
        return format_overview_display_name(value.name)
    if isinstance(value, SearchMap):
        return value.name
    if isinstance(value, SearchTile):
        return value.name
    if isinstance(value, BatchPosition):
        return value.name or value.id
    if isinstance(value, TiltSeries):
        return value.name
    return str(value)


def chips_for_item(value: Any) -> list[str]:
    if isinstance(value, Atlas):
        chips: list[str] = []
        tile_count = atlas_tile_count(value)
        if tile_count:
            chips.append(count_phrase(tile_count, "tile"))
        if value.mrc_metadata and value.mrc_metadata.nx and value.mrc_metadata.ny:
            chips.append(f"{value.mrc_metadata.nx} × {value.mrc_metadata.ny}")
        return chips
    if isinstance(value, Overview):
        chips = []
        if value.linked_search_map_ids:
            chips.append(count_phrase(len(value.linked_search_map_ids), "search map"))
        return chips
    if isinstance(value, SearchMap):
        chips = []
        if value.tile_paths:
            chips.append(count_phrase(len({path.stem for path in value.tile_paths}), "tile"))
        if value.linked_batch_position_ids:
            chips.append(count_phrase(len(value.linked_batch_position_ids), "batch position"))
        return chips
    if isinstance(value, SearchTile):
        # The title already reads "<exposure> · Tile N" (harmonisation B3), so
        # no tile chip, and the batch only when it is not that exposure.
        chips = []
        if value.batch_position_name and not (value.name or "").startswith(f"{value.batch_position_name} · "):
            chips.append(value.batch_position_name)
        if value.linked_tilt_series_ids:
            chips.append(f"{len(value.linked_tilt_series_ids)} tilt series")
        return chips
    if isinstance(value, BatchPosition):
        # The recorded parser status is in the context panel; the header shows
        # the shared row status instead (``ViewerTab._update_header``).
        chips = []
        if value.linked_tilt_series_ids:
            chips.append(f"{len(value.linked_tilt_series_ids)} tilt series")
        return chips
    if isinstance(value, TiltSeries):
        chips = []
        if value.tilt_count:
            chips.append(count_phrase(value.tilt_count, "frame"))
        step = tilt_step_chip(value)
        if step:
            chips.append(step)
        if value.tilt_range:
            chips.append(range_chip(value.tilt_range, DEGREE))
        duration = tilt_duration_chip(value)
        if duration:
            chips.append(duration)
        if value.pixel_size:
            chips.append(f"{value.pixel_size:g} {ANGSTROM_PER_PIXEL}")
        return chips
    return []


def summary_for_item(value: Any) -> str:
    if isinstance(value, Overview):
        return count_phrase(len(value.linked_search_map_ids), "linked search map")
    if isinstance(value, SearchMap):
        tile_count = len({path.stem for path in value.tile_paths})
        return f"{count_phrase(tile_count, 'tile')} · {count_phrase(len(value.linked_batch_position_ids), 'batch position')}"
    if isinstance(value, SearchTile):
        parts = []
        if value.search_map_name:
            parts.append(value.search_map_name)
        if value.batch_position_name:
            parts.append(value.batch_position_name)
        if value.linked_tilt_series_ids:
            parts.append(count_phrase(len(value.linked_tilt_series_ids), "tilt series", "tilt series"))
        return " · ".join(parts)
    if isinstance(value, BatchPosition):
        status = value.status or "unknown"
        return f"{status} · {count_phrase(len(value.linked_tilt_series_ids), 'linked tilt series', 'linked tilt series')}"
    if isinstance(value, TiltSeries):
        tilt_count = value.tilt_count or len(value.sections)
        frames = count_phrase(tilt_count, "frame") if tilt_count else "unknown frames"
        return f"{frames} · {range_chip(value.tilt_range, DEGREE) if value.tilt_range else 'range unknown'}"
    if isinstance(value, Atlas):
        return count_phrase(atlas_tile_count(value), "tile")
    return ""


def status_label_for_item(value: Any) -> str:
    if isinstance(value, Atlas):
        image_path = value.image_path
        if image_path is None:
            return "missing"
        try:
            return "available" if image_path.exists() else "missing"
        except OSError:
            return "missing"
    if isinstance(value, BatchPosition):
        raw = (value.status or "").strip().lower()
        if raw in {"acquired", "done", "complete", "completed"}:
            return "done"
        if raw in {"failed", "error"}:
            return "fail"
        if raw in {"pending", "queued", "scheduled"}:
            return "queued"
        return raw or ""
    if isinstance(value, SearchMap):
        return "done" if value.tile_paths else "empty"
    if isinstance(value, SearchTile):
        return "done" if value.image_path is not None else "missing"
    if isinstance(value, TiltSeries):
        if value.mrc_path is None or not value.mrc_path.exists():
            return "missing"
        return "available"
    return ""


def range_chip(value: tuple[float, float] | None, suffix: str) -> str:
    if value is None:
        return "unknown"
    lo, hi = value
    return f"{lo:g}{suffix} → {hi:g}{suffix}"


def tilt_step_chip(value: TiltSeries) -> str | None:
    if value.tilt_range is None:
        return None
    # ``tilt_range`` spans the MDOC sections' angles, so divide by their count.
    # ``tilt_count`` is the frames in the stack, fewer when it was truncated.
    count = len(value.sections) or value.tilt_count or 0
    if count <= 1:
        return None
    low, high = value.tilt_range
    step = abs((high - low) / (count - 1))
    if step <= 0:
        return None
    return f"{step:g}{DEGREE} step"


def tilt_duration_chip(value: TiltSeries) -> str | None:
    segment = build_acquisition_timeline(value)
    if segment is None:
        return None
    seconds = max((segment.end - segment.start).total_seconds(), 0)
    return format_duration_compact(seconds)
