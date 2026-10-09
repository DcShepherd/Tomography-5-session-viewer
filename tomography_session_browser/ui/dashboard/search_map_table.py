"""Search map table components for the dashboard."""
from __future__ import annotations
from PySide6.QtCore import QAbstractTableModel, QModelIndex, Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QWidget
from tomography_session_browser.presentation.dashboard.models import SearchMapOverviewRowModel
from tomography_session_browser.ui.dashboard.common import search_map_overview_tooltip, search_map_status_label, status_color


class SearchMapOverviewTableModel(QAbstractTableModel):
    """Virtual table model for large search-map overview lists."""

    _HEADERS = ("Search map", "Status", "Batch positions")

    def __init__(self, rows: list[SearchMapOverviewRowModel], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._rows = list(rows)

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:  # noqa: N802
        return 0 if parent.isValid() else len(self._rows)

    def columnCount(self, parent: QModelIndex = QModelIndex()) -> int:  # noqa: N802
        return 0 if parent.isValid() else len(self._HEADERS)

    def headerData(self, section: int, orientation: Qt.Orientation, role: int = Qt.ItemDataRole.DisplayRole):  # noqa: N802
        if orientation == Qt.Orientation.Horizontal and role == Qt.ItemDataRole.DisplayRole:
            return self._HEADERS[section]
        return None

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        row = self._rows[index.row()]
        column = index.column()
        if role == Qt.ItemDataRole.ToolTipRole:
            return search_map_overview_tooltip(row)
        if role == Qt.ItemDataRole.TextAlignmentRole and column != 0:
            return Qt.AlignmentFlag.AlignCenter
        if role == Qt.ItemDataRole.ForegroundRole:
            return QColor(status_color(row.status))
        if role != Qt.ItemDataRole.DisplayRole:
            return None
        if column == 0:
            return row.label
        if column == 1:
            return search_map_status_label(row)
        if column == 2:
            return row.summary
        return None

    def row(self, index: int) -> SearchMapOverviewRowModel | None:
        if 0 <= index < len(self._rows):
            return self._rows[index]
        return None

    def sort(self, column: int, order: Qt.SortOrder = Qt.SortOrder.AscendingOrder) -> None:  # noqa: N802
        reverse = order == Qt.SortOrder.DescendingOrder
        key_funcs = {
            0: lambda row: row.label.casefold(),
            1: lambda row: search_map_status_label(row),
            2: lambda row: (
                row.acquired_batch_positions,
                row.batch_positions,
            ) if row.acquisition_associated else (-1, -1),
        }
        self.layoutAboutToBeChanged.emit()
        self._rows.sort(key=key_funcs.get(column, key_funcs[0]), reverse=reverse)
        # Keep non-acquisition maps at the bottom even when users sort table
        # columns. They are contextual records, not completion failures.
        self._rows.sort(key=lambda row: not row.acquisition_associated)
        self.layoutChanged.emit()

