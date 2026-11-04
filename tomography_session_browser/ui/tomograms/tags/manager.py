"""Explicit catalogue operations; a merge commits only one reviewed proposal."""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QDialog, QDialogButtonBox, QHBoxLayout,
                               QLabel, QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout)

from tomography_session_browser.services.tomogram_tags.project import rename, remove_tag
from tomography_session_browser.services.tomogram_tags import binding
from tomography_session_browser.domain.display_names import count_phrase
from tomography_session_browser.presentation.messages import ActionPrompt, ActionRole, MessageAction, MessageContent, TextAccepted
from tomography_session_browser.presentation.tomogram_tag_messages import MoveAccepted
from tomography_session_browser.ui.widgets import message_sheet
from tomography_session_browser.ui.tomograms.tags import move_dialog, merge_dialog
from tomography_session_browser.presentation.tomogram_tag_merge import MergeCommitted


class TagManager(QDialog):
    def __init__(self, owner):
        super().__init__(owner.tab)
        self.owner = owner
        self.session = owner.target().session
        self.setWindowTitle("Manage tags")
        layout = QVBoxLayout(self)
        self.notice = QLabel(self)
        self.notice.setTextFormat(Qt.TextFormat.PlainText)
        self.notice.setWordWrap(True)
        layout.addWidget(self.notice)
        self.saved = QLabel(self)
        self.saved.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(self.saved)
        self.table = QTableWidget(0, 3, self)
        self.table.setHorizontalHeaderLabels(("Tag", "Tomograms", "Counted"))
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        layout.addWidget(self.table)
        actions = QHBoxLayout()
        for label, callback in (("Rename…", self.rename), ("Merge…", self.merge), ("Remove…", self.remove), ("Find moved folder…", self.move)):
            button = QPushButton(label, self)
            button.clicked.connect(callback)
            actions.addWidget(button)
        layout.addLayout(actions)
        close = QDialogButtonBox(QDialogButtonBox.StandardButton.Close, self)
        close.rejected.connect(self.reject)
        layout.addWidget(close)
        self.resize(620, 430)
        self.owner.projects.changed.connect(self.refresh)
        self.refresh()

    def refresh(self) -> None:
        project = self.session.project
        if project is None:
            self.reject()
            return
        current = self.selected_id()
        self.table.setRowCount(len(project.catalogue))
        for index, tag in enumerate(sorted(project.catalogue, key=lambda t: t.name.casefold())):
            assignments = [a for r in project.tomograms for a in r.tags if a.tag_id == tag.id]
            for column, text in enumerate((tag.name, str(len(assignments)), str(sum(a.count is not None for a in assignments)))):
                item = QTableWidgetItem(text)
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                item.setData(Qt.ItemDataRole.UserRole, tag.id)
                self.table.setItem(index, column, item)
            if tag.id == current:
                self.table.selectRow(index)
        loaded = {s.id for s in self.owner.tab.payload.sources}
        unmatched = sum(not any(s.id in loaded and any(r.id == s.id + "::" + saved.ref.relative for r in s.tomograms)
                                 for s in self.owner.tab.payload.sources if any(t.id == saved.ref.source and t.path == s.path for t in project.sources))
                        for saved in project.tomograms)
        verb = "is" if unmatched == 1 else "are"
        self.show_notice(f"{count_phrase(unmatched, 'tomogram')} {verb} not in the loaded folders. Find moved folder…" if unmatched else "")
        self.saved.setText(self.session.save_state.status.value)
        self.table.resizeColumnsToContents()

    def show_notice(self, message: str) -> None:
        self.notice.setText(message)
        self.notice.setVisible(bool(message))

    def selected_id(self):
        item = self.table.item(self.table.currentRow(), 0)
        return item.data(Qt.ItemDataRole.UserRole) if item else None

    def apply(self, project, remapped=None) -> None:
        try:
            self.session.edit(project)
            self.owner.filters.refresh(remapped)
            self.owner.changed()
            self.refresh()
        except ValueError as exc:
            self.show_notice(str(exc))

    def rename(self) -> None:
        identity = self.selected_id()
        project = self.session.project
        if not identity or not project:
            return
        tag = next(t for t in project.catalogue if t.id == identity)
        answer = message_sheet.request_text(self, MessageContent("Rename tag", "The tag keeps its observations and captured views."),
            label="Tag name", value=tag.name, accept_label="Rename")
        if isinstance(answer, TextAccepted):
            try:
                self.apply(rename(self.session.project, identity, answer.value))
            except ValueError as exc:
                self.show_notice(str(exc))

    def remove(self) -> None:
        identity, project = self.selected_id(), self.session.project
        if not identity or not project:
            return
        total = sum(any(a.tag_id == identity for a in r.tags) for r in project.tomograms)
        tag = next(t for t in project.catalogue if t.id == identity)
        answer = message_sheet.choose_action(self, ActionPrompt(
            MessageContent(f"Remove “{tag.name}” from {count_phrase(total, 'tomogram')}?", "Its tag assignments will be removed. Images and capture history are kept."),
            (MessageAction("cancel", "Cancel"), MessageAction("remove", "Remove tag", ActionRole.DESTRUCTIVE)), "cancel", "cancel"))
        if answer == "remove":
            self.apply(remove_tag(self.session.project, identity))

    def merge(self) -> None:
        removed, project = self.selected_id(), self.session.project
        if not removed or project is None or len(project.catalogue) < 2:
            return
        answer = merge_dialog.review_merge(self, self.session, removed)
        if isinstance(answer, MergeCommitted):
            self.owner.filters.refresh({answer.removed_id: answer.surviving_id})
            self.owner.changed()
            self.refresh()

    def move(self) -> None:
        project = self.session.project
        if not project:
            return
        loaded = self.owner.tab.payload.sources
        missing = [s for s in project.sources if not any(s.path == f.path for f in loaded)]
        available = [f for f in loaded if self.owner.projects.session_for(f.path) is None]
        if not missing or not available:
            self.show_notice("Load the moved folder first. It must not belong to another project.")
            return
        answer = move_dialog.review_move(self, self.session, tuple(missing), tuple(available))
        if isinstance(answer, MoveAccepted):
            try:
                self.session.edit(binding.confirm_move(self.session.project, answer.proposal))
                self.owner.projects.bind_after_save(answer.proposal.folder.path, self.session)
                self.owner.changed()
                self.refresh()
            except ValueError as exc:
                self.show_notice(str(exc))
