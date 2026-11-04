"""Explicit per-row name decisions. Merely opening this dialog never links data."""
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QDialog, QHeaderView, QHBoxLayout, QLabel, QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout

from tomography_session_browser.presentation.tomogram_link_review import NameLinkRow


class NameLinkReview(QDialog):
    decisions_requested = Signal(object)

    def __init__(self, rows: tuple[NameLinkRow, ...], parent=None):
        super().__init__(parent)
        self.rows = rows
        self.setWindowTitle('Review name-only links')
        self.resize(880, 520)
        layout = QVBoxLayout(self)
        label = QLabel('Names alone do not establish acquisition identity. Check each proposed pair before linking. '
                       'Decisions are saved in application settings; source folders remain unchanged.', self)
        label.setWordWrap(True)
        layout.addWidget(label)
        self.table = QTableWidget(len(rows), 4, self)
        self.table.setHorizontalHeaderLabels(('Choose', 'Tomogram', 'Proposed Tilt series', 'Decision'))
        self.table.verticalHeader().hide()
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        for index, row in enumerate(rows):
            choose = QTableWidgetItem()
            choose.setCheckState(Qt.CheckState.Checked if row.decision is None else Qt.CheckState.Unchecked)
            self.table.setItem(index, 0, choose)
            for column, text in enumerate((row.tomogram, row.target, row.decision or 'Not confirmed'), 1):
                cell = QTableWidgetItem(text)
                detail = row.target_detail if column == 2 else text
                cell.setToolTip(detail)
                cell.setData(Qt.ItemDataRole.AccessibleDescriptionRole, detail)
                self.table.setItem(index, column, cell)
        header = self.table.horizontalHeader()
        for column in (0, 3):
            header.setSectionResizeMode(column, QHeaderView.ResizeMode.ResizeToContents)
        for column in (1, 2):
            header.setSectionResizeMode(column, QHeaderView.ResizeMode.Stretch)
        layout.addWidget(self.table, 1)
        self.feedback = QLabel('', self)
        self.feedback.setWordWrap(True)
        layout.addWidget(self.feedback)
        buttons = QHBoxLayout()
        self.link = QPushButton('Link selected by name', self)
        self.link.setObjectName('primaryButton')
        self.unlink = QPushButton('Unlink selected', self)
        self.later = QPushButton('Not now', self)
        self.link.clicked.connect(lambda: self.submit('confirmed'))
        self.unlink.clicked.connect(lambda: self.submit('revoked'))
        self.later.clicked.connect(self.dismiss)
        for button in (self.link, self.unlink, self.later):
            buttons.addWidget(button)
        layout.addLayout(buttons)
        self.table.itemChanged.connect(self.selection_changed)
        self.selection_changed()

    def selected(self):
        return tuple(row for index, row in enumerate(self.rows)
                     if self.table.item(index, 0).checkState() == Qt.CheckState.Checked)

    def selection_changed(self):
        link = sum(row.decision != 'confirmed' for row in self.selected())
        unlink = sum(row.decision == 'confirmed' for row in self.selected())
        self.link.setText(f'Link {link} by name')
        self.unlink.setText(f'Unlink {unlink}')
        self.link.setEnabled(bool(link))
        self.unlink.setEnabled(bool(unlink))

    def submit(self, decision):
        changes = tuple(row.record(decision) for row in self.selected()
                        if (row.decision == 'confirmed') == (decision == 'revoked'))
        if changes:
            self.set_busy(True)
            self.decisions_requested.emit(changes)

    def dismiss(self):
        changes = tuple(row.record('dismissed') for row in self.rows if row.decision is None)
        if changes:
            self.set_busy(True)
            self.decisions_requested.emit(changes)
        else:
            self.reject()

    def set_busy(self, busy):
        self.table.setEnabled(not busy)
        for button in (self.link, self.unlink, self.later):
            button.setEnabled(not busy)
        self.feedback.setText('Saving name-link decisions…' if busy else '')
        if not busy:
            self.selection_changed()
