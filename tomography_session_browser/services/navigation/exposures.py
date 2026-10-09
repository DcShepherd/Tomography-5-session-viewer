"""Exposure identity and marker matching with explicit inputs and no widgets."""

from __future__ import annotations

import logging
import re
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any

from tomography_session_browser.domain.markers import ImageMarker, MarkerType
from tomography_session_browser.domain.models import (
    BatchPosition,
    Overview,
    SearchMap,
    SearchTile,
    TiltSeries,
)
from tomography_session_browser.parsers.path_utils import casefold_natural_key

from .models import ExposureSelection, NavigationResolution
from .resolution import navigable_resolution, navigation_resolution_from_candidates, unresolved_resolution

LOGGER = logging.getLogger(__name__)


def exposure_area_id(marker: ImageMarker) -> str:
    """The exposure ``marker`` draws. A camera field of view records its
    exposure area (``exposure_marker_id``); an exposure area is its own, so
    the two markers of one exposure share this ID."""

    return str(marker.metadata.get("exposure_marker_id") or marker.id)


def exposure_selection(marker: ImageMarker, batch: BatchPosition) -> ExposureSelection:
    index = exposure_index_for_marker(marker, batch)
    exposure_type = str(
        marker.metadata.get("exposure_type")
        or ("main" if index == 0 else "additional")
    )
    return ExposureSelection(
        batch_position_id=batch.id,
        exposure_index=index,
        exposure_area_id=exposure_area_id(marker),
        exposure_type=exposure_type,
    )


def exposure_index_for_marker(marker: ImageMarker, batch: BatchPosition) -> int | None:
    raw_index = marker.metadata.get("exposure_index")
    try:
        if raw_index is not None:
            return int(raw_index)
    except (TypeError, ValueError):
        pass
    area_name = str(marker.metadata.get("area_name") or raw_marker_name(marker) or "").strip()
    if area_name.casefold() == "exposure":
        return 0
    match = re.fullmatch(r"exposure\s*(\d+)", area_name, flags=re.IGNORECASE)
    if match:
        return max(0, int(match.group(1)) - 1)
    batch_key = normalise_exposure_key(batch.name or batch.id)
    marker_key = marker_exposure_match_key(marker)
    if marker_key and batch_key:
        if marker_key == batch_key:
            return 0
        suffix = marker_key.removeprefix(f"{batch_key}_")
        if suffix != marker_key and suffix.isdigit():
            return max(0, int(suffix) - 1)
    return None


def expected_tilt_keys_for_exposure(
    batch: BatchPosition,
    exposure: ExposureSelection,
) -> set[str]:
    batch_key = normalise_exposure_key(batch.name or batch.id)
    if not batch_key or exposure.exposure_index is None:
        return set()
    if exposure.exposure_index == 0:
        return {batch_key}
    return {f"{batch_key}_{exposure.exposure_index + 1}"}


def tilt_exposure_match_keys(tilt: TiltSeries) -> set[str]:
    raw_values = {
        tilt.name,
        tilt.id,
        Path(tilt.id).stem,
        tilt.mrc_path.stem if tilt.mrc_path is not None else None,
    }
    return {key for value in raw_values if (key := normalise_exposure_key(value))}


def marker_exposure_match_key(marker: ImageMarker) -> str:
    raw_values = (
        marker.metadata.get("linked_area_name"),
        marker.metadata.get("area_name"),
        raw_marker_name(marker),
        marker.label,
        marker.id.rsplit(":", 1)[-1] if marker.id else None,
    )
    for value in raw_values:
        key = normalise_exposure_key(value)
        if key and key != "exposure":
            return key
    return normalise_exposure_key(marker.metadata.get("area_name") or raw_marker_name(marker))


def normalise_exposure_key(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    text = text.replace("\\", "/").split("/")[-1].split(":")[-1]
    for suffix in (".mrc", ".mdoc", ".xml", ".jpg", ".jpeg", ".png", ".tif", ".tiff"):
        if text.lower().endswith(suffix):
            text = text[: -len(suffix)]
            break
    return text.strip().casefold()


def explicit_tilt_ids(marker: ImageMarker) -> tuple[str, ...]:
    keys = (
        "tilt_series_id",
        "TiltSeriesId",
        "TiltSeriesID",
        "linked_tilt_series_id",
        "LinkedTiltSeriesId",
    )
    metadata = marker.metadata or {}
    raw = metadata.get("raw")
    values = [metadata.get(key) for key in keys]
    if isinstance(raw, dict):
        values.extend(raw.get(key) for key in keys)
    return tuple(dict.fromkeys(str(value) for value in values if value))


def linked_tilts_in_batch_order(
    batch: BatchPosition,
    candidates: Iterable[TiltSeries],
) -> tuple[TiltSeries, ...]:
    order = {tilt_id: index for index, tilt_id in enumerate(batch.linked_tilt_series_ids or ())}
    return tuple(
        sorted(
            candidates,
            key=lambda tilt: (
                order.get(tilt.id, 10**9),
                casefold_natural_key(tilt.name or tilt.id),
            ),
        )
    )


def raw_marker_name(marker: ImageMarker) -> str | None:
    raw = marker.metadata.get("raw") if marker.metadata else None
    if isinstance(raw, dict):
        value = raw.get("Name")
        return str(value) if value is not None else None
    return None


def coerce_exposure_index(value: Any) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def unique_preferred_exposure_marker(
    markers: list[ImageMarker],
) -> ImageMarker | None:
    exposure_markers = [
        marker
        for marker in markers
        if marker.marker_type == MarkerType.EXPOSURE_AREA
    ]
    if len(exposure_markers) == 1:
        return exposure_markers[0]
    if len(exposure_markers) > 1:
        return None
    return markers[0] if len(markers) == 1 else None


def preferred_batch_marker(
    markers: list[ImageMarker],
    batch_position: BatchPosition,
) -> ImageMarker | None:
    """The marker to highlight on arriving at ``batch_position``: its first
    exposure area, else camera field, template, batch marker, tracking, focus.

    "Arrive on the first exposure" is deliberate (divergence-register rule 6):
    the batch is already resolved, so this picks a highlight, not a link.
    """

    linked = [marker for marker in markers if marker.linked_object_id == batch_position.id]
    for marker_type in (
        MarkerType.EXPOSURE_AREA,
        MarkerType.CAMERA_FOV,
        MarkerType.TEMPLATE_AREA,
        MarkerType.BATCH_POSITION,
        MarkerType.TRACKING_AREA,
        MarkerType.FOCUS_AREA,
    ):
        match = next((marker for marker in linked if marker.marker_type == marker_type), None)
        if match is not None:
            return match
    if linked:
        return linked[0]

    batch_key = normalise_exposure_key(batch_position.name or batch_position.id)
    if not batch_key:
        return None
    for marker_type in (
        MarkerType.EXPOSURE_AREA,
        MarkerType.CAMERA_FOV,
        MarkerType.BATCH_POSITION,
        MarkerType.TEMPLATE_AREA,
        MarkerType.TRACKING_AREA,
        MarkerType.FOCUS_AREA,
    ):
        for marker in markers:
            if marker.marker_type != marker_type:
                continue
            marker_key = marker_exposure_match_key(marker)
            if marker_key == batch_key:
                return marker
    return None


def preferred_navigation_marker(
    markers: list[ImageMarker],
    tilt: TiltSeries,
    *,
    batch: BatchPosition | None,
) -> ImageMarker | None:
    direct = next((marker for marker in markers if marker.linked_object_id == tilt.id), None)
    if direct is not None:
        return direct
    exposure = exact_exposure_marker(markers, tilt, batch=batch)
    if exposure is not None:
        return exposure
    if batch is None:
        return None
    linked = [marker for marker in markers if marker.linked_object_id == batch.id]
    for marker_type in (
        MarkerType.EXPOSURE_AREA,
        MarkerType.TEMPLATE_AREA,
        MarkerType.BATCH_POSITION,
        MarkerType.TRACKING_AREA,
        MarkerType.FOCUS_AREA,
    ):
        match = next((marker for marker in linked if marker.marker_type == marker_type), None)
        if match is not None:
            return match
    return next(iter(linked), None)


def exact_exposure_marker(
    markers: list[ImageMarker],
    tilt: TiltSeries,
    *,
    batch: BatchPosition | None,
) -> ImageMarker | None:
    candidates = tilt_exposure_match_keys(tilt)
    if not candidates:
        return None
    exposure_markers = [
        marker
        for marker in markers
        if marker.marker_type == MarkerType.EXPOSURE_AREA
        and (batch is None or marker.linked_object_id == batch.id)
    ]
    exact = [
        marker
        for marker in exposure_markers
        if marker_exposure_match_key(marker) in candidates
    ]
    if len(exact) == 1:
        return exact[0]
    if len(exact) > 1:
        LOGGER.debug(
            "multiple exact exposure markers matched tilt=%s candidates=%s markers=%s",
            tilt.name,
            sorted(candidates),
            [marker.id for marker in exact],
        )
        return exact[0]
    if batch is not None and batch.name and normalise_exposure_key(tilt.name) == normalise_exposure_key(batch.name):
        return next(
            (
                marker
                for marker in exposure_markers
                if normalise_exposure_key(marker.metadata.get("area_name") or raw_marker_name(marker)) == "exposure"
            ),
            None,
        )
    LOGGER.debug(
        "no exact exposure marker matched tilt=%s candidates=%s available=%s",
        tilt.name,
        sorted(candidates),
        [marker_exposure_match_key(marker) for marker in exposure_markers],
    )
    return None


def batch_for_exposure_marker(marker: ImageMarker, *, find_object: Callable[[str | None], object | None]) -> BatchPosition | None:
    marker_metadata = marker.metadata or {}
    for object_id in (
        marker_metadata.get("batch_id"),
        marker.linked_object_id,
        marker.source_object_id,
    ):
        value = find_object(object_id)
        if isinstance(value, BatchPosition):
            return value
    return None


def search_map_ids_for_exposure_search(
    source: Overview | SearchMap | None,
    batch: BatchPosition,
    search_maps: Iterable[SearchMap],
) -> set[str]:
    if isinstance(source, SearchMap):
        return {source.id}
    search_map_ids: set[str] = set()
    if batch.linked_search_map_id:
        search_map_ids.add(batch.linked_search_map_id)
    if isinstance(source, Overview):
        search_map_ids.update(source.linked_search_map_ids or [])
        for search_map in search_maps:
            if search_map.overview is source:
                search_map_ids.add(search_map.id)
                continue
            if search_map.overview is not None and search_map.overview.id == source.id:
                search_map_ids.add(search_map.id)
    return search_map_ids


EXPOSURE_SEARCH_PROVENANCE = "exposure.search_tile"
EXPOSURE_SEARCH_FOUND = "Jump to the search item associated with this exposure area."
EXPOSURE_SEARCH_MISSING = "No linked search item found for this exposure area."
EXPOSURE_SEARCH_AMBIGUOUS = "Several search items match this exposure area"


def search_tile_for_exposure_marker(
    marker: ImageMarker,
    *,
    batch: BatchPosition,
    source: Overview | SearchMap | None,
    search_maps: Iterable[SearchMap],
    search_tiles: Iterable[SearchTile],
) -> SearchTile | None:
    """The search tile, when exactly one is resolved; see the resolution."""

    resolution = resolve_search_tile_for_exposure_marker(
        marker, batch=batch, source=source, search_maps=search_maps, search_tiles=search_tiles,
    )
    return resolution.target if resolution.navigable else None


def resolve_search_tile_for_exposure_marker(
    marker: ImageMarker,
    *,
    batch: BatchPosition,
    source: Overview | SearchMap | None,
    search_maps: Iterable[SearchMap],
    search_tiles: Iterable[SearchTile],
) -> NavigationResolution:
    """The search tile an exposure marker was acquired from, or why there is none.

    Several tiles matching equally well is ``ambiguous``, with their IDs; no
    tile at all is ``unresolved``. The jump stays inert either way, but the
    reviewer is told which it is.
    """

    search_map_ids = search_map_ids_for_exposure_search(source, batch, search_maps)
    candidates = [
        tile
        for tile in search_tiles
        if tile.batch_position_id == batch.id
        and (not search_map_ids or tile.search_map_id in search_map_ids)
    ]
    if not candidates:
        return unresolved_resolution(provenance=EXPOSURE_SEARCH_PROVENANCE, explanation=EXPOSURE_SEARCH_MISSING)

    area_keys = {
        key
        for value in (
            marker.metadata.get("area_name"),
            marker.metadata.get("linked_area_name"),
            raw_marker_name(marker),
            marker.label,
        )
        if (key := normalise_exposure_key(value))
    }
    if area_keys:
        exact = [
            tile
            for tile in candidates
            if area_keys.intersection(
                {
                    key
                    for value in (
                        tile.metadata.get("ExposureAreaName"),
                        tile.metadata.get("ExposureDisplayName"),
                        tile.name,
                    )
                    if (key := normalise_exposure_key(value))
                }
            )
        ]
        if exact:
            return _exposure_search_resolution(exact)

    exposure_index = exposure_index_for_marker(marker, batch)
    primary = [tile for tile in candidates if tile.metadata.get("ExposureIsPrimary") is True]
    if exposure_index in (None, 0) and len(primary) == 1:
        return navigable_resolution(primary[0], provenance=EXPOSURE_SEARCH_PROVENANCE, explanation=EXPOSURE_SEARCH_FOUND)
    if area_keys and len(candidates) > 1:
        # The exposure is named and none of its batch's tiles carries that
        # name: they belong to its other exposures. Nothing was recorded for
        # this one, which is unresolved, not a choice between them.
        others = ", ".join(sorted(str(tile.metadata.get("ExposureAreaName") or tile.name) for tile in candidates))
        return unresolved_resolution(
            provenance=EXPOSURE_SEARCH_PROVENANCE,
            explanation=f"{EXPOSURE_SEARCH_MISSING} Its batch position's search items are for other exposures: {others}.",
        )
    return _exposure_search_resolution(candidates)


def _exposure_search_resolution(candidates: list[SearchTile]) -> NavigationResolution:
    return navigation_resolution_from_candidates(
        candidates,
        provenance=EXPOSURE_SEARCH_PROVENANCE,
        navigable_explanation=EXPOSURE_SEARCH_FOUND,
        ambiguous_prefix=EXPOSURE_SEARCH_AMBIGUOUS,
        unresolved_explanation=EXPOSURE_SEARCH_MISSING,
    )


def marker_fallback_resolution(
    candidates: Iterable[tuple[Any, list[ImageMarker]]],
    *,
    provenance: str,
    navigable_explanation: str,
    ambiguous_prefix: str,
    unresolved_explanation: str,
    predicate: Callable[[Any, list[ImageMarker]], bool],
) -> NavigationResolution:
    """Resolve a lazy stream of candidate/marker pairs, stopping at two matches.

    Marker derivation stays with the caller. This rule receives already derived
    markers and preserves the early stop used for large sessions.
    """

    # Proving uniqueness costs more than the old first-match loop, because
    # a second candidate has to be looked for at all. Two is enough to
    # decide, so stop there rather than building markers for every
    # remaining entity in a large session.
    matched: list[Any] = []
    for candidate, markers in candidates:
        if predicate(candidate, markers):
            matched.append(candidate)
            if len(matched) > 1:
                break
    return navigation_resolution_from_candidates(
        matched,
        provenance=provenance,
        navigable_explanation=navigable_explanation,
        ambiguous_prefix=ambiguous_prefix,
        unresolved_explanation=unresolved_explanation,
    )

