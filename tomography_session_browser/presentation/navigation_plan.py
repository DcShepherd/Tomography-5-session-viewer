"""One navigation plan for button state and execution, with lazy arrivals."""
from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from functools import cached_property, partial
from types import MappingProxyType
from typing import Any, Literal

from tomography_session_browser.domain.display_names import (
    format_overview_display_name,
    format_search_tile_display_name,
)
from tomography_session_browser.domain.markers import ImageMarker, MarkerType
from tomography_session_browser.domain.models import (
    Atlas,
    BatchPosition,
    Overview,
    SearchMap,
    SearchTile,
    TiltSeries,
)
from tomography_session_browser.presentation.navigation_context import NavigationContext
from tomography_session_browser.presentation.workspace import EntityKind, Scope, Workspace
from tomography_session_browser.services.markers.builders import markers_for_object
from tomography_session_browser.services.markers.context import MarkerContext
from tomography_session_browser.services.navigation import (
    NavigationResolution,
    resolve_search_map_overview,
)
from tomography_session_browser.services.navigation.arrival import (
    marker_id_for_tilt_navigation,
    marker_id_for_search_tile_navigation,
    marker_id_for_batch_position_navigation,
    marker_id_for_search_map_navigation,
    marker_id_for_exposure_search_navigation,
    marker_id_for_exposure_context_navigation,
)
from tomography_session_browser.services.tilt_series_validation import (
    STATUS_FAILED,
    validate_session_tilt_series,
)

JumpKey = Literal["atlas", "overview", "search_map", "search", "batch", "tilt_series"]
JUMP_LABELS: Mapping[JumpKey, str] = MappingProxyType({
    "atlas": "Atlas", "overview": "Overview", "search_map": "Search map",
    "search": "Search tile", "batch": "Batch", "tilt_series": "Tilt series",
})


@dataclass(frozen=True)
class Jump:
    resolution: NavigationResolution
    tooltip: str
    arrival: Callable[[], str | None] | None = None
    frame: bool = True
    tilt_hand_off: bool = False
    atlas_arrival: Callable[[], tuple[tuple[str, str, str | None], ...]] | None = None
    #: When the arrival rule matches nothing, clear the destination's selection
    #: rather than leave the viewer's default highlight standing in for it.
    clear_unmatched_arrival: bool = False

    @cached_property
    def arrival_marker_id(self) -> str | None:
        """Project a destination's markers once, only if the jump is executed."""
        return self.arrival() if self.arrival is not None else None

    @cached_property
    def atlas_region(self) -> tuple[tuple[str, str, str | None], ...]:
        return self.atlas_arrival() if self.atlas_arrival is not None else ()


def _label(value: Any, context: NavigationContext) -> str:
    if isinstance(value, Overview):
        return format_overview_display_name(value.name)
    if isinstance(value, SearchTile):
        return format_search_tile_display_name(value.name, value.search_map_name)
    if isinstance(value, Atlas):
        return context.workspace.viewer_atlas_label(value)
    return getattr(value, "name", None) or value.id


def _tooltip(explanation: str, target: Any, context: NavigationContext) -> str:
    if target is None:
        return explanation
    separator = " " if explanation.endswith(".") else ". "
    return f"{explanation}{separator}Target: {_label(target, context)}."


def _linked_tooltip(kind: str, resolution: NavigationResolution, context: NavigationContext, inferred: str = "") -> str:
    if resolution.navigable:
        return f"Open linked {kind}: {_label(resolution.target, context)}"
    if resolution.ambiguous and resolution.explanation:
        # The resolver's own words, which name the candidates: "no link" would
        # say the metadata is silent where it in fact disagrees.
        return resolution.explanation
    return f"No linked {kind} found.{inferred}"


def _markers(target: Any, context: NavigationContext, marker_context: MarkerContext):
    # Keep early guards in arrival.py lazy, and never project while building buttons.
    yield from context.marker_provider(target, context=marker_context)


def _jump(resolution: NavigationResolution, tooltip: str, arrival_rule: Callable[..., str | None],
          context: NavigationContext, marker_context: MarkerContext, source: Any, *,
          clear_unmatched: bool = False, **kwargs) -> Jump:
    arrival = (partial(arrival_rule, resolution.target, source,
                       markers=_markers(resolution.target, context, marker_context), **kwargs)
               if resolution.navigable else None)
    return Jump(resolution, tooltip, arrival, clear_unmatched_arrival=clear_unmatched)


def _tilt(source: TiltSeries, context: NavigationContext) -> dict[JumpKey, Jump]:
    targets, markers = context.resolve_tilt_navigation(source)
    inferred = f" Inferred group: {targets.inferred_batch_label}." if targets.inferred_batch_label else ""
    resolutions: dict[JumpKey, tuple[NavigationResolution, str]] = {
        "batch": (targets.batch_position_resolution, "batch position"),
        "search": (targets.search_tile_resolution, "search tile"),
        "search_map": (targets.search_map_resolution, "search map"),
        "overview": (targets.overview_resolution, "overview"),
    }
    return {key: _jump(resolution, _linked_tooltip(kind, resolution, context, inferred),
                       marker_id_for_tilt_navigation, context, markers, source, batch=targets.batch_position,
                       clear_unmatched=key == "batch")
            for key, (resolution, kind) in resolutions.items()}


def _tile(source: SearchTile, context: NavigationContext) -> dict[JumpKey, Jump]:
    markers = context.marker_context_for(source)
    map_resolution = context.search_map_resolution_for_search_tile(source, markers)
    overview = (resolve_search_map_overview(map_resolution.target, overviews=markers.overviews)
                if map_resolution.navigable and map_resolution.target is not None else map_resolution)
    resolutions: dict[JumpKey, tuple[NavigationResolution, str]] = {
        "batch": (context.batch_resolution_for_search_tile(source, markers), "batch position"),
        "search_map": (map_resolution, "search map"), "overview": (overview, "overview"),
        "tilt_series": (context.tilt_resolution_for_search_tile(source, markers), "tilt series"),
    }
    return {key: _jump(resolution, _linked_tooltip(kind, resolution, context),
                       marker_id_for_search_tile_navigation, context, markers, source,
                       tilt_series=markers.tilt_series, batch_positions=markers.batch_positions)
            for key, (resolution, kind) in resolutions.items()}


def _batch(source: BatchPosition, context: NavigationContext) -> dict[JumpKey, Jump]:
    markers = context.marker_context_for(source)
    map_resolution = context.search_map_resolution_for_batch_position(source, markers)
    overview = context.overview_resolution_for_batch_position(source, map_resolution.target, markers)
    resolutions: dict[JumpKey, tuple[NavigationResolution, str]] = {
        "search_map": (map_resolution, "search map"), "overview": (overview, "overview"),
    }
    result: dict[JumpKey, Jump] = {key: _jump(resolution, _linked_tooltip(kind, resolution, context),
                         marker_id_for_batch_position_navigation, context, markers, source)
              for key, (resolution, kind) in resolutions.items()}
    exposure = context.tilt_resolution_for_exposure_marker(context.selected_marker, batch=source, marker_context=markers)
    resolution, tooltip = exposure.resolution, exposure.tooltip
    result["tilt_series"] = Jump(resolution, _tooltip(tooltip, resolution.target, context), tilt_hand_off=True)
    return result


def _exposure_search(source: Overview | SearchMap, context: NavigationContext, markers: MarkerContext) -> Jump:
    selected = context.selected_marker
    if selected is None or selected.marker_type not in {MarkerType.EXPOSURE_AREA, MarkerType.CAMERA_FOV}:
        explanation = "Select an exposure area to jump to its linked search item."
        return Jump(NavigationResolution(explanation=explanation, selection_required=True), explanation)
    resolution = context.search_tile_resolution_for_exposure_marker(selected)
    target = resolution.target if resolution.navigable else None
    # An arrival that matches no single exposure clears the tile's default
    # highlight, which would otherwise pick the first by area name (rule 6b).
    return _jump(resolution, _tooltip(resolution.explanation, target, context), marker_id_for_exposure_search_navigation,
                 context, markers, selected, clear_unmatched=True)


def _overview(source: Overview, context: NavigationContext) -> dict[JumpKey, Jump]:
    return {"search": _exposure_search(source, context, context.marker_context_for(source))}


def _map(source: SearchMap, context: NavigationContext) -> dict[JumpKey, Jump]:
    markers = context.marker_context_for(source)
    search = _exposure_search(source, context, markers)
    overview = resolve_search_map_overview(source, overviews=markers.overviews)
    selected = context.selected_marker
    if selected is not None and selected.marker_type in {MarkerType.EXPOSURE_AREA, MarkerType.CAMERA_FOV}:
        jump = _jump(overview, _linked_tooltip("overview", overview, context),
                     marker_id_for_exposure_context_navigation, context, markers, selected,
                     batch=context.batch_for_exposure_marker(selected))
    else:
        jump = _jump(overview, _linked_tooltip("overview", overview, context),
                     marker_id_for_search_map_navigation, context, markers, source)
        jump = Jump(jump.resolution, jump.tooltip, jump.arrival, frame=False)
    return {"search": search, "overview": jump}


_STRATEGIES = {TiltSeries: _tilt, SearchTile: _tile, BatchPosition: _batch, Overview: _overview, SearchMap: _map}


def plan_jumps(source: Any, workspace: Workspace, scope: Scope, selected_marker: ImageMarker | None,
               *, failed_tilt_ids: frozenset[str] | None = None,
               marker_provider: Callable[..., list[ImageMarker]] = markers_for_object) -> Mapping[JumpKey, Jump]:
    """Resolve the same actions for buttons and clicks without changing the workspace.

    The caller supplies the active scope and selected marker. A transient plan
    retains entities only for this interaction; it is never stored in history.
    """
    strategy = next((_STRATEGIES[cls] for cls in type(source).__mro__ if cls in _STRATEGIES), None)
    if strategy is None:
        return MappingProxyType({})
    if failed_tilt_ids is None:
        failed_tilt_ids = frozenset(value.tilt_series_id for value in validate_session_tilt_series(
            workspace.entities(EntityKind.TILT_SERIES)) if value.status == STATUS_FAILED)
    context = NavigationContext(workspace, scope, source, selected_marker, failed_tilt_ids, marker_provider)
    jumps = strategy(source, context)
    atlas = context.atlas_resolution_for(source)
    atlas_jump = Jump(atlas, _tooltip(atlas.explanation, atlas.target, context),
                      atlas_arrival=lambda: tuple(context.atlas_region_of_interest(source)))
    return MappingProxyType({"atlas": atlas_jump, **jumps})
