"""HistoryController owns its window interaction responsibilities."""
from __future__ import annotations
from typing import (
    Any,
)
from PySide6.QtCore import (
    QObject,
    QSignalBlocker,
    QTimer,
)
from PySide6.QtWidgets import (
    QMainWindow,
)
from tomography_session_browser.ui.shell.chrome import (
    TAB_LABELS,
)
from tomography_session_browser.presentation.navigation_history import (
    HistoryLocation,
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
        viewer_tab = self.ui.viewer_tabs.get(tab)
        state = viewer_tab.capture_view_state() if viewer_tab is not None else None
        current = getattr(viewer_tab.parts.tab_selection, 'current_value', None) if viewer_tab is not None else None
        return HistoryLocation(
            scope_key=self.parts.viewers.viewer_state_scope_key(),
            tab=tab,
            object_type=type(current).__name__ if current is not None else "",
            object_key=(state.object_key if state is not None else "") or "",
            marker_id=(state.marker_id if state is not None else "") or "",
            frame_index=state.frame_index if state is not None else None,
            filter_text=state.filter_text if state is not None else "",
            filter_keys=state.filter_keys if state is not None else None,
            filter_scope_label=state.filter_scope_label if state is not None else "",
            label=self.parts.scopes.label_for(current) if current is not None else "",
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
        self.ui.back_action.setEnabled(self.state.history.history.can_go_back)
        self.ui.forward_action.setEnabled(self.state.history.history.can_go_forward)


    def navigate_history_back(self) -> bool:
        """Return to the previous explicit navigation step."""

        return self.parts.history.apply_history_location(self.state.history.history.back(), direction="Back")


    def navigate_history_forward(self) -> bool:
        """Move forward again after going back."""

        return self.parts.history.apply_history_location(self.state.history.history.forward(), direction="Forward")


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
            scope = self.parts.history.scope_for_history_key(location.scope_key)
            if scope is not None and scope is not self.state.review.scope:
                # The tabs list what the project tree shows, so its row follows
                # the scope (without announcing a selection), then the views.
                with QSignalBlocker(self.ui.tree):
                    self.ui.tree.setCurrentItem(self.parts.viewers.tree_item_for_scope(scope))
                self.state.review.scope = scope
                self.parts.viewers.render_viewer_tabs()
                self.parts.dashboard.render_dashboard_for_scope()
                self.parts.chrome.update_tab_counts()
            if location.tab in TAB_LABELS:
                self.ui.tabs.setCurrentIndex(TAB_LABELS.index(location.tab))
            viewer_tab = self.ui.viewer_tabs.get(location.tab)
            if viewer_tab is not None:
                viewer_tab.set_query(location.filter_text, row_keys=location.filter_keys,
                                     scope_label=location.filter_scope_label)
                value = viewer_tab.parts.list_pane.rows.value_for_key(location.object_key)
                if value is not None:
                    self.parts.viewers.select_viewer_object(value)
                    if location.list_anchor_key:
                        viewer_tab.parts.tab_selection.restore_list_anchor(
                            location.list_anchor_key, location.list_anchor_offset
                        )
                    if location.marker_id:
                        viewer_tab.select_marker(location.marker_id, arrive=True)
                    if location.frame_index is not None:
                        QTimer.singleShot(
                            0,
                            lambda viewer_tab=viewer_tab, frame=location.frame_index: (
                                viewer_tab.select_frame(frame)
                            ),
                        )
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


    def scope_for_history_key(self, scope_key: str) -> Scope:
        """Resolve a stored scope key back to a live scope object.

        Keys are compared by content, so a rebuilt cross-session linked group
        still matches. Returns None when the scope has gone, which is how a
        stale entry is detected.
        """

        if not scope_key or scope_key == "none":
            return None
        matches = [
            candidate for candidate in self.parts.history.candidate_history_scopes()
            if self.parts.viewers.scope_key_for(candidate) == scope_key
        ]
        return matches[0] if len(matches) == 1 else None


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
            lambda entry: self.parts.history.scope_for_history_key(entry.scope_key) is not None
        )
        self.parts.history.update_history_actions()

