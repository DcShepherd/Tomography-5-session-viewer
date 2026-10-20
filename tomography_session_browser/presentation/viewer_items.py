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
from tomography_session_browser.domain.units import DEGREE
from tomography_session_browser.presentation.empty_states import EmptyState, image_missing_state
from tomography_session_browser.presentation.formatting import format_duration_compact
from tomography_session_browser.services.entity_links import planned_exposures_for_batch_position
from tomography_session_browser.services.timeline_service import build_acquisition_timeline


STATUS_ROW_TYPES = (SearchMap, SearchTile, BatchPosition, TiltSeries)

#: What an atlas's header says, after "Missing", when its image file is not there.
ATLAS_IMAGE_MISSING_SUMMARY = "Image file not found"


def missing_image_state(value: Any) -> EmptyState | None:
    """The page for an item whose image file is not there, where that has a form of its own.

    Today that is an atlas: its record is there, and the folder it was read
    from has no mosaic. ``None`` for anything else, whose page is the general
    "no preview" one.
    """

    if isinstance(value, Atlas) and status_label_for_item(value) == "missing":
        return image_missing_state(
            explanation="Atlas metadata exists, but no Atlas_*.jpg or Atlas_*.mrc was found in the sample folder.",
            path=str(value.folder) if value.folder is not None else "",
        )
    return None


def panel_title_for_items(items: list[Any]) -> str:
    if not items:
        return "Items"
    first = items[0]
    if isinstance(first, Overview):
        return "OVERVIEWS"
    if isinstance(first, SearchMap):
        return "SEARCH MAPS"
    if isinstance(first, SearchTile):
        return "SEARCH TILES"
    if isinstance(first, BatchPosition):
        return "BATCH POSITIONS"
    if isinstance(first, TiltSeries):
        return "TILT SERIES"
    if isinstance(first, Atlas):
        return "ATLASES"
    return "ITEMS"


_SINGULAR_TITLES = {
    "OVERVIEWS": "OVERVIEW",
    "SEARCH MAPS": "SEARCH MAP",
    "SEARCH TILES": "SEARCH TILE",
    "BATCH POSITIONS": "BATCH POSITION",
    "ATLASES": "ATLAS",
}


def singular_title_for_items(items: list[Any]) -> str:
    """``"ATLAS"`` for a list of atlases: what one row of the list is."""

    title = panel_title_for_items(items)
    return _SINGULAR_TITLES.get(title, title)


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
    """The readouts a header shows after its status and the row's summary.

    The header already says what the row says (``ItemListStatus.summary``:
    frames, tile coverage, acquisitions, the search map a tile is on), so a
    chip here is only a fact the row does not carry. The pixel size is the
    image badge's and Context's, never the header's.
    """

    if isinstance(value, Atlas):
        return ["Missing", ATLAS_IMAGE_MISSING_SUMMARY] if status_label_for_item(value) == "missing" else []
    if isinstance(value, BatchPosition):
        # The recorded parser status is in the context panel; the header shows
        # the shared row status instead (``ViewerTab._update_header``).
        chips = []
        linked = len(value.linked_tilt_series_ids)
        if linked and linked != planned_exposures_for_batch_position(value):
            chips.append(f"{linked} tilt series")
        return chips
    if isinstance(value, TiltSeries):
        frames = value.tilt_count or len(value.sections)
        if frames == 1:
            # One frame has no step, range or duration: those chips read as
            # measured ("-0.02° → -0.02°", "0s"). Say what it is, truthfully.
            angle = value.tilt_range[0] if value.tilt_range else None
            return [f"single frame at {angle:g}{DEGREE}" if angle is not None else "single frame"]
        chips = []
        step = tilt_step_chip(value)
        if step:
            chips.append(step)
        if value.tilt_range:
            chips.append(range_chip(value.tilt_range, DEGREE))
        duration = tilt_duration_chip(value)
        if duration:
            chips.append(duration)
        return chips
    # Overview, search map and search tile: the row's summary says it all.
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
        # The prepared row supplies its shared contact-sheet context.
        return ""
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
