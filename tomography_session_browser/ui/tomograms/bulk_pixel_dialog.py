"""Review one pixel size for several versions. Opening it applies nothing.

The list freezes what the user reviewed. Apply checks every file again first: if
one changed, nothing is applied and the user reviews the updated list. The rules
live in ``presentation/tomogram_calibration.py``; this module only shows them.
"""
from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
import logging
from typing import TYPE_CHECKING

from PySide6.QtCore import QAbstractTableModel, QModelIndex, QObject, Qt, Slot
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import (QAbstractItemView, QDialog, QDoubleSpinBox, QFrame, QHBoxLayout, QHeaderView, QLabel,
                               QPushButton, QSizePolicy, QTableView, QVBoxLayout)

from tomography_session_browser.domain.tomograms import Volume
from tomography_session_browser.presentation.design_tokens import SPACE_M, SPACE_S, readable_tone
from tomography_session_browser.presentation.tomogram_calibration import (BulkReview, OfferedVersion, bulk_actions, changed_notice,
                                                                          changed_on_disk, not_offered_reason)
from tomography_session_browser.services.volume.pixel_sizes import signature_of
from tomography_session_browser.ui.loading.tasks import BackgroundTask
from tomography_session_browser.ui.theme import current_palette
from tomography_session_browser.ui.widgets.section_header import SectionHeader

if TYPE_CHECKING:
    from tomography_session_browser.ui.tomograms.pixel_sizes import PixelSizeOwner

HEADERS = ("", "Tomogram", "Version", "Voxels (X×Y×Z)", "In name", "Header")
TICK, TOMOGRAM, VERSION, VOXELS, NAMED, HEADER = range(6)
#: Sets a column's width, so the columns stay still whatever the list holds.
COLUMN_SAMPLES = {VERSION: "Deconvolved", VOXELS: "Voxels (X×Y×Z)", NAMED: "Not given", HEADER: "1.0 Å, suspicious"}


class BulkPixelModel(QAbstractTableModel):
    """Virtual rows, so several hundred versions stay responsive."""

    def __init__(self, parent: QObject) -> None:
        super().__init__(parent)
        self.rows: tuple[OfferedVersion, ...] = ()
        self.ticked: list[bool] = []
        self.disagreeing: list[bool] = []
        self.chosen: dict[str, bool] = {}  # A tick the user set by hand; it outlives a value or list change.
        self.value = 1.

    def install(self, rows: tuple[OfferedVersion, ...], value: float) -> None:
        self.beginResetModel()
        self.rows, self.value = rows, value
        self.evaluate()
        self.endResetModel()

    def evaluate(self) -> None:
        self.disagreeing = [row.disagrees(self.value) for row in self.rows]
        # A version whose file name disagrees starts unticked, and follows the value until the user ticks it.
        self.ticked = [self.chosen.get(row.volume.id, not disagrees)
                       for row, disagrees in zip(self.rows, self.disagreeing, strict=True)]

    def set_value(self, value: float) -> None:
        self.value = value
        self.evaluate()
        if self.rows:
            self.dataChanged.emit(self.index(0, TICK), self.index(len(self.rows) - 1, NAMED))

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:  # noqa: N802
        return 0 if parent.isValid() else len(self.rows)

    def columnCount(self, parent: QModelIndex = QModelIndex()) -> int:  # noqa: N802
        return 0 if parent.isValid() else len(HEADERS)

    def headerData(self, section: int, orientation: Qt.Orientation, role: int = Qt.ItemDataRole.DisplayRole):  # noqa: N802
        if orientation == Qt.Orientation.Horizontal:
            if role == Qt.ItemDataRole.DisplayRole:
                return HEADERS[section]
            if role == Qt.ItemDataRole.TextAlignmentRole:
                return Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
        return None

    def flags(self, index: QModelIndex) -> Qt.ItemFlag:
        flags = Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable
        return flags | Qt.ItemFlag.ItemIsUserCheckable if index.column() == TICK else flags

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        row, column = self.rows[index.row()], index.column()
        disagrees = column == NAMED and self.disagreeing[index.row()]
        if role == Qt.ItemDataRole.CheckStateRole and column == TICK:
            return Qt.CheckState.Checked if self.ticked[index.row()] else Qt.CheckState.Unchecked
        if role == Qt.ItemDataRole.AccessibleTextRole and column == TICK:
            return f"Include {row.label}"
        if role == Qt.ItemDataRole.ToolTipRole:
            if disagrees:
                return f"The file name records {row.named:.2f} Å. The value in the field is {self.value:.3f} Å."
            return str(row.volume.path)
        if role == Qt.ItemDataRole.ForegroundRole and disagrees:
            palette = current_palette()
            return QColor(readable_tone(palette.chart_amber, palette.surface, palette.text_strong))
        if role == Qt.ItemDataRole.FontRole and disagrees:
            font = QFont()
            font.setWeight(QFont.Weight.DemiBold)
            return font
        if role != Qt.ItemDataRole.DisplayRole:
            return None
        if column == TOMOGRAM:
            return f"{row.tomogram} · {row.source}" if row.source else row.tomogram
        if column == VERSION:
            return row.volume.version
        if column == VOXELS:
            return row.voxels
        if column == NAMED:
            return f"{row.named:.2f} Å" if row.named is not None else "Not given"
        return row.header if column == HEADER else None

    def setData(self, index: QModelIndex, value: object, role: int = Qt.ItemDataRole.EditRole) -> bool:  # noqa: N802
        if role != Qt.ItemDataRole.CheckStateRole or index.column() != TICK or not index.isValid():
            return False
        ticked = Qt.CheckState(value) == Qt.CheckState.Checked  # type: ignore[arg-type]
        self.ticked[index.row()] = ticked
        self.chosen[self.rows[index.row()].volume.id] = ticked
        self.dataChanged.emit(index, index, [Qt.ItemDataRole.CheckStateRole])
        return True

    def counts(self) -> tuple[int, int, int, int]:
        ticked = sum(self.ticked)
        disagreeing = sum(self.disagreeing)
        both = sum(tick and disagrees for tick, disagrees in zip(self.ticked, self.disagreeing, strict=True))
        return len(self.rows), ticked, disagreeing, both


@dataclass(frozen=True, slots=True, eq=False)
class FileCheck:
    """One Apply: the versions and value the user reviewed, frozen when they clicked."""
    versions: tuple[OfferedVersion, ...]
    value: float


class BulkPixelDialog(QDialog):
    def __init__(self, owner: PixelSizeOwner, value: float) -> None:
        super().__init__(owner.tab)
        self.owner = owner
        self.check: FileCheck | None = None
        self.setObjectName("bulkPixelDialog")
        self.setWindowTitle("Apply one pixel size")
        self.setAccessibleName("Apply one pixel size to several versions")
        self.setWindowModality(Qt.WindowModality.WindowModal)
        self.resize(860, 620)
        self.setMinimumSize(560, 420)
        column = QVBoxLayout(self)
        column.setContentsMargins(SPACE_M, SPACE_M, SPACE_M, SPACE_M)
        column.setSpacing(SPACE_S)
        title = QLabel("Apply one pixel size to several versions", self)
        title.setObjectName("messageSheetTitle")
        column.addWidget(title)
        note = QLabel("Use one value only if these versions have the same voxel size. The app cannot check this. "
                      "The MRC files do not change.", self)
        note.setWordWrap(True)
        column.addWidget(note)
        row = QHBoxLayout()
        row.setSpacing(SPACE_S)
        label = QLabel("Pixel size", self)
        self.spacing = QDoubleSpinBox(self)
        self.spacing.setRange(.001, 100000)
        self.spacing.setDecimals(3)
        self.spacing.setButtonSymbols(QDoubleSpinBox.ButtonSymbols.NoButtons)
        self.spacing.setAccessibleName("Pixel size in ångström")
        self.spacing.setFixedWidth(90)
        self.spacing.setValue(value)
        label.setBuddy(self.spacing)
        self.count = QLabel(self)
        self.count.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        row.addWidget(label)
        row.addWidget(self.spacing)
        row.addWidget(QLabel("Å/voxel", self))
        row.addStretch(1)
        row.addWidget(self.count)
        column.addLayout(row)
        self.model = BulkPixelModel(self)
        self.table = QTableView(self)
        self.table.setObjectName("bulkPixelTable")
        self.table.setAccessibleName("Versions that need a pixel size")
        self.table.setModel(self.model)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)  # Ticks choose the rows.
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.table.setTextElideMode(Qt.TextElideMode.ElideMiddle)
        self.table.setTabKeyNavigation(False)
        self.table.setShowGrid(False)
        self.table.setWordWrap(False)
        self.table.setAlternatingRowColors(True)
        self.table.verticalHeader().hide()
        self.table.verticalHeader().setDefaultSectionSize(self.fontMetrics().height() + 12)
        self.table.verticalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Fixed)
        header = self.table.horizontalHeader()
        header.setHighlightSections(False)
        header.setSectionResizeMode(TICK, QHeaderView.ResizeMode.Fixed)
        header.setSectionResizeMode(TOMOGRAM, QHeaderView.ResizeMode.Stretch)
        header.resizeSection(TICK, 36)
        for section, sample in COLUMN_SAMPLES.items():
            header.setSectionResizeMode(section, QHeaderView.ResizeMode.Interactive)
            header.resizeSection(section, self.fontMetrics().horizontalAdvance(sample) + 2 * SPACE_M)
        # Space ticks the row from any column, because only the first column holds the tick.
        self.table.selectionModel().currentChanged.connect(self.keep_tick_current)
        column.addWidget(self.table, 1)
        self.not_offered = SectionHeader("Not offered", self)
        self.not_offered_text = QLabel(self)
        self.not_offered_text.setWordWrap(True)
        self.not_offered_text.hide()
        self.not_offered.toggled.connect(self.not_offered_text.setVisible)
        column.addWidget(self.not_offered, 0, Qt.AlignmentFlag.AlignLeft)
        column.addWidget(self.not_offered_text)
        self.notice = QFrame(self)
        self.notice.setObjectName("inlineNotice")
        notice_column = QVBoxLayout(self.notice)
        heading = QLabel("Nothing was applied.", self.notice)
        heading.setObjectName("inlineNoticeTitle")
        self.notice_text = QLabel(self.notice)
        self.notice_text.setWordWrap(True)
        self.notice_text.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        notice_column.addWidget(heading)
        notice_column.addWidget(self.notice_text)
        self.notice.hide()
        column.addWidget(self.notice)
        actions = QHBoxLayout()
        actions.setSpacing(SPACE_S)
        self.warning = QLabel(self)
        self.warning.setObjectName("toneText")
        self.warning.setProperty("tone", "warn")
        self.warning.setWordWrap(True)
        self.warning.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        actions.addWidget(self.warning, 1)
        self.cancel_button = self.button("Cancel", self.reject)
        self.update_button = self.button("Update the list", self.update_list)
        self.update_button.setObjectName("primaryButton")
        self.update_button.setToolTip("Show the list without the versions that changed. The app applies nothing.")
        self.update_button.hide()
        self.all_button = self.button("Apply to all", self.apply_all)
        self.selected_button = self.button("Apply to selected", self.apply_selected)
        for widget in (self.cancel_button, self.update_button, self.all_button, self.selected_button):
            actions.addWidget(widget)
        column.addLayout(actions)
        self.spacing.valueChanged.connect(self.model.set_value)
        self.model.dataChanged.connect(self.sync)
        owner.signals.finished.connect(self._check_finished)
        owner.signals.failed.connect(self._check_failed)
        self.reload()
        self.spacing.setFocus()
        self.spacing.selectAll()

    def button(self, text: str, slot) -> QPushButton:
        """Enter in the value field must not press a button: an action here changes many values."""
        button = QPushButton(text, self)
        button.setAutoDefault(False)
        button.setDefault(False)
        button.clicked.connect(lambda _checked=False: slot())
        return button

    def keep_tick_current(self, current: QModelIndex, _previous: QModelIndex) -> None:
        if current.isValid() and current.column() != TICK:
            self.table.setCurrentIndex(current.siblingAtColumn(TICK))

    def reload(self) -> None:
        review: BulkReview = self.owner.review()
        self.model.install(review.offered, self.spacing.value())
        total = review.not_offered_total
        self.not_offered.set_title(f"Not offered ({total})")
        self.not_offered_text.setText(review.not_offered_text)
        self.not_offered.setVisible(total > 0)
        self.not_offered_text.setVisible(total > 0 and self.not_offered.isChecked())
        self.sync()

    def sync(self, *_args) -> None:
        total, ticked, disagreeing, disagreeing_ticked = self.model.counts()
        actions = bulk_actions(total, ticked, disagreeing, disagreeing_ticked)
        self.count.setText(actions.count)
        self.warning.setText(actions.warning)
        self.set_action(self.all_button, actions.all_label, actions.all_primary, actions.all_enabled,
                        "Use this voxel size for every version in the list, also for versions you did not select. "
                        "The MRC files do not change." if actions.all_enabled else "No version in the list needs a value")
        self.selected_button.setVisible(actions.selected_label is not None)
        if actions.selected_label is not None:
            self.set_action(self.selected_button, actions.selected_label, actions.selected_primary, actions.selected_enabled,
                            "Use this voxel size for the selected versions. The MRC files do not change."
                            if actions.selected_enabled else "Select a version to apply")

    @staticmethod
    def set_action(button: QPushButton, text: str, primary: bool, enabled: bool, tooltip: str) -> None:
        button.setText(text)
        button.setEnabled(enabled)
        button.setToolTip(tooltip)
        name = "primaryButton" if primary else "bulkPixelOutline"
        if button.objectName() != name:
            button.setObjectName(name)
            button.style().unpolish(button)
            button.style().polish(button)

    def apply_all(self) -> None:
        self.start_check(self.model.rows)

    def apply_selected(self) -> None:
        self.start_check(tuple(row for row, ticked in zip(self.model.rows, self.model.ticked, strict=True) if ticked))

    def start_check(self, versions: tuple[OfferedVersion, ...]) -> None:
        self.spacing.interpretText()
        if not versions:
            return
        self.check = check = FileCheck(versions, self.spacing.value())
        self.set_busy(True)
        volumes = tuple(row.volume for row in versions)
        self.owner.pool.start(BackgroundTask(lambda: changed_on_disk(volumes), context=check, signals=self.owner.signals,
                                             logger=logging.getLogger(__name__), failure_log="Could not check volume files"))

    def set_busy(self, busy: bool) -> None:
        for widget in (self.spacing, self.table, self.all_button, self.selected_button):
            widget.setEnabled(not busy)
        if busy:
            self.count.setText("Checking the files…")

    @Slot(object, object)
    def _check_finished(self, check: object, changed: tuple[Volume, ...]) -> None:
        if check is not self.check or not isinstance(check, FileCheck):
            return
        self.check = None
        moved = {volume.id for volume in changed}
        self.owner.changed.update((volume.id, signature_of(volume)) for volume in changed)
        blocked = tuple(row.label for row in check.versions if row.volume.id in moved
                        or not_offered_reason(row.volume, self.owner.values.value_for(row.volume)) is not None)
        if blocked:
            self.show_notice(blocked)
            return
        self.owner.apply(tuple(row.volume for row in check.versions), check.value)
        self.accept()

    @Slot(object, str)
    def _check_failed(self, check: object, message: str) -> None:
        if check is self.check:
            self.check = None
            self.show_notice((), failure=f"The app could not check the files: {message}")

    def show_notice(self, labels: Iterable[str], *, failure: str = "") -> None:
        self.notice_text.setText(failure or changed_notice(tuple(labels)))
        self.notice.show()
        self.update_button.show()
        self.all_button.hide()
        self.selected_button.hide()
        self.warning.setText("")
        self.spacing.setEnabled(False)
        self.table.setEnabled(False)
        self.count.setText("")
        self.update_button.setFocus()

    def update_list(self) -> None:
        self.notice.hide()
        self.update_button.hide()
        self.all_button.show()
        self.set_busy(False)
        self.reload()

    def reject(self) -> None:
        # A check still running reports to nobody, so Cancel applies nothing.
        self.check = None
        super().reject()
