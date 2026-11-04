"""MarkerNavigation owns its window interaction responsibilities."""
from __future__ import annotations
from collections.abc import (
    Mapping,
)
from typing import (
    Any,
    Callable,
)
from PySide6.QtCore import (
    QObject,
)
from PySide6.QtWidgets import (
    QMainWindow,
)
from tomography_session_browser.ui.shell.chrome import (
    TAB_LABELS,
)
from tomography_session_browser.domain.markers import (
    ImageMarker,
    MarkerType,
    SelectionState,
)
from tomography_session_browser.domain.models import (
    BatchPosition,
    Overview,
    SearchMap,
    SearchTile,
    TiltSeries,
)
from tomography_session_browser.domain.status import ValidationStatus
from tomography_session_browser.presentation.navigation_context import (
    NavigationContext,
)
from tomography_session_browser.presentation.context_stack import entity_display_label_singular
from tomography_session_browser.presentation.navigation_plan import (
    JUMP_LABELS,
    Jump,
    JumpKey,
    plan_jumps,
)
from tomography_session_browser.ui.navigation.controller import (
    execute_jump,
    NavigationEffects,
)
from tomography_session_browser.presentation.workspace import (
    EntityKind,
)
from tomography_session_browser.services.markers.builders import markers_for_object
from tomography_session_browser.services.markers.context import MarkerContext
from tomography_session_browser.services.navigation.arrival import (
    marker_id_for_batch_position_navigation,
)
from tomography_session_browser.services.navigation.resolution import navigation_resolution_from_candidates, navigable_resolution
from tomography_session_browser.services.navigation import (
    ExposureTiltSeriesResolution,
    NavigationResolution,
    resolve_batch_position_overview,
    resolve_batch_position_search_map,
    tab_label_for_object,
)
from tomography_session_browser.ui.viewer.models import ViewerNavigationAction
from tomography_session_browser.ui.tomograms.navigation import TomogramNavigation
from tomography_session_browser.presentation.empty_states import (
    unresolved_relationship_state,
)

from tomography_session_browser.ui.window_parts.contracts import (
    WindowParts,
    WindowUi,
)
from tomography_session_browser.ui.window_parts.state import (
    WindowState,
)
from tomography_session_browser.presentation.tab_keys import TabKey
from tomography_session_browser.presentation.exposure_areas import exposure_area_title
from collections.abc import Iterable

def navigation_markers(value: Any, context: MarkerContext) -> Iterable[ImageMarker]:
    """Derive markers only when an arrival rule consumes them."""
    yield from markers_for_object(value, context=context)


def _same_plan_inputs(first: tuple[Any, ...], second: tuple[Any, ...]) -> bool:
    """Same source, workspace and scope objects, the same selected marker id
    and failed-tilt set. Objects compare by identity: a rebuilt scope is a
    new scope."""

    source, marker_id, workspace, scope, failed = first
    return (
        source is second[0]
        and marker_id == second[1]
        and workspace is second[2]
        and scope is second[3]
        and failed == second[4]
    )


class MarkerNavigation(QObject):
    def __init__(self, window: QMainWindow, state: WindowState, ui: WindowUi, parts: WindowParts) -> None:
        super().__init__(window)
        self.window, self.state, self.ui, self.parts = window, state, ui, parts

    def atlas_cluster_member_activated(self, marker: ImageMarker) -> None:
        resolution = self.resolve_action_target(
            str(marker.metadata.get("batch_position_id") or marker.linked_object_id or ""),
            entity_type=BatchPosition, source=self.current_source(),
        )
        batch = resolution.target
        if not resolution.navigable or not isinstance(batch, BatchPosition):
            self.parts.context.post_navigation_message(resolution.explanation)
            return
        context = self.marker_context_for_batch_position(batch)
        target = resolve_batch_position_search_map(batch, search_maps=context.search_maps)
        if not target.navigable or target.search_map is None:
            self.ui.context_panel.set_title(marker.label or "Batch position")
            self.ui.context_panel.set_text(f"{marker.tooltip or ''}\n\n{target.explanation}".strip())
            self.parts.context.post_navigation_message(target.explanation)
            return
        search_map = target.search_map
        self.state.viewer.selection_state = SelectionState(
            selected_object_type=type(batch).__name__, selected_object_id=batch.id,
            selected_marker_id=marker.id, source="atlas-cluster-member",
        )
        self.state.dashboard.dashboard_scope_prefers_tree = True
        self.open_resolved_destination(
            search_map,
            message=f"Opened search map {self.parts.scopes.label_for(search_map)} for batch position {self.parts.scopes.label_for(batch)}.",
            arrival=lambda: self.marker_id_for_batch_position_navigation(search_map, batch, context=context),
        )


    def navigation_context(self, source: Any) -> NavigationContext:
        return NavigationContext(self.state.review.workspace, self.state.review.scope, source,
                                 failed_tilt_ids=self.parts.navigation.failed_tilt_ids(),
                                 marker_provider=markers_for_object)

    def current_source(self) -> Any:
        tab = self.ui.viewer_tabs.get(TAB_LABELS[self.ui.tabs.currentIndex()])
        return tab.current_object() if tab is not None else None

    def resolve_action_target(self, identifier: str | None, *, entity_type: type | tuple[type, ...] | None = None,
                              source: Any = None) -> NavigationResolution:
        """ID-originated actions must establish uniqueness in their source scope."""
        workspace = self.state.review.workspace
        candidates = [value for value in workspace.find_candidates(identifier)
                      if entity_type is None or isinstance(value, entity_type)]
        scope = self.parts.scopes.context_for_object(source) if source is not None else self.state.review.scope
        members = workspace.members_for(source) if source is not None else ()
        scoped = [value for value in candidates if (
            any(workspace.sample_contains(sample, value) for sample in members)
            if members else workspace.contains(scope, value)
        )]
        return navigation_resolution_from_candidates(
            scoped or candidates, provenance="action.source_scope+id",
            navigable_explanation="Unique destination in the source scope.",
            ambiguous_prefix="Ambiguous destination ID", unresolved_explanation="The requested item could not be resolved in the loaded sessions.",
        )

    def open_resolved_destination(self, target: Any, *, message: str,
                                  frame_index: int | None = None, highlight_summary: bool = False,
                                  arrival: Callable[[], str | None] | None = None, frame: bool = False) -> None:
        resolution = navigable_resolution(target, provenance="action.resolved", explanation=message)
        self.execute_planned_jump(target, "destination", Jump(
            resolution, message, arrival=arrival, frame=frame, message=message,
            frame_index=frame_index, highlight_summary=highlight_summary,
        ))


    def navigation_plan_for(self, source: Any) -> Mapping[JumpKey, Jump]:
        """The item's planned jumps; each tab's last plan is reused while its inputs
        are unchanged.

        One tab switch asks three times for the same plan (the header's
        buttons, then the context header's reasons twice), and a Tilt series
        plan sweeps every Search map's markers. Identical inputs give an
        identical plan, so it is worked out once.
        """

        marker_id = None
        tab = None
        if isinstance(source, Overview | SearchMap | BatchPosition):
            tab = self.ui.viewer_tabs.get(tab_label_for_object(source) or "")
            marker_id = tab.selected_marker_id() if tab is not None else None
        inputs = (source, marker_id, self.state.review.workspace, self.state.review.scope,
                  self.parts.navigation.failed_tilt_ids())
        tab_key = tab_label_for_object(source)
        last = self.state.viewer.navigation_plans.get(tab_key)
        if last is not None and _same_plan_inputs(last[0], inputs):
            return last[1]
        marker = None
        if marker_id:
            # The tab already holds the markers it drew, built from the same
            # workspace context; rebuilding them to find one is the cost.
            markers = tab.held_markers(source) if tab is not None else None
            if markers is None:
                context = self.parts.navigation.navigation_context(source).marker_context_for(source)
                markers = markers_for_object(source, context=context)
            marker = next((item for item in markers if item.id == marker_id), None)
        plan = plan_jumps(source, self.state.review.workspace, self.state.review.scope, marker,
                          failed_tilt_ids=inputs[4], marker_provider=markers_for_object)
        self.state.viewer.navigation_plans[tab_key] = (inputs, plan)
        return plan


    def viewer_navigation_actions(self, value: Any) -> list[ViewerNavigationAction]:
        return [ViewerNavigationAction(key, f"\u2197 {JUMP_LABELS[key]}",
                                       enabled=jump.resolution.navigable, tooltip=jump.tooltip,
                                       selection_required=jump.resolution.selection_required)
                for key, jump in self.parts.navigation.navigation_plan_for(value).items()] + TomogramNavigation(self.parts).acquisition_actions(value)


    def viewer_navigation_requested(self, value: Any, key: str) -> None:
        if key == "tomograms":
            TomogramNavigation(self.parts).open_acquisition(value)
            return
        jump = self.parts.navigation.navigation_plan_for(value).get(key)
        if jump is not None:
            self.execute_planned_jump(value, key, jump)


    def execute_planned_jump(self, value: Any, key: str, jump: Jump) -> None:
        self.parts.viewers.cancel_scope_preparation()
        effects = NavigationEffects(
            viewer_tabs=self.ui.viewer_tabs,
            post_message=self.parts.context.post_navigation_message,
            inert_navigation_message=self.inert_navigation_message,
            show_unresolved_relationship_panel=self.show_unresolved_relationship_panel,
            begin_navigation=self.parts.history.begin_navigation,
            enter_navigation_scope=self.enter_navigation_scope,
            select_viewer_object=self.parts.viewers.select_viewer_object,
            label_for=self.parts.scopes.label_for,
            show_arrival_message=self.parts.context.show_arrival_message,
            record_history=self.parts.history.record_history,
            highlight_tilt=self.parts.dashboard.set_dashboard_highlighted_tilt_series_id,
        )
        execute_jump(effects, value, key, jump)


    def inert_navigation_message(
        self,
        value: Any,
        key: str,
        resolution: NavigationResolution | None,
    ) -> str:
        """Explain an action that resolved to nothing.

        An ambiguous link and a missing link both leave the page unchanged, but
        they are different findings and must read differently: one says the
        metadata disagrees, the other says there is none.
        """

        subject = getattr(value, "name", None) or getattr(value, "id", "this item")
        if resolution is not None and resolution.explanation:
            return f"{resolution.explanation} No change to {subject}."
        return f"No linked {key.replace('_', ' ')} found for {subject}."


    def show_unresolved_relationship_panel(
        self,
        value: Any,
        key: str,
        resolution: NavigationResolution | None,
    ) -> None:
        """Explain a refused jump on the page, not only in the status bar.

        E1 modelled this variant and nothing ever reached it, so the one place
        the reviewer was told a relationship could not be resolved was a status
        message that the next action overwrites. It appears only when there is
        no image to cover — the panel never hides real content.
        """

        label = TAB_LABELS[self.ui.tabs.currentIndex()]
        tab = self.ui.viewer_tabs.get(label)
        if tab is None or tab.viewer.has_image():
            return
        explanation = (
            resolution.explanation
            if resolution is not None and resolution.explanation
            else f"No linked {key.replace('_', ' ')} is recorded for this item."
        )
        tab.show_empty_state(
            unresolved_relationship_state(
                subject=self.parts.scopes.label_for(value),
                explanation=explanation,
                still_inspectable=(
                    f"{self.parts.scopes.label_for(value)} and its metadata remain readable here.",
                ),
            )
        )


    def enter_navigation_scope(self, target: Any) -> None:
        """Switch scope for an arrival.

        ``_render_viewer_tabs()`` is called unconditionally, but it is no
        longer an unconditional *rebuild*: S1 split the marker refresh out of
        the tab teardown, so when ``has_same_items()`` reports the rows are
        unchanged it takes ``ViewerTab.refresh_providers()`` instead and the
        selection, filter, frame and scroll position all survive. Same-scope
        navigation therefore costs a provider re-point and an overlay repaint,
        not a list rebuild.
        """

        self.state.review.preserve_tree_root_context = False
        if isinstance(target, TiltSeries):
            self.state.dashboard.dashboard_focus_value = None
            self.state.dashboard.dashboard_scope_prefers_tree = False
            self.state.dashboard.tilt_viewer_selected_tilt_series_id = target.id
        context = self.parts.scopes.context_for_object(target)
        if context is None:
            return
        self.state.review.scope = context
        self.parts.viewers.render_viewer_tabs()
        if isinstance(target, TiltSeries):
            self.parts.chrome.update_tab_counts()
            self.parts.dashboard.render_dashboard_for_scope()


    def marker_context_for_batch_position(self, batch_position: BatchPosition) -> MarkerContext:
        return self.parts.navigation.navigation_context(batch_position).marker_context_for(batch_position)


    def marker_context_for_overview(self, overview: Overview) -> MarkerContext:
        return self.parts.navigation.navigation_context(overview).marker_context_for(overview)


    def failed_tilt_ids(self) -> frozenset[str]:
        if self.state.viewer.failed_tilt_ids_cache is not None:
            return self.state.viewer.failed_tilt_ids_cache
        workspace = self.state.review.workspace
        self.state.viewer.failed_tilt_ids_cache = frozenset(
            tilt_id
            for tilt_id, validation in workspace.tilt_validations.for_series(workspace.entities(EntityKind.TILT_SERIES)).items()
            if validation.status == ValidationStatus.FAILED
        )
        return self.state.viewer.failed_tilt_ids_cache


    def marker_id_for_batch_position_navigation(
        self,
        target: Any,
        batch_position: BatchPosition,
        *,
        context: MarkerContext,
    ) -> str | None:
        return marker_id_for_batch_position_navigation(
            target, batch_position,
            markers=navigation_markers(target, context),
        )


    def should_open_tilt_from_exposure_marker(self, marker: ImageMarker) -> bool:
        if TAB_LABELS[self.ui.tabs.currentIndex()] not in {TabKey.SEARCH, TabKey.BATCH_POSITION}:
            return False
        if marker.marker_type not in {MarkerType.EXPOSURE_AREA, MarkerType.CAMERA_FOV, MarkerType.LINK_LINE}:
            return False
        # Exposure refusals belong to their resolver; falling through to a
        # generic linked-object action could rescue an ambiguous batch ID.
        return (marker.marker_type != MarkerType.LINK_LINE
                or self.parts.navigation.batch_for_exposure_marker(marker) is not None)


    def should_open_search_tile_from_exposure_marker(self, marker: ImageMarker) -> bool:
        if TAB_LABELS[self.ui.tabs.currentIndex()] not in {TabKey.OVERVIEW, TabKey.SEARCH_MAP}:
            return False
        if marker.marker_type not in {MarkerType.EXPOSURE_AREA, MarkerType.CAMERA_FOV}:
            return False
        return True


    def batch_for_exposure_marker(self, marker: ImageMarker) -> BatchPosition | None:
        return self.navigation_context(self.current_source()).batch_for_exposure_marker(marker)


    def tilt_resolution_for_exposure_marker(
        self,
        marker: ImageMarker,
        *,
        batch: BatchPosition | None = None,
        marker_context: MarkerContext | None = None,
    ) -> ExposureTiltSeriesResolution:
        return self.parts.navigation.navigation_context(batch).tilt_resolution_for_exposure_marker(
            marker, batch=batch, marker_context=marker_context,
        )


    def tilt_for_exposure_marker(self, marker: ImageMarker) -> TiltSeries | None:
        batch = self.parts.navigation.batch_for_exposure_marker(marker)
        if batch is None:
            return None
        return self.parts.navigation.tilt_resolution_for_exposure_marker(marker, batch=batch).tilt_series


    def exposure_search_source(self, marker: ImageMarker) -> Overview | SearchMap | None:
        """The image an exposure marker is drawn on: its own source, else the
        item the current tab shows."""

        source = self.current_source()
        if not isinstance(source, Overview | SearchMap) or source.id != marker.source_object_id:
            resolution = self.resolve_action_target(marker.source_object_id, entity_type=(Overview, SearchMap), source=source)
            source = resolution.target if resolution.navigable else None
        if isinstance(source, Overview | SearchMap):
            return source
        viewer = self.ui.viewer_tabs.get(TAB_LABELS[self.ui.tabs.currentIndex()])
        current = viewer.current_object() if viewer is not None else None
        return current if isinstance(current, Overview | SearchMap) else None


    def search_tile_for_exposure_marker(self, marker: ImageMarker) -> SearchTile | None:
        source = self.parts.navigation.exposure_search_source(marker)
        resolution = self.parts.navigation.navigation_context(source).search_tile_resolution_for_exposure_marker(marker)
        return resolution.target if resolution.navigable else None


    def select_marker(self, marker: ImageMarker, *, navigate: bool) -> None:
        if marker.metadata.get("atlas_lod_role") in {"batch_position", "unattributed"}:
            if not bool(marker.metadata.get("navigation_enabled", False)):
                message = str(
                    marker.metadata.get("navigation_explanation")
                    or "This Atlas marker has no unambiguous Overview link."
                )
                self.ui.context_panel.set_title(marker.label or "Atlas marker")
                self.ui.context_panel.set_text(marker.tooltip or message)
                self.parts.context.post_navigation_message(message)
                return
            if navigate and self.parts.navigation.open_atlas_batch_overview(marker):
                return
        open_exposure_search_tile = navigate and self.parts.navigation.should_open_search_tile_from_exposure_marker(marker)
        open_exposure_tilt = navigate and not open_exposure_search_tile and self.parts.navigation.should_open_tilt_from_exposure_marker(marker)
        refusal: NavigationResolution | None = None
        if open_exposure_search_tile:
            # The Search button's own planned jump: the same destination, the
            # same refusal when it is ambiguous, the same arrival (rule 6b).
            source = self.parts.navigation.exposure_search_source(marker)
            jump = (
                plan_jumps(source, self.state.review.workspace, self.state.review.scope, marker,
                           failed_tilt_ids=self.parts.navigation.failed_tilt_ids(),
                           marker_provider=markers_for_object).get("search")
                if source is not None else None
            )
            if jump is not None:
                target = jump.resolution.target if jump.resolution.navigable else None
                self.state.viewer.selection_state = SelectionState(
                    selected_object_type=type(target).__name__ if target is not None else None,
                    selected_object_id=getattr(target, "id", None) or marker.linked_object_id,
                    selected_marker_id=marker.id,
                    source="marker-open",
                )
                if target is not None:
                    self.state.dashboard.dashboard_scope_prefers_tree = True
                self.parts.navigation.execute_planned_jump(source, "search", jump)
                return
            linked = None
        elif open_exposure_tilt:
            exposure = self.tilt_resolution_for_exposure_marker(marker)
            refusal = exposure.resolution
            linked = refusal.target if refusal.navigable else None
        else:
            linked = None
        if linked is None and not open_exposure_tilt and not open_exposure_search_tile:
            resolution = (self.navigation_context(self.current_source()).exposure_batch_resolution(marker)
                          if marker.marker_type in {MarkerType.EXPOSURE_AREA, MarkerType.CAMERA_FOV}
                          else self.resolve_action_target(marker.linked_object_id, source=self.current_source()))
            refusal = resolution
            linked = resolution.target if resolution.navigable else None
        self.state.viewer.selection_state = SelectionState(
            selected_object_type=type(linked).__name__ if linked is not None else None,
            selected_object_id=getattr(linked, "id", None) or marker.linked_object_id,
            selected_marker_id=marker.id,
            source="marker-open" if navigate else "marker",
        )
        if linked is None:
            self.ui.context_panel.set_title(marker.label or "Marker")
            status_text = (
                refusal.explanation if refusal is not None and refusal.explanation else
                "No linked tilt series found for this exposure area."
                if open_exposure_tilt
                else "No linked search item found for this exposure area."
                if open_exposure_search_tile
                else "Marker link could not be resolved."
            )
            lines = [
                marker.tooltip or status_text,
                "",
                f"Type: {marker.marker_type}",
                f"Linked object: {marker.linked_object_id or 'none'}",
                f"Status: {refusal.state if refusal is not None else 'unresolved'}",
            ]
            self.ui.context_panel.set_text("\n".join(lines))
            self.parts.context.post_navigation_message(status_text)
            return
        if not navigate:
            self.parts.viewers.select_visible_tab_object(linked)
            self.parts.context.set_context(self.parts.scopes.label_for(linked), linked)
            message = (
                f"{exposure_area_title(marker)} selected."
                if marker.marker_type == MarkerType.EXPOSURE_AREA
                else f"Selected {entity_display_label_singular(tab_label_for_object(linked))} {self.parts.scopes.label_for(linked)} from marker."
            )
            self.parts.context.post_navigation_message(message)
            return
        self.state.dashboard.dashboard_scope_prefers_tree = True
        kind = entity_display_label_singular(tab_label_for_object(linked))
        self.open_resolved_destination(linked, message=f"Opened {kind} {self.parts.scopes.label_for(linked)} from marker.")


    def open_atlas_batch_overview(self, marker: ImageMarker) -> bool:
        resolution = self.resolve_action_target(
            str(marker.metadata.get("batch_position_id") or marker.linked_object_id or ""),
            entity_type=BatchPosition, source=self.current_source(),
        )
        batch = resolution.target
        if not resolution.navigable or not isinstance(batch, BatchPosition):
            self.parts.context.post_navigation_message(resolution.explanation)
            return False
        marker_context = self.marker_context_for_batch_position(batch)
        target = resolve_batch_position_overview(batch, search_maps=marker_context.search_maps, overviews=marker_context.overviews)
        if not target.navigable or target.overview is None:
            self.ui.context_panel.set_title(marker.label or "Batch position")
            self.ui.context_panel.set_text(f"{marker.tooltip or ''}\n\n{target.explanation}".strip())
            self.parts.context.post_navigation_message(target.explanation)
            return True
        overview = target.overview
        self.state.viewer.selection_state = SelectionState(
            selected_object_type=type(batch).__name__, selected_object_id=batch.id,
            selected_marker_id=marker.id, source="atlas-batch-marker",
        )
        self.state.dashboard.dashboard_scope_prefers_tree = True
        def arrival() -> str | None:
            markers = markers_for_object(overview, context=self.marker_context_for_overview(overview))
            return next((candidate.id for candidate in markers
                         if candidate.marker_type == MarkerType.BATCH_POSITION and candidate.linked_object_id == batch.id), None)
        self.open_resolved_destination(
            overview,
            message=f"Opened overview {self.parts.scopes.label_for(overview)} for batch position {self.parts.scopes.label_for(batch)}.",
            arrival=arrival,
        )
        self.parts.context.set_context(self.parts.scopes.label_for(batch), batch)
        return True

