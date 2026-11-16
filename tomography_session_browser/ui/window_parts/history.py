"""HistoryController owns its window interaction responsibilities."""
from __future__ import annotations
from tomography_session_browser.presentation.tomogram_arrival import TomogramArrival
from tomography_session_browser.presentation.tomogram_list_filter import ListFilter
from tomography_session_browser.presentation.tab_keys import TabKey
from tomography_session_browser.presentation.scope_identity import resolve_scope_key
from tomography_session_browser.presentation.tomogram_selection import AllTomograms, FolderTomograms, TomogramLocation
from typing import (
    Any,
)
from PySide6.QtCore import (
    QObject,
    QSignalBlocker,
)
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import (
    QMainWindow,
)
from tomography_session_browser.ui.shell.chrome import (
    TAB_LABELS,
)
from tomography_session_browser.ui.tomograms.tab import TomogramTab
from tomography_session_browser.presentation.navigation_history import (
    IDLE_TEXT,
    HistoryLocation,
    destination_text,
)
from tomography_session_browser.presentation.workspace import (
    Scope,
)
from tomography_session_browser.presentation.scopes import (
    build_linked_sample_group,
)

from tomography_session_browser.ui.window_parts.contracts import (
    WindowParts,
    WindowUi,
)
from tomography_session_browser.ui.window_parts.state import (
    WindowState,
)

class HistoryController(QObject):
    def __init__(self, window: QMainWindow, state: WindowState, ui: WindowUi, parts: WindowParts) -> None:
        super().__init__(window)
        self.window, self.state, self.ui, self.parts = window, state, ui, parts

    def current_history_location(self) -> HistoryLocation | None:
        """Snapshot the current destination as identifiers only."""

        tab = TAB_LABELS[self.ui.tabs.currentIndex()]
        viewer_tab = self.ui.review_tabs.get(tab)
        scope = self.parts.scopes.viewer_tab_context()
        selection = TomogramLocation.capture(scope) if isinstance(scope, AllTomograms | FolderTomograms) else None
        scope_label = self.parts.scopes.label_for(scope) if scope is not None else ""
        if isinstance(viewer_tab, TomogramTab):
            volume_state = viewer_tab.capture_view_state()
            value = viewer_tab.current_object()
            listing = viewer_tab.review_list
            anchor, offset = listing.capture_anchor()
            return HistoryLocation(self.parts.viewers.viewer_state_scope_key(), tab,
                                   object_type="Tomogram", object_key=volume_state.row_key or "",
                                   filter_text=volume_state.query, label=value.name if value else "",
                                   scope_label=scope_label,
                                   filter_keys=listing.route_keys, filter_scope_label=listing.scope_label,
                                   tag_filter=listing.tag_filter, list_membership=listing.membership,
                                   list_population=frozenset(listing.rows),
                                   list_population_scope=viewer_tab.population_scope,
                                   list_anchor_key=anchor, list_anchor_offset=offset,
                                   tomogram_state=volume_state, tomogram_selection=selection)
        state = viewer_tab.capture_view_state() if viewer_tab is not None else None
        current = viewer_tab.current_object() if viewer_tab is not None else None
        return HistoryLocation(
            scope_key=self.parts.viewers.viewer_state_scope_key(),
            tomogram_selection=selection,
            tab=tab,
            object_type=type(current).__name__ if current is not None else "",
            object_key=(state.object_key if state is not None else "") or "",
            marker_id=(state.marker_id if state is not None else "") or "",
            frame_index=state.frame_index if state is not None else None,
            filter_text=state.filter_text if state is not None else "",
            filter_keys=state.filter_keys if state is not None else None,
            filter_scope_label=state.filter_scope_label if state is not None else "",
            label=self.parts.scopes.label_for(current) if current is not None else "",
            scope_label=scope_label,
            list_anchor_key=(state.list_anchor_key if state is not None else "") or "",
            list_anchor_offset=state.list_anchor_offset if state is not None else 0,
        )


    def clear_navigation_message(self) -> None:
        """End the last route's arrival or refusal message, if it is still showing.

        That message says where the last jump went or why it refused, so it
        ends when the next navigation begins. Only that message is cleared: a
        status shown for other work (a report being generated) stays.
        """

        posted = self.state.history.navigation_message
        self.state.history.navigation_message = ""
        status_bar = self.window.statusBar()
        if posted and status_bar.currentMessage() == posted:
            status_bar.clearMessage()


    def begin_navigation(self) -> HistoryLocation | None:
        """Start a route that records history: end the previous route's
        message, and snapshot where the reviewer is going from.

        Every such route calls this first, before it posts its own message, so
        a message never outlives the navigation that follows it.
        """

        self.parts.history.clear_navigation_message()
        return self.parts.history.current_history_location()


    def record_history(self, departure: HistoryLocation | None = None) -> None:
        """Record an explicit jump.

        Deliberately *not* called from selection changes, preview loads or
        ordinary tab switches: those are not navigation, and recording them
        would bury the steps the reviewer actually chose.
        """

        if self.state.history.suppress_history_record:
            return
        if departure is not None:
            self.state.history.history.record(departure)
        location = self.parts.history.current_history_location()
        if location is None:
            return
        self.state.history.history.record(location)
        self.parts.history.update_history_actions()


    def update_history_actions(self) -> None:
        history = self.state.history.history
        entries = history.entries
        index = self.requested_history_index()
        back = entries[index - 1] if index > 0 else None
        forward = entries[index + 1] if 0 <= index < len(entries) - 1 else None
        self.ui.back_action.setEnabled(back is not None)
        self.ui.forward_action.setEnabled(forward is not None)
        for action, direction, location in ((self.ui.back_action, "Back", back),
                                             (self.ui.forward_action, "Forward", forward)):
            self.parts.history.describe_history_action(action, direction, location)


    def describe_history_action(self, action: QAction, direction: str, location: HistoryLocation | None) -> None:
        text = destination_text(direction, location) if location is not None else IDLE_TEXT[direction]
        hint = action.shortcut().toString(QKeySequence.SequenceFormat.NativeText)
        action.setToolTip(f"{text}  ({hint})" if hint else text)
        button = self.ui.main_toolbar.widgetForAction(action)
        if button is not None:
            button.setAccessibleDescription(text)


    def navigate_history_back(self) -> bool:
        """Return to the previous explicit navigation step."""

        return self.navigate_history(-1, "Back")


    def navigate_history_forward(self) -> bool:
        """Move forward again after going back."""

        return self.navigate_history(1, "Forward")


    def requested_history_index(self) -> int:
        history = self.state.history.history
        owner = self.parts.viewers.scope_preparation
        request = owner.pending or owner.running
        arrival = request.arrival if request is not None and request.generation == owner.generation else None
        if arrival is not None and arrival.history_revision == history.revision and arrival.history_index is not None:
            return arrival.history_index
        return history.index

    def navigate_history(self, delta: int, direction: str) -> bool:
        history = self.state.history.history
        index = self.requested_history_index() + delta
        entries = history.entries
        if not 0 <= index < len(entries):
            return False
        location = entries[index]
        if location.tab == TabKey.TOMOGRAMS and location.tomogram_state is not None and location.object_key:
            scope = self.scope_for_location(location)
            if scope is None and location.scope_key != "none":
                self.parts.context.post_navigation_message("The saved tomogram scope is no longer available.")
                return False
            view = location.tomogram_state
            filters = ListFilter(location.filter_text, view.segment, view.sort, location.filter_keys,
                                 location.filter_scope_label, location.tag_filter)
            intent = TomogramArrival(location.object_key, None, filters, location.list_membership,
                                     location.list_population, view, (location.list_anchor_key, location.list_anchor_offset),
                                     index, history.revision, location.list_population_scope,
                                     location.tomogram_selection.version if location.tomogram_selection else None)
            self.parts.viewers.request_scope(scope, location.scope_label or "tomograms",
                                              destination_tab=TabKey.TOMOGRAMS, arrival=intent)
            self.update_history_actions()
            return True
        self.parts.viewers.cancel_scope_preparation()
        history.commit_cursor(index, history.revision)
        return self.apply_history_location(location, direction=direction)

    def apply_history_location(
        self,
        location: HistoryLocation | None,
        *,
        direction: str,
    ) -> bool:
        if location is None:
            return False
        self.parts.history.clear_navigation_message()
        previous = self.state.history.suppress_history_record
        self.state.history.suppress_history_record = True
        try:
            scope = self.scope_for_location(location)
            if (scope is not None or location.scope_key == "none") and scope is not self.state.review.scope:
                # The tabs list what the project tree shows, so its row follows
                # the scope (without announcing a selection), then the views.
                with QSignalBlocker(self.ui.tree):
                    self.ui.tree.setCurrentItem(self.parts.viewers.tree_item_for_scope(scope))
                self.state.review.scope = scope
                self.parts.viewers.render_viewer_tabs()
                self.parts.dashboard.render_dashboard_for_scope()
                self.parts.chrome.update_tab_counts()
            self.restore_tab_location(location)
        finally:
            self.state.history.suppress_history_record = previous
        self.parts.history.update_history_actions()
        self.parts.context.refresh_context_header()
        self.parts.context.show_arrival_message(
            f"{direction} to {location.label}."
            if location.label
            else f"{direction} to the {'previous' if direction == 'Back' else 'next'} step."
        )
        return True


    def restore_tab_location(self, location: HistoryLocation) -> None:
        if location.tab in TAB_LABELS:
            self.ui.tabs.setCurrentIndex(TAB_LABELS.index(location.tab))
        viewer_tab = self.ui.review_tabs.get(location.tab)
        if isinstance(viewer_tab, TomogramTab):
            if location.tomogram_state is not None:
                viewer_tab.restore_view_state(location.tomogram_state)
            if location.filter_keys is not None:
                viewer_tab.review_list.restrict(location.filter_keys, location.filter_scope_label)
            viewer_tab.tags.filters.restore(location.tag_filter)
        elif viewer_tab is not None:
            viewer_tab.set_query(location.filter_text, row_keys=location.filter_keys,
                                 scope_label=location.filter_scope_label)
            value = viewer_tab.object_for_key(location.object_key)
            if value is not None:
                self.parts.viewers.select_viewer_object(value)
                if location.list_anchor_key:
                    viewer_tab.restore_list_anchor(
                        location.list_anchor_key, location.list_anchor_offset
                    )
                viewer_tab.select_marker(location.marker_id or None, arrive=True)
                if location.frame_index is not None:
                    viewer_tab.select_frame(location.frame_index)

    def scope_for_location(self, location: HistoryLocation) -> Scope:
        if location.tomogram_selection is not None:
            return location.tomogram_selection.resolve(self.state.review.workspace, self.candidate_history_scopes())
        return self.scope_for_history_key(location.scope_key)

    def scope_for_history_key(self, scope_key: str) -> Scope:
        """Resolve a stored scope key back to a live scope object.

        Keys are compared by content, so a rebuilt cross-session linked group
        still matches. Returns None when the scope has gone, which is how a
        stale entry is detected.
        """

        if not scope_key or scope_key == "none":
            return None
        return resolve_scope_key(self.parts.history.candidate_history_scopes(), scope_key)


    def candidate_history_scopes(self) -> list[Any]:
        scopes: list[Any] = list(self.state.review.workspace.sessions)
        for session in self.state.review.workspace.sessions:
            scopes.extend(session.samples)
        for samples in self.state.review.workspace.sample_groups:
            if not samples:
                continue
            label = self.parts.project.linked_sample_label(samples)
            scopes.append(build_linked_sample_group(label, list(samples)))
        scopes.extend(self.state.review.workspace.project_groups)
        return scopes


    def prune_history(self) -> None:
        """Drop entries whose scope no longer exists, after a project change."""

        self.state.history.history.prune(
            lambda entry: (entry.scope_key == "none" and self.state.review.workspace.has_sources)
                          or self.scope_for_location(entry) is not None
        )
        self.parts.history.update_history_actions()

