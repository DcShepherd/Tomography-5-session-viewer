"""One composition switch for an empty scope, with window-local recovery."""
from __future__ import annotations
from dataclasses import replace
from typing import TYPE_CHECKING

from tomography_session_browser.presentation.empty_states import (
    COMMAND_OPEN_SESSION, EmptyStateAction, no_entities_state, no_processing_state,
    empty_processing_state, tomogram_reload_state, unlinked_tomograms_state,
)
from tomography_session_browser.ui.widgets.empty_state_panel import EmptyStatePanel

if TYPE_CHECKING:
    from tomography_session_browser.ui.tomograms.tab import TomogramTab


class EmptyArea:
    def __init__(self, tab: TomogramTab) -> None:
        self.tab = tab
        self.panel = EmptyStatePanel(tab)
        self.panel.action_requested.connect(self.activate)
        tab.outer.addWidget(self.panel, 1)
        self.last_state = None
        self.last_payload = None
        self.expanded = False

    def activate(self, command: str) -> None:
        if command == COMMAND_OPEN_SESSION:
            self.tab.load_requested.emit()
        elif command == "all_tomograms":
            self.tab.all_tomograms_requested.emit()
        elif command == "capture_history":
            self.tab.history.show_browser()
        elif command == "link_details":
            self.expanded = not self.expanded
            self.refresh()
        elif command in {f'processing_scope:{choice.key}' for choice in self.tab.payload.processing_scopes}:
            self.tab.processing_scope_requested.emit(command.removeprefix('processing_scope:'))

    def refresh(self) -> None:
        empty = not self.tab.payload.rows
        self.tab.splitter.setVisible(not empty)
        self.panel.setVisible(empty)
        if not empty:
            return
        if self.last_payload is not self.tab.payload:
            self.last_payload = self.tab.payload
            self.expanded = False
        payload = self.tab.payload
        if payload.notice:
            state = tomogram_reload_state(payload.notice)
        elif payload.processing_scopes:
            state = unlinked_tomograms_state(payload.processing_scopes, payload.link_explanations,
                                             expanded=self.expanded)
        elif not payload.has_sources:
            state = no_processing_state()
        elif payload.processing_only:
            state = empty_processing_state()
        else:
            state = no_entities_state(scope_name="this scope", entity_label="tomograms")
            state = replace(state, primary=EmptyStateAction("Load processing folder…", COMMAND_OPEN_SESSION))
        if self.tab.captures.failures or self.tab.contact_sheet.pending is not None:
            state = replace(state, secondary=state.secondary + (EmptyStateAction("All captures · Review", "capture_history"),))
        if state != self.last_state:
            self.panel.set_state(state)
            self.last_state = state
