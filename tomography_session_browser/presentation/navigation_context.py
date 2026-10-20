"""Qt-free navigation inputs, scope queries and existing fallback policies."""
from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from typing import Any

from tomography_session_browser.domain.traversal import has_collection_data
from tomography_session_browser.domain.models import (
    BatchPosition,
    Overview,
    Sample,
    SearchMap,
    SearchTile,
    Session,
    TiltSeries,
)
from tomography_session_browser.domain.markers import ImageMarker, MarkerType
from tomography_session_browser.presentation.workspace import Workspace, Scope, EntityKind
from tomography_session_browser.presentation.project_tree import ProjectTreeGroup, group_contains_value
from tomography_session_browser.presentation.scopes import LinkedSampleGroup, build_linked_sample_group
from tomography_session_browser.services.markers.builders import markers_for_object
from tomography_session_browser.services.markers.context import MarkerContext
from tomography_session_browser.services.session_linking import atlas_link_resolutions
from tomography_session_browser.services.navigation import (
    NavigationResolution,
    ExposureTiltSeriesResolution,
    resolve_tilt_series_navigation_targets,
    resolve_search_tile_batch_position,
    resolve_search_tile_search_map,
    resolve_search_tile_tilt_series,
    resolve_batch_position_search_map,
    resolve_batch_position_overview,
    resolve_search_map_overview,
    resolve_item_atlas,
    resolve_exposure_tilt_series,
)
from tomography_session_browser.services.navigation.exposures import (
    batch_for_exposure_marker,
    EXPOSURE_SEARCH_MISSING,
    EXPOSURE_SEARCH_PROVENANCE,
    resolve_search_tile_for_exposure_marker,
    marker_fallback_resolution,
    preferred_batch_marker,
    preferred_navigation_marker,
)

_ATLAS_ITEM_LISTS = {
    Overview: "overviews", SearchMap: "search_maps", SearchTile: "search_tiles",
    BatchPosition: "batch_positions", TiltSeries: "tilt_series",
}


@dataclass(frozen=True)
class NavigationContext:
    workspace: Workspace
    scope: Scope
    source: Any
    selected_marker: ImageMarker | None = None
    failed_tilt_ids: frozenset[str] = frozenset()
    marker_provider: Callable[..., list[ImageMarker]] = markers_for_object

    def marker_context_for(self, value: Any) -> MarkerContext:
        fallback = self.workspace.project_marker_context(self.failed_tilt_ids)
        return self.workspace.marker_context(value, fallback)

    def batch_for_exposure_marker(self, marker: ImageMarker) -> BatchPosition | None:
        return batch_for_exposure_marker(marker, find_object=self.workspace.find)

    def resolve_tilt_navigation(
        self,
        tilt: TiltSeries,
    ):
        marker_context = self.marker_context_for(tilt)
        targets = resolve_tilt_series_navigation_targets(
            tilt,
            batch_positions=marker_context.batch_positions,
            search_maps=marker_context.search_maps,
            overviews=marker_context.overviews,
            search_tiles=self.workspace.entities(EntityKind.SEARCH_TILE, self.context_for_object(tilt)),
        )
        # A marker sweep may only assist a link the metadata never recorded.
        # When the metadata is ambiguous the action must stay inert, otherwise
        # the resolver's refusal to guess is simply replaced by a different
        # guess (plan item N1 -> N2).
        map_resolution = targets.search_map_resolution
        if map_resolution.unresolved:
            map_resolution = self.search_map_resolution_from_tilt_marker(tilt, marker_context)
        search_map = map_resolution.target

        overview_resolution = targets.overview_resolution
        if overview_resolution.unresolved and search_map is not None:
            overview_resolution = resolve_search_map_overview(
                search_map,
                overviews=marker_context.overviews,
            )
        if overview_resolution.unresolved:
            overview_resolution = self.overview_resolution_from_tilt_marker(
                tilt,
                marker_context,
            )
        overview = overview_resolution.target

        if search_map is targets.search_map and overview is targets.overview:
            return targets, marker_context
        return replace(
            targets,
            search_map=search_map,
            overview=overview,
            search_map_resolution=map_resolution,
            overview_resolution=overview_resolution,
        ), marker_context

    def batch_resolution_for_search_tile(
        self,
        search_tile: SearchTile,
        context: MarkerContext,
    ) -> NavigationResolution:
        return resolve_search_tile_batch_position(
            search_tile,
            batch_positions=context.batch_positions,
        )

    def search_map_resolution_for_search_tile(
        self,
        search_tile: SearchTile,
        context: MarkerContext,
    ) -> NavigationResolution:
        return resolve_search_tile_search_map(search_tile, search_maps=context.search_maps)

    def tilt_resolution_for_search_tile(
        self,
        search_tile: SearchTile,
        context: MarkerContext,
    ) -> NavigationResolution:
        return resolve_search_tile_tilt_series(search_tile, tilt_series=context.tilt_series)

    def search_map_resolution_for_batch_position(
        self,
        batch_position: BatchPosition,
        context: MarkerContext,
    ) -> NavigationResolution:
        resolution = resolve_batch_position_search_map(
            batch_position,
            search_maps=context.search_maps,
        ).resolution
        if not resolution.unresolved:
            # Navigable or ambiguous: the metadata has already decided, and a
            # marker sweep must not overrule an ambiguity.
            return resolution
        return marker_fallback_resolution(
            ((candidate, self.marker_provider(candidate, context=context)) for candidate in context.search_maps),
            provenance="search_map.batch_marker",
            navigable_explanation="Resolved from a batch marker painted on the Search map.",
            ambiguous_prefix="Batch markers appear on more than one Search map",
            unresolved_explanation=resolution.explanation,
            predicate=lambda candidate, markers: preferred_batch_marker(
                markers, batch_position
            )
            is not None,
        )

    def overview_resolution_for_batch_position(
        self,
        batch_position: BatchPosition,
        search_map: SearchMap | None,
        context: MarkerContext,
    ) -> NavigationResolution:
        resolution = resolve_batch_position_overview(
            batch_position,
            search_maps=context.search_maps,
            overviews=context.overviews,
        ).resolution
        if not resolution.unresolved:
            return resolution
        if search_map is not None:
            via_map = resolve_search_map_overview(search_map, overviews=context.overviews)
            if not via_map.unresolved:
                return via_map
        return marker_fallback_resolution(
            ((candidate, self.marker_provider(candidate, context=context)) for candidate in context.overviews),
            provenance="overview.batch_marker",
            navigable_explanation="Resolved from a batch marker painted on the Overview.",
            ambiguous_prefix="Batch markers appear on more than one Overview",
            unresolved_explanation=resolution.explanation,
            predicate=lambda candidate, markers: preferred_batch_marker(
                markers, batch_position
            )
            is not None,
        )

    def search_map_resolution_from_tilt_marker(
        self,
        tilt: TiltSeries,
        context: MarkerContext,
    ) -> NavigationResolution:
        return marker_fallback_resolution(
            ((candidate, self.marker_provider(candidate, context=context)) for candidate in context.search_maps),
            provenance="search_map.tilt_marker",
            navigable_explanation="Resolved from a tilt marker painted on the Search map.",
            ambiguous_prefix="Tilt markers appear on more than one Search map",
            unresolved_explanation="No Search map carries a marker for this tilt series.",
            predicate=lambda candidate, markers: preferred_navigation_marker(
                markers, tilt, batch=None
            )
            is not None,
        )

    def overview_resolution_from_tilt_marker(
        self,
        tilt: TiltSeries,
        context: MarkerContext,
    ) -> NavigationResolution:
        return marker_fallback_resolution(
            ((candidate, self.marker_provider(candidate, context=context)) for candidate in context.overviews),
            provenance="overview.tilt_marker",
            navigable_explanation="Resolved from a tilt marker painted on the Overview.",
            ambiguous_prefix="Tilt markers appear on more than one Overview",
            unresolved_explanation="No Overview carries a marker for this tilt series.",
            predicate=lambda candidate, markers: preferred_navigation_marker(
                markers, tilt, batch=None
            )
            is not None,
        )

    def atlas_resolution_for(self, value: Any) -> NavigationResolution:
        """The Atlas of the grid ``value`` was acquired on, through the resolver.

        The facts come from the folders and the AtlasId link the project tree
        already trusts; an ambiguous AtlasId passes on every candidate so the
        jump stays inert and names them. Items are found by identity: two
        sessions can hold equal-looking records.
        """

        field = _ATLAS_ITEM_LISTS.get(type(value))
        if field is None:
            return resolve_item_atlas(own_atlas=None)
        sample = next(
            (sample for sample in self.workspace.samples if any(item is value for item in getattr(sample, field))),
            None,
        )
        if sample is None:
            session = next(
                (session for session in self.workspace.sessions if any(item is value for item in getattr(session, field))),
                None,
            )
            return resolve_item_atlas(own_atlas=session.atlas if session is not None else None)
        if sample.atlas is not None:
            return resolve_item_atlas(own_atlas=sample.atlas)
        link = atlas_link_resolutions(self.workspace.sessions).get(id(sample))
        if link is None:
            return resolve_item_atlas(own_atlas=None)
        candidates = (link.sample,) if link.linked and link.sample is not None else link.candidates
        return resolve_item_atlas(
            own_atlas=None,
            linked_atlases=[candidate.atlas for candidate in candidates if candidate.atlas is not None],
        )

    def atlas_region_of_interest(self, value: Any) -> list[tuple[str, str, str | None]]:
        """Where ``value`` is on its Atlas, most exact first.

        Each entry is a marker type, the linked ID to find it by and, for a
        stand-in, what it is. The item's own marker comes first; where the
        Atlas does not draw the item, the nearest item that it does draw,
        reached through the resolver's verified links only.
        """

        region: list[tuple[str, str, str | None]] = []

        def add(marker_type: str, target: Any, kind: str | None) -> None:
            if target is not None:
                region.append((marker_type, target.id, kind))

        if isinstance(value, Overview):
            add(MarkerType.OVERVIEW, value, None)
        elif isinstance(value, SearchMap):
            context = self.marker_context_for(value)
            add(MarkerType.SEARCH_MAP, value, None)
            add(MarkerType.OVERVIEW, resolve_search_map_overview(value, overviews=context.overviews).target, "Overview")
        elif isinstance(value, SearchTile):
            context = self.marker_context_for(value)
            search_map = self.search_map_resolution_for_search_tile(value, context).target
            add(MarkerType.BATCH_POSITION, self.batch_resolution_for_search_tile(value, context).target, "batch position")
            add(MarkerType.SEARCH_MAP, search_map, "Search map")
            if search_map is not None:
                overview = resolve_search_map_overview(search_map, overviews=context.overviews).target
                add(MarkerType.OVERVIEW, overview, "Overview")
        elif isinstance(value, BatchPosition):
            context = self.marker_context_for(value)
            search_map = self.search_map_resolution_for_batch_position(value, context).target
            add(MarkerType.BATCH_POSITION, value, None)
            add(MarkerType.SEARCH_MAP, search_map, "Search map")
            add(
                MarkerType.OVERVIEW,
                self.overview_resolution_for_batch_position(value, search_map, context).target,
                "Overview",
            )
        elif isinstance(value, TiltSeries):
            targets, _context = self.resolve_tilt_navigation(value)
            # A failed tilt series with no batch is drawn where its MRC
            # header puts it (approximately).
            add(MarkerType.TILT_SERIES, value, None)
            add(MarkerType.BATCH_POSITION, targets.batch_position, "batch position")
            add(MarkerType.SEARCH_MAP, targets.search_map, "Search map")
            add(MarkerType.OVERVIEW, targets.overview, "Overview")
        return region

    def context_for_object(self, value: Any) -> Session | Sample | LinkedSampleGroup | ProjectTreeGroup | None:
        if isinstance(value, LinkedSampleGroup):
            return value
        if isinstance(self.scope, Session) and (
            self.workspace.session_scope_contains(self.scope, value)
            or value in self.workspace.linked_atlases(self.scope)
        ):
            return self.scope
        if isinstance(self.scope, Sample) and (
            self.workspace.sample_contains(self.scope, value)
            or value in self.workspace.linked_atlases(self.scope)
        ):
            return self.scope
        scoped_sessions = self.workspace.sessions
        scoped_group = self.scope if isinstance(self.scope, ProjectTreeGroup) else None
        if scoped_group is not None and group_contains_value(scoped_group, value):
            scoped_sessions = scoped_group.sessions
        for samples in self.workspace.linked_groups_for(scoped_sessions):
            if any(self.workspace.sample_contains(sample, value) for sample in samples):
                if len(scoped_sessions) > 1 and self.samples_form_cross_session_link(samples, scoped_sessions):
                    label = self.linked_sample_label(samples)
                    return build_linked_sample_group(label, list(samples))
                return next(sample for sample in samples if self.workspace.sample_contains(sample, value))
        for group in self.workspace.project_groups:
            if group_contains_value(group, value):
                if len(group.sessions) > 1:
                    return group
                break
        for session in scoped_sessions:
            if self.workspace.session_contains(session, value):
                return session
        return self.scope if isinstance(self.scope, Session | Sample | LinkedSampleGroup | ProjectTreeGroup) else None

    def samples_form_cross_session_link(self, samples: Sequence[Sample], sessions: Sequence[Session]) -> bool:
        session_ids = {id(session) for session in sessions}
        parent_sessions: set[int] = set()
        has_atlas = False
        has_collection = False
        for session in sessions:
            for sample in session.samples:
                if not any(sample is candidate for candidate in samples):
                    continue
                parent_sessions.add(id(session))
                has_atlas = has_atlas or sample.atlas is not None
                has_collection = has_collection or has_collection_data(sample)
        return len(parent_sessions & session_ids) > 1 and has_atlas and has_collection

    def linked_sample_label(self, samples: Sequence[Sample]) -> str:
        atlas_sample = next((sample for sample in samples if sample.atlas is not None), None)
        if atlas_sample is not None:
            return atlas_sample.name
        collection_sample = next((sample for sample in samples if has_collection_data(sample)), samples[0])
        return collection_sample.name

    def search_tile_resolution_for_exposure_marker(self, marker: ImageMarker) -> NavigationResolution:
        batch = self.batch_for_exposure_marker(marker)
        if batch is None:
            return NavigationResolution(explanation=EXPOSURE_SEARCH_MISSING, provenance=EXPOSURE_SEARCH_PROVENANCE)
        source = self.workspace.find(marker.source_object_id)
        if not isinstance(source, Overview | SearchMap):
            source = self.source if isinstance(self.source, Overview | SearchMap) else None
        context = self.context_for_object(source or batch)
        return resolve_search_tile_for_exposure_marker(
            marker,
            batch=batch,
            source=source,
            search_maps=self.workspace.entities(EntityKind.SEARCH_MAP, context),
            search_tiles=self.workspace.entities(EntityKind.SEARCH_TILE, context),
        )

    def tilt_resolution_for_exposure_marker(
        self,
        marker: ImageMarker | None,
        *,
        batch: BatchPosition | None = None,
        marker_context: MarkerContext | None = None,
    ) -> ExposureTiltSeriesResolution:
        if marker is None or marker.marker_type not in {MarkerType.EXPOSURE_AREA, MarkerType.CAMERA_FOV}:
            hint = "Select an exposure area to jump to its tilt series."
            return ExposureTiltSeriesResolution(
                None,
                None,
                hint,
                resolution=NavigationResolution(explanation=hint, selection_required=True),
            )
        batch = batch or self.batch_for_exposure_marker(marker)
        if batch is None:
            return ExposureTiltSeriesResolution(
                None,
                None,
                "No tilt series is associated with the selected exposure area.",
                resolution=NavigationResolution(explanation="No tilt series is associated with the selected exposure area."),
            )
        marker_context = marker_context or self.marker_context_for(batch)
        return resolve_exposure_tilt_series(
            marker,
            batch=batch,
            tilt_series=marker_context.tilt_series,
        )
