from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

from tomography_session_browser.domain.models import Atlas, BatchPosition, Overview, SearchMap, SearchTile, TiltSeries
from tomography_session_browser.services.batch_inference import inferred_batch_label_for_tilt
from tomography_session_browser.services.tilt_series_validation import (
    STATUS_FAILED,
    validate_tilt_series,
)


# Resolution states. A destination is only offered when exactly one candidate
# survives; "ambiguous" (several candidates) and "unresolved" (none) are kept
# distinct because they need different explanations and different recovery
# advice, and collapsing them was a source of silent first-match navigation.
STATE_NAVIGABLE = "navigable"
STATE_AMBIGUOUS = "ambiguous"
STATE_UNRESOLVED = "unresolved"

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


@dataclass(frozen=True, slots=True)
class NavigationResolution:
    """One relationship resolved to exactly one destination, or to nothing.

    Transient and display-only: this is UI navigation state, never a domain
    relationship, and it is never written back onto the source model.

    ``provenance`` records which rule produced the outcome so a disabled action
    can explain itself and so tests can assert *why* a destination resolved,
    not merely that it did.
    """

    target: Any | None = None
    candidate_ids: tuple[str, ...] = ()
    state: str = STATE_UNRESOLVED
    explanation: str = ""
    provenance: str = ""

    @property
    def navigable(self) -> bool:
        return self.state == STATE_NAVIGABLE and self.target is not None

    @property
    def ambiguous(self) -> bool:
        return self.state == STATE_AMBIGUOUS

    @property
    def unresolved(self) -> bool:
        return self.state == STATE_UNRESOLVED


@dataclass(frozen=True, slots=True)
class TiltSeriesNavigationTargets:
    """Resolved destinations for a selected tilt series.

    The four object attributes are the long-standing public surface. The
    matching ``*_resolution`` fields carry the ambiguity state and explanation
    for the same relationship and default to "unresolved" so existing
    construction sites keep working.

    Rebuild this with :func:`dataclasses.replace`, never by keyword from the
    legacy fields alone — that silently drops the resolutions, and with them
    every explanation the UI shows for a refused jump.
    """

    batch_position: BatchPosition | None = None
    search_tile: SearchTile | None = None
    search_map: SearchMap | None = None
    overview: Overview | None = None
    inferred_batch_label: str | None = None
    batch_position_resolution: NavigationResolution = field(default_factory=NavigationResolution)
    search_tile_resolution: NavigationResolution = field(default_factory=NavigationResolution)
    search_map_resolution: NavigationResolution = field(default_factory=NavigationResolution)
    overview_resolution: NavigationResolution = field(default_factory=NavigationResolution)


@dataclass(frozen=True, slots=True)
class BatchPositionOverviewTarget:
    overview: Overview | None
    explanation: str
    ambiguous: bool = False
    resolution: NavigationResolution = field(default_factory=NavigationResolution)

    @property
    def navigable(self) -> bool:
        return self.overview is not None and not self.ambiguous


@dataclass(frozen=True, slots=True)
class BatchPositionSearchMapTarget:
    search_map: SearchMap | None
    explanation: str
    ambiguous: bool = False
    resolution: NavigationResolution = field(default_factory=NavigationResolution)

    @property
    def navigable(self) -> bool:
        return self.search_map is not None and not self.ambiguous


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


# ---------------------------------------------------------------------------
# Shared uniqueness helpers
# ---------------------------------------------------------------------------


def _identity(value: Any) -> str:
    identifier = getattr(value, "id", None)
    return str(identifier) if identifier is not None else ""


def _display_name(value: Any) -> str:
    name = getattr(value, "name", None)
    if isinstance(name, str) and name:
        return name
    return _identity(value) or "unnamed"


def _signature(value: Any) -> tuple[Any, ...]:
    """Content fingerprint used to tell a duplicate from an ID collision.

    Display fields alone are not sufficient here. Two records can share an ID,
    name and status while pointing at different source files or relationships;
    collapsing those records would silently restore the first-match behaviour
    this resolver exists to remove. The fingerprint therefore includes the
    physical-source and relationship fields that define each navigable entity.
    """

    common = (
        type(value).__name__,
        _identity(value),
        getattr(value, "name", None),
        getattr(value, "status", None),
    )
    if isinstance(value, Overview):
        return common + (
            _path_signature(value.image_path),
            tuple(sorted(value.linked_search_map_ids or [])),
            tuple(sorted(value.linked_batch_position_ids or [])),
        )
    if isinstance(value, SearchMap):
        return common + (
            _path_signature(value.image_path),
            _path_signature(value.mrc_path),
            _path_signature(value.xml_path),
            _path_signature(value.dm_path),
            _referenced_signature(value.overview),
            tuple(sorted(value.linked_batch_position_ids or [])),
            tuple(sorted(value.linked_tilt_series_ids or [])),
        )
    if isinstance(value, SearchTile):
        return common + (
            _path_signature(value.image_path),
            _path_signature(value.xml_path),
            value.search_map_id,
            value.batch_position_id,
            value.tile_index,
            value.acquisition_time,
            value.link_method,
            tuple(sorted(value.linked_tilt_series_ids or [])),
        )
    if isinstance(value, BatchPosition):
        return common + (
            _path_signature(value.overview_image_path),
            _path_signature(value.search_image_path),
            _path_signature(value.search_metadata_path),
            _path_signature(value.tracking_image_path),
            _path_signature(value.exposure_image_path),
            tuple(_path_signature(path) for path in value.exposure_image_paths),
            value.linked_search_map_id,
            value.linked_search_tile_id,
            value.linked_overview_id,
            tuple(sorted(value.linked_tilt_series_ids or [])),
        )
    if isinstance(value, TiltSeries):
        return common + (
            _path_signature(value.mrc_path),
            _path_signature(value.mdoc_path),
            value.linked_batch_position_id,
        )
    return common


def _path_signature(value: Any) -> str:
    if value is None:
        return ""
    return str(value).replace("\\", "/").casefold()


def _referenced_signature(value: Any) -> tuple[str, str]:
    """Fingerprint for an entity held by reference on another entity.

    The ID alone is not enough for the same reason it is not enough at the top
    level: two records can share an ID and still be different objects. Pairing
    it with the physical source keeps a held reference as discriminating as the
    entity it points at.
    """

    if value is None:
        return ("", "")
    return (_identity(value), _path_signature(getattr(value, "image_path", None)))


def _dedupe(candidates: Iterable[Any]) -> tuple[Any, ...]:
    """Collapse repeated entities, preserving first-seen order.

    Two-sided metadata routinely yields the same entity from more than one
    rule; that is one candidate, not an ambiguity.

    Records that share an ID *and* look alike are treated as one entity — real
    session folders can be parsed into duplicate records of a single batch
    position, and reporting those as an ambiguity would disable navigation over
    what is really one destination. Records that share an ID but differ in
    substance are a genuine collision and stay separate, because choosing
    between them would be a guess.
    """

    unique: dict[tuple[Any, ...], Any] = {}
    seen_objects: set[int] = set()
    for candidate in candidates:
        if candidate is None or id(candidate) in seen_objects:
            continue
        seen_objects.add(id(candidate))
        unique.setdefault(_signature(candidate), candidate)
    return tuple(unique.values())


def _navigable(target: Any, *, provenance: str, explanation: str) -> NavigationResolution:
    return NavigationResolution(
        target=target,
        candidate_ids=(_identity(target),),
        state=STATE_NAVIGABLE,
        explanation=explanation,
        provenance=provenance,
    )


def _ambiguous(
    candidates: Iterable[Any],
    *,
    provenance: str,
    explanation: str,
) -> NavigationResolution:
    return NavigationResolution(
        target=None,
        candidate_ids=tuple(sorted(_identity(item) for item in candidates)),
        state=STATE_AMBIGUOUS,
        explanation=explanation,
        provenance=provenance,
    )


def _unresolved(*, provenance: str, explanation: str) -> NavigationResolution:
    return NavigationResolution(
        state=STATE_UNRESOLVED,
        explanation=explanation,
        provenance=provenance,
    )


def navigation_resolution_from_candidates(
    candidates: Iterable[Any],
    *,
    provenance: str,
    navigable_explanation: str,
    ambiguous_prefix: str,
    unresolved_explanation: str,
) -> NavigationResolution:
    """Public entry point to the uniqueness rule.

    Exposed so viewer-side resolution that depends on Qt marker geometry — and
    therefore cannot live in this module — still applies the same rule instead
    of writing its own loop.
    """

    return _classify(
        candidates,
        provenance=provenance,
        navigable_explanation=navigable_explanation,
        ambiguous_prefix=ambiguous_prefix,
        unresolved_explanation=unresolved_explanation,
    )


def _classify(
    candidates: Iterable[Any],
    *,
    provenance: str,
    navigable_explanation: str,
    ambiguous_prefix: str,
    unresolved_explanation: str,
) -> NavigationResolution:
    """Offer a destination only when exactly one candidate survives."""

    unique = _dedupe(candidates)
    if len(unique) == 1:
        return _navigable(unique[0], provenance=provenance, explanation=navigable_explanation)
    if len(unique) > 1:
        names = ", ".join(sorted(_display_name(item) for item in unique))
        return _ambiguous(
            unique,
            provenance=provenance,
            explanation=f"{ambiguous_prefix}: {names}.",
        )
    return _unresolved(provenance=provenance, explanation=unresolved_explanation)


# ---------------------------------------------------------------------------
# Tilt-series relationships
# ---------------------------------------------------------------------------


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
        explicit = _dedupe(
            batch for batch in batch_positions if batch.id == tilt.linked_batch_position_id
        )
        if len(explicit) == 1:
            return _navigable(
                explicit[0],
                provenance="tilt.linked_batch_position_id",
                explanation="Resolved from the tilt series' explicit batch-position ID.",
            )
        if len(explicit) > 1:
            return _ambiguous(
                explicit,
                provenance="tilt.linked_batch_position_id",
                explanation=(
                    "More than one batch position has the tilt series' explicit "
                    "batch-position ID."
                ),
            )
        # A recorded ID outside the current scope is not a failure on its own;
        # reciprocal metadata may still identify the batch.

    return _classify(
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
        return _classify(
            direct,
            provenance="search_tile.linked_tilt_series_ids",
            navigable_explanation="Resolved from the Search tile's linked tilt-series IDs.",
            ambiguous_prefix="Search tile link is ambiguous between",
            unresolved_explanation="No unambiguous Search tile is linked to this tilt series.",
        )

    if batch is not None and batch.linked_search_tile_id:
        explicit = _dedupe(tile for tile in search_tiles if tile.id == batch.linked_search_tile_id)
        if len(explicit) == 1:
            return _navigable(
                explicit[0],
                provenance="batch.linked_search_tile_id",
                explanation="Resolved from the batch position's explicit Search tile ID.",
            )
        if len(explicit) > 1:
            return _ambiguous(
                explicit,
                provenance="batch.linked_search_tile_id",
                explanation=(
                    "More than one Search tile has the batch position's explicit "
                    "Search tile ID."
                ),
            )

    if batch is not None:
        return _classify(
            [tile for tile in search_tiles if tile.batch_position_id == batch.id],
            provenance="search_tile.batch_position_id",
            navigable_explanation="Resolved from the Search tile's batch-position ID.",
            ambiguous_prefix="Search tile link is ambiguous between",
            unresolved_explanation="No unambiguous Search tile is linked to this batch position.",
        )

    return _unresolved(
        provenance="search_tile",
        explanation="No Search tile is linked to this tilt series.",
    )


def _resolve_search_map_for_tilt(
    tilt: TiltSeries,
    batch: BatchPosition | None,
    search_maps: tuple[SearchMap, ...],
) -> NavigationResolution:
    if batch is not None and batch.linked_search_map_id:
        explicit = _dedupe(
            search_map
            for search_map in search_maps
            if search_map.id == batch.linked_search_map_id
        )
        if len(explicit) == 1:
            return _navigable(
                explicit[0],
                provenance="batch.linked_search_map_id",
                explanation="Resolved from the batch position's explicit Search map ID.",
            )
        if len(explicit) > 1:
            return _ambiguous(
                explicit,
                provenance="batch.linked_search_map_id",
                explanation=(
                    "The batch position's Search map ID matches more than one Search "
                    "map in the current scope."
                ),
            )

    return _classify(
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
) -> NavigationResolution:
    if batch is not None and batch.linked_overview_id:
        explicit = _dedupe(
            overview for overview in overviews if overview.id == batch.linked_overview_id
        )
        if len(explicit) == 1:
            return _navigable(
                explicit[0],
                provenance="batch.linked_overview_id",
                explanation="Resolved from the batch position's explicit Overview ID.",
            )
        if len(explicit) > 1:
            return _ambiguous(
                explicit,
                provenance="batch.linked_overview_id",
                explanation=(
                    "More than one Overview has the batch position's explicit "
                    "Overview ID."
                ),
            )

    if search_map is not None:
        if search_map.overview is not None:
            return _navigable(
                search_map.overview,
                provenance="search_map.overview",
                explanation="Resolved from the Search map's Overview reference.",
            )
        return _classify(
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

    return _unresolved(
        provenance="overview",
        explanation="No Overview is linked to this tilt series.",
    )


# ---------------------------------------------------------------------------
# Search-tile and search-map relationships
#
# These replace widget-local `next(...)` helpers in ui/main_window.py so the
# viewer and the resolver cannot disagree about what a link means.
# ---------------------------------------------------------------------------


def resolve_search_tile_tilt_series(
    search_tile: SearchTile,
    *,
    tilt_series: Iterable[TiltSeries],
) -> NavigationResolution:
    """Resolve the tilt series a Search tile points at."""

    linked_ids = set(search_tile.linked_tilt_series_ids or [])
    if not linked_ids:
        return _unresolved(
            provenance="search_tile.linked_tilt_series_ids",
            explanation="No tilt series is linked to this Search tile.",
        )
    return _classify(
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
        return _unresolved(
            provenance="search_tile.batch_position_id",
            explanation="No batch position is linked to this Search tile.",
        )
    return _classify(
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
        return _unresolved(
            provenance="search_tile.search_map_id",
            explanation="No Search map is linked to this Search tile.",
        )
    return _classify(
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
        return _navigable(
            search_map.overview,
            provenance="search_map.overview",
            explanation="Resolved from the Search map's Overview reference.",
        )
    return _classify(
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


# ---------------------------------------------------------------------------
# Batch-position relationships
# ---------------------------------------------------------------------------


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
        explicit = _dedupe(
            overview for overview in overviews if overview.id == batch.linked_overview_id
        )
        if len(explicit) == 1:
            return _navigable(
                explicit[0],
                provenance="batch.linked_overview_id",
                explanation="Resolved from the batch position's explicit Overview ID.",
            )
        if len(explicit) > 1:
            return _ambiguous(
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
        return _classify(
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
        linked_maps = _dedupe(
            search_map
            for search_map in search_maps
            if search_map.id == batch.linked_search_map_id
        )
        if len(linked_maps) > 1:
            return _ambiguous(
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

    return _classify(
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
        explicit = _dedupe(
            search_map
            for search_map in search_maps
            if search_map.id == batch.linked_search_map_id
        )
        if len(explicit) == 1:
            return _navigable(
                explicit[0],
                provenance="batch.linked_search_map_id",
                explanation="Resolved from the batch position's explicit Search map ID.",
            )
        if len(explicit) > 1:
            return _ambiguous(
                explicit,
                provenance="batch.linked_search_map_id",
                explanation=(
                    "The explicit Search map ID resolves to more than one Search map "
                    "in the current scope."
                ),
            )
        # Silent, not vetoing — as above. The tilt-series intersection below is
        # chained evidence and stays out of reach once an explicit ID dangled.
        return _classify(
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

    tilt_ids = set(batch.linked_tilt_series_ids or [])
    return _classify(
        [
            search_map
            for search_map in search_maps
            if (
                batch.id in (search_map.linked_batch_position_ids or [])
                or (
                    tilt_ids
                    and tilt_ids.intersection(search_map.linked_tilt_series_ids or [])
                )
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
