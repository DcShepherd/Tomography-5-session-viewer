"""Explicit catalogue operations; a merge commits only one reviewed proposal."""
from datetime import datetime

from PySide6.QtCore import QItemSelectionModel, QSignalBlocker, Qt, QTimer
from PySide6.QtWidgets import (QAbstractItemView, QDialog, QDialogButtonBox, QHBoxLayout, QHeaderView,
                               QLabel, QLineEdit, QMenu, QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout)

from tomography_session_browser.services.tomogram_tags.project import rename, remove_tag
from tomography_session_browser.services.tomogram_tags.session import SaveStatus
from tomography_session_browser.services.tomogram_tags import binding
from tomography_session_browser.services.tomogram_tags.binding import ProjectKey
from tomography_session_browser.domain.display_names import count_phrase
from tomography_session_browser.presentation.dashboard.tag_summary import number_text
from tomography_session_browser.presentation.messages import ActionPrompt, ActionRole, MessageAction, MessageContent, TextAccepted
from tomography_session_browser.presentation.tomogram_tag_catalogue import catalogue_rows, merge_order, saved_text, selection_text
from tomography_session_browser.presentation.tomogram_tag_changes import review_total, unmatched_count
from tomography_session_browser.presentation.tomogram_tag_messages import MoveAccepted
from tomography_session_browser.ui.theme import TIER_SUPPORTING, apply_tier
from tomography_session_browser.ui.widgets import message_sheet
from tomography_session_browser.ui.widgets.elided_label import ElidedLabel
from tomography_session_browser.ui.widgets.link_button import link_button
from tomography_session_browser.ui.tomograms.tags import move_dialog, merge_dialog
from tomography_session_browser.presentation.tomogram_tag_merge import MergeCommitted

NUMBER_ALIGNMENT = Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter


class TagManager(QDialog):
    def __init__(self, owner):
        super().__init__(owner.tab)
        self.owner = owner
        self.session = owner.target().session
        self.rows = ()
        self.setWindowTitle("Manage tags")
        layout = QVBoxLayout(self)
        self.review_button = link_button("Review tag sources…", self)
        self.review_button.clicked.connect(self.review_sources)
        layout.addWidget(self.review_button, alignment=Qt.AlignmentFlag.AlignLeft)
        self.notice = QLabel(self)
        self.notice.setTextFormat(Qt.TextFormat.PlainText)
        self.notice.setWordWrap(True)
        layout.addWidget(self.notice)
        # Apart from the notice, which each refresh rewrites.
        self.export_result = QLabel(self)
        self.export_result.setTextFormat(Qt.TextFormat.PlainText)
        self.export_result.setWordWrap(True)
        self.export_result.hide()
        layout.addWidget(self.export_result)
        finder = QHBoxLayout()
        self.find = QLineEdit(self)
        self.find.setPlaceholderText("Find a tag…")
        self.find.setAccessibleName("Find a tag")
        self.find.setClearButtonEnabled(True)
        self.find.textChanged.connect(self.apply_find)
        self.selection_count = apply_tier(QLabel(self), TIER_SUPPORTING)
        finder.addWidget(self.find, 1)
        finder.addWidget(self.selection_count)
        layout.addLayout(finder)
        self.table = QTableWidget(0, 4, self)
        self.table.setAccessibleName("Tags in this project")
        self.table.setHorizontalHeaderLabels(("Tag", "Tomograms", "Counted", "Total"))
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.table.setWordWrap(False)
        self.table.verticalHeader().hide()
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for column in range(1, self.table.columnCount()):
            header.setSectionResizeMode(column, QHeaderView.ResizeMode.ResizeToContents)
            self.table.horizontalHeaderItem(column).setTextAlignment(NUMBER_ALIGNMENT)
        self.table.horizontalHeaderItem(0).setTextAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self.table.itemSelectionChanged.connect(self.refresh_actions)
        layout.addWidget(self.table)
        actions = QHBoxLayout()
        self.rename_button = self.action("Rename…", self.rename, actions)
        self.merge_button = self.action("Merge…", self.merge, actions)
        self.remove_button = self.action("Remove…", self.remove, actions)
        self.move_button = self.action("Find moved folder…", self.move, actions)
        actions.addStretch(1)
        self.export_button = self.action("Export CSV…", lambda: self.owner.export(parent=self), actions)
        layout.addLayout(actions)
        footer = QHBoxLayout()
        self.project_file = apply_tier(ElidedLabel(parent=self, mode=Qt.TextElideMode.ElideMiddle), TIER_SUPPORTING)
        self.saved = apply_tier(QLabel(self), TIER_SUPPORTING)
        self.saved.setTextFormat(Qt.TextFormat.PlainText)
        self.change = link_button("Change…", self, tooltip="Open another tag project, or save this one as a new file")
        self.change.clicked.connect(self.change_project)
        footer.addWidget(self.project_file, 1)
        footer.addWidget(self.saved)
        footer.addWidget(self.change)
        layout.addLayout(footer)
        close = QDialogButtonBox(QDialogButtonBox.StandardButton.Close, self)
        close.rejected.connect(self.reject)
        # Enter in Find must not close the dialog.
        close.button(QDialogButtonBox.StandardButton.Close).setAutoDefault(False)
        layout.addWidget(close)
        self.saved_key = None
        self.resize(620, 500)
        self.owner.projects.changed.connect(self.refresh)
        self.owner.export_reported.connect(self.sync_export)
        self.refresh()

    def action(self, label: str, callback, row: QHBoxLayout) -> QPushButton:
        button = QPushButton(label, self)
        button.setAutoDefault(False)
        button.clicked.connect(lambda: callback())
        row.addWidget(button)
        return button

    def refresh(self) -> None:
        project = self.session.project
        if project is None:
            self.reject()
            return
        current = set(self.selected_ids())
        rows = catalogue_rows(project)
        self.rows = rows
        with QSignalBlocker(self.table):
            self.table.clearSelection()
            self.table.setRowCount(len(rows))
            for index, row in enumerate(rows):
                for column, text in enumerate((row.name, str(row.tomograms), str(row.counted), number_text(row.total))):
                    item = QTableWidgetItem(text)
                    item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                    item.setData(Qt.ItemDataRole.UserRole, row.tag_id)
                    if column:
                        item.setTextAlignment(NUMBER_ALIGNMENT)
                    self.table.setItem(index, column, item)
                if row.tag_id in current:
                    self.table.selectionModel().select(self.table.model().index(index, 0),
                        QItemSelectionModel.SelectionFlag.Select | QItemSelectionModel.SelectionFlag.Rows)
        proposal = self.owner.change_proposal(self.session)
        review = proposal.rows if proposal else ()
        self.review_button.setText(review_total(review))
        self.review_button.setVisible(bool(review))
        unmatched = unmatched_count(project, self.owner.tab.payload.loaded_sources)
        verb = "is" if unmatched == 1 else "are"
        self.show_notice(f"{count_phrase(unmatched, 'tomogram')} {verb} not in the loaded folders. Find moved folder…" if unmatched else "")
        self.move_button.setVisible(bool(unmatched))
        path = self.session.store.path
        self.project_file.setText(f"Project file · {path}")
        self.project_file.setToolTip(str(path))
        self.show_saved()
        self.sync_export()
        self.apply_find()

    def show_saved(self) -> None:
        state = self.session.save_state
        key = state.status, state.revision
        if key != self.saved_key:
            self.saved_key = key
            when = self.session.store.last_saved() if state.status == SaveStatus.SAVED else None
            self.saved.setText(saved_text(state.status.value, when, datetime.now()))

    def apply_find(self) -> None:
        wanted = self.find.text().strip().casefold()
        for index in range(self.table.rowCount()):
            self.table.setRowHidden(index, wanted not in self.table.item(index, 0).text().casefold())
        self.refresh_actions()

    def refresh_actions(self) -> None:
        selected = len(self.selected_ids())
        rows = self.rows
        shown = sum(not self.table.isRowHidden(index) for index in range(self.table.rowCount()))
        self.selection_count.setText(selection_text(shown, len(rows), selected))
        self.rename_button.setEnabled(selected == 1)
        self.rename_button.setToolTip("" if selected == 1 else "Select one tag to rename it.")
        self.merge_button.setEnabled(selected in (1, 2) and len(rows) >= 2)
        self.merge_button.setToolTip("" if self.merge_button.isEnabled() else "Select one or two tags to merge them." if len(rows) >= 2 else "A merge needs two tags.")
        self.remove_button.setEnabled(selected >= 1)
        self.remove_button.setToolTip("" if selected else "Select a tag to remove it.")

    def show_notice(self, message: str) -> None:
        self.notice.setText(message)
        self.notice.setVisible(bool(message))

    def sync_export(self) -> None:
        project = self.session.project
        if project is None:
            return
        content = self.owner.export_feedback.get(ProjectKey.at(self.session.store.path, project))
        self.export_result.setText(
            "\n".join((f"{content.title}. {content.consequence}", *content.items[:1])) if content else ""
        )
        self.export_result.setVisible(content is not None)

    def selected_ids(self) -> list[str]:
        """The chosen tags in table order. A row that Find hides is not chosen,
        so no action reaches a tag that is out of sight."""

        indexes = sorted({index.row() for index in self.table.selectionModel().selectedRows()})
        return [self.table.item(i, 0).data(Qt.ItemDataRole.UserRole) for i in indexes if not self.table.isRowHidden(i)]

    def apply(self, project, remapped=None) -> None:
        try:
            self.session.edit(project)
            self.owner.filters.refresh(remapped)
            self.owner.changed()
            self.refresh()
        except ValueError as exc:
            self.show_notice(str(exc))

    def rename(self) -> None:
        selected, project = self.selected_ids(), self.session.project
        if len(selected) != 1 or not project:
            return
        identity = selected[0]
        tag = next(t for t in project.catalogue if t.id == identity)
        answer = message_sheet.request_text(self, MessageContent("Rename tag", "The tag keeps its observations and captured views."),
            label="Tag name", value=tag.name, accept_label="Rename")
        if isinstance(answer, TextAccepted):
            label = answer.value.strip().casefold()
            clash = next((t for t in self.session.project.catalogue if t.id != identity and t.name.casefold() == label), None)
            if clash is not None:
                self.offer_merge(identity, clash)
                return
            try:
                self.apply(rename(self.session.project, identity, answer.value))
            except ValueError as exc:
                self.show_notice(str(exc))

    def offer_merge(self, identity: str, clash) -> None:
        answer = message_sheet.choose_action(self, ActionPrompt(
            MessageContent(f"A tag named “{clash.name}” already exists.", "Merge the two tags to keep one name. You review the counts before anything changes."),
            (MessageAction("cancel", "Cancel"), MessageAction("merge", "Review merge…", ActionRole.PRIMARY)), "cancel", "cancel"))
        if answer == "merge":
            self.review_merge(identity, clash.id)

    def remove(self) -> None:
        identities, project = self.selected_ids(), self.session.project
        if not identities or not project:
            return
        chosen = set(identities)
        total = sum(any(a.tag_id in chosen for a in r.tags) for r in project.tomograms)
        if len(identities) == 1:
            tag = next(t for t in project.catalogue if t.id == identities[0])
            title, detail = f"Remove “{tag.name}”", "Its tag assignments will be removed."
        else:
            title, detail = f"Remove {len(identities)} tags", "Their tag assignments will be removed."
        label = "Remove tag" if len(identities) == 1 else "Remove tags"
        answer = message_sheet.choose_action(self, ActionPrompt(
            MessageContent(f"{title} from {count_phrase(total, 'tomogram')}?", f"{detail} Images and capture history are kept."),
            (MessageAction("cancel", "Cancel"), MessageAction("remove", label, ActionRole.DESTRUCTIVE)), "cancel", "cancel"))
        if answer == "remove":
            revised = self.session.project
            for identity in identities:
                revised = remove_tag(revised, identity)
            self.apply(revised)

    def merge(self) -> None:
        selected, project = self.selected_ids(), self.session.project
        if project is None or len(project.catalogue) < 2 or len(selected) not in (1, 2):
            return
        if len(selected) == 1:
            self.review_merge(selected[0], None)
            return
        self.review_merge(*merge_order(tuple(row for row in self.rows if row.tag_id in selected)))

    def review_merge(self, removed: str, surviving: str | None) -> None:
        answer = merge_dialog.review_merge(self, self.session, removed, surviving)
        if isinstance(answer, MergeCommitted):
            self.owner.filters.refresh({answer.removed_id: answer.surviving_id})
            self.owner.changed()
            self.refresh()

    def change_project(self) -> None:
        menu = QMenu(self)
        menu.addAction("Open project…", lambda: self.leave_for(self.owner.project_dialog))
        menu.addAction("Save As…", lambda: self.leave_for(self.owner.save_as))
        menu.exec(self.change.mapToGlobal(self.change.rect().bottomLeft()))

    def leave_for(self, action) -> None:
        # This dialog holds one project's session, so it closes before that changes.
        self.reject()
        QTimer.singleShot(0, self.owner, lambda: action())

    def move(self) -> None:
        project = self.session.project
        if not project:
            return
        loaded = self.owner.tab.payload.loaded_sources
        missing = [s for s in project.sources if not any(s.path == f.path for f in loaded)]
        available = [f for f in loaded if self.owner.projects.session_for(f.path) is None
                     and binding.tag_source(project, f.path) is None]
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

    def review_sources(self) -> None:
        from tomography_session_browser.ui.tomograms.tags.change_review import ChangeReviewDialog

        dialog = ChangeReviewDialog(self)
        try:
            dialog.exec()
        finally:
            dialog.deleteLater()
