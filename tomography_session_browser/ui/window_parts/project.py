"""ProjectActions owns its window interaction responsibilities."""
from __future__ import annotations
import logging
from collections.abc import (
    Iterable,
)
from pathlib import (
    Path,
)
from PySide6.QtCore import (
    QObject,
    QElapsedTimer,
    QTimer,
)
from PySide6.QtWidgets import (
    QApplication,
    QInputDialog,
    QMainWindow,
    QMenu,
    QMessageBox,
    QTreeWidgetItem,
)
from tomography_session_browser.domain.traversal import (
    has_collection_data,
)
from tomography_session_browser.domain.models import (
    Sample,
    Session,
    TiltSeries,
)
from tomography_session_browser.services.navigation import (
    tab_label_for_object,
)
from tomography_session_browser.presentation.project_tree import (
    ProjectTreeGroup,
    session_key,
)
from tomography_session_browser.ui.project_tree.panel import (
    ProjectTreePanel,
)
from tomography_session_browser.ui.project_tree.widgets import (
    OBJECT_ROLE,
    RECENT_PATH_ROLE,
)
from tomography_session_browser.presentation.scopes import (
    EntityGroup,
    LinkedSampleGroup,
)

LOGGER = logging.getLogger("tomography_session_browser.ui.main_window")
from tomography_session_browser.ui.window_parts.contracts import (
    WindowParts,
    WindowUi,
)
from tomography_session_browser.ui.window_parts.state import (
    WindowState,
)
from tomography_session_browser.ui.window_parts.support import (
    LOGGER,
)

class ProjectActions(QObject):
    def __init__(self, window: QMainWindow, state: WindowState, ui: WindowUi, parts: WindowParts) -> None:
        super().__init__(window)
        self.window, self.state, self.ui, self.parts = window, state, ui, parts

    def tree_selection_changed(self, current: QTreeWidgetItem | None, previous: QTreeWidgetItem | None) -> None:
        timer = QElapsedTimer()
        timer.start()
        self.parts.project_panel.repaint_rows(current, previous)
        if current is None:
            return
        value = current.data(0, OBJECT_ROLE)
        if value is None:
            self.ui.context_panel.set_title(current.text(0))
            self.ui.context_panel.set_text("")
            return
        self.parts.viewers.request_tree_selection(value, current.text(0))
        LOGGER.debug(
            "project tree selection changed label=%s value_type=%s total_ms=%d",
            current.text(0),
            type(value).__name__,
            timer.elapsed(),
        )


    def project_tree_item_clicked(self, item: QTreeWidgetItem, column: int) -> None:
        # A dataset choice is complete on one click. Object rows still need
        # their second click (or Enter) to activate through the normal route.
        if isinstance(item.data(0, OBJECT_ROLE), ProjectTreeGroup | Session | Sample | LinkedSampleGroup):
            self.parts.chrome.close_project_drawer()

    def project_tree_item_pressed(self, item: QTreeWidgetItem, column: int) -> None:
        value = item.data(0, OBJECT_ROLE)
        self.state.dashboard.project_tree_tilt_click_timer.stop()
        self.state.dashboard.pending_project_tree_clear_tilt_id = None
        if not isinstance(value, TiltSeries):
            return
        if self.state.dashboard.dashboard_highlighted_tilt_series_id != value.id:
            self.parts.dashboard.set_dashboard_highlighted_tilt_series_id(value.id)
            return
        self.state.dashboard.pending_project_tree_clear_tilt_id = value.id
        self.state.dashboard.project_tree_tilt_click_timer.start(QApplication.doubleClickInterval())


    def apply_pending_project_tree_highlight_clear(self) -> None:
        tilt_series_id = self.state.dashboard.pending_project_tree_clear_tilt_id
        self.state.dashboard.pending_project_tree_clear_tilt_id = None
        if not tilt_series_id:
            return
        if self.state.dashboard.dashboard_highlighted_tilt_series_id == tilt_series_id:
            self.parts.dashboard.set_dashboard_highlighted_tilt_series_id(None)


    def project_tree_item_double_clicked(self, item: QTreeWidgetItem, column: int) -> None:
        # Qt already opens or closes a row with children on a double-click
        # (``expandsOnDoubleClick``). A scope row toggling here as well was
        # toggled straight back, so a double-click did nothing.
        self.parts.project.activate_project_tree_item(item, toggle_scope=False)
        self.parts.chrome.close_project_drawer()


    def project_tree_item_activated(self, item: QTreeWidgetItem, column: int) -> None:
        """Keyboard activation (Enter) on the project tree.

        Qt emits ``itemActivated`` for Enter *and*, on most platforms, for
        double-click. Both routes land here, and the guard below stops a
        double-click firing the action twice.
        """

        self.parts.project.activate_project_tree_item(item)
        self.parts.chrome.close_project_drawer()


    def activate_project_tree_item(self, item: QTreeWidgetItem | None, *, toggle_scope: bool = True) -> bool:
        """Open the tree item's object in its viewer tab.

        Selection and activation stay distinct: selection re-scopes and
        describes, activation opens. Only activation is an explicit navigation
        event, which is what H1 will record in history.

        Opening a scope row expands or collapses it. ``toggle_scope`` is False
        for a double-click, where the tree itself does that.

        Returns True when something was opened.
        """

        if item is None:
            return False
        recent_path = item.data(0, RECENT_PATH_ROLE)
        if isinstance(recent_path, str) and recent_path:
            return self.parts.project.activate_recent_session(item, recent_path)
        value = item.data(0, OBJECT_ROLE)
        if value is None:
            return False
        if self.state.review.project_tree_activation_guard == id(item):
            return False
        self.state.review.project_tree_activation_guard = id(item)
        # Release the guard once this event turn finishes, so a deliberate
        # second activation still works.
        QTimer.singleShot(0, self.parts.project.clear_project_tree_activation_guard)

        self.state.dashboard.project_tree_tilt_click_timer.stop()
        self.state.dashboard.pending_project_tree_clear_tilt_id = None

        if isinstance(value, TiltSeries):
            # navigate_to_tilt_series records history itself.
            self.parts.dashboard.navigate_to_tilt_series(value.id, source="project panel")
            return True
        if isinstance(value, EntityGroup):
            departure = self.parts.history.begin_navigation()
            self.parts.viewers.switch_to_group_tab(value.label)
            first_value = next(iter(value.values), None)
            if first_value is not None:
                self.parts.viewers.select_viewer_object(first_value)
            self.parts.history.record_history(departure)
            return True
        if tab_label_for_object(value) is not None:
            # Atlas, Overview, Search map, Search tile and Batch position all
            # became keyboard-reachable here; previously only a Tilt series
            # responded, and only to a double-click.
            departure = self.parts.history.begin_navigation()
            self.parts.viewers.select_viewer_object(value)
            self.parts.history.record_history(departure)
            return True
        if isinstance(value, ProjectTreeGroup | Session | Sample | LinkedSampleGroup):
            # Scope rows have no destination beyond themselves; activating one
            # expands it, which is what a reviewer means by pressing Enter.
            if toggle_scope:
                item.setExpanded(not item.isExpanded())
            return True
        return False


    def activate_recent_session(self, item: QTreeWidgetItem, path: str) -> bool:
        """Reopen a recent session, or say why it cannot be reopened.

        A path in the recent list can have been moved or deleted since it was
        last opened. Reporting that is better than the previous behaviour,
        which was to do nothing at all and leave the reviewer guessing whether
        the click registered.
        """

        if self.state.review.project_tree_activation_guard == id(item):
            return False
        self.state.review.project_tree_activation_guard = id(item)
        QTimer.singleShot(0, self.parts.project.clear_project_tree_activation_guard)

        folder = Path(path)
        if not folder.exists():
            self.window.statusBar().showMessage(
                f"{folder.name} is no longer at {folder}. It may have been moved or removed."
            )
            return False

        self.parts.loading.queue_session_loads([(str(folder), not self.state.review.workspace.sessions, bool(self.state.review.workspace.sessions), True)])
        return True


    def clear_project_tree_activation_guard(self) -> None:
        self.state.review.project_tree_activation_guard = None


    def on_project_tree_context_menu(self, position) -> None:
        item = self.ui.tree.itemAt(position)
        if item is None:
            return
        value = item.data(0, OBJECT_ROLE)
        group = ProjectTreePanel.group_for_item(item)
        menu = QMenu(self.window)

        if isinstance(value, ProjectTreeGroup):
            rename_action = menu.addAction("Rename group...")
            revert_action = menu.addAction("Revert group name")
            revert_action.setEnabled(any(key in self.state.review.project_display_names for key in value.name_lookup_keys))
            menu.addSeparator()
            remove_action = menu.addAction("Remove group from project")
            chosen = menu.exec(self.ui.tree.viewport().mapToGlobal(position))
            if chosen is rename_action:
                self.parts.project.rename_project_group(value)
            elif chosen is revert_action:
                self.parts.project.revert_project_group_name(value)
            elif chosen is remove_action:
                self.parts.project.remove_project_group(value)
            return

        if isinstance(value, Session) and group is not None and group.kind == "linked":
            label = "Remove atlas session from project" if value is group.atlas_session else "Remove data collection from project"
            remove_action = menu.addAction(label)
            chosen = menu.exec(self.ui.tree.viewport().mapToGlobal(position))
            if chosen is remove_action:
                self.parts.project.remove_session_from_project(value)


    def rename_project_group(self, group: ProjectTreeGroup) -> None:
        name, accepted = QInputDialog.getText(
            self.window,
            "Rename project group",
            "Display name",
            text=group.display_name,
        )
        if not accepted:
            return
        display_name = name.strip()
        if not display_name:
            return
        self.parts.project.forget_project_group_name(group)
        if display_name != group.automatic_name:
            # Saved against the anchor session, not the group key, so the name
            # survives the group changing kind (atlas-only becoming linked).
            self.state.review.project_display_names[group.name_key] = display_name
        self.parts.project.refresh_project_state_after_edit(self.state.review.workspace.sessions, select_group_key=group.key)


    def revert_project_group_name(self, group: ProjectTreeGroup) -> None:
        self.parts.project.forget_project_group_name(group)
        self.parts.project.refresh_project_state_after_edit(self.state.review.workspace.sessions, select_group_key=group.key)


    def forget_project_group_name(self, group: ProjectTreeGroup) -> None:
        for key in (group.key, *group.name_lookup_keys):
            self.state.review.project_display_names.pop(key, None)


    def remove_project_group(self, group: ProjectTreeGroup) -> None:
        session_word = "session" if len(group.sessions) == 1 else "sessions"
        response = QMessageBox.question(
            self.window,
            "Remove project group",
            f"Remove '{group.display_name}' and its {len(group.sessions)} loaded {session_word} from the project?\n\n"
            "Source files on disk will not be changed.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if response != QMessageBox.StandardButton.Yes:
            return
        remove_keys = {session_key(session) for session in group.sessions}
        self.parts.project.forget_project_group_name(group)
        self.parts.project.refresh_project_state_after_edit(
            [session for session in self.state.review.workspace.sessions if session_key(session) not in remove_keys]
        )


    def remove_session_from_project(self, session: Session) -> None:
        # Asked first, as removing a whole group is: the linked view (and any
        # custom group name tied to it) changes when one session leaves.
        response = QMessageBox.question(
            self.window,
            "Remove session",
            f"Remove '{session.name or session.path.name}' from the project?\n\n"
            "Source files on disk will not be changed.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if response != QMessageBox.StandardButton.Yes:
            return
        self.parts.project.refresh_project_state_after_edit([candidate for candidate in self.state.review.workspace.sessions if candidate is not session])


    def refresh_project_state_after_edit(
        self,
        sessions: Iterable[Session],
        *,
        select_group_key: str | None = None,
    ) -> None:
        """Make ``sessions`` the project after an edit to it, and redraw it."""

        self.parts.loading.set_loaded_sessions(sessions)
        self.parts.scopes.populate_tree()
        if select_group_key:
            self.parts.project_panel.select_group_by_key(select_group_key)
        elif self.ui.tree.currentItem() is not None:
            self.parts.project.tree_selection_changed(self.ui.tree.currentItem(), None)
        self.parts.dashboard.render_session_summary()
        self.parts.viewers.render_viewer_tabs()
        self.parts.chrome.update_tab_counts()
        self.ui.refresh_button.setEnabled(bool(self.state.review.workspace.sessions))
        self.ui.report_action.setEnabled(bool(self.state.review.workspace.sessions) and not self.parts.report.active)


    def linked_sample_label(self, samples: list[Sample]) -> str:
        atlas_sample = next((sample for sample in samples if sample.atlas is not None), None)
        if atlas_sample is not None:
            return atlas_sample.name
        collection_sample = next((sample for sample in samples if has_collection_data(sample)), samples[0])
        return collection_sample.name

