from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from tomography_session_browser.domain.models import Atlas, BatchPosition, Overview, SearchMap, SearchTile, TiltSeries
from tomography_session_browser.services.batch_inference import (
    batch_inference_key,
    batch_inference_keys,
    inferred_batch_label_for_tilt,
)


@dataclass(frozen=True, slots=True)
class TiltSeriesNavigationTargets:
    batch_position: BatchPosition | None = None
    search_tile: SearchTile | None = None
    search_map: SearchMap | None = None
    overview: Overview | None = None
    inferred_batch_label: str | None = None


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
    inferred_label = None if batch is not None else inferred_batch_label_for_tilt(tilt)
    return TiltSeriesNavigationTargets(
        batch_position=batch,
        search_tile=search_tile,
        search_map=search_map,
        overview=overview,
        inferred_batch_label=inferred_label,
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
    inferred_label = inferred_batch_label_for_tilt(tilt)
    inferred_key = batch_inference_key(inferred_label)
    if not inferred_key:
        return None
    return next(
        (batch for batch in batch_positions if inferred_key in batch_inference_keys(batch)),
        None,
    )


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
    direct = next(
        (tile for tile in search_tiles if tilt.id in (tile.linked_tilt_series_ids or [])),
        None,
    )
    if direct is not None:
        return direct
    if batch is not None and batch.linked_search_tile_id:
        linked = next(
            (tile for tile in search_tiles if tile.id == batch.linked_search_tile_id),
            None,
        )
        if linked is not None:
            return linked
    if batch is not None:
        return next(
            (tile for tile in search_tiles if tile.batch_position_id == batch.id),
            None,
        )
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
