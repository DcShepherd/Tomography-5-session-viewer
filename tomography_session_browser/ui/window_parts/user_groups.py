"""The user's own project-panel groups: menu items and edits (B4, owner 9 Nov 2026).

A group is a display heading for this app session, like a rename. Grouping
rebuilds the tree with its signals blocked, so the scope, Summary and Context
stay as they were; no link, count or report scope changes.
"""
from __future__ import annotations

from collections.abc import Callable

from PySide6.QtGui import QAction
from PySide6.QtWidgets import QMainWindow, QMenu, QTreeWidgetItem

from tomography_session_browser.domain.display_names import count_phrase
from tomography_session_browser.presentation.messages import MessageContent, TextAccepted
from tomography_session_browser.presentation.project_folders import (UserHeading, heading_text, member_keys, renamed,
                                                                     ungrouped, with_new_group, without_keys)
from tomography_session_browser.ui.project_tree.items import project_group_for_row, user_group_for_row
from tomography_session_browser.ui.project_tree.widgets import MARK_ROLE
from tomography_session_browser.ui.widgets import message_sheet
from tomography_session_browser.ui.window_parts.contracts import WindowParts, WindowUi
from tomography_session_browser.ui.window_parts.state import WindowState

ONLY_IN_APP = "The group is used only in the app, until you close it. Source files stay unchanged."


class UserGroupActions:
    def __init__(self, window: QMainWindow, state: WindowState, ui: WindowUi, parts: WindowParts) -> None:
        self.window, self.state, self.ui, self.parts = window, state, ui, parts

    # -- menus -----------------------------------------------------------------

    def heading_menu(self, menu: QMenu, item: QTreeWidgetItem) -> dict[QAction, Callable[[], None]]:
        group = user_group_for_row(item)
        assert group is not None
        return {menu.addAction("Rename group..."): lambda: self.rename(group.key, group.name),
                menu.addAction("Ungroup"): lambda: self.edit(ungrouped(self.state.review.user_groups, group.key))}

    def leading_items(self, menu: QMenu, item: QTreeWidgetItem) -> dict[QAction, Callable[[], None]]:
        """On a marked row: Group N marked rows, first in its menu."""

        if not item.data(0, MARK_ROLE):
            return {}
        marked = self.parts.project_panel.tree.marked_items()
        action = menu.addAction(f"Group {count_phrase(len(marked), 'marked row')}...")
        menu.addSeparator()
        return {action: self.group_marked}

    def member_items(self, menu: QMenu, item: QTreeWidgetItem) -> dict[QAction, Callable[[], None]]:
        """On a row inside a heading: Remove from group."""

        row = project_group_for_row(item)
        if row is None or user_group_for_row(item.parent()) is None:
            return {}
        keys = member_keys(row)
        return {menu.addAction("Remove from group"): lambda: self.edit(without_keys(self.state.review.user_groups, keys))}

    # -- edits -------------------------------------------------------------------

    def group_marked(self) -> None:
        rows = [row for row in map(project_group_for_row, self.parts.project_panel.tree.marked_items()) if row is not None]
        serial = self.state.review.user_group_serial + 1
        answer = message_sheet.request_text(self.window, MessageContent("Group project rows", ONLY_IN_APP),
                                            label="Group name", value=f"Group {serial}", accept_label="Group")
        name = answer.value.strip() if isinstance(answer, TextAccepted) else ""
        if not rows or not name:
            return  # Cancel keeps the marks.
        self.state.review.user_group_serial = serial
        key = f"user-group-{serial}"
        self.edit(with_new_group(self.state.review.user_groups, key, name, rows), open_user_group=key)

    def rename(self, key: str, name: str) -> None:
        answer = message_sheet.request_text(self.window, MessageContent("Rename group", ONLY_IN_APP),
                                            label="Group name", value=name, accept_label="Rename")
        if isinstance(answer, TextAccepted) and answer.value.strip():
            self.edit(renamed(self.state.review.user_groups, key, answer.value.strip()))

    def edit(self, user_groups, *, open_user_group: str | None = None) -> None:
        self.state.review.user_groups = tuple(user_groups)
        self.parts.scopes.populate_tree(open_user_group=open_user_group)

    # -- Context -------------------------------------------------------------------

    def describe(self, item: QTreeWidgetItem) -> bool:
        """Describe a heading in Context: its members. A heading has no scope, so Summary stays."""

        group = user_group_for_row(item)
        if group is None:
            return False
        children = (item.child(index) for index in range(item.childCount()))
        members = tuple(row for row in (project_group_for_row(child) for child in children if child is not None)
                        if row is not None)
        workspace = self.state.review.workspace
        self.ui.context_panel.set_title(group.name)
        self.ui.context_panel.set_text(heading_text(UserHeading(group, members),
                                                    lambda row: len(workspace.tomograms(row))))
        return True
