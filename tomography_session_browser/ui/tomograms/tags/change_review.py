"""Project-wide review with an explicit selection and one acceptance edit."""
from PySide6.QtCore import QAbstractTableModel, QModelIndex, Qt, QTimer
from PySide6.QtWidgets import QDialog, QHBoxLayout, QHeaderView, QLabel, QPushButton, QTableView, QVBoxLayout

from tomography_session_browser.domain.display_names import count_phrase
from tomography_session_browser.domain.tomogram_tags import ChangeKind
from tomography_session_browser.presentation.help_content import InteractionKind, LocalBinding
from tomography_session_browser.presentation.tomogram_tag_changes import change_review, issue_text, show_action, volume_lines
from tomography_session_browser.services.tomogram_tags.changes import worst_kind
from tomography_session_browser.ui.widgets.literal_text import literal_tooltip
from tomography_session_browser.ui.tomograms.bulk_pixel_dialog import BulkPixelDialog


LOCAL_KEYS = (
    LocalBinding("tags.review.toggle", "Tag source review", InteractionKind.KEYBOARD, ("Space",),
                 "Tick or clear the current tomogram or group"),
)


class ChangeReviewModel(QAbstractTableModel):
    def __init__(self, parent, proposal, *, editable: bool, select_dates: bool):
        super().__init__(parent)
        self.lines = []
        self.chosen = set()
        multi_folder = len({row.folder_path for row in proposal.rows}) > 1 if proposal else False
        for title, rows in change_review(proposal) if proposal else ():
            eligible = frozenset(row.key for row in rows if editable and row.issue is None)
            self.lines.append((title, "", eligible))
            for row in rows:
                keys = frozenset({row.key}) & eligible
                name = f"{row.row_name} · {row.folder_name}" if multi_folder else row.row_name
                detail = " ".join((*volume_lines(row.saved, row.changes), issue_text(row))).strip()
                self.lines.append((name, detail, keys))
                if select_dates and worst_kind(row.changes) is ChangeKind.DATE:
                    self.chosen.update(keys)

    def rowCount(self, parent=QModelIndex()):  # noqa: N802
        return 0 if parent.isValid() else len(self.lines)

    def columnCount(self, parent=QModelIndex()):  # noqa: N802
        return 0 if parent.isValid() else 2

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):  # noqa: N802
        if orientation == Qt.Orientation.Horizontal and role == Qt.ItemDataRole.DisplayRole:
            return ("Tomogram", "Recorded and current files")[section]
        return None

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        name, detail, keys = self.lines[index.row()]
        if role == Qt.ItemDataRole.DisplayRole:
            return name if index.column() == 0 else detail
        if role == Qt.ItemDataRole.ToolTipRole:
            return literal_tooltip(name if index.column() == 0 else detail)
        if role == Qt.ItemDataRole.CheckStateRole and index.column() == 0 and keys:
            selected = keys & self.chosen
            if selected == keys:
                return Qt.CheckState.Checked
            return Qt.CheckState.PartiallyChecked if selected else Qt.CheckState.Unchecked
        return None

    def flags(self, index):
        flags = super().flags(index)
        if index.isValid() and index.column() == 0 and self.lines[index.row()][2]:
            flags |= Qt.ItemFlag.ItemIsUserCheckable
        return flags

    def setData(self, index, value, role=Qt.ItemDataRole.EditRole):  # noqa: N802
        if role != Qt.ItemDataRole.CheckStateRole or not index.isValid() or index.column() != 0:
            return False
        keys = self.lines[index.row()][2]
        if not keys:
            return False
        if value in (Qt.CheckState.Checked, Qt.CheckState.Checked.value):
            self.chosen.update(keys)
        else:
            self.chosen.difference_update(keys)
        self.dataChanged.emit(self.index(0, 0), self.index(len(self.lines) - 1, 0), [Qt.ItemDataRole.CheckStateRole])
        return True


class ChangeReviewTable(QTableView):
    def keyPressEvent(self, event):  # noqa: N802
        if event.key() == Qt.Key.Key_Space and self.currentIndex().isValid():
            index = self.currentIndex().siblingAtColumn(0)
            state = index.data(Qt.ItemDataRole.CheckStateRole)
            if state is not None:
                new = Qt.CheckState.Unchecked if state == Qt.CheckState.Checked else Qt.CheckState.Checked
                self.model().setData(index, new, Qt.ItemDataRole.CheckStateRole)
                return
        super().keyPressEvent(event)


class ChangeReviewDialog(QDialog):
    def __init__(self, manager):
        super().__init__(manager)
        self.manager = manager
        self.owner, self.session = manager.owner, manager.session
        self.setWindowTitle("Review tag sources")
        self.resize(780, 480)
        layout = QVBoxLayout(self)
        intro = QLabel("Accepting records the current files for the ticked tomograms. "
                       "Tags, counts, notes, captures and source files stay as they are.", self)
        intro.setWordWrap(True)
        layout.addWidget(intro)
        self.reason, self.caution = QLabel(self), QLabel(self)
        for label in (self.reason, self.caution):
            label.setTextFormat(Qt.TextFormat.PlainText)
            label.setWordWrap(True)
            layout.addWidget(label)
        self.table = ChangeReviewTable(self)
        self.table.setAccessibleName("Tomograms with tag source differences")
        self.table.verticalHeader().hide()
        layout.addWidget(self.table, 1)
        actions = QHBoxLayout()
        self.show_button = self._button(actions, "Show these", self.show_rows)
        actions.addStretch(1)
        self._button(actions, "Cancel", self.reject)
        self.accept_button = self._button(actions, "Accept current files", self.apply)
        layout.addLayout(actions)
        self.rebuild()

    def _button(self, layout, title, action):
        button = QPushButton(title, self)
        button.setAutoDefault(False)
        button.setDefault(False)
        button.clicked.connect(action)
        layout.addWidget(button)
        return button

    def rebuild(self, refusal=""):
        self.proposal = self.owner.change_proposal(self.session)
        editable = self.session.editable and not self.owner.projects.stopping
        reason = self.session.reason or "This project is not editable."
        self.reason.setText("\n".join(filter(None, (
            refusal, "Selection cleared. Select the tomograms again." if refusal else "", reason if not editable else ""))))
        self.reason.setVisible(bool(self.reason.text()))
        previous = self.table.model()
        self.model = ChangeReviewModel(self, self.proposal, editable=editable, select_dates=not refusal)
        self.table.setModel(self.model)
        if previous is not None:
            previous.deleteLater()
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.table.setColumnWidth(0, 245)
        self.model.dataChanged.connect(self.refresh_actions)
        self.refresh_actions()

    def route(self):
        in_view = frozenset(row.tomogram.id for row in self.owner.tab.payload.rows)
        return show_action(self.proposal, in_view) if self.proposal else None

    def refresh_actions(self, *_args):
        chosen = self.model.chosen
        label = f"Accept {count_phrase(len(chosen), 'tomogram')}" if chosen else "Accept"
        BulkPixelDialog.set_action(self.accept_button, label, bool(chosen), bool(chosen),
                                   "Record the files for the ticked tomograms." if chosen else "Tick at least one tomogram.")
        action = self.route()
        self.show_button.setEnabled(bool(action and action.keys))
        count = len(action.keys) if action else 0
        if not count:
            self.show_button.setText("Show these in the list")
        elif self.proposal is not None and count == len(self.proposal.rows):
            self.show_button.setText(f"Show these {count} in the list")
        else:
            self.show_button.setText(f"Show the {count} in this view")
        self.show_button.setToolTip(
            "Show only these tomograms. This replaces the current filter. Clear shows all tomograms again."
            if count else "These tomograms are in other loaded folders. Choose their folder first.")
        amber = self.proposal and any(row.key in chosen and worst_kind(row.changes) is not ChangeKind.DATE
                                      for row in self.proposal.rows)
        self.caution.setText("Check counts recorded on the affected versions before accepting." if amber else
                             "Equal size and geometry do not prove identical voxels.")

    def apply(self):
        if self.proposal is None or not self.model.chosen:
            return
        refusal = self.owner.accept_changes(self.session, self.proposal, None, frozenset(self.model.chosen))
        if refusal:
            self.rebuild(refusal)
        else:
            self.accept()

    def show_rows(self):
        action = self.route()
        if action is None or not action.keys:
            return
        tab = self.owner.tab
        self.reject()
        self.manager.reject()
        QTimer.singleShot(0, tab, lambda: tab.set_query("", row_keys=action.keys, scope_label=action.scope_label))
