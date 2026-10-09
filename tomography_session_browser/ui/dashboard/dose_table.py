"""Dose table components for the dashboard."""
from __future__ import annotations
from PySide6.QtCore import QAbstractTableModel, QModelIndex, Qt
from PySide6.QtWidgets import QWidget
from tomography_session_browser.presentation.dashboard.models import DoseInformationPointModel
from tomography_session_browser.presentation.dashboard.sections.metrics import dose_information_point_tooltip
from tomography_session_browser.ui.dashboard.common import format_dose


class DoseInformationTableModel(QAbstractTableModel):
    """Virtual detail table for dose points, created only when the card exists."""

    _HEADERS = ("Tilt series", "Frame", "Dose e⁻/Å²", "Tilt", "Acquisition", "Source")

    def __init__(self, rows: list[DoseInformationPointModel], parent: QWidget | None = None) -> None:
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
            return dose_information_point_tooltip(row)
        if role == Qt.ItemDataRole.TextAlignmentRole and column in {1, 2, 3}:
            return Qt.AlignmentFlag.AlignCenter
        if role != Qt.ItemDataRole.DisplayRole:
            return None
        if column == 0:
            return row.tilt_series_name
        if column == 1:
            return f"{row.frame_index}/{row.frame_count or '?'}"
        if column == 2:
            return format_dose(row.dose_e_per_angstrom2)
        if column == 3:
            return "n/a" if row.tilt_angle is None else f"{row.tilt_angle:g}"
        if column == 4:
            if row.acquisition_time is not None:
                return row.acquisition_time.isoformat(sep=" ", timespec="seconds")
            return f"Frame order {row.frame_order}"
        if column == 5:
            return row.metadata_source
        return None

