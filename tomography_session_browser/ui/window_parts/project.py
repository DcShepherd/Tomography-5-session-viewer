"""ProjectActions owns its window interaction responsibilities."""
from __future__ import annotations
from tomography_session_browser.services.load_results import LoadRequest, LoadIntent
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
    QMainWindow,
    QMenu,
    QTreeWidgetItem,
)
from tomography_session_browser.domain.identity import path_identity_key
from tomography_session_browser.domain.display_names import count_phrase
from tomography_session_browser.presentation.messages import ActionPrompt, ActionRole, MessageAction, MessageContent, TextAccepted
from tomography_session_browser.ui.widgets import message_sheet
from tomography_session_browser.domain.traversal import (
    has_collection_data,
)
from tomography_session_browser.domain.tomograms import ProcessingSource
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
    processing_group_key,
    session_key,
)
from tomography_session_browser.ui.project_tree.panel import (
    ProjectTreePanel,
)
from tomography_session_browser.ui.project_tree.items import project_group_for_row, session_for_row, user_group_for_row
from tomography_session_browser.ui.window_parts.user_groups import UserGroupActions
from tomography_session_browser.ui.shell.chrome import TAB_LABELS
from tomography_session_browser.presentation.scope_identity import scope_key
from tomography_session_browser.presentation.tomogram_selection import AllTomograms, FolderTomograms
from tomography_session_browser.ui.file_actions import add_source_actions
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
        self.user_groups = UserGroupActions(window, state, ui, parts)

    def tree_selection_changed(self, current: QTreeWidgetItem | None, previous: QTreeWidgetItem | None) -> None:
        timer = QElapsedTimer()
        timer.start()
        self.parts.project_panel.repaint_rows(current, previous)
        if current is None:
            return
        value = current.data(0, OBJECT_ROLE)
        if value is None and self.user_groups.describe(current):
            return
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
        value = item.data(0, OBJECT_ROLE)
        if isinstance(value, AllTomograms | FolderTomograms):
            # A Tomograms row names its tab, so a click on the highlighted row
            # still opens Tomograms; the route ignores a repeated request.
            self.parts.viewers.request_tree_selection(value, item.text(0))
            self.parts.chrome.close_project_drawer()
        elif isinstance(value, ProjectTreeGroup | Session | Sample | LinkedSampleGroup):
            if scope_key(value) != scope_key(self.state.review.scope):
                self.parts.viewers.request_tree_selection(value, item.text(0))
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
        if isinstance(value, AllTomograms | FolderTomograms):
            return self.parts.viewers.open_tree_tomograms(value, item.text(0),
                                                          departure=self.parts.history.begin_navigation())
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

        self.parts.loading.queue_session_loads([LoadRequest(str(folder), quiet=self.state.review.workspace.has_sources)])
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
        if user_group_for_row(item) is not None:
            run = self.user_groups.heading_menu(menu, item)
            chosen = menu.exec(self.ui.tree.viewport().mapToGlobal(position))
            if chosen in run:
                run[chosen]()
            return
        row_group = project_group_for_row(item)
        run = self.user_groups.leading_items(menu, item) if row_group is not None else {}
        sources = self.sources_for_scope(value)
        if sources:
            for source in sources:
                destination = menu if len(sources) == 1 else menu.addMenu(source.name)
                add_source_actions(destination, source, self.refresh_processing_source, self.remove_processing_source)
            if row_group is None:
                menu.exec(self.ui.tree.viewport().mapToGlobal(position))
                return
            menu.addSeparator()

        if row_group is not None:
            value = row_group
            rename_action = menu.addAction("Rename group...")
            revert_action = menu.addAction("Revert group name")
            revert_action.setEnabled(any(key in self.state.review.project_display_names for key in value.name_lookup_keys))
            run.update(self.user_groups.member_items(menu, item))
            menu.addSeparator()
            remove_action = menu.addAction("Remove processing folder from project" if value.processing_source is not None
                                          else "Remove group from project")
            chosen = menu.exec(self.ui.tree.viewport().mapToGlobal(position))
            if chosen in run:
                run[chosen]()
            elif chosen is rename_action:
                self.parts.project.rename_project_group(value)
            elif chosen is revert_action:
                self.parts.project.revert_project_group_name(value)
            elif chosen is remove_action:
                self.parts.project.remove_project_group(value)
            return

        value = session_for_row(item)
        if value is not None and group is not None and group.kind == "linked":
            label = "Remove atlas session from project" if value is group.atlas_session else "Remove data collection from project"
            remove_action = menu.addAction(label)
            chosen = menu.exec(self.ui.tree.viewport().mapToGlobal(position))
            if chosen is remove_action:
                self.parts.project.remove_session_from_project(value)


    def sources_for_scope(self, scope):
        workspace = self.state.review.workspace
        if isinstance(scope, ProjectTreeGroup) and scope.processing_source is not None:
            ids = {scope.processing_source.id}
        elif isinstance(scope, FolderTomograms):
            ids = {scope.source_id}
        elif isinstance(scope, AllTomograms):
            ids = {row.source_id for row in workspace.tomograms(scope)}
        else:
            return ()
        return tuple(source for source in workspace.processing_sources if source.id in ids)

    def refresh_processing_source(self, source_id: str) -> None:
        sources = [source for source in self.state.review.workspace.processing_sources if source.id == source_id]
        if len(sources) == 1:
            source = sources[0]
            self.parts.loading.queue_session_loads([LoadRequest(str(source.path), LoadIntent.RELOAD)])

    def remove_processing_source(self, source_id: str) -> None:
        sources = [source for source in self.state.review.workspace.processing_sources if source.id == source_id]
        if len(sources) == 1:
            source = sources[0]
            key = processing_group_key(source.id)
            group = ProjectTreeGroup(key, 'processing', source.name, source.name, [],
                                     name_lookup_keys=(key,), processing_source=source)
            self.remove_project_group(group)

    def rename_project_group(self, group: ProjectTreeGroup) -> None:
        answer = message_sheet.request_text(self.window,
            MessageContent("Rename project group", "This name is used only in the app. Source files stay unchanged."),
            label="Display name", value=group.display_name, accept_label="Rename")
        if not isinstance(answer, TextAccepted):
            return
        display_name = answer.value.strip()
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
        session_word = "processing folder" if group.processing_source is not None else "session"
        source_count = 1 if group.processing_source is not None else len(group.sessions)
        response = message_sheet.choose_action(self.window, ActionPrompt(
            MessageContent(f"Remove “{group.display_name}” from the project?",
                f"Its {count_phrase(source_count, session_word)} leave this review. Source files stay unchanged.",
                tuple(str(s.path) for s in group.sessions) if group.processing_source is None else (str(group.processing_source.path),)),
            (MessageAction("cancel", "Cancel"), MessageAction("remove", "Remove group", ActionRole.DESTRUCTIVE)), "cancel", "cancel"))
        if response != "remove":
            return
        remove_keys = {session_key(session) for session in group.sessions}
        self.parts.project.forget_project_group_name(group)
        processing = tuple(source for source in self.state.review.workspace.processing_sources
                           if source is not group.processing_source)
        self.parts.project.refresh_project_state_after_edit(
            [session for session in self.state.review.workspace.sessions if session_key(session) not in remove_keys],
            processing_sources=processing,
        )


    def remove_session_from_project(self, session: Session) -> None:
        # Asked first, as removing a whole group is: the linked view (and any
        # custom group name tied to it) changes when one session leaves.
        response = message_sheet.choose_action(self.window, ActionPrompt(
            MessageContent(f"Remove “{session.name or session.path.name}” from the project?",
                "The session leaves this review. Source files stay unchanged.", (str(session.path),)),
            (MessageAction("cancel", "Cancel"), MessageAction("remove", "Remove session", ActionRole.DESTRUCTIVE)), "cancel", "cancel"))
        if response != "remove":
            return
        self.parts.project.refresh_project_state_after_edit([candidate for candidate in self.state.review.workspace.sessions if candidate is not session])


    def refresh_project_state_after_edit(
        self,
        sessions: Iterable[Session],
        *,
        select_group_key: str | None = None,
        processing_sources: Iterable[ProcessingSource] | None = None,
    ) -> None:
        """Make ``sessions`` the project after an edit to it, and redraw it."""

        previous_scope = self.state.review.scope
        self.parts.loading.set_loaded_sessions(sessions, scope=previous_scope if isinstance(previous_scope, AllTomograms | FolderTomograms) else None,
                                              processing_sources=processing_sources)
        self.parts.scopes.populate_tree()
        if select_group_key:
            self.parts.project_panel.select_group_by_key(select_group_key)
        if isinstance(self.state.review.scope, AllTomograms | FolderTomograms):
            selected = self.state.review.scope
            self.parts.viewers.open_scope(selected, destination_tab=TAB_LABELS[self.ui.tabs.currentIndex()])
            self.parts.viewers.request_scope(selected, self.parts.scopes.label_for(selected),
                                            destination_tab=TAB_LABELS[self.ui.tabs.currentIndex()])
        elif self.ui.tree.currentItem() is not None and self.state.review.workspace.has_sources:
            # A rebuild may already have selected this row with signals blocked.
            # Publish its canonical scope through the usual preparation owner;
            # a synchronous viewer render here would cancel that request.
            self.parts.project.tree_selection_changed(self.ui.tree.currentItem(), None)
        else:
            self.parts.dashboard.render_session_summary()
            self.parts.viewers.render_viewer_tabs()
            self.parts.context.sync_context_panel_to_active_scope()
            self.parts.chrome.update_tab_counts()
        self.ui.refresh_button.setEnabled(self.state.review.workspace.has_sources)
        self.parts.report.refresh_action()


    def linked_sample_label(self, samples: list[Sample]) -> str:
        atlas_sample = next((sample for sample in samples if sample.atlas is not None), None)
        if atlas_sample is not None:
            return atlas_sample.name
        collection_sample = next((sample for sample in samples if has_collection_data(sample)), samples[0])
        return collection_sample.name

