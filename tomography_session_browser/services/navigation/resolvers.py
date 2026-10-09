"""Conservative relationship resolution from recorded metadata."""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import replace
from typing import Any

from tomography_session_browser.domain.markers import ImageMarker, MarkerType
from tomography_session_browser.domain.models import (
    Atlas,
    BatchPosition,
    Overview,
    SearchMap,
    SearchTile,
    TiltSeries,
)
from tomography_session_browser.services.batch_inference import inferred_batch_label_for_tilt
from tomography_session_browser.services.entity_links import (
    planned_exposures_from_metadata,
    shares_linked_tilt_series,
    tilt_on_batch,
)
from tomography_session_browser.services.tilt_series_validation import (
    STATUS_FAILED,
    validate_tilt_series,
)

from .models import (
    NavigationResolution,
    ExposureTiltSeriesResolution,
    ExposureSelection,
    TiltSeriesNavigationTargets,
    BatchPositionOverviewTarget,
    BatchPositionSearchMapTarget,
    STATE_AMBIGUOUS,
)
from .resolution import (
    navigation_resolution_from_candidates,
    dedupe_navigation_candidates,
    navigable_resolution,
    ambiguous_resolution,
    unresolved_resolution,
)
from .exposures import (
    exposure_selection,
    explicit_tilt_ids,
    expected_tilt_keys_for_exposure,
    tilt_exposure_match_keys,
    marker_exposure_match_key,
    linked_tilts_in_batch_order,
    normalise_exposure_key,
)


# One rule for an explicit ID that names nothing in the current scope, applied
# by every resolver in this module.
#
# Such an ID is **silent, not vetoing**. The same relationship recorded from
# the other side is equally explicit metadata — N4 established that both
# directions must produce the same answer — so the reciprocal link is still
# consulted, and it still has to prove uniqueness before it offers anything.
#
# Evidence *chained through a third entity* is a different matter and is not
# consulted once an explicit ID has dangled: preferring a two-hop inference
# over the recorded value would be exactly the guess this resolver exists to
# refuse. Where a resolver is inherently chained (a tilt series reaching an
# Overview by way of its Search map) that is its only route, and the rule does
# not apply.
#
# Two resolvers previously dead-ended here while the rest fell through, so the
# module stated opposite rules in adjacent functions. The frozen contract
# (``tests/navigation_contract.py``: ``tilt_batch_explicit_absent``) already
# specifies the fall-through, so that is the behaviour they were unified onto.


def tab_label_for_object(value: Any) -> str | None:
    """Return the internal tab key for an entity.

    These are lookup keys, not display text. ``SearchTile`` maps to ``Search``
    because that is the tab key; render it through the caller's display-label
    mapping before showing it to a user.
    """

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


def resolve_exposure_tilt_series(
    marker: ImageMarker,
    *,
    batch: BatchPosition,
    tilt_series: Iterable[TiltSeries],
) -> ExposureTiltSeriesResolution:
    """Resolve an exposure marker to one tilt series without guessing.

    Explicit IDs, expected acquisition names, reciprocal batch links, and the
    recorded batch order are evaluated here so mouse, keyboard, and button
    paths share one decision. Conflicting explicit IDs are an ambiguity even
    if one happens to name an object currently in scope.
    """

    unavailable = "No tilt series is associated with the selected exposure area."
    if marker.marker_type not in {MarkerType.EXPOSURE_AREA, MarkerType.CAMERA_FOV}:
        resolution = unresolved_resolution(provenance="marker.marker_type", explanation=unavailable)
        return ExposureTiltSeriesResolution(None, None, unavailable, resolution=resolution)

    exposure = exposure_selection(marker, batch)
    candidates = tuple(tilt_series)
    explicit_ids = explicit_tilt_ids(marker)
    if len(explicit_ids) > 1:
        explanation = (
            "Exposure metadata is ambiguous and names multiple tilt series: "
            + ", ".join(explicit_ids)
            + "."
        )
        resolution = NavigationResolution(
            candidate_ids=explicit_ids,
            state=STATE_AMBIGUOUS,
            explanation=explanation,
            provenance="marker.explicit_tilt_series_id",
        )
        return ExposureTiltSeriesResolution(exposure, None, explanation, True, resolution)
    if explicit_ids:
        explicit = navigation_resolution_from_candidates(
            (tilt for tilt in candidates if tilt.id == explicit_ids[0]),
            provenance="marker.explicit_tilt_series_id",
            navigable_explanation="Resolved from the exposure marker's explicit tilt-series ID.",
            ambiguous_prefix="Ambiguous exposure tilt-series ID matches multiple records",
            unresolved_explanation=unavailable,
        )
        if explicit.ambiguous:
            return ExposureTiltSeriesResolution(exposure, None, explicit.explanation, True, explicit)
        if explicit.navigable and explicit.target is not None:
            selected = replace(exposure, linked_tilt_series_id=explicit.target.id)
            return ExposureTiltSeriesResolution(
                selected,
                explicit.target,
                "Jump to the tilt series associated with this exposure area.",
                resolution=explicit,
            )

    linked = tuple(tilt for tilt in candidates if tilt_on_batch(tilt, batch))
    pool = linked or candidates
    if not pool:
        resolution = unresolved_resolution(provenance="batch.linked_tilt_series_ids", explanation=unavailable)
        return ExposureTiltSeriesResolution(exposure, None, unavailable, resolution=resolution)

    expected_keys = expected_tilt_keys_for_exposure(batch, exposure)
    if expected_keys:
        linked_matches = tuple(
            tilt for tilt in linked if tilt_exposure_match_keys(tilt).intersection(expected_keys)
        )
        if linked_matches:
            return _exposure_result_from_candidates(exposure, linked_matches, "exposure.expected_name+batch_link")
        name_matches = tuple(
            tilt for tilt in candidates if tilt_exposure_match_keys(tilt).intersection(expected_keys)
        )
        if name_matches:
            return _exposure_result_from_candidates(exposure, name_matches, "exposure.expected_name")

    marker_key = marker_exposure_match_key(marker)
    if marker_key:
        exact = tuple(
            tilt for tilt in pool if marker_key in tilt_exposure_match_keys(tilt)
        )
        if exact:
            return _exposure_result_from_candidates(exposure, exact, "exposure.marker_name")

    ordered = linked_tilts_in_batch_order(batch, linked)
    planned = planned_exposures_from_metadata(batch.metadata)
    complete_order = (
        planned > 0
        and len(ordered) == planned
        and len(set(batch.linked_tilt_series_ids)) == len(batch.linked_tilt_series_ids) == planned
        and {tilt.id for tilt in ordered} == set(batch.linked_tilt_series_ids)
    )
    if complete_order:
        batch_key = normalise_exposure_key(batch.name or batch.id)
        for index, tilt in enumerate(ordered):
            keys = tilt_exposure_match_keys(tilt)
            recorded_slot_keys = {key for key in keys if key == batch_key or re.fullmatch(re.escape(batch_key) + r"_\d+", key)}
            expected_key = batch_key if index == 0 else f"{batch_key}_{index + 1}"
            if recorded_slot_keys and recorded_slot_keys != {expected_key}:
                complete_order = False
                break
    if complete_order and exposure.exposure_index is not None and 0 <= exposure.exposure_index < len(ordered):
        return _exposure_result_from_candidates(
            exposure,
            (ordered[exposure.exposure_index],),
            "batch.linked_tilt_series_ids+exposure_index",
        )
    if exposure.exposure_index in (None, 0) and planned == 1 and len(linked) == 1:
        return _exposure_result_from_candidates(exposure, linked, "single_tilt_candidate")
    # A partial list contains acquisitions, not exposure slots. Neither its
    # ordinal positions nor a sole unrelated candidate establish this link.
    resolution = unresolved_resolution(provenance="exposure.missing_slot", explanation=unavailable)
    return ExposureTiltSeriesResolution(exposure, None, unavailable, resolution=resolution)


def _exposure_result_from_candidates(
    exposure: ExposureSelection,
    candidates: Iterable[TiltSeries],
    provenance: str,
) -> ExposureTiltSeriesResolution:
    resolution = navigation_resolution_from_candidates(
        candidates,
        provenance=provenance,
        navigable_explanation="Resolved the selected exposure area to one tilt series.",
        ambiguous_prefix="Multiple tilt series candidates remain for this exposure area",
        unresolved_explanation="No tilt series is associated with the selected exposure area.",
    )
    if resolution.navigable and resolution.target is not None:
        selected = replace(exposure, linked_tilt_series_id=resolution.target.id)
        return ExposureTiltSeriesResolution(
            selected,
            resolution.target,
            "Jump to the tilt series associated with this exposure area.",
            resolution=resolution,
        )
    return ExposureTiltSeriesResolution(
        exposure,
        None,
        resolution.explanation,
        resolution.ambiguous,
        resolution,
    )


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

    batch_resolution = _resolve_batch_for_tilt(tilt, batches)
    batch = batch_resolution.target
    tile_resolution = _resolve_search_tile_for_tilt(tilt, batch, tiles)
    map_resolution = _resolve_search_map_for_tilt(tilt, batch, maps)
    overview_resolution = _resolve_overview_for_tilt(
        batch,
        map_resolution.target,
        overview_items,
        maps,
    )

    inferred_label = None
    if batch is None and validate_tilt_series(tilt).status == STATUS_FAILED:
        inferred_label = inferred_batch_label_for_tilt(tilt)

    return TiltSeriesNavigationTargets(
        batch_position=batch,
        search_tile=tile_resolution.target,
        search_map=map_resolution.target,
        overview=overview_resolution.target,
        inferred_batch_label=inferred_label,
        batch_position_resolution=batch_resolution,
        search_tile_resolution=tile_resolution,
        search_map_resolution=map_resolution,
        overview_resolution=overview_resolution,
    )


def _resolve_batch_for_tilt(
    tilt: TiltSeries,
    batch_positions: tuple[BatchPosition, ...],
) -> NavigationResolution:
    if tilt.linked_batch_position_id:
        explicit = dedupe_navigation_candidates(
            batch for batch in batch_positions if batch.id == tilt.linked_batch_position_id
        )
        if len(explicit) == 1:
            return navigable_resolution(
                explicit[0],
                provenance="tilt.linked_batch_position_id",
                explanation="Resolved from the tilt series' explicit batch-position ID.",
            )
        if len(explicit) > 1:
            return ambiguous_resolution(
                explicit,
                provenance="tilt.linked_batch_position_id",
                explanation=(
                    "More than one batch position has the tilt series' explicit "
                    "batch-position ID."
                ),
            )
        # A recorded ID outside the current scope is not a failure on its own;
        # reciprocal metadata may still identify the batch.

    return navigation_resolution_from_candidates(
        [
            batch
            for batch in batch_positions
            if tilt.id in (batch.linked_tilt_series_ids or [])
        ],
        provenance="batch.linked_tilt_series_ids",
        navigable_explanation="Resolved from the batch position's linked tilt-series IDs.",
        ambiguous_prefix="Batch-position link is ambiguous between",
        unresolved_explanation="No unambiguous batch position is linked to this tilt series.",
    )


def _resolve_search_tile_for_tilt(
    tilt: TiltSeries,
    batch: BatchPosition | None,
    search_tiles: tuple[SearchTile, ...],
) -> NavigationResolution:
    direct = [tile for tile in search_tiles if tilt.id in (tile.linked_tilt_series_ids or [])]
    if direct:
        return navigation_resolution_from_candidates(
            direct,
            provenance="search_tile.linked_tilt_series_ids",
            navigable_explanation="Resolved from the Search tile's linked tilt-series IDs.",
            ambiguous_prefix="Search tile link is ambiguous between",
            unresolved_explanation="No unambiguous Search tile is linked to this tilt series.",
        )

    if batch is not None and batch.linked_search_tile_id:
        explicit = dedupe_navigation_candidates(tile for tile in search_tiles if tile.id == batch.linked_search_tile_id)
        if len(explicit) == 1:
            return navigable_resolution(
                explicit[0],
                provenance="batch.linked_search_tile_id",
                explanation="Resolved from the batch position's explicit Search tile ID.",
            )
        if len(explicit) > 1:
            return ambiguous_resolution(
                explicit,
                provenance="batch.linked_search_tile_id",
                explanation=(
                    "More than one Search tile has the batch position's explicit "
                    "Search tile ID."
                ),
            )

    if batch is not None:
        return navigation_resolution_from_candidates(
            [tile for tile in search_tiles if tile.batch_position_id == batch.id],
            provenance="search_tile.batch_position_id",
            navigable_explanation="Resolved from the Search tile's batch-position ID.",
            ambiguous_prefix="Search tile link is ambiguous between",
            unresolved_explanation="No unambiguous Search tile is linked to this batch position.",
        )

    return unresolved_resolution(
        provenance="search_tile",
        explanation="No Search tile is linked to this tilt series.",
    )


def _resolve_search_map_for_tilt(
    tilt: TiltSeries,
    batch: BatchPosition | None,
    search_maps: tuple[SearchMap, ...],
) -> NavigationResolution:
    # A resolved batch answers as its own Search map jump does, so the two
    # tabs cannot disagree. Only when the batch leads nowhere does the tilt's
    # own map-side link get a say; an ambiguous batch stays ambiguous.
    if batch is not None:
        via_batch = _resolve_batch_search_map(batch, search_maps=search_maps)
        if not via_batch.unresolved:
            return via_batch

    return navigation_resolution_from_candidates(
        [
            search_map
            for search_map in search_maps
            if tilt.id in (search_map.linked_tilt_series_ids or [])
        ],
        provenance="search_map.linked_tilt_series_ids",
        navigable_explanation="Resolved from the Search map's linked tilt-series IDs.",
        ambiguous_prefix="Search map link is ambiguous between",
        unresolved_explanation="No unambiguous Search map is linked to this tilt series.",
    )


def _resolve_overview_for_tilt(
    batch: BatchPosition | None,
    search_map: SearchMap | None,
    overviews: tuple[Overview, ...],
    search_maps: tuple[SearchMap, ...] = (),
) -> NavigationResolution:
    # As for the Search map: a resolved batch answers as its own Overview
    # jump does. The Search map's Overview reference used to short-circuit
    # here, so the tilt jumped where its batch refused (another Overview
    # also listed the batch).
    if batch is not None:
        via_batch = _resolve_batch_overview(batch, search_maps=search_maps, overviews=overviews)
        if not via_batch.unresolved:
            return via_batch

    if search_map is not None:
        if search_map.overview is not None:
            return navigable_resolution(
                search_map.overview,
                provenance="search_map.overview",
                explanation="Resolved from the Search map's Overview reference.",
            )
        return navigation_resolution_from_candidates(
            [
                overview
                for overview in overviews
                if search_map.id in (overview.linked_search_map_ids or [])
            ],
            provenance="overview.linked_search_map_ids",
            navigable_explanation="Resolved from the Overview's linked Search map IDs.",
            ambiguous_prefix="Overview link is ambiguous between",
            unresolved_explanation="No unambiguous Overview is linked to this Search map.",
        )

    return unresolved_resolution(
        provenance="overview",
        explanation="No Overview is linked to this tilt series.",
    )


def resolve_search_tile_tilt_series(
    search_tile: SearchTile,
    *,
    tilt_series: Iterable[TiltSeries],
) -> NavigationResolution:
    """Resolve the tilt series a Search tile points at."""

    linked_ids = set(search_tile.linked_tilt_series_ids or [])
    if not linked_ids:
        return unresolved_resolution(
            provenance="search_tile.linked_tilt_series_ids",
            explanation="No tilt series is linked to this Search tile.",
        )
    return navigation_resolution_from_candidates(
        [tilt for tilt in tilt_series if tilt.id in linked_ids],
        provenance="search_tile.linked_tilt_series_ids",
        navigable_explanation="Resolved from the Search tile's linked tilt-series IDs.",
        ambiguous_prefix="Tilt series link is ambiguous between",
        unresolved_explanation=(
            "The Search tile records tilt-series IDs that are not present in the "
            "current scope."
        ),
    )


def resolve_search_tile_batch_position(
    search_tile: SearchTile,
    *,
    batch_positions: Iterable[BatchPosition],
) -> NavigationResolution:
    """Resolve the batch position a Search tile was derived from."""

    if not search_tile.batch_position_id:
        return unresolved_resolution(
            provenance="search_tile.batch_position_id",
            explanation="No batch position is linked to this Search tile.",
        )
    return navigation_resolution_from_candidates(
        [
            batch
            for batch in batch_positions
            if batch.id == search_tile.batch_position_id
        ],
        provenance="search_tile.batch_position_id",
        navigable_explanation="Resolved from the Search tile's batch-position ID.",
        ambiguous_prefix="Batch-position link is ambiguous between",
        unresolved_explanation=(
            "The Search tile records a batch-position ID that is not present in the "
            "current scope."
        ),
    )


def resolve_search_tile_search_map(
    search_tile: SearchTile,
    *,
    search_maps: Iterable[SearchMap],
) -> NavigationResolution:
    """Resolve the Search map a Search tile belongs to."""

    if not search_tile.search_map_id:
        return unresolved_resolution(
            provenance="search_tile.search_map_id",
            explanation="No Search map is linked to this Search tile.",
        )
    return navigation_resolution_from_candidates(
        [search_map for search_map in search_maps if search_map.id == search_tile.search_map_id],
        provenance="search_tile.search_map_id",
        navigable_explanation="Resolved from the Search tile's Search map ID.",
        ambiguous_prefix="Search map link is ambiguous between",
        unresolved_explanation=(
            "The Search tile records a Search map ID that is not present in the "
            "current scope."
        ),
    )


def resolve_search_map_overview(
    search_map: SearchMap,
    *,
    overviews: Iterable[Overview],
) -> NavigationResolution:
    """Resolve the Overview a Search map sits on."""

    if search_map.overview is not None:
        return navigable_resolution(
            search_map.overview,
            provenance="search_map.overview",
            explanation="Resolved from the Search map's Overview reference.",
        )
    return navigation_resolution_from_candidates(
        [
            overview
            for overview in overviews
            if search_map.id in (overview.linked_search_map_ids or [])
        ],
        provenance="overview.linked_search_map_ids",
        navigable_explanation="Resolved from the Overview's linked Search map IDs.",
        ambiguous_prefix="Overview link is ambiguous between",
        unresolved_explanation="No unambiguous Overview is linked to this Search map.",
    )


def resolve_item_atlas(
    *,
    own_atlas: Atlas | None,
    linked_atlases: Iterable[Atlas] = (),
) -> NavigationResolution:
    """Resolve the Atlas of the grid an item was acquired on.

    For an Overview, Search map, search tile, batch position or tilt series
    alike. ``own_atlas`` is the Atlas in the folder that holds the item (its
    sample's, or for a session-level item its session's). A data-collection
    sample usually has none; its Session.dm records an AtlasId naming an
    atlas-screening sample instead, and ``linked_atlases`` are the atlases that
    AtlasId matched (``services.session_linking.resolve_atlas_for_sample``). Several
    matches stay inert rather than taking the first.
    """

    if own_atlas is not None:
        return navigable_resolution(
            own_atlas,
            provenance="sample.atlas",
            explanation="Resolved from the Atlas in the item's own sample folder.",
        )
    return navigation_resolution_from_candidates(
        linked_atlases,
        provenance="sample.AtlasId",
        navigable_explanation="Resolved from the sample's AtlasId path.",
        ambiguous_prefix="Atlas link is ambiguous between",
        unresolved_explanation="No Atlas is recorded for this item's sample.",
    )


def resolve_batch_position_overview(
    batch: BatchPosition,
    *,
    search_maps: Iterable[SearchMap],
    overviews: Iterable[Overview],
) -> BatchPositionOverviewTarget:
    """Resolve a batch-position Overview without choosing among candidates."""

    resolution = _resolve_batch_overview(
        batch,
        search_maps=tuple(search_maps),
        overviews=tuple(overviews),
    )
    return BatchPositionOverviewTarget(
        resolution.target,
        resolution.explanation,
        ambiguous=resolution.ambiguous,
        resolution=resolution,
    )


def _resolve_batch_overview(
    batch: BatchPosition,
    *,
    search_maps: tuple[SearchMap, ...],
    overviews: tuple[Overview, ...],
) -> NavigationResolution:
    if batch.linked_overview_id:
        explicit = dedupe_navigation_candidates(
            overview for overview in overviews if overview.id == batch.linked_overview_id
        )
        if len(explicit) == 1:
            return navigable_resolution(
                explicit[0],
                provenance="batch.linked_overview_id",
                explanation="Resolved from the batch position's explicit Overview ID.",
            )
        if len(explicit) > 1:
            return ambiguous_resolution(
                explicit,
                provenance="batch.linked_overview_id",
                explanation=(
                    "More than one Overview has the batch position's explicit "
                    "Overview ID."
                ),
            )
        # A dangling explicit ID is silent, not vetoing: the same relationship
        # recorded Overview-side is equally explicit metadata. Candidates
        # chained through the linked Search map are deliberately *not*
        # consulted here — see the rule at the top of this module.
        return navigation_resolution_from_candidates(
            [
                overview
                for overview in overviews
                if batch.id in (overview.linked_batch_position_ids or [])
            ],
            provenance="overview.linked_batch_position_ids",
            navigable_explanation="Resolved from the Overview's linked batch-position IDs.",
            ambiguous_prefix="Overview link is ambiguous between",
            unresolved_explanation=(
                "The batch position records an Overview ID that is not present in "
                "the current scope, and no Overview records this batch position."
            ),
        )

    candidates: list[Overview] = [
        overview
        for overview in overviews
        if batch.id in (overview.linked_batch_position_ids or [])
    ]
    if batch.linked_search_map_id:
        linked_maps = dedupe_navigation_candidates(
            search_map
            for search_map in search_maps
            if search_map.id == batch.linked_search_map_id
        )
        if len(linked_maps) > 1:
            return ambiguous_resolution(
                linked_maps,
                provenance="batch.linked_search_map_id",
                explanation=(
                    "The linked Search map ID resolves to more than one Search map "
                    "in the current scope."
                ),
            )
        if len(linked_maps) == 1:
            search_map = linked_maps[0]
            if search_map.overview is not None:
                candidates.append(search_map.overview)
            candidates.extend(
                overview
                for overview in overviews
                if search_map.id in (overview.linked_search_map_ids or [])
            )

    return navigation_resolution_from_candidates(
        candidates,
        provenance="batch.linked_metadata",
        navigable_explanation="Resolved from the batch position's explicit linked metadata.",
        ambiguous_prefix="Overview link is ambiguous between",
        unresolved_explanation=(
            "No unambiguous Overview link is recorded for this batch position."
        ),
    )


def resolve_batch_position_search_map(
    batch: BatchPosition,
    *,
    search_maps: Iterable[SearchMap],
) -> BatchPositionSearchMapTarget:
    """Resolve a batch-position Search map without guessing among candidates."""

    resolution = _resolve_batch_search_map(batch, search_maps=tuple(search_maps))
    return BatchPositionSearchMapTarget(
        resolution.target,
        resolution.explanation,
        ambiguous=resolution.ambiguous,
        resolution=resolution,
    )


def _resolve_batch_search_map(
    batch: BatchPosition,
    *,
    search_maps: tuple[SearchMap, ...],
) -> NavigationResolution:
    if batch.linked_search_map_id:
        explicit = dedupe_navigation_candidates(
            search_map
            for search_map in search_maps
            if search_map.id == batch.linked_search_map_id
        )
        if len(explicit) == 1:
            return navigable_resolution(
                explicit[0],
                provenance="batch.linked_search_map_id",
                explanation="Resolved from the batch position's explicit Search map ID.",
            )
        if len(explicit) > 1:
            return ambiguous_resolution(
                explicit,
                provenance="batch.linked_search_map_id",
                explanation=(
                    "The explicit Search map ID resolves to more than one Search map "
                    "in the current scope."
                ),
            )
        # Silent, not vetoing — as above. The tilt-series intersection below is
        # chained evidence and stays out of reach once an explicit ID dangled.
        return navigation_resolution_from_candidates(
            [
                search_map
                for search_map in search_maps
                if batch.id in (search_map.linked_batch_position_ids or [])
            ],
            provenance="search_map.linked_batch_position_ids",
            navigable_explanation="Resolved from the Search map's linked batch-position IDs.",
            ambiguous_prefix="Search map link is ambiguous between",
            unresolved_explanation=(
                "The batch position records a Search map ID that is not present in "
                "the current scope, and no Search map records this batch position."
            ),
        )

    return navigation_resolution_from_candidates(
        [
            search_map
            for search_map in search_maps
            if (
                batch.id in (search_map.linked_batch_position_ids or [])
                or shares_linked_tilt_series(batch, search_map)
            )
        ],
        provenance="search_map.linked_metadata",
        navigable_explanation=(
            "Resolved from explicit batch-position or tilt-series links on the Search map."
        ),
        ambiguous_prefix="Search map link is ambiguous between",
        unresolved_explanation=(
            "No unambiguous Search map link is recorded for this batch position."
        ),
    )
