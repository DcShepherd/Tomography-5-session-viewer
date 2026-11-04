"""Qt-free text for viewer titles, chips, summaries and fallback status.

Each entity type's texts are one ``_ItemKind`` in ``_KINDS``; the public
functions look a value's kind up once instead of each testing every type.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from operator import attrgetter
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
from tomography_session_browser.domain.status import RowStatus, parse_recorded_status
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

    if isinstance(value, Atlas) and _atlas_status(value) == RowStatus.MISSING:
        return image_missing_state(
            explanation="Atlas metadata exists, but no Atlas_*.jpg or Atlas_*.mrc was found in the sample folder.",
            path=str(value.folder) if value.folder is not None else "",
        )
    return None


def panel_title_for_items(items: list[Any]) -> str:
    if not items:
        return "Items"
    kind = _kind(items[0])
    return kind.plural if kind is not None else "ITEMS"


def singular_title_for_items(items: list[Any]) -> str:
    """``"ATLAS"`` for a list of atlases: what one row of the list is."""

    kind = _kind(items[0]) if items else None
    return kind.singular if kind is not None else panel_title_for_items(items)


def title_for_item(value: Any) -> str:
    kind = _kind(value)
    return kind.title(value) if kind is not None else str(value)


def chips_for_item(value: Any) -> list[str]:
    """The readouts a header shows after its status and the row's summary.

    The header already says what the row says (``ItemListStatus.summary``:
    frames, tile coverage, acquisitions, the search map a tile is on), so a
    chip here is only a fact the row does not carry. The pixel size is the
    image badge's and Context's, never the header's. An overview, search map
    or search tile has none: the row's summary says it all.
    """

    kind = _kind(value)
    return kind.chips(value) if kind is not None else []


def summary_for_item(value: Any) -> str:
    kind = _kind(value)
    return kind.summary(value) if kind is not None else ""


def status_label_for_item(value: Any) -> str:
    kind = _kind(value)
    return kind.status(value) if kind is not None else ""


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


# --- each type's texts --------------------------------------------------------


def _no_text(_value: Any) -> str:
    return ""


def _no_chips(_value: Any) -> list[str]:
    return []


@dataclass(frozen=True, slots=True)
class _ItemKind:
    """How one entity type reads in a viewer tab."""

    #: The list's title, and what one row of it is.
    plural: str
    singular: str
    title: Callable[[Any], str]
    summary: Callable[[Any], str] = _no_text
    chips: Callable[[Any], list[str]] = _no_chips
    #: The status a row shows when the tab has no status provider.
    status: Callable[[Any], str] = _no_text


def _atlas_status(value: Atlas) -> str:
    image_path = value.image_path
    if image_path is None:
        return RowStatus.MISSING
    try:
        return RowStatus.AVAILABLE if image_path.exists() else RowStatus.MISSING
    except OSError:
        return RowStatus.MISSING


def _atlas_chips(value: Atlas) -> list[str]:
    return ["Missing", ATLAS_IMAGE_MISSING_SUMMARY] if _atlas_status(value) == RowStatus.MISSING else []


def _overview_summary(value: Overview) -> str:
    return count_phrase(len(value.linked_search_map_ids), "linked search map")


def _search_map_summary(value: SearchMap) -> str:
    tile_count = len({path.stem for path in value.tile_paths})
    return f"{count_phrase(tile_count, 'tile')} · {count_phrase(len(value.linked_batch_position_ids), 'batch position')}"


def _search_map_status(value: SearchMap) -> str:
    return RowStatus.DONE if value.tile_paths else "empty"


def _search_tile_summary(value: SearchTile) -> str:
    parts = []
    if value.search_map_name:
        parts.append(value.search_map_name)
    if value.batch_position_name:
        parts.append(value.batch_position_name)
    if value.linked_tilt_series_ids:
        parts.append(count_phrase(len(value.linked_tilt_series_ids), "tilt series", "tilt series"))
    return " · ".join(parts)


def _search_tile_status(value: SearchTile) -> str:
    return RowStatus.DONE if value.image_path is not None else RowStatus.MISSING


def _batch_summary(value: BatchPosition) -> str:
    status = value.status or "unknown"
    return f"{status} · {count_phrase(len(value.linked_tilt_series_ids), 'linked tilt series', 'linked tilt series')}"


def _batch_chips(value: BatchPosition) -> list[str]:
    # The recorded parser status is in the context panel; the header shows
    # the shared row status instead (``ViewerHeader.update_header``).
    chips = []
    linked = len(value.linked_tilt_series_ids)
    if linked and linked != planned_exposures_for_batch_position(value):
        chips.append(f"{linked} tilt series")
    return chips


def _batch_status(value: BatchPosition) -> str:
    # Without a status provider a batch shows its recorded word, read by
    # the one parse; "fail" and "queued" are this fallback's own words.
    recorded = parse_recorded_status(value.status)
    if recorded == RowStatus.DONE:
        return RowStatus.DONE
    if recorded == RowStatus.FAILED:
        return "fail"
    if recorded == RowStatus.PENDING:
        return "queued"
    return (value.status or "").strip().lower()


def _tilt_summary(value: TiltSeries) -> str:
    tilt_count = value.tilt_count or len(value.sections)
    frames = count_phrase(tilt_count, "frame") if tilt_count else "unknown frames"
    return f"{frames} · {range_chip(value.tilt_range, DEGREE) if value.tilt_range else 'range unknown'}"


def _tilt_chips(value: TiltSeries) -> list[str]:
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


def _tilt_status(value: TiltSeries) -> str:
    if value.mrc_path is None or not value.mrc_path.exists():
        return RowStatus.MISSING
    return RowStatus.AVAILABLE


_KINDS: dict[type, _ItemKind] = {
    # An atlas's row summary is its prepared contact-sheet context, not this.
    Atlas: _ItemKind("ATLASES", "ATLAS", lambda value: "Atlas", chips=_atlas_chips, status=_atlas_status),
    Overview: _ItemKind("OVERVIEWS", "OVERVIEW", lambda value: format_overview_display_name(value.name),
                        summary=_overview_summary),
    SearchMap: _ItemKind("SEARCH MAPS", "SEARCH MAP", attrgetter("name"),
                         summary=_search_map_summary, status=_search_map_status),
    SearchTile: _ItemKind("SEARCH TILES", "SEARCH TILE", attrgetter("name"),
                          summary=_search_tile_summary, status=_search_tile_status),
    BatchPosition: _ItemKind("BATCH POSITIONS", "BATCH POSITION", lambda value: value.name or value.id,
                             summary=_batch_summary, chips=_batch_chips, status=_batch_status),
    TiltSeries: _ItemKind("TILT SERIES", "TILT SERIES", attrgetter("name"),
                          summary=_tilt_summary, chips=_tilt_chips, status=_tilt_status),
}


def _kind(value: Any) -> _ItemKind | None:
    return next((_KINDS[cls] for cls in type(value).__mro__ if cls in _KINDS), None)
