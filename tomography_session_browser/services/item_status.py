from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable

from tomography_session_browser.domain.display_names import count_phrase as _count_phrase
from tomography_session_browser.domain.models import BatchPosition, SearchMap, SearchTile, TiltSeries
from tomography_session_browser.domain.units import ANGSTROM_PER_PIXEL
from tomography_session_browser.parsers.path_utils import natural_key
from tomography_session_browser.parsers.xml_parser import find_first
from tomography_session_browser.services.batch_inference import (
    batch_inference_key,
    batch_inference_keys,
    inferred_batch_label_for_tilt,
)
from tomography_session_browser.services.timeline_service import datetime_sort_key
from tomography_session_browser.services.tilt_series_validation import (
    STATUS_COMPLETE,
    STATUS_FAILED,
    STATUS_INCOMPLETE,
    STATUS_UNKNOWN,
    TiltSeriesValidation,
    actual_tilt_count,
    validate_session_tilt_series,
)


STATUS_DONE = "done"
STATUS_PARTIAL = "partial"
STATUS_INCOMPLETE_LABEL = "incomplete"
STATUS_FAILED_LABEL = "failed"
STATUS_MISSING = "missing"
STATUS_UNKNOWN_LABEL = "unknown"


@dataclass(frozen=True, slots=True)
class ItemListStatus:
    status: str
    summary: str
    tooltip: str = ""


@dataclass(frozen=True, slots=True)
class ItemStatusContext:
    search_maps: tuple[SearchMap, ...]
    search_tiles: tuple[SearchTile, ...]
    batch_positions: tuple[BatchPosition, ...]
    tilt_series: tuple[TiltSeries, ...]
    validations: dict[str, TiltSeriesValidation]
    inferred_batch_labels: dict[str, str]


@dataclass(frozen=True, slots=True)
class BatchPositionStatusCounts:
    """Validator-backed acquisition counts for one batch position.

    The Atlas LOD overlay consumes this public, read-only summary instead of
    duplicating the viewer-list classification rules. ``include_inferred`` is
    deliberately configurable because orphaned failed tilt series remain
    distinct markers on the Atlas rather than being promoted into a scientific
    batch relationship.
    """

    planned: int
    complete: int
    incomplete: int
    failed: int
    missing: int
    unknown: int

    @property
    def acquired(self) -> int:
        return self.complete + self.incomplete + self.failed + self.unknown


def build_item_status_context(
    *,
    search_maps: Iterable[SearchMap] = (),
    search_tiles: Iterable[SearchTile] = (),
    batch_positions: Iterable[BatchPosition] = (),
    tilt_series: Iterable[TiltSeries] = (),
) -> ItemStatusContext:
    """Build the shared status context for viewer-list rows.

    This mirrors the dashboard/report source of truth: tilt series are
    classified through ``validate_session_tilt_series`` and search maps /
    batch positions derive their list badges from the linked tilt series and
    planned exposure counts.
    """

    search_maps = tuple(search_maps)
    search_tiles = tuple(search_tiles)
    batch_positions = tuple(batch_positions)
    tilt_series = tuple(tilt_series)
    validations = {v.tilt_series_id: v for v in validate_session_tilt_series(tilt_series)}
    return ItemStatusContext(
        search_maps=search_maps,
        search_tiles=search_tiles,
        batch_positions=batch_positions,
        tilt_series=tilt_series,
        validations=validations,
        inferred_batch_labels=_inferred_failed_batch_labels(
            batch_positions,
            tilt_series,
            validations,
        ),
    )


def item_list_status(value: Any, context: ItemStatusContext | None, *, include_inferred: bool = True) -> ItemListStatus:
    if context is None:
        return ItemListStatus(status=STATUS_UNKNOWN_LABEL, summary="")
    if isinstance(value, SearchMap):
        return _search_map_status(value, context)
    if isinstance(value, SearchTile):
        return _search_tile_status(value, context)
    if isinstance(value, BatchPosition):
        return _batch_position_status(value, context, include_inferred=include_inferred)
    if isinstance(value, TiltSeries):
        return _tilt_series_status(value, context)
    return ItemListStatus(status=STATUS_UNKNOWN_LABEL, summary="")


def batch_position_status_counts(
    batch: BatchPosition,
    context: ItemStatusContext,
    *,
    include_inferred: bool = True,
) -> BatchPositionStatusCounts:
    """Return the shared planned/acquired outcome counts for ``batch``."""

    linked_tilts = _tilt_series_for_batch(
        batch,
        context,
        include_inferred=include_inferred,
    )
    planned = _planned_exposures_for_batch(batch, linked_tilts)
    counts = _status_counts(linked_tilts, context)
    counts.missing += max(planned - len(linked_tilts), 0)
    return BatchPositionStatusCounts(
        planned=planned,
        complete=counts.complete,
        incomplete=counts.incomplete,
        failed=counts.failed,
        missing=counts.missing,
        unknown=counts.unknown,
    )


def batch_positions_for_search_map(
    search_map: SearchMap,
    context: ItemStatusContext,
) -> list[BatchPosition]:
    """Batches linked to ``search_map`` in either metadata direction.

    Tomography 5 records the relationship on whichever side happened to be
    written, so a row that reads only ``BatchPosition.linked_search_map_id``
    disagrees with navigation, which also honours
    ``SearchMap.linked_batch_position_ids``. Both directions are accepted here
    and deduplicated by batch ID, so metadata written on both sides counts
    once.

    Only explicit metadata participates. Inferred failed-batch labels stay out:
    an orphaned failed tilt series must not make a Search map claim a batch
    that no metadata links to it.
    """

    reciprocal_ids = set(search_map.linked_batch_position_ids or [])
    linked: list[BatchPosition] = []
    seen: set[str] = set()
    for batch in context.batch_positions:
        if batch.id in seen:
            continue
        if batch.linked_search_map_id == search_map.id or batch.id in reciprocal_ids:
            linked.append(batch)
            seen.add(batch.id)
    return linked


def _search_map_status(search_map: SearchMap, context: ItemStatusContext) -> ItemListStatus:
    linked_batches = batch_positions_for_search_map(search_map, context)
    counts = _StatusCounts()
    planned = 0
    for batch in linked_batches:
        batch_tilts = _tilt_series_for_batch(batch, context)
        batch_planned = _planned_exposures_for_batch(batch, batch_tilts)
        planned += batch_planned
        counts += _status_counts(batch_tilts, context)
        counts.missing += max(batch_planned - len(batch_tilts), 0)

    tile_planned, tile_acquired = _search_map_tile_counts(search_map)
    tile_count = tile_acquired or len({path.stem for path in search_map.tile_paths})
    if planned:
        status = _status_from_counts(counts, planned=planned)
        progress = f"{counts.complete}/{planned} tilt series"
    else:
        status = STATUS_DONE if tile_count else STATUS_MISSING
        progress = "no linked batch positions" if not linked_batches else "0 planned tilt series"

    if tile_planned > 0 and tile_acquired < tile_planned:
        tile_status = STATUS_MISSING if tile_acquired <= 0 else STATUS_PARTIAL
        status = _combine_status(status, tile_status)

    tile_text = (
        f"{tile_acquired}/{tile_planned} tiles"
        if tile_planned > 0
        else _count_phrase(tile_count, "tile")
    )
    parts = [tile_text, _count_phrase(len(linked_batches), "batch position"), progress]
    if counts.failed:
        parts.append(f"{counts.failed} failed")
    if counts.incomplete:
        parts.append(f"{counts.incomplete} incomplete")
    tooltip = _join_tooltip(
        search_map.name,
        parts,
        status=status,
    )
    return ItemListStatus(status=status, summary=" · ".join(parts), tooltip=tooltip)


def _search_tile_status(search_tile: SearchTile, context: ItemStatusContext) -> ItemListStatus:
    batch = next(
        (item for item in context.batch_positions if item.id == search_tile.batch_position_id),
        None,
    )
    if batch is None:
        status = STATUS_DONE if search_tile.image_path is not None else STATUS_MISSING
        parts = []
        if search_tile.tile_index is not None:
            parts.append(f"tile {search_tile.tile_index}")
        if search_tile.search_map_name:
            parts.append(search_tile.search_map_name)
        if search_tile.image_path is None:
            parts.append("no image")
        tooltip = _join_tooltip(search_tile.name, parts or ["No linked batch position"], status=status)
        return ItemListStatus(status=status, summary=" · ".join(parts), tooltip=tooltip)

    batch_status = _batch_position_status(batch, context)
    linked_tilts = [
        tilt for tilt in context.tilt_series if tilt.id in set(search_tile.linked_tilt_series_ids or [])
    ]
    if linked_tilts:
        counts = _status_counts(linked_tilts, context)
        status = _status_from_counts(counts, planned=len(linked_tilts))
    else:
        status = batch_status.status
    if search_tile.image_path is None:
        status = _combine_status(status, STATUS_MISSING)
    parts: list[str] = []
    if search_tile.tile_index is not None:
        parts.append(f"tile {search_tile.tile_index}")
    if search_tile.batch_position_name:
        parts.append(search_tile.batch_position_name)
    if search_tile.linked_tilt_series_ids:
        parts.append(_count_phrase(len(search_tile.linked_tilt_series_ids), "tilt series", "tilt series"))
    if search_tile.image_path is None:
        parts.append("no image")
    tooltip_lines = [
        search_tile.name,
        f"Status: {status}",
        f"Search map: {search_tile.search_map_name or 'unknown'}",
        f"Batch position: {search_tile.batch_position_name or 'unknown'}",
        f"Linked tilt series: {len(search_tile.linked_tilt_series_ids)}",
    ]
    if batch_status.summary:
        tooltip_lines.append(batch_status.summary)
    if search_tile.link_method:
        tooltip_lines.append(f"Linked by: {search_tile.link_method}")
    if search_tile.warnings:
        tooltip_lines.extend(search_tile.warnings)
    return ItemListStatus(status=status, summary=" · ".join(parts), tooltip="\n".join(tooltip_lines))


def _batch_position_status(batch: BatchPosition, context: ItemStatusContext, *, include_inferred: bool = True) -> ItemListStatus:
    details = batch_position_status_counts(batch, context, include_inferred=include_inferred)
    return batch_position_status_from_counts(batch, details)


def batch_position_status_from_counts(batch: BatchPosition, details: BatchPositionStatusCounts) -> ItemListStatus:
    """Classify already-derived counts for GUI, Atlas and presenter consumers."""
    planned = details.planned
    counts = _StatusCounts(
        complete=details.complete,
        incomplete=details.incomplete,
        failed=details.failed,
        missing=details.missing,
        unknown=details.unknown,
    )

    explicit_status = _normalise_batch_status(batch.status)
    if planned == 0:
        status = explicit_status or STATUS_UNKNOWN_LABEL
    elif explicit_status == STATUS_FAILED_LABEL and not counts.failed:
        counts.failed += 1
        counts.missing = max(counts.missing - 1, 0)
        status = STATUS_FAILED_LABEL
    else:
        status = _status_from_counts(counts, planned=planned)

    parts = [
        _count_phrase(planned, "exposure area") if planned else "no planned exposure areas"
    ]
    if counts.complete:
        parts.append(f"{counts.complete} complete")
    if counts.failed:
        parts.append(f"{counts.failed} failed")
    if counts.incomplete:
        parts.append(f"{counts.incomplete} incomplete")
    if counts.missing:
        parts.append(f"{counts.missing} missing")
    tooltip = _join_tooltip(batch.name or batch.id, parts, status=status)
    return ItemListStatus(status=status, summary=" · ".join(parts), tooltip=tooltip)


def _tilt_series_status(tilt: TiltSeries, context: ItemStatusContext) -> ItemListStatus:
    validation = context.validations.get(tilt.id)
    status = _status_from_validation(tilt, validation)
    parts: list[str] = []
    if validation is not None:
        if validation.expected_count:
            parts.append(f"{validation.actual_count}/{validation.expected_count} frames")
        else:
            parts.append(_count_phrase(validation.actual_count, "frame"))
    else:
        actual = _actual_tilt_count(tilt)
        parts.append(_count_phrase(actual, "frame") if actual else "unknown frames")
    if tilt.pixel_size:
        parts.append(f"{tilt.pixel_size:g} {ANGSTROM_PER_PIXEL}")
    inferred = context.inferred_batch_labels.get(tilt.id)
    if inferred:
        parts.append(f"inferred batch position {inferred}")
    tooltip_lines = [tilt.name]
    if validation is not None:
        tooltip_lines.append(f"Status: {_display_status(status)}")
        tooltip_lines.append(f"Reason: {validation.reason}")
    if inferred:
        tooltip_lines.append(f"Inferred batch position: {inferred}")
    return ItemListStatus(status=status, summary=" · ".join(parts), tooltip="\n".join(tooltip_lines))


@dataclass(slots=True)
class _StatusCounts:
    complete: int = 0
    incomplete: int = 0
    failed: int = 0
    missing: int = 0
    unknown: int = 0

    def __iadd__(self, other: "_StatusCounts") -> "_StatusCounts":
        self.complete += other.complete
        self.incomplete += other.incomplete
        self.failed += other.failed
        self.missing += other.missing
        self.unknown += other.unknown
        return self


def _status_counts(
    tilt_series: Iterable[TiltSeries],
    context: ItemStatusContext,
) -> _StatusCounts:
    counts = _StatusCounts()
    for tilt in tilt_series:
        status = _status_from_validation(tilt, context.validations.get(tilt.id))
        if status == STATUS_DONE:
            counts.complete += 1
        elif status == STATUS_FAILED_LABEL:
            counts.failed += 1
        elif status == STATUS_INCOMPLETE_LABEL:
            counts.incomplete += 1
        elif status == STATUS_MISSING:
            counts.missing += 1
        else:
            counts.unknown += 1
    return counts


def _status_from_counts(counts: _StatusCounts, *, planned: int) -> str:
    if planned <= 0:
        return STATUS_UNKNOWN_LABEL
    if counts.complete >= planned and not (counts.failed or counts.incomplete or counts.missing):
        return STATUS_DONE
    if counts.failed and not counts.complete and not counts.incomplete:
        return STATUS_FAILED_LABEL
    if counts.complete or counts.failed or counts.incomplete:
        return STATUS_PARTIAL
    if counts.missing >= planned:
        return STATUS_MISSING
    return STATUS_INCOMPLETE_LABEL


def _combine_status(left: str, right: str) -> str:
    order = {
        STATUS_DONE: 0,
        STATUS_UNKNOWN_LABEL: 1,
        STATUS_MISSING: 2,
        STATUS_INCOMPLETE_LABEL: 3,
        STATUS_PARTIAL: 4,
        STATUS_FAILED_LABEL: 5,
    }
    return left if order.get(left, 1) >= order.get(right, 1) else right


def _status_from_validation(
    tilt: TiltSeries,
    validation: TiltSeriesValidation | None,
) -> str:
    if tilt.mrc_path is None or not tilt.mrc_path.exists():
        return STATUS_MISSING
    if validation is None:
        return STATUS_UNKNOWN_LABEL
    if validation.status == STATUS_COMPLETE:
        return STATUS_DONE
    if validation.status == STATUS_FAILED:
        return STATUS_FAILED_LABEL
    if validation.status == STATUS_INCOMPLETE:
        return STATUS_INCOMPLETE_LABEL
    if validation.status == STATUS_UNKNOWN:
        return STATUS_MISSING if validation.actual_count == 0 else STATUS_UNKNOWN_LABEL
    return STATUS_UNKNOWN_LABEL


def _normalise_batch_status(status: str | None) -> str | None:
    raw = (status or "").strip().lower()
    if raw in {"acquired", "done", "complete", "completed"}:
        return STATUS_DONE
    if raw in {"failed", "error"}:
        return STATUS_FAILED_LABEL
    if raw in {"pending", "queued", "scheduled", "missing"}:
        return STATUS_MISSING
    if raw in {"partial", "incomplete", "warning"}:
        return STATUS_PARTIAL
    return None


def _tilt_series_for_batch(
    batch: BatchPosition,
    context: ItemStatusContext,
    *,
    include_inferred: bool = True,
) -> list[TiltSeries]:
    linked_ids = set(batch.linked_tilt_series_ids or [])
    batch_keys = _batch_inference_keys(batch)
    linked: list[TiltSeries] = []
    seen: set[str] = set()
    for tilt in context.tilt_series:
        if tilt.id in seen:
            continue
        inferred_key = (
            batch_inference_key(context.inferred_batch_labels.get(tilt.id))
            if include_inferred
            else ""
        )
        if (
            tilt.id in linked_ids
            or tilt.linked_batch_position_id == batch.id
            or (inferred_key and inferred_key in batch_keys)
        ):
            linked.append(tilt)
            seen.add(tilt.id)
    linked.sort(
        key=lambda tilt: (
            *datetime_sort_key(tilt.acquisition_time_start),
            natural_key(tilt.name or tilt.id),
        )
    )
    return linked


def _planned_exposures_for_batch(batch: BatchPosition, linked_tilts: list[TiltSeries]) -> int:
    planned = planned_exposures_from_metadata(batch.metadata or {})
    if planned:
        return max(planned, len(linked_tilts))
    if batch.exposure_image_paths:
        return max(len(batch.exposure_image_paths), len(linked_tilts))
    if batch.linked_tilt_series_ids:
        return max(len(batch.linked_tilt_series_ids), len(linked_tilts))
    return len(linked_tilts)


def planned_exposures_from_metadata(metadata: dict[str, Any]) -> int:
    """Count recorded exposure slots, excluding XML nil placeholders."""
    main = metadata.get("ExposureTemplateAreaParameters")
    has_main = isinstance(main, dict) and (
        not isinstance(main.get("_attributes"), dict)
        or main["_attributes"].get("nil") != "true"
    )

    raw_areas = find_first(metadata.get("AdditionalExposureTemplateAreas"), "ExposureTemplateAreaParameters")
    if isinstance(raw_areas, dict):
        raw_areas = [raw_areas]
    if isinstance(raw_areas, list):
        additional_count = sum(
            1
            for entry in raw_areas
            if isinstance(entry, dict)
            and (
                not isinstance(entry.get("_attributes"), dict)
                or entry["_attributes"].get("nil") != "true"
            )
        )
    else:
        additional_count = 0

    return (1 if has_main else 0) + additional_count


def _search_map_tile_counts(search_map: SearchMap) -> tuple[int, int]:
    metadata = search_map.metadata or {}
    planned = _walk_int(metadata, "NumberOfTilesPlanned")
    acquired = _walk_int(metadata, "NumberOfTilesAcquired")
    if planned == 0:
        grid = _walk_grid_size(metadata)
        if grid is not None:
            planned = grid
    if acquired == 0:
        acquired = len({path.stem for path in search_map.tile_paths})
    return planned, acquired


def _walk_int(node: Any, key: str) -> int:
    if isinstance(node, dict):
        if key in node:
            return _safe_int(node[key])
        for value in node.values():
            found = _walk_int(value, key)
            if found:
                return found
    elif isinstance(node, list):
        for item in node:
            found = _walk_int(item, key)
            if found:
                return found
    return 0


def _walk_grid_size(node: Any) -> int | None:
    if isinstance(node, dict):
        grid = node.get("GridSize")
        if isinstance(grid, dict):
            width = _safe_int(grid.get("width"))
            height = _safe_int(grid.get("height"))
            product = width * height
            if product > 0:
                return product
        if "GridSizeX" in node and "GridSizeY" in node:
            x = _safe_int(node.get("GridSizeX"))
            y = _safe_int(node.get("GridSizeY"))
            return x * y if x and y else None
        for value in node.values():
            found = _walk_grid_size(value)
            if found is not None:
                return found
    elif isinstance(node, list):
        for item in node:
            found = _walk_grid_size(item)
            if found is not None:
                return found
    return None


def _safe_int(value: Any) -> int:
    try:
        return int(float(str(value)))
    except (TypeError, ValueError):
        return 0


def _actual_tilt_count(tilt: TiltSeries) -> int:
    return actual_tilt_count(tilt)


def _inferred_failed_batch_labels(
    batch_positions: tuple[BatchPosition, ...],
    tilt_series: tuple[TiltSeries, ...],
    validations: dict[str, TiltSeriesValidation],
) -> dict[str, str]:
    explicitly_linked = _explicitly_linked_tilt_ids(batch_positions, tilt_series)
    labels: dict[str, str] = {}
    for tilt in tilt_series:
        if tilt.id in explicitly_linked or tilt.linked_batch_position_id:
            continue
        validation = validations.get(tilt.id)
        if validation is None or validation.status != STATUS_FAILED:
            continue
        label = inferred_batch_label_for_tilt(tilt)
        if label is not None:
            labels[tilt.id] = label
    return labels


def _explicitly_linked_tilt_ids(
    batch_positions: tuple[BatchPosition, ...],
    tilt_series: tuple[TiltSeries, ...],
) -> set[str]:
    known_batch_ids = {batch.id for batch in batch_positions}
    linked = {tilt.id for tilt in tilt_series if tilt.linked_batch_position_id in known_batch_ids}
    for batch in batch_positions:
        linked.update(batch.linked_tilt_series_ids or [])
    return linked


def _batch_inference_keys(batch: BatchPosition) -> set[str]:
    return batch_inference_keys(batch)


def _join_tooltip(title: str, parts: list[str], *, status: str) -> str:
    lines = [title, f"Status: {_display_status(status)}"]
    lines.extend(parts)
    return "\n".join(lines)


def _display_status(status: str) -> str:
    if status == STATUS_DONE:
        return "complete"
    if status == STATUS_PARTIAL:
        return "incomplete"
    if status == STATUS_MISSING:
        return "missing"
    if status == STATUS_UNKNOWN_LABEL:
        return "unavailable"
    return status


def display_status_label(status: str) -> str:
    """Return the consistent user-facing label for an internal item status."""

    normalized = (status or "").strip().lower()
    labels = {
        STATUS_DONE: "Complete",
        "complete": "Complete",
        "completed": "Complete",
        "acquired": "Complete",
        STATUS_PARTIAL: "Incomplete",
        STATUS_INCOMPLETE_LABEL: "Incomplete",
        STATUS_FAILED_LABEL: "Failed",
        "error": "Failed",
        STATUS_MISSING: "Unavailable",
        STATUS_UNKNOWN_LABEL: "Unavailable",
        "n/a": "Unavailable",
        "queued": "Pending",
        "pending": "Pending",
        "warning": "Warning",
    }
    return labels.get(normalized, normalized.replace("_", " ").title())
