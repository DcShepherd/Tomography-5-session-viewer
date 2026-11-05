"""ViewerCoordinator owns its window interaction responsibilities."""
from __future__ import annotations
from typing import (
    Any,
    Callable,
)
from PySide6.QtCore import (
    QObject,
    QPoint,
    QRect,
    QSignalBlocker,
)
from PySide6.QtWidgets import (
    QMainWindow,
)
from tomography_session_browser.ui.shell.chrome import (
    TAB_LABELS,
)
from tomography_session_browser.domain.identity import entity_key_text
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
    prepare_viewer_tabs,
)
from tomography_session_browser.services.markers.builders import atlas_lod_markers, markers_for_object
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
from tomography_session_browser.presentation.navigation_history import HistoryLocation
from tomography_session_browser.presentation.project_tree import (
    ProjectTreeGroup,
    group_contains_value,
    session_key,
)
from tomography_session_browser.presentation.describe.context import (
    describe_loaded_sessions,
    describe_project_group,
)
from tomography_session_browser.presentation.prepared_session import (
    PreparedSessionUi,
)
from tomography_session_browser.presentation.scopes import (
    EntityGroup,
    LinkedSampleGroup,
)

from tomography_session_browser.ui.project_tree.widgets import OBJECT_ROLE
from tomography_session_browser.ui.window_parts.contracts import (
    WindowParts,
    WindowUi,
)
from tomography_session_browser.ui.window_parts.state import (
    WindowState,
)
from tomography_session_browser.ui.window_parts.support import format_tilt_angle, VIEWER_STATE_CACHE_LIMIT
from tomography_session_browser.presentation.tab_keys import TabKey, VIEWER_TAB_KEYS
from tomography_session_browser.presentation.tomogram_tabs import prepare_tomogram_tab
from tomography_session_browser.presentation.tomogram_state import TomogramViewState
from tomography_session_browser.domain.tomograms import Tomogram
from tomography_session_browser.presentation.workspace import EntityKind
from tomography_session_browser.ui.loading.scope_preparation import ScopePreparation, ScopeRequest
from tomography_session_browser.presentation.scope_identity import dashboard_key, scope_key
from tomography_session_browser.presentation.dashboard.scope_value import dashboard_scope_value
from tomography_session_browser.presentation.tomogram_selection import (
    AllTomograms,
    FolderTomograms,
    TreeRowRefusal,
    acquisition_scope,
    tree_row_destination,
)

class ViewerCoordinator(QObject):
    def __init__(self, window: QMainWindow, state: WindowState, ui: WindowUi, parts: WindowParts) -> None:
        super().__init__(window)
        self.window, self.state, self.ui, self.parts = window, state, ui, parts
        self.last_prepared: tuple[object, object, PreparedViewerTabs] | None = None
        self.applying_payload: PreparedSessionUi | None = None
        self.applied_tabs: dict[str, tuple[object, object, PreparedViewerTab]] = {}
        #: The scope whose rows the viewer tabs list right now. Remembered tab
        #: state is saved under this, never under a scope already installed
        #: ahead of the rebuild.
        self.rendered_scope_key: str | None = None
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
        if not isinstance(value, (ProjectTreeGroup, Session, Sample, LinkedSampleGroup, AllTomograms, FolderTomograms)):
            self.scope_preparation.invalidate()
            self.select_from_left_tree(value, label)
            return
        if isinstance(value, AllTomograms | FolderTomograms):
            self.open_tree_tomograms(value, label)
            return
        # A project-panel selection keeps the tab the reviewer is on: the
        # selected dataset's data is shown there (U1). Taken once, now.
        self.request_scope(value, label, destination_tab=TAB_LABELS[self.ui.tabs.currentIndex()])

    def open_tree_tomograms(self, selection: AllTomograms | FolderTomograms, label: str, *,
                            departure: HistoryLocation | None = None) -> bool:
        """Open a Tomograms row of the project tree in the Tomograms tab.

        The row names its tab, as an Overviews row does, so it does not keep
        the active one. ``tree_row_destination`` chooses the kind shown, or
        refuses with a reason. The row stays highlighted (``current_row_opens``).
        Returns True when the tab opens or will open.
        """

        destination = tree_row_destination(self.state.review.workspace, selection)
        if isinstance(destination, TreeRowRefusal):
            self.parts.context.post_navigation_message(destination.reason)
            return False
        if (self.state.review.preserve_tree_root_context
                and scope_key(self.state.review.scope) == scope_key(destination)):
            # Installed already, so only the tab changes. An earlier request
            # still being prepared must not publish over this choice.
            self.scope_preparation.invalidate()
            if TAB_LABELS[self.ui.tabs.currentIndex()] != TabKey.TOMOGRAMS:
                self.ui.tabs.setCurrentIndex(TAB_LABELS.index(TabKey.TOMOGRAMS))
                if departure is not None:
                    self.parts.history.record_history(departure)
            return True
        self.request_scope(destination, label, destination_tab=TabKey.TOMOGRAMS, departure=departure)
        return True

    def current_row_opens(self, selection: AllTomograms | FolderTomograms) -> bool:
        """The highlighted project-tree row is a Tomograms row that opens ``selection``.

        A category or folder row then stays highlighted for the kind it opened,
        instead of handing the highlight to that kind's own row: the arrow keys
        would otherwise never get past the category.
        """

        item = self.ui.tree.currentItem()
        value = item.data(0, OBJECT_ROLE) if item is not None else None
        if not isinstance(value, AllTomograms | FolderTomograms):
            return False
        destination = tree_row_destination(self.state.review.workspace, value)
        return not isinstance(destination, TreeRowRefusal) and scope_key(destination) == scope_key(selection)

    def request_scope(self, value: Any, label: str, *, destination_tab: TabKey,
                      departure: HistoryLocation | None = None) -> None:
        """Prepare a broad scope, to be published with ``destination_tab`` shown.

        The one path both ordinary tree selection and an explicit route
        (``open_scope``) take. A request for the scope and tab already being
        prepared is kept as it is, with its original departure.
        """

        scope = acquisition_scope(value)
        owner = self.scope_preparation
        current = owner.pending or owner.running
        if (current is not None and current.generation == owner.generation
                and current.workspace is self.state.review.workspace
                and current.destination_tab == destination_tab
                and scope_key(current.viewer_scope) == scope_key(value)):
            return
        self.window.statusBar().showMessage(f"Preparing {label}...")
        viewer_scope = value  # The typed selection owns folder and version; the request derives its acquisition scope.
        scope_value = dashboard_scope_value(self.state.review.workspace, viewer_scope, self.state.review.workspace.atlas_label)
        cached = self.last_prepared
        cached_viewer = (cached[2] if cached is not None and cached[0] is self.state.review.workspace
                         and cached[1] is viewer_scope else None)
        owner.request(self.state.review.workspace, scope, viewer_scope, value, label, destination_tab,
            departure=departure,
            cached_dashboard=self.parts.dashboard_host.model_cache.get(dashboard_key(scope_value)),
            cached_viewer_tabs=cached_viewer)

    def open_scope(self, target: Any, *, destination_tab: TabKey, departure: HistoryLocation | None = None) -> None:
        """Go to a broad scope on purpose: a summary's own link, not a tree click.

        A tree selection signal carries no history intent and does not fire for
        the row that is already current, so an explicit route starts here: it
        selects the target's row without a second request, then prepares the
        scope, and the history entry is recorded when that request publishes.
        The scope already committed needs no rebuild: another destination is
        only a tab change, and the same destination is nothing to do.
        """

        if (self.state.review.preserve_tree_root_context
                and scope_key(self.state.review.scope) == scope_key(target)):
            if TAB_LABELS[self.ui.tabs.currentIndex()] != destination_tab:
                self.ui.tabs.setCurrentIndex(TAB_LABELS.index(destination_tab))
                if departure is not None:
                    self.parts.history.record_history(departure)
            return
        with QSignalBlocker(self.ui.tree):
            item = self.tree_item_for_scope(target)
            self.ui.tree.setCurrentItem(item)
        self.request_scope(target, self.scope_label_for(target), destination_tab=destination_tab, departure=departure)

    def tree_item_for_scope(self, target: Any) -> Any:
        """The project-tree row showing ``target``, or None when it has none."""

        alias = self.parts.project_panel.alias_for_scope(target)
        if alias is not None:
            return alias
        pending = [self.ui.tree.topLevelItem(index) for index in range(self.ui.tree.topLevelItemCount())]
        while pending:
            item = pending.pop(0)
            value = item.data(0, OBJECT_ROLE)
            if value is target or (value is not None and self.parts.scopes.same_context(value, target)):
                return item
            pending.extend(item.child(index) for index in range(item.childCount()))
        return None

    def scope_label_for(self, target: Any) -> str:
        item = self.tree_item_for_scope(target)
        if item is not None:
            return item.text(0)
        return getattr(target, "label", None) or getattr(target, "name", None) or "dataset"

    def publish_scope(self, request: ScopeRequest, payload: PreparedSessionUi) -> None:
        if request.workspace is not self.state.review.workspace:
            return
        scope_value = dashboard_scope_value(request.workspace, payload.tomograms.selection or request.viewer_scope, request.workspace.atlas_label)
        if payload.dashboard_model is not None:
            self.parts.dashboard_host.model_cache[dashboard_key(scope_value)] = (payload.dashboard_model, payload.timeline)
        self.applying_payload = payload
        previous = self.state.review.suppress_viewer_context
        self.state.review.suppress_viewer_context = True
        try:
            # The destination is applied here, once, before anything renders: a
            # viewer tab that is already showing stays, and a Session
            # destination is shown before the viewers rebuild, so the hidden
            # ones stay lazy. ``applying_payload`` is set, so the change does
            # not cancel the preparation it belongs to.
            counts = {key: len(tab.items) for key, tab in payload.viewer_tabs.items()}
            counts[TabKey.TOMOGRAMS] = len(payload.tomograms.rows)
            self.parts.chrome.apply_tab_availability(counts, request.destination_tab,
                processing_only=payload.tomograms.processing_only)
            selected = payload.tomograms.selection or request.value
            if isinstance(selected, AllTomograms | FolderTomograms) and not self.current_row_opens(selected):
                with QSignalBlocker(self.ui.tree):
                    self.ui.tree.setCurrentItem(self.tree_item_for_scope(selected))
            self.select_from_left_tree(selected, request.label)
            self.window.statusBar().clearMessage()
            if payload.tomograms.notice:
                self.parts.context.post_navigation_message(payload.tomograms.notice)
        finally:
            self.state.review.suppress_viewer_context = previous
            self.applying_payload = None
        self.parts.history.finish_scope_restore(request.generation)
        if request.departure is not None:
            self.parts.history.record_history(request.departure)

    def viewer_state_scope_key(self) -> str:
        """A stable identity for the current review scope.

        Keyed by content rather than object identity because
        ``_context_for_object`` rebuilds cross-session linked groups on every
        call, so identity would treat one logical scope as many.
        """

        return scope_key(self.parts.scopes.viewer_tab_context())

    def capture_viewer_states(self) -> None:
        """Remember each tab's selection before a rebuild discards it."""

        scope_key = self.rendered_scope_key
        if scope_key is None:
            return
        for label, tab in self.ui.review_tabs.items():
            state = tab.capture_view_state()
            if state.is_empty:
                continue
            self.state.viewer.viewer_states[(scope_key, label)] = state
            self.state.viewer.viewer_states.move_to_end((scope_key, label))
        while len(self.state.viewer.viewer_states) > VIEWER_STATE_CACHE_LIMIT:
            self.state.viewer.viewer_states.popitem(last=False)


    def remembered_viewer_state(self, label: str) -> ViewerViewState | None:
        state = self.state.viewer.viewer_states.get((self.parts.viewers.viewer_state_scope_key(), label))
        return state if isinstance(state, ViewerViewState) else None


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
        self.rendered_scope_key = None
        self.state.viewer.viewer_states.clear()
        self.ui.tomogram_tab.rendering.invalidate()
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
        else:
            self.last_prepared = (self.state.review.workspace, scope_context, PreparedViewerTabs(prepared.viewer_tabs, prepared.availability))
            tab_payloads = prepared.viewer_tabs

        requested_labels = labels or [*VIEWER_TAB_KEYS, TabKey.TOMOGRAMS]
        volume_payload = prepared.tomograms if prepared is not None else prepare_tomogram_tab(self.state.review.workspace, scope_context)
        # The "fallback" context contains every overlayable item across every
        # loaded session. ``Workspace.marker_context`` narrows this to the active
        # value's parent sample so e.g. Violet Search Maps don't bleed onto
        # the Russet Atlas. Items with no known parent sample (top-level
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
            if TabKey.TOMOGRAMS in requested_labels:
                remembered = self.state.viewer.viewer_states.get((self.viewer_state_scope_key(), TabKey.TOMOGRAMS))
                self.ui.tomogram_tab.install_prepared(volume_payload,
                    remembered if isinstance(remembered, TomogramViewState) else None)
            labels_to_render = [label for label in requested_labels if label in VIEWER_TAB_KEYS]
            counts = {key: len(tab.items) for key, tab in tab_payloads.items()}
            counts[TabKey.TOMOGRAMS] = len(volume_payload.rows)
            self.parts.chrome.apply_tab_availability(counts)
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
                        and last[2].statuses == payload.statuses and last[2].sources == payload.sources
                        and last[2].entries == payload.entries):
                    continue
                self.applied_tabs[label] = (self.state.review.workspace, scope_context, payload)
                if label == TabKey.ATLAS:
                    tab.set_atlas_collection_overlay_provider(
                        atlas_collection_options, atlas_collection_visibility_changed)
                tab.install_prepared(
                    payload, label_for=self.parts.scopes.label_for,
                    markers_for=markers_by_label[label], auto_load_preview=auto_load_preview,
                    restore_state=self.parts.viewers.remembered_viewer_state(label),
                )
            self.rendered_scope_key = self.viewer_state_scope_key()
        finally:
            self.state.review.suppress_viewer_context = previous
        self.parts.context.refresh_context_header()


    def select_tomogram_version(self, version: str, state: TomogramViewState) -> None:
        from dataclasses import replace
        scope = self.state.review.scope
        if not isinstance(scope, AllTomograms | FolderTomograms):
            scope = AllTomograms(acquisition_scope(self.parts.scopes.viewer_tab_context()))
        target = replace(scope, version=version)
        self.state.viewer.viewer_states[(scope_key(target), TabKey.TOMOGRAMS)] = state
        self.open_scope(target, destination_tab=TabKey.TOMOGRAMS,
                        departure=self.parts.history.begin_navigation())

    def open_all_tomograms(self) -> None:
        self.open_scope(AllTomograms(acquisition_scope(self.state.review.scope)), destination_tab=TabKey.TOMOGRAMS,
                        departure=self.parts.history.begin_navigation())

    def open_processing_scope(self, key: str) -> None:
        groups = [group for group in self.state.review.workspace.project_groups
                  if group.key == key and group.processing_source is not None]
        if len(groups) != 1:
            self.parts.context.post_navigation_message('The processing folder is no longer loaded. Choose a folder in Project.')
            return
        self.open_scope(groups[0], destination_tab=TabKey.TOMOGRAMS,
                        departure=self.parts.history.begin_navigation())

    def open_tomogram_parent(self, row_key: str) -> None:
        link = self.state.review.workspace.tomogram_links.get(row_key)
        if link is None or link.status != "resolved":
            self.ui.tomogram_tab.status.message.setText(link.explanation if link else "No recorded parent is available.")
            return
        targets = [value for value in self.state.review.workspace.entities(EntityKind.TILT_SERIES)
                   if entity_key_text(value) == link.target_key]
        if len(targets) != 1:
            self.ui.tomogram_tab.status.message.setText("The recorded parent is no longer unique. Reload the source folders.")
            return
        departure = self.parts.history.begin_navigation()
        target = targets[0]
        self.parts.navigation.enter_navigation_scope(target)
        self.select_viewer_object(target)
        self.parts.context.show_arrival_message(f"Opened linked Tilt series {target.name}.")
        self.parts.history.record_history(departure)

    def select_viewer_object(self, value: Any, *, update_context: bool = True) -> None:
        previous = self.state.review.suppress_viewer_context
        self.state.review.suppress_viewer_context = previous or not update_context
        label = tab_label_for_object(value)
        filter_cleared = False
        try:
            if label is not None:
                tab = self.ui.viewer_tabs[label]
                with tab.parts.tab_selection.arriving(value):
                    self.ui.tabs.setCurrentIndex(TAB_LABELS.index(label))
                filter_cleared = tab.select_object(value)
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
            # The publication owner has already shown the destination tab.
            self.parts.viewers.render_viewer_tabs()
            self.parts.dashboard.render_dashboard_for_scope()
            self.parts.chrome.update_tab_counts()

            def describe_scope() -> None:
                self.parts.context.show_scope_description(
                    f"{type(value).__name__}:{id(value)}", value.display_name,
                    lambda: describe_project_group(value, self.state.review.workspace)
                )

            self.show_installed_scope(describe_scope)
            return
        if value is None:
            # The loaded project (``Scope``'s ``None``): every session's
            # dashboard, and the sessions' folders and shared settings.
            self.state.dashboard.dashboard_focus_value = None
            self.parts.dashboard.set_dashboard_highlighted_tilt_series_id(None)
            self.state.review.scope = None
            self.state.review.preserve_tree_root_context = True
            self.parts.viewers.render_viewer_tabs()
            self.parts.dashboard.render_dashboard_for_scope()
            self.parts.chrome.update_tab_counts()
            sessions = self.state.review.workspace.sessions

            def describe_project() -> None:
                self.parts.context.show_scope_description(
                    "sessions:project", label, lambda: describe_loaded_sessions(sessions)
                )

            self.show_installed_scope(describe_project)
            return
        if isinstance(value, Session | Sample | LinkedSampleGroup | AllTomograms | FolderTomograms):
            self.state.dashboard.dashboard_focus_value = None
            self.parts.dashboard.set_dashboard_highlighted_tilt_series_id(None)
            self.state.review.scope = value
            self.state.review.preserve_tree_root_context = True
            self.parts.viewers.render_viewer_tabs()
            # Rebuild the Session-tab dashboard for the new scope. The
            # destination tab is already active, so hidden viewer previews
            # remain lazy.
            self.parts.dashboard.render_dashboard_for_scope()
            self.parts.chrome.update_tab_counts()
            self.show_installed_scope(lambda: self.parts.context.set_context(label, value))
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


    def show_installed_scope(self, describe_scope: Callable[[], None]) -> None:
        """Point Context at what the active tab shows once a broad scope is installed.

        The Session tab shows the scope's summary. A viewer tab describes the
        item it displays, or, with nothing to show, the scope: the summary
        must never sit beside a viewer image.
        """

        tab = self.ui.review_tabs.get(TAB_LABELS[self.ui.tabs.currentIndex()])
        if tab is None:
            describe_scope()
            return
        value = tab.current_object()
        if value is None:
            self.parts.context.sync_context_panel_to_active_scope()
        else:
            self.parts.context.set_context(self.parts.scopes.label_for(value), value)


    def list_column_rect(self) -> QRect | None:
        """The shown viewer tab's list column in window coordinates, or None.

        None on a page with no such column (the Session summary), and while the
        list is out of sight (collapsed to the selector, B5.2): the Context
        drawer then takes its own width.
        """

        index = self.ui.tabs.currentIndex()
        tab = self.ui.review_tabs.get(TAB_LABELS[index]) if 0 <= index < len(TAB_LABELS) else None
        panel = None if tab is None else tab.list_column()
        if panel is None or not panel.isVisibleTo(tab) or panel.width() <= 0:
            return None
        return QRect(panel.mapTo(self.window, QPoint(0, 0)), panel.size())

    def viewer_item_selected(self, value: Any) -> None:
        if self.state.review.suppress_viewer_context:
            return
        self.cancel_scope_preparation()
        if isinstance(value, Tomogram):
            self.parts.context.set_context(value.name, value)
            self.parts.context.refresh_context_header()
            return
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
        current = viewer.current_object() if viewer is not None else None
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

