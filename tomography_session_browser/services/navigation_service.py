from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from tomography_session_browser.domain.models import Atlas, BatchPosition, Overview, SearchMap, SearchTile, TiltSeries
from tomography_session_browser.services.batch_inference import inferred_batch_label_for_tilt
from tomography_session_browser.services.tilt_series_validation import (
    STATUS_FAILED,
    validate_tilt_series,
)


@dataclass(frozen=True, slots=True)
class TiltSeriesNavigationTargets:
    batch_position: BatchPosition | None = None
    search_tile: SearchTile | None = None
    search_map: SearchMap | None = None
    overview: Overview | None = None
    inferred_batch_label: str | None = None


@dataclass(frozen=True, slots=True)
class BatchPositionOverviewTarget:
    overview: Overview | None
    explanation: str
    ambiguous: bool = False

    @property
    def navigable(self) -> bool:
        return self.overview is not None and not self.ambiguous


@dataclass(frozen=True, slots=True)
class BatchPositionSearchMapTarget:
    search_map: SearchMap | None
    explanation: str
    ambiguous: bool = False

    @property
    def navigable(self) -> bool:
        return self.search_map is not None and not self.ambiguous


def tab_label_for_object(value: Any) -> str | None:
    if isinstance(value, Atlas):
        return "Atlas"
    if isinstance(value, Overview):
        return "Overview"
    if isinstance(value, SearchMap):
        return "Search map"
    if isinstance(value, SearchTile):
        return "Search"
    if isinstance(value, BatchPosition):
        return "Batch position"
    if isinstance(value, TiltSeries):
        return "Tilt series"
    return None


def resolve_tilt_series_navigation_targets(
    tilt: TiltSeries,
    *,
    batch_positions: Iterable[BatchPosition],
    search_maps: Iterable[SearchMap],
    overviews: Iterable[Overview],
    search_tiles: Iterable[SearchTile] = (),
) -> TiltSeriesNavigationTargets:
    """Resolve metadata-linked objects for a selected tilt series."""

    batches = tuple(batch_positions)
    maps = tuple(search_maps)
    tiles = tuple(search_tiles)
    overview_items = tuple(overviews)
    batch = _batch_position_for_tilt(tilt, batches)
    search_tile = _search_tile_for_tilt(tilt, batch, tiles)
    search_map = _search_map_for_tilt(tilt, batch, maps)
    overview = _overview_for_tilt(batch, search_map, overview_items)
    inferred_label = None
    if batch is None and validate_tilt_series(tilt).status == STATUS_FAILED:
        inferred_label = inferred_batch_label_for_tilt(tilt)
    return TiltSeriesNavigationTargets(
        batch_position=batch,
        search_tile=search_tile,
        search_map=search_map,
        overview=overview,
        inferred_batch_label=inferred_label,
    )


def resolve_batch_position_overview(
    batch: BatchPosition,
    *,
    search_maps: Iterable[SearchMap],
    overviews: Iterable[Overview],
) -> BatchPositionOverviewTarget:
    """Resolve a batch-position Overview without choosing among candidates."""

    maps = tuple(search_maps)
    overview_items = tuple(overviews)
    if batch.linked_overview_id:
        explicit = [
            overview
            for overview in overview_items
            if overview.id == batch.linked_overview_id
        ]
        if len(explicit) == 1:
            return BatchPositionOverviewTarget(
                explicit[0],
                "Resolved from the batch position's explicit Overview ID.",
            )
        if len(explicit) > 1:
            return BatchPositionOverviewTarget(
                None,
                "More than one Overview has the batch position's explicit Overview ID.",
                ambiguous=True,
            )
        return BatchPositionOverviewTarget(
            None,
            "The batch position records an Overview ID that is not present in the current scope.",
        )

    candidates: dict[str, Overview] = {
        overview.id: overview
        for overview in overview_items
        if batch.id in (overview.linked_batch_position_ids or [])
    }
    if batch.linked_search_map_id:
        linked_maps = [
            search_map
            for search_map in maps
            if search_map.id == batch.linked_search_map_id
        ]
        if len(linked_maps) > 1:
            return BatchPositionOverviewTarget(
                None,
                "The linked Search map ID resolves to more than one Search map in the current scope.",
                ambiguous=True,
            )
        if len(linked_maps) == 1:
            search_map = linked_maps[0]
            if search_map.overview is not None:
                candidates[search_map.overview.id] = search_map.overview
            for overview in overview_items:
                if search_map.id in (overview.linked_search_map_ids or []):
                    candidates[overview.id] = overview

    resolved = tuple(candidates.values())
    if len(resolved) == 1:
        return BatchPositionOverviewTarget(
            resolved[0],
            "Resolved from the batch position's explicit linked metadata.",
        )
    if len(resolved) > 1:
        names = ", ".join(sorted(overview.name for overview in resolved))
        return BatchPositionOverviewTarget(
            None,
            f"Overview link is ambiguous between: {names}.",
            ambiguous=True,
        )
    return BatchPositionOverviewTarget(
        None,
        "No unambiguous Overview link is recorded for this batch position.",
    )


def resolve_batch_position_search_map(
    batch: BatchPosition,
    *,
    search_maps: Iterable[SearchMap],
) -> BatchPositionSearchMapTarget:
    """Resolve a batch-position Search map without guessing among candidates."""

    maps = tuple(search_maps)
    if batch.linked_search_map_id:
        explicit = [
            search_map
            for search_map in maps
            if search_map.id == batch.linked_search_map_id
        ]
        if len(explicit) == 1:
            return BatchPositionSearchMapTarget(
                explicit[0],
                "Resolved from the batch position's explicit Search map ID.",
            )
        if len(explicit) > 1:
            return BatchPositionSearchMapTarget(
                None,
                "The explicit Search map ID resolves to more than one Search map in the current scope.",
                ambiguous=True,
            )
        return BatchPositionSearchMapTarget(
            None,
            "The batch position records a Search map ID that is not present in the current scope.",
        )

    tilt_ids = set(batch.linked_tilt_series_ids or [])
    candidates = {
        search_map.id: search_map
        for search_map in maps
        if (
            batch.id in (search_map.linked_batch_position_ids or [])
            or (
                tilt_ids
                and tilt_ids.intersection(search_map.linked_tilt_series_ids or [])
            )
        )
    }
    resolved = tuple(candidates.values())
    if len(resolved) == 1:
        return BatchPositionSearchMapTarget(
            resolved[0],
            "Resolved from explicit batch-position or tilt-series links on the Search map.",
        )
    if len(resolved) > 1:
        names = ", ".join(sorted(search_map.name for search_map in resolved))
        return BatchPositionSearchMapTarget(
            None,
            f"Search map link is ambiguous between: {names}.",
            ambiguous=True,
        )
    return BatchPositionSearchMapTarget(
        None,
        "No unambiguous Search map link is recorded for this batch position.",
    )


def _batch_position_for_tilt(
    tilt: TiltSeries,
    batch_positions: tuple[BatchPosition, ...],
) -> BatchPosition | None:
    if tilt.linked_batch_position_id:
        explicit = next(
            (batch for batch in batch_positions if batch.id == tilt.linked_batch_position_id),
            None,
        )
        if explicit is not None:
            return explicit
    direct = next(
        (batch for batch in batch_positions if tilt.id in (batch.linked_tilt_series_ids or [])),
        None,
    )
    if direct is not None:
        return direct
    return None


def _search_map_for_tilt(
    tilt: TiltSeries,
    batch: BatchPosition | None,
    search_maps: tuple[SearchMap, ...],
) -> SearchMap | None:
    if batch is not None and batch.linked_search_map_id:
        linked = next(
            (search_map for search_map in search_maps if search_map.id == batch.linked_search_map_id),
            None,
        )
        if linked is not None:
            return linked
    return next(
        (search_map for search_map in search_maps if tilt.id in (search_map.linked_tilt_series_ids or [])),
        None,
    )


def _search_tile_for_tilt(
    tilt: TiltSeries,
    batch: BatchPosition | None,
    search_tiles: tuple[SearchTile, ...],
) -> SearchTile | None:
    direct = [
        tile for tile in search_tiles if tilt.id in (tile.linked_tilt_series_ids or [])
    ]
    if len(direct) == 1:
        return direct[0]
    if len(direct) > 1:
        return None
    if batch is not None and batch.linked_search_tile_id:
        linked = next(
            (tile for tile in search_tiles if tile.id == batch.linked_search_tile_id),
            None,
        )
        if linked is not None:
            return linked
    if batch is not None:
        candidates = [tile for tile in search_tiles if tile.batch_position_id == batch.id]
        if len(candidates) == 1:
            return candidates[0]
    return None


def _overview_for_tilt(
    batch: BatchPosition | None,
    search_map: SearchMap | None,
    overviews: tuple[Overview, ...],
) -> Overview | None:
    if batch is not None and batch.linked_overview_id:
        linked = next(
            (overview for overview in overviews if overview.id == batch.linked_overview_id),
            None,
        )
        if linked is not None:
            return linked
    if search_map is not None:
        if search_map.overview is not None:
            return search_map.overview
        linked = next(
            (overview for overview in overviews if search_map.id in (overview.linked_search_map_ids or [])),
            None,
        )
        if linked is not None:
            return linked
    return None
