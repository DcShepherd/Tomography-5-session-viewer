"""ViewerCoordinator owns its window interaction responsibilities."""
from __future__ import annotations
from typing import (
    Any,
    Callable,
)
from PySide6.QtCore import (
    QObject,
    QSignalBlocker,
)
from PySide6.QtWidgets import (
    QMainWindow,
)
from tomography_session_browser.ui.shell.chrome import (
    TAB_LABELS,
)
from tomography_session_browser.domain.markers import (
    ImageMarker,
    SelectionState,
)
from tomography_session_browser.domain.models import (
    Atlas,
    BatchPosition,
    Sample,
    SearchMap,
    Session,
    TiltSeries,
)
from tomography_session_browser.presentation.viewer_tabs import (
    PreparedViewerTab,
    PreparedViewerTabs,
    VIEWER_TAB_SOURCES,
    prepare_viewer_tabs,
)
from tomography_session_browser.services.preview_sources import (
    atlas_preview_path,
    batch_position_preview_path,
    overview_preview_path,
    search_map_preview_path,
    search_tile_preview_path,
    tilt_series_preview_path,
)
from tomography_session_browser.services.markers.builders import atlas_lod_markers, markers_for_object
from tomography_session_browser.services.item_status import (
    ItemListStatus,
)
from tomography_session_browser.services.navigation import (
    tab_label_for_object,
)
from tomography_session_browser.services.tilt_angle_service import (
    stack_order_tilt_angles,
)
from tomography_session_browser.ui.viewer.models import AtlasCollectionOverlayOption, ViewerViewState
from tomography_session_browser.presentation.context_stack import (
    ENTITY_DISPLAY_LABELS,
    describe_scope_value,
    entity_display_label,
    tab_key_for_display_label,
)
from tomography_session_browser.presentation.project_tree import (
    ProjectTreeGroup,
    group_contains_value,
    session_key,
)
from tomography_session_browser.presentation.describe.context import (
    describe_project_group,
)
from tomography_session_browser.presentation.prepared_session import (
    PreparedSessionUi,
)
from tomography_session_browser.presentation.scopes import (
    EntityGroup,
    LinkedSampleGroup,
)

from tomography_session_browser.ui.window_parts.contracts import (
    WindowParts,
    WindowUi,
)
from tomography_session_browser.ui.window_parts.state import (
    WindowState,
)
from tomography_session_browser.ui.window_parts.support import format_tilt_angle, VIEWER_STATE_CACHE_LIMIT
from tomography_session_browser.presentation.tab_keys import TabKey
from tomography_session_browser.ui.loading.scope_preparation import ScopePreparation, ScopeRequest
from tomography_session_browser.ui.dashboard_host import dashboard_cache_key
from tomography_session_browser.presentation.dashboard.scope_value import dashboard_scope_value

class ViewerCoordinator(QObject):
    def __init__(self, window: QMainWindow, state: WindowState, ui: WindowUi, parts: WindowParts) -> None:
        super().__init__(window)
        self.window, self.state, self.ui, self.parts = window, state, ui, parts
        self.last_prepared: tuple[object, object, PreparedViewerTabs] | None = None
        self.applying_payload: PreparedSessionUi | None = None
        self.applied_tabs: dict[str, tuple[object, object, PreparedViewerTab]] = {}
        self.scope_preparation = ScopePreparation(
            publish=self.publish_scope, report_error=self.scope_failed)

    def scope_failed(self, message: str) -> None:
        with QSignalBlocker(self.ui.tree):
            self.ui.tree.setCurrentItem(None)
        self.window.statusBar().showMessage(message)

    def cancel_scope_preparation(self) -> None:
        owner = self.scope_preparation
        current = owner.pending or owner.running
        if current is not None and current.generation == owner.generation:
            # A superseding viewer action keeps the committed scope. Do not
            # leave the uncommitted tree row governing tab scope resolution.
            with QSignalBlocker(self.ui.tree):
                self.ui.tree.setCurrentItem(None)
            self.window.statusBar().clearMessage()
        owner.invalidate()

    def request_tree_selection(self, value: Any, label: str) -> None:
        if not isinstance(value, (ProjectTreeGroup, list, Session, Sample, LinkedSampleGroup)):
            self.scope_preparation.invalidate()
            self.select_from_left_tree(value, label)
            return
        scope = None if isinstance(value, list) else value
        has_tree_scope, viewer_scope = self.parts.scopes.tree_item_scope_context(self.ui.tree.currentItem())
        self.window.statusBar().showMessage(f"Preparing {label}...")
        viewer_scope = viewer_scope if has_tree_scope else scope
        scope_value = dashboard_scope_value(self.state.review.workspace, scope, self.state.review.workspace.atlas_label)
        cached = self.last_prepared
        cached_viewer = (cached[2] if cached is not None and cached[0] is self.state.review.workspace
                         and cached[1] is viewer_scope else None)
        self.scope_preparation.request(self.state.review.workspace, scope, viewer_scope, value, label,
            cached_dashboard=self.parts.dashboard_host.model_cache.get(dashboard_cache_key(scope_value)),
            cached_viewer_tabs=cached_viewer)

    def publish_scope(self, request: ScopeRequest, payload: PreparedSessionUi) -> None:
        if request.workspace is not self.state.review.workspace:
            return
        scope_value = dashboard_scope_value(request.workspace, request.scope, request.workspace.atlas_label)
        if payload.dashboard_model is not None:
            self.parts.dashboard_host.model_cache[dashboard_cache_key(scope_value)] = (payload.dashboard_model, payload.timeline)
        self.applying_payload = payload
        previous = self.state.review.suppress_viewer_context
        self.state.review.suppress_viewer_context = True
        try:
            self.select_from_left_tree(request.value, request.label)
            self.window.statusBar().clearMessage()
        finally:
            self.state.review.suppress_viewer_context = previous
            self.applying_payload = None

    def viewer_state_scope_key(self) -> str:
        """A stable identity for the current review scope.

        Keyed by content rather than object identity because
        ``_context_for_object`` rebuilds cross-session linked groups on every
        call, so identity would treat one logical scope as many.
        """

        context = self.parts.scopes.viewer_tab_context()
        if context is None:
            return "none"
        if isinstance(context, LinkedSampleGroup):
            paths = sorted(self.parts.viewers.history_path_key(sample) for sample in context.samples)
            return "linked:" + "|".join(paths)
        if isinstance(context, ProjectTreeGroup):
            return "group:" + context.key
        if isinstance(context, Session):
            return "Session:" + session_key(context)
        if isinstance(context, Sample):
            return "Sample:" + self.parts.viewers.history_path_key(context)
        identifier = getattr(context, "id", None) or getattr(context, "name", "")
        return f"{type(context).__name__}:{identifier}:{self.parts.viewers.history_path_key(context)}"


    @staticmethod
    def history_path_key(value: Any) -> str:
        path = getattr(value, "path", None)
        if path is None:
            return ""
        return str(path).replace("\\", "/").rstrip("/").casefold()


    def capture_viewer_states(self) -> None:
        """Remember each tab's selection before a rebuild discards it."""

        scope_key = self.parts.viewers.viewer_state_scope_key()
        for label, tab in self.ui.viewer_tabs.items():
            state = tab.capture_view_state()
            if state.is_empty:
                continue
            self.state.viewer.viewer_states[(scope_key, label)] = state
            self.state.viewer.viewer_states.move_to_end((scope_key, label))
        while len(self.state.viewer.viewer_states) > VIEWER_STATE_CACHE_LIMIT:
            self.state.viewer.viewer_states.popitem(last=False)


    def remembered_viewer_state(self, label: str) -> ViewerViewState | None:
        return self.state.viewer.viewer_states.get((self.parts.viewers.viewer_state_scope_key(), label))


    def clear_viewer_states(self) -> None:
        """Drop remembered viewer state.

        Called whenever the loaded sessions change, because a remembered
        object ID could otherwise be matched against a re-parsed entity that
        merely reuses the identifier. Remembered viewports go with it, for the
        same reason.
        """

        self.scope_preparation.invalidate()
        self.applied_tabs.clear()
        self.last_prepared = None
        self.state.viewer.viewer_states.clear()
        for tab in self.ui.viewer_tabs.values():
            tab.viewer.forget_remembered_views()


    def render_viewer_tabs(
        self,
        *,
        prepared: PreparedSessionUi | None = None,
        labels: list[str] | None = None,
    ) -> None:
        prepared = prepared or self.applying_payload
        if prepared is None:
            self.scope_preparation.invalidate()
        self.parts.viewers.capture_viewer_states()
        previous = self.state.review.suppress_viewer_context
        self.state.review.suppress_viewer_context = True
        # Pre-compute the validator-flagged failed tilt-series ids so
        # the marker pipeline can paint failed batches' overlays in
        # red (template / exposure / tracking / focus / batch dot).
        failed_tilt_ids = self.parts.navigation.failed_tilt_ids()
        scope_context = self.parts.scopes.viewer_tab_context()
        if prepared is None:
            cached = self.last_prepared
            if (cached is not None and cached[0] is self.state.review.workspace
                    and cached[1] is scope_context and cached[2].files_unchanged()):
                prepared_tabs = cached[2]
            else:
                prepared_tabs = prepare_viewer_tabs(self.state.review.workspace, scope_context)
                self.last_prepared = (self.state.review.workspace, scope_context, prepared_tabs)
            tab_payloads = prepared_tabs.tabs
            self.state.viewer.viewer_tilt_validations = prepared_tabs.tilt_validations
        else:
            self.last_prepared = (self.state.review.workspace, scope_context, PreparedViewerTabs(prepared.viewer_tabs, prepared.tilt_validations, prepared.availability))
            tab_payloads = prepared.viewer_tabs
            self.state.viewer.viewer_tilt_validations = prepared.tilt_validations

        # The "fallback" context contains every overlayable item across every
        # loaded session. ``Workspace.marker_context`` narrows this to the active
        # value's parent sample so e.g. Sample A's Search Maps don't bleed onto
        # Sample B's Atlas. Items with no known parent sample (top-level
        # session atlases) keep the fallback so we don't silently hide them.
        # The workspace keeps it, so the navigation planner shares this one.
        fallback_context = self.state.review.workspace.project_marker_context(failed_tilt_ids)

        def scoped(value: Any) -> list[ImageMarker]:
            return markers_for_object(
                value,
                context=self.state.review.workspace.marker_context(value, fallback_context),
            )

        def atlas_scoped(value: Any) -> list[ImageMarker]:
            if not isinstance(value, Atlas):
                return scoped(value)
            base_context = self.state.review.workspace.marker_context(
                value,
                fallback_context,
            )
            return atlas_lod_markers(
                value,
                self.parts.scopes.atlas_marker_context_for_collection_scope(
                    value,
                    base_context,
                    scope_context,
                ),
            )

        def atlas_collection_options(
            value: Any,
        ) -> list[AtlasCollectionOverlayOption]:
            if not isinstance(value, Atlas):
                return []
            return self.parts.scopes.atlas_collection_overlay_options(
                value,
                scope_context,
            )

        def atlas_collection_visibility_changed(
            collection_key: str,
            visible: bool,
        ) -> None:
            self.state.viewer.atlas_collection_visibility[
                (
                    self.parts.scopes.atlas_collection_scope_key(scope_context),
                    collection_key,
                )
            ] = visible

        def status_from_payload(payload: PreparedViewerTab) -> Callable[[Any], ItemListStatus]:
            def list_status(value: Any) -> ItemListStatus:
                return payload.statuses.get(getattr(value, "id", ""), ItemListStatus(status="unknown", summary=""))

            return list_status

        markers_by_label = {
            TabKey.ATLAS: atlas_scoped,
            TabKey.OVERVIEW: scoped,
            TabKey.SEARCH_MAP: scoped,
            TabKey.SEARCH: scoped,
            TabKey.BATCH_POSITION: scoped,
            # The Tilt series tab renders no overlay markers of its own.
            TabKey.TILT_SERIES: None,
        }

        try:
            labels_to_render = labels or [TabKey.ATLAS, TabKey.OVERVIEW, TabKey.SEARCH_MAP, TabKey.SEARCH, TabKey.BATCH_POSITION, TabKey.TILT_SERIES]
            current_tab_label = TAB_LABELS[self.ui.tabs.currentIndex()]
            for label in labels_to_render:
                payload = tab_payloads[label]
                last = self.applied_tabs.get(label)
                auto_load_preview = label == current_tab_label
                tab = self.ui.viewer_tabs[label]
                tab.set_scope_description(
                    has_sessions=bool(self.state.review.workspace.sessions),
                    scope_name=describe_scope_value(scope_context),
                    entity_label=entity_display_label(label),
                )
                if (last is not None and last[0] is self.state.review.workspace
                        and last[1] is scope_context and tab.has_same_items(payload.items)
                        and last[2].statuses == payload.statuses and last[2].sources == payload.sources):
                    continue
                self.applied_tabs[label] = (self.state.review.workspace, scope_context, payload)
                if payload.items and tab.has_same_items(payload.items):
                    # Same rows as before: refresh the marker/status providers
                    # only. Rebuilding the list here would discard selection,
                    # filter, frame and scroll for no visible gain.
                    if label == TabKey.ATLAS:
                        tab.set_atlas_collection_overlay_provider(
                            atlas_collection_options,
                            atlas_collection_visibility_changed,
                        )
                    item_status_provider = (
                        status_from_payload(payload) if payload.statuses else None
                    )
                    tab.refresh_providers(
                        VIEWER_TAB_SOURCES[label],
                        markers_for=markers_by_label[label],
                        item_status_for=item_status_provider,
                        prepared_sources=payload.sources,
                    )
                    continue
                if label == TabKey.ATLAS:
                    self.ui.viewer_tabs[
                        label
                    ].set_atlas_collection_overlay_provider(
                        atlas_collection_options,
                        atlas_collection_visibility_changed,
                    )
                    self.ui.viewer_tabs[label].set_items(
                        payload.items,
                        atlas_preview_path,
                        self.parts.scopes.label_for,
                        markers_for=atlas_scoped,
                        prepared_sources=payload.sources,
                        auto_load_preview=auto_load_preview,
                        restore_state=self.parts.viewers.remembered_viewer_state(label),
                    )
                elif label == TabKey.OVERVIEW:
                    self.ui.viewer_tabs[label].set_items(
                        payload.items,
                        overview_preview_path,
                        self.parts.scopes.label_for,
                        markers_for=scoped,
                        prepared_sources=payload.sources,
                        auto_load_preview=auto_load_preview,
                        restore_state=self.parts.viewers.remembered_viewer_state(label),
                    )
                elif label == TabKey.SEARCH_MAP:
                    self.ui.viewer_tabs[label].set_items(
                        payload.items,
                        search_map_preview_path,
                        self.parts.scopes.label_for,
                        markers_for=scoped,
                        item_status_for=status_from_payload(payload),
                        prepared_sources=payload.sources,
                        auto_load_preview=auto_load_preview,
                        restore_state=self.parts.viewers.remembered_viewer_state(label),
                    )
                elif label == TabKey.SEARCH:
                    self.ui.viewer_tabs[label].set_items(
                        payload.items,
                        search_tile_preview_path,
                        self.parts.scopes.label_for,
                        markers_for=scoped,
                        item_status_for=status_from_payload(payload),
                        prepared_sources=payload.sources,
                        auto_load_preview=auto_load_preview,
                        restore_state=self.parts.viewers.remembered_viewer_state(label),
                    )
                elif label == TabKey.BATCH_POSITION:
                    self.ui.viewer_tabs[label].set_items(
                        payload.items,
                        batch_position_preview_path,
                        self.parts.scopes.label_for,
                        markers_for=scoped,
                        item_status_for=status_from_payload(payload),
                        prepared_sources=payload.sources,
                        auto_load_preview=auto_load_preview,
                        restore_state=self.parts.viewers.remembered_viewer_state(label),
                    )
                elif label == TabKey.TILT_SERIES:
                    self.ui.viewer_tabs[label].set_items(
                        payload.items,
                        tilt_series_preview_path,
                        self.parts.scopes.label_for,
                        item_status_for=status_from_payload(payload),
                        prepared_sources=payload.sources,
                        auto_load_preview=auto_load_preview,
                        restore_state=self.parts.viewers.remembered_viewer_state(label),
                    )
        finally:
            self.state.review.suppress_viewer_context = previous
        self.parts.context.refresh_context_header()


    def select_viewer_object(self, value: Any, *, update_context: bool = True) -> None:
        previous = self.state.review.suppress_viewer_context
        self.state.review.suppress_viewer_context = previous or not update_context
        label = tab_label_for_object(value)
        filter_cleared = False
        try:
            if label is not None:
                self.ui.tabs.setCurrentIndex(TAB_LABELS.index(label))
                filter_cleared = self.ui.viewer_tabs[label].select_object(value)
        finally:
            self.state.review.suppress_viewer_context = previous
        if label is None:
            return
        if filter_cleared:
            # Hold the notice rather than showing it now: the caller is about
            # to post its own arrival message, which would overwrite this one
            # before anybody read it.
            self.state.review.pending_filter_notice = (
                f"Filter cleared on {ENTITY_DISPLAY_LABELS.get(label, label)} "
                f"to show {self.parts.scopes.label_for(value)}."
            )
        if update_context:
            self.parts.context.set_context(self.parts.scopes.label_for(value), value)


    def select_from_left_tree(self, value: Any, label: str) -> None:
        self.state.dashboard.dashboard_scope_prefers_tree = False
        if isinstance(value, ProjectTreeGroup):
            self.state.dashboard.dashboard_focus_value = None
            self.parts.dashboard.set_dashboard_highlighted_tilt_series_id(None)
            self.state.review.scope = value
            self.state.review.preserve_tree_root_context = True
            # Activate the destination before rebuilding the viewer tabs.
            # Otherwise the previously visible Overview/Tilt series tab
            # auto-loads the first item in the new scope, including its
            # synchronous marker/navigation derivation, even though the user
            # is being sent to Session.
            self.ui.tabs.setCurrentIndex(TAB_LABELS.index(TabKey.SESSION))
            self.parts.viewers.render_viewer_tabs()
            self.parts.dashboard.render_dashboard_for_scope()
            self.parts.chrome.update_tab_counts()
            self.ui.context_panel.set_title(value.display_name)
            self.ui.context_panel.set_text(describe_project_group(value))
            return
        if isinstance(value, list):
            # The "Linked sessions" project-tree root carries the full session
            # list. Treat it as the aggregate scope: dashboard rebuilds, the
            # Session tab is brought forward, and the right-hand context shows
            # the multi-session summary.
            self.state.dashboard.dashboard_focus_value = None
            self.parts.dashboard.set_dashboard_highlighted_tilt_series_id(None)
            self.state.review.scope = None  # signals "use the project root scope"
            self.state.review.preserve_tree_root_context = True
            self.ui.tabs.setCurrentIndex(TAB_LABELS.index(TabKey.SESSION))
            self.parts.viewers.render_viewer_tabs()
            self.parts.dashboard.render_dashboard_for_scope()
            self.parts.chrome.update_tab_counts()
            # Show a short context-panel summary listing the loaded sessions.
            summary = "\n".join(
                [f"Linked sessions ({len(value)}):"] + [f"  - {s.name}" for s in value]
            )
            self.ui.context_panel.set_title(label)
            self.ui.context_panel.set_text(summary)
            return
        if isinstance(value, Session | Sample | LinkedSampleGroup):
            self.state.dashboard.dashboard_focus_value = None
            self.parts.dashboard.set_dashboard_highlighted_tilt_series_id(None)
            self.state.review.scope = value
            self.state.review.preserve_tree_root_context = True
            self.ui.tabs.setCurrentIndex(TAB_LABELS.index(TabKey.SESSION))
            self.parts.viewers.render_viewer_tabs()
            # Rebuild the Session-tab dashboard for the new scope. The tab is
            # already active so hidden viewer previews remain lazy.
            self.parts.dashboard.render_dashboard_for_scope()
            self.parts.chrome.update_tab_counts()
            self.parts.context.set_context(label, value)
            return
        if isinstance(value, EntityGroup):
            self.state.dashboard.dashboard_focus_value = None
            self.parts.dashboard.set_dashboard_highlighted_tilt_series_id(None)
            self.state.review.preserve_tree_root_context = False
            has_tree_scope, scope_context = self.parts.scopes.tree_item_scope_context(self.ui.tree.currentItem())
            if not has_tree_scope:
                scope_context = self.parts.scopes.context_for_entity_group(value)
            self.state.review.scope = scope_context
            self.parts.viewers.render_viewer_tabs()
            self.parts.chrome.update_tab_counts()
            self.parts.viewers.switch_to_group_tab(value.label)
            first_value = next(iter(value.values), None)
            if first_value is not None:
                self.parts.viewers.select_viewer_object(first_value, update_context=False)
                self.parts.context.set_context(self.parts.scopes.label_for(first_value), first_value)
            else:
                self.parts.context.set_context(label, value)
            return
        if isinstance(value, TiltSeries):
            self.state.dashboard.dashboard_focus_value = None
            self.parts.context.set_context(label, value)
            self.state.review.preserve_tree_root_context = False
            self.state.review.scope = self.parts.scopes.context_for_object(value)
            return
        self.state.dashboard.dashboard_focus_value = value if isinstance(value, SearchMap | BatchPosition) else None
        if isinstance(value, SearchMap | BatchPosition):
            self.parts.dashboard.set_dashboard_highlighted_tilt_series_id(None)
        self.parts.context.set_context(label, value)
        self.state.review.preserve_tree_root_context = False
        self.state.review.scope = self.parts.scopes.context_for_object(value)
        self.parts.viewers.render_viewer_tabs()
        self.parts.chrome.update_tab_counts()
        self.parts.viewers.select_viewer_object(value)
        if isinstance(value, SearchMap | BatchPosition | TiltSeries):
            self.parts.dashboard.render_dashboard_for_scope()


    def viewer_item_selected(self, value: Any) -> None:
        if self.state.review.suppress_viewer_context:
            return
        self.cancel_scope_preparation()
        if isinstance(value, TiltSeries):
            self.state.dashboard.tilt_viewer_selected_tilt_series_id = value.id
        self.state.viewer.selection_state = SelectionState(
            selected_object_type=type(value).__name__,
            selected_object_id=getattr(value, "id", None),
            selected_marker_id=None,
            source="list",
        )
        self.state.dashboard.dashboard_scope_prefers_tree = True
        if self.parts.viewers.tree_scope_should_remain_active_for_viewer_selection(value):
            self.parts.chrome.update_tab_counts()
            self.parts.context.set_context(self.parts.scopes.label_for(value), value)
            return
        if isinstance(value, Atlas) and self.state.review.workspace.contains(self.state.review.scope, value):
            self.parts.chrome.update_tab_counts()
            self.parts.context.set_context(self.parts.scopes.label_for(value), value)
            return
        self.state.review.preserve_tree_root_context = False
        context = self.parts.scopes.context_for_object(value)
        if not self.parts.scopes.same_context(context, self.state.review.scope):
            self.state.review.scope = context
            self.parts.viewers.render_viewer_tabs()
            self.parts.chrome.update_tab_counts()
            self.parts.viewers.select_viewer_object(value)
            if isinstance(value, TiltSeries):
                self.state.dashboard.dashboard_focus_value = None
                self.parts.context.set_context(self.parts.scopes.label_for(value), value)
            elif isinstance(value, SearchMap | BatchPosition):
                self.state.dashboard.dashboard_focus_value = value
                self.parts.dashboard.set_dashboard_highlighted_tilt_series_id(None)
                self.parts.dashboard.render_dashboard_for_scope()
            return
        self.state.review.scope = context
        self.parts.chrome.update_tab_counts()
        self.parts.context.set_context(self.parts.scopes.label_for(value), value)
        if isinstance(value, TiltSeries):
            self.state.dashboard.dashboard_focus_value = None
        elif isinstance(value, SearchMap | BatchPosition):
            self.state.dashboard.dashboard_focus_value = value
            self.parts.dashboard.set_dashboard_highlighted_tilt_series_id(None)
            self.parts.dashboard.render_dashboard_for_scope()


    def tree_scope_should_remain_active_for_viewer_selection(self, value: Any) -> bool:
        has_tree_scope, context = self.parts.scopes.tree_item_scope_context(self.ui.tree.currentItem())
        if isinstance(context, ProjectTreeGroup):
            return group_contains_value(context, value)
        if not self.state.review.preserve_tree_root_context:
            return False
        return self.state.review.workspace.contains(self.state.review.scope, value)


    def tab_activation_preserves_broad_scope(self) -> bool:
        has_tree_scope, context = self.parts.scopes.tree_item_scope_context(self.ui.tree.currentItem())
        if isinstance(context, ProjectTreeGroup):
            return True
        return self.state.review.preserve_tree_root_context and (
            self.state.review.scope is None or isinstance(self.state.review.scope, ProjectTreeGroup)
        )


    def viewer_marker_selected(self, marker: ImageMarker) -> None:
        self.parts.navigation.select_marker(marker, navigate=False)


    def viewer_marker_opened(self, marker: ImageMarker) -> None:
        self.parts.navigation.select_marker(marker, navigate=True)


    def viewer_marker_selection_cleared(self) -> None:
        current_label = TAB_LABELS[self.ui.tabs.currentIndex()]
        viewer = self.ui.viewer_tabs.get(current_label)
        current = getattr(viewer.parts.tab_selection, 'current_value', None) if viewer is not None else None
        self.state.viewer.selection_state = SelectionState(
            selected_object_type=type(current).__name__ if current is not None else None,
            selected_object_id=getattr(current, "id", None),
            selected_marker_id=None,
            source="viewer-clear",
        )
        if current is not None:
            self.parts.context.set_context(self.parts.scopes.label_for(current), current)
        self.parts.context.show_toast("Cleared marker selection and highlights.")


    def switch_to_group_tab(self, label: str) -> None:
        """Activate the tab that owns a project-tree entity group.

        Derived from the shared display-label map rather than a second
        hand-written table, which is what let the tree's group heading and the
        tab key drift apart.
        """

        target = tab_key_for_display_label(label)
        if target is not None and target in TAB_LABELS:
            self.ui.tabs.setCurrentIndex(TAB_LABELS.index(target))


    def select_visible_tab_object(self, value: Any) -> None:
        current_label = TAB_LABELS[self.ui.tabs.currentIndex()]
        if current_label == tab_label_for_object(value):
            previous = self.state.review.suppress_viewer_context
            self.state.review.suppress_viewer_context = True
            try:
                self.ui.viewer_tabs[current_label].select_object(value)
            finally:
                self.state.review.suppress_viewer_context = previous


    def tilt_angle_for(self, value: Any, index: int) -> str | None:
        if not isinstance(value, TiltSeries):
            return None
        angle_info = self.parts.viewers.stack_order_tilt_angles(value)
        if angle_info is None:
            return None
        angles, source = angle_info
        if index >= len(angles):
            return None
        angle = angles[index]
        if isinstance(angle, int | float):
            suffix = " (inferred)" if source == "inferred" else ""
            return f"{format_tilt_angle(float(angle))}\N{DEGREE SIGN}{suffix}"
        return None


    def stack_order_tilt_angles(self, tilt_series: TiltSeries) -> tuple[list[float], str] | None:
        frame_count = self.parts.viewers.frame_count_for_tilt_series(tilt_series)
        return stack_order_tilt_angles(tilt_series, frame_count)


    def frame_angle_source(self, tilt_series: TiltSeries, frame_count: int) -> str:
        angle_info = self.parts.viewers.stack_order_tilt_angles(tilt_series)
        if angle_info is None:
            return "unavailable"
        _angles, source = angle_info
        return source


    def frame_count_for_tilt_series(self, tilt_series: TiltSeries) -> int:
        if tilt_series.mrc_metadata is not None and tilt_series.mrc_metadata.nz:
            return tilt_series.mrc_metadata.nz
        return len(tilt_series.sections)


    def frame_metadata_for(self, tilt_series: TiltSeries, frame_index: int) -> dict[str, Any] | None:
        metadata = tilt_series.mrc_metadata
        if metadata is None or frame_index < 0 or frame_index >= len(metadata.frame_metadata):
            return None
        frame_metadata = metadata.frame_metadata[frame_index]
        return frame_metadata if isinstance(frame_metadata, dict) else None

