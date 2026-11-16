"""Explicit per-row name decisions. Merely opening this dialog never links data.

Parent tilt series and alignment jobs each get a section, shown only when it
has rows. ``decisions_requested`` carries ``(parent, alignment)`` records,
because each kind is saved in its own store.
"""
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QDialog, QHeaderView, QHBoxLayout, QLabel, QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout

from tomography_session_browser.presentation.tomogram_link_review import NameLinkRow
from tomography_session_browser.ui.context.fields import section

#: Per kind: section title, then (column title, row text) after Choose.
COLUMNS = {
    'parent': ('Parent tilt series', (('Tomogram', lambda row: row.tomogram), ('Proposed tilt series', lambda row: row.target))),
    'alignment': ('Alignment job', (('Tomogram', lambda row: row.tomogram), ('Alignment job', lambda row: row.target),
                                    ('Not compared', lambda row: row.not_compared))),
}


class NameLinkReview(QDialog):
    decisions_requested = Signal(object, object)

    def __init__(self, rows: tuple[NameLinkRow, ...], parent=None):
        super().__init__(parent)
        self.rows = rows
        self.setWindowTitle('Review name-only links')
        self.resize(900, 600)
        layout = QVBoxLayout(self)
        label = QLabel('Only the names agree. Check each pair before you link it.', self)
        label.setWordWrap(True)
        layout.addWidget(label)
        self.tables: list[tuple[tuple[NameLinkRow, ...], QTableWidget]] = []
        for kind, (title, columns) in COLUMNS.items():
            kind_rows = tuple(row for row in rows if row.kind == kind)
            if kind_rows:
                layout.addWidget(section(f'{title} · {len(kind_rows)}', self))
                table = self.table_for(kind_rows, columns)
                layout.addWidget(table, 1)
                self.tables.append((kind_rows, table))
        # The first section keeps its historical name for callers and tests.
        self.table = self.tables[0][1] if self.tables else self.table_for((), COLUMNS['parent'][1])
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
        for _rows, table in self.tables:
            table.itemChanged.connect(self.selection_changed)
        self.selection_changed()

    def table_for(self, rows, columns) -> QTableWidget:
        table = QTableWidget(len(rows), len(columns) + 2, self)
        table.setHorizontalHeaderLabels(('Choose', *(title for title, _text in columns), 'Decision'))
        table.verticalHeader().hide()
        table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        for index, row in enumerate(rows):
            choose = QTableWidgetItem()
            choose.setCheckState(Qt.CheckState.Checked if row.decision is None else Qt.CheckState.Unchecked)
            table.setItem(index, 0, choose)
            texts = (*(text(row) for _title, text in columns), row.decision or 'Not confirmed')
            for column, text in enumerate(texts, 1):
                cell = QTableWidgetItem(text)
                detail = row.target_detail if column == 2 and row.target_detail else text
                cell.setToolTip(detail)
                cell.setData(Qt.ItemDataRole.AccessibleDescriptionRole, detail)
                table.setItem(index, column, cell)
        header = table.horizontalHeader()
        last = len(columns) + 1
        for column in range(last + 1):
            mode = QHeaderView.ResizeMode.ResizeToContents if column in (0, last) else QHeaderView.ResizeMode.Stretch
            header.setSectionResizeMode(column, mode)
        return table

    def selected(self):
        return tuple(row for rows, table in self.tables for index, row in enumerate(rows)
                     if table.item(index, 0).checkState() == Qt.CheckState.Checked)

    def selection_changed(self):
        link = sum(row.decision != 'confirmed' for row in self.selected())
        unlink = sum(row.decision == 'confirmed' for row in self.selected())
        self.link.setText(f'Link {link} by name')
        self.unlink.setText(f'Unlink {unlink}')
        self.link.setEnabled(bool(link))
        self.unlink.setEnabled(bool(unlink))

    def send(self, rows, decision):
        if rows:
            self.set_busy(True)
            self.decisions_requested.emit(*(tuple(row.record(decision) for row in rows if row.kind == kind)
                                            for kind in COLUMNS))
        return bool(rows)

    def submit(self, decision):
        self.send(tuple(row for row in self.selected() if (row.decision == 'confirmed') == (decision == 'revoked')), decision)

    def dismiss(self):
        if not self.send(tuple(row for row in self.rows if row.decision is None), 'dismissed'):
            self.reject()

    def set_busy(self, busy):
        for _rows, table in self.tables:
            table.setEnabled(not busy)
        for button in (self.link, self.unlink, self.later):
            button.setEnabled(not busy)
        self.feedback.setText('Saving name-link decisions…' if busy else '')
        if not busy:
            self.selection_changed()
