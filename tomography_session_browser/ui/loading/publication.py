"""The committed view retained while a prepared load replaces its widgets."""
from collections import OrderedDict
from copy import deepcopy
from dataclasses import dataclass

from PySide6.QtCore import QSignalBlocker

from tomography_session_browser.presentation.workspace import Scope, Workspace
from tomography_session_browser.presentation.navigation_history import NavigationHistory
from tomography_session_browser.ui.viewer.models import ViewerViewState
from tomography_session_browser.presentation.tomogram_state import TomogramViewState
from tomography_session_browser.presentation.processing_context import SourceReadTimes
from tomography_session_browser.ui.window_parts.contracts import WindowParts, WindowUi
from tomography_session_browser.ui.window_parts.state import WindowState


@dataclass
class CommittedLoadView:
    workspace: Workspace
    scope: Scope
    current_path: str | None
    preserve_tree_root: bool
    tab_index: int
    viewer_states: OrderedDict[tuple[str, str], ViewerViewState | TomogramViewState]
    history: NavigationHistory
    recent_sessions: list[str]
    source_reads: SourceReadTimes

    @classmethod
    def capture(cls, state: WindowState, ui: WindowUi, parts: WindowParts) -> 'CommittedLoadView':
        parts.viewers.capture_viewer_states()
        return cls(state.review.workspace, state.review.scope, state.review.current_path,
                   state.review.preserve_tree_root_context, ui.tabs.currentIndex(),
                   state.viewer.viewer_states.copy(), deepcopy(state.history.history),
                   list(state.review.settings.recent_sessions), state.review.source_reads)

    def restore(self, state: WindowState, ui: WindowUi, parts: WindowParts) -> None:
        parts.loading.apply_workspace(self.workspace, self.scope)
        state.review.source_reads = self.source_reads
        parts.loading.reset_workspace_views()
        state.review.current_path = self.current_path
        state.review.preserve_tree_root_context = self.preserve_tree_root
        state.review.settings.recent_sessions = self.recent_sessions
        state.viewer.viewer_states.update(self.viewer_states)
        parts.dashboard_host.reset_pending()
        parts.scopes.populate_tree()
        with QSignalBlocker(ui.tree):
            ui.tree.setCurrentItem(parts.viewers.tree_item_for_scope(self.scope))
        ui.tabs.setCurrentIndex(self.tab_index)
        parts.viewers.render_viewer_tabs()
        parts.dashboard.render_session_summary(defer_heavy_cards=False)
        state.history.history = self.history
        parts.history.update_history_actions()
        parts.context.sync_context_panel_to_active_scope()
