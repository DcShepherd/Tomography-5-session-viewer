"""Choose arrival markers from derived markers, preserving existing priorities."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from tomography_session_browser.domain.markers import ImageMarker, MarkerType
from tomography_session_browser.domain.models import (
    BatchPosition,
    Overview,
    SearchMap,
    SearchTile,
    TiltSeries,
)
from .exposures import (
    exposure_area_id,
    preferred_navigation_marker,
    preferred_batch_marker,
    marker_exposure_match_key,
    exposure_index_for_marker,
    coerce_exposure_index,
    unique_preferred_exposure_marker,
)
from .resolvers import resolve_search_tile_tilt_series, resolve_search_tile_batch_position


def atlas_arrival_marker(
    region: Iterable[tuple[str, str, str | None]],
    markers: Iterable[ImageMarker],
) -> tuple[str, str | None] | None:
    """Find the most exact drawn Atlas marker, excluding reconstructed map tiles."""
    markers = list(markers)
    return next(
        ((marker.id, kind) for marker_type, linked_id, kind in region for marker in markers
         if marker.marker_type == marker_type and marker.linked_object_id == linked_id
         and marker.metadata.get("atlas_lod_role") != "search_map_tile"),
        None,
    )


def marker_id_for_tilt_navigation(
    target: Any,
    tilt: TiltSeries,
    *,
    batch: BatchPosition | None,
    markers: Iterable[ImageMarker],
) -> str | None:
    markers = list(markers)
    marker = preferred_navigation_marker(markers, tilt, batch=batch)
    return marker.id if marker is not None else None


def marker_id_for_search_tile_navigation(
    target: Any,
    search_tile: SearchTile,
    *,
    markers: Iterable[ImageMarker],
    tilt_series: Iterable[TiltSeries],
    batch_positions: Iterable[BatchPosition],
) -> str | None:
    if isinstance(target, TiltSeries):
        return None
    markers = list(markers)
    linked_tilt = resolve_search_tile_tilt_series(search_tile, tilt_series=tilt_series).target
    batch = resolve_search_tile_batch_position(search_tile, batch_positions=batch_positions).target
    if linked_tilt is not None:
        marker = preferred_navigation_marker(markers, linked_tilt, batch=batch)
        if marker is not None:
            return marker.id
    if batch is None:
        return None
    marker = next((item for item in markers if item.linked_object_id == batch.id), None)
    return marker.id if marker is not None else None


def marker_id_for_batch_position_navigation(
    target: Any,
    batch_position: BatchPosition,
    *,
    markers: Iterable[ImageMarker],
) -> str | None:
    markers = list(markers)
    marker = preferred_batch_marker(markers, batch_position)
    return marker.id if marker is not None else None


def marker_id_for_search_map_navigation(
    target: Any,
    search_map: SearchMap,
    *,
    markers: Iterable[ImageMarker],
) -> str | None:
    """Find the marker on ``target`` that represents ``search_map`` whole.

    The Overview draws a Search map as one region, or, once it has more
    than one tile, as a grid of reconstructed tiles with no marker for
    the whole map. Taking the first marker linked to the map then picked
    one tile and ringed an arbitrary corner, so a grid selects nothing:
    the Overview opens at fit with the map in view.
    """

    if not isinstance(target, Overview):
        return None
    markers = list(markers)
    marker = next(
        (
            item
            for item in markers
            if item.linked_object_id == search_map.id
            and item.marker_type == MarkerType.SEARCH_MAP
            and item.metadata.get("tile_index") is None
        ),
        None,
    )
    return marker.id if marker is not None else None


def _single_exposure(candidates: list[ImageMarker]) -> str | None:
    """The first candidate when every candidate draws one exposure (its area
    and camera field of view, say); ``None`` when they draw several."""

    if candidates and len({exposure_area_id(marker) for marker in candidates}) == 1:
        return candidates[0].id
    return None


def marker_id_for_exposure_search_navigation(
    target: SearchTile,
    source_marker: ImageMarker | None,
    *,
    markers: Iterable[ImageMarker],
) -> str | None:
    """The exposure to highlight on arriving at search tile ``target``.

    The selected marker's exposure key first; the tile's area name only when
    the key matches nothing. Matches that draw more than one exposure are
    ambiguous: nothing is highlighted, and the area name does not stand in
    for an ambiguous key (divergence-register rule 6b). The jump's destination
    is decided elsewhere and does not depend on this.
    """

    if source_marker is None:
        return None
    exposures = [
        marker for marker in markers if marker.marker_type in {MarkerType.EXPOSURE_AREA, MarkerType.CAMERA_FOV}
    ]
    marker_key = marker_exposure_match_key(source_marker)
    if marker_key:
        exact = [marker for marker in exposures if marker_exposure_match_key(marker) == marker_key]
        if exact:
            return _single_exposure(exact)
    area_name = str(target.metadata.get("ExposureAreaName") or "").strip().casefold()
    if area_name:
        return _single_exposure(
            [marker for marker in exposures if str(marker.metadata.get("area_name") or "").strip().casefold() == area_name]
        )
    return None


def marker_id_for_exposure_context_navigation(
    target: Overview | SearchMap,
    source_marker: ImageMarker,
    *,
    markers: Iterable[ImageMarker],
    batch: BatchPosition | None,
) -> str | None:
    """Find the same batch exposure on another image context.

    Marker IDs include their source image ID, so an exposure selected on a
    Search map cannot be selected verbatim on its Overview. Match the
    display-only exposure identity instead: batch position first, then
    exposure index, with area name as a conservative fallback.
    """

    if source_marker.marker_type not in {
        MarkerType.EXPOSURE_AREA,
        MarkerType.CAMERA_FOV,
    }:
        return None
    batch_id = (
        batch.id
        if batch is not None
        else str(
            source_marker.metadata.get("batch_id")
            or source_marker.linked_object_id
            or ""
        )
    )
    if not batch_id:
        return None

    candidates = [
        marker
        for marker in markers
        if marker.marker_type
        in {MarkerType.EXPOSURE_AREA, MarkerType.CAMERA_FOV}
        and str(marker.metadata.get("batch_id") or marker.linked_object_id or "")
        == batch_id
    ]
    if not candidates:
        return None

    source_index = (
        exposure_index_for_marker(source_marker, batch)
        if batch is not None
        else coerce_exposure_index(
            source_marker.metadata.get("exposure_index")
        )
    )
    if source_index is not None:
        indexed = [
            marker
            for marker in candidates
            if (
                exposure_index_for_marker(marker, batch)
                if batch is not None
                else coerce_exposure_index(
                    marker.metadata.get("exposure_index")
                )
            )
            == source_index
        ]
        preferred = unique_preferred_exposure_marker(indexed)
        if preferred is not None:
            return preferred.id

    source_key = marker_exposure_match_key(source_marker)
    if source_key:
        named = [
            marker
            for marker in candidates
            if marker_exposure_match_key(marker) == source_key
        ]
        preferred = unique_preferred_exposure_marker(named)
        if preferred is not None:
            return preferred.id
    return None
