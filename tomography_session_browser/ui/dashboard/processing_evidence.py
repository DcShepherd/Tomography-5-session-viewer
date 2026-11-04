"""Folded, sortable evidence tables with canonical row-key routes."""
from __future__ import annotations
from dataclasses import dataclass
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QAbstractItemView, QHeaderView, QTableWidget, QTableWidgetItem, QWidget

@dataclass(frozen=True, slots=True)
class TableEntry:
    values: tuple[str, ...]
    row_keys: frozenset[str]
    detail: str
    sort_values: tuple[tuple[str, float] | str | None, ...] = ()


class RecordedCell(QTableWidgetItem):
    def __init__(self, text: str, value: tuple[str, float] | str | None) -> None:
        super().__init__(text)
        self.sort_value = (1, "", 0.) if value is None else (0, value[0], value[1]) if isinstance(value, tuple) else (0, value.casefold(), 0.)

    def __lt__(self, other: QTableWidgetItem) -> bool:
        return self.sort_value < other.sort_value if isinstance(other, RecordedCell) else super().__lt__(other)


class EvidenceTable(QTableWidget):
    rows_requested = Signal(object)

    def __init__(self, headers: tuple[str, ...], rows: tuple[TableEntry, ...], parent: QWidget) -> None:
        super().__init__(len(rows), len(headers), parent)
        self.setHorizontalHeaderLabels(headers)
        self.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.setWordWrap(False)
        self.setAlternatingRowColors(True)
        self.verticalHeader().hide()
        self.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self.horizontalHeader().setStretchLastSection(True)
        self.setMinimumHeight(150)
        self.setMaximumHeight(340)
        for index, row in enumerate(rows):
            for column, text in enumerate(row.values):
                value = row.sort_values[column] if row.sort_values else text
                cell = RecordedCell(text, value)
                cell.setData(Qt.ItemDataRole.UserRole, row.row_keys)
                cell.setToolTip(row.detail)
                cell.setData(Qt.ItemDataRole.AccessibleDescriptionRole, row.detail)
                self.setItem(index, column, cell)
        self.setSortingEnabled(True)
        self.sortItems(0, Qt.SortOrder.AscendingOrder)
        self.cellActivated.connect(self.activate_row)

    def activate_row(self, row: int, column: int) -> None:
        cell = self.item(row, column)
        if cell is not None:
            self.rows_requested.emit(cell.data(Qt.ItemDataRole.UserRole))

