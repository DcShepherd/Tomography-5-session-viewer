"""The pop-up for a folder whose MRC files no record decides (rule 5; owner D12, D13 a).

One pop-up for each folder, after the load's pass: it names the files and asks
how to read them. No button is the default, so Enter selects nothing; Esc and
closing the window select "Do not load". The pop-up only asks: the loading part
keeps the selection in the app's settings and loads the folder again, and the
folder itself never changes.
"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QAccessible, QAccessibleAnnouncementEvent
from PySide6.QtWidgets import (
    QAbstractItemView, QDialog, QHBoxLayout, QHeaderView, QLabel, QPushButton, QSizePolicy,
    QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from tomography_session_browser.domain.folder_type import FolderQuestion, ReadAs
from tomography_session_browser.presentation.design_tokens import SPACE_L, SPACE_M, SPACE_S, SPACE_XL, SPACE_XS
from tomography_session_browser.presentation.folder_type import (
    DO_NOT_LOAD, QUESTION_BODY, QUESTION_COLUMNS, QUESTION_NOTE, file_cells, folder_chip, question_title,
)
from tomography_session_browser.services.session_intake import READ_AS_WORDS
from tomography_session_browser.ui.icons import themed_icon
from tomography_session_browser.ui.theme import current_palette
from tomography_session_browser.ui.widgets.elided_label import ElidedLabel
from tomography_session_browser.ui.widgets.session_drop import Glyph

#: Rows the file table shows before it scrolls.
VISIBLE_FILES = 6
#: The buttons from left to right: "Do not load" alone, then the two types (D12).
CHOICES: tuple[tuple[str, ReadAs], ...] = ((READ_AS_WORDS[ReadAs.TILT_SERIES].capitalize(), ReadAs.TILT_SERIES),
                                           (READ_AS_WORDS[ReadAs.TOMOGRAMS].capitalize(), ReadAs.TOMOGRAMS))


class FolderTypeDialog(QDialog):
    """Asks how to read one folder's undecided MRC files; ``selection`` is the answer (None: do not load)."""

    def __init__(self, question: FolderQuestion, position: tuple[int, int] = (1, 1),
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.question = question
        self.selection: ReadAs | None = None
        title = question_title(question)
        self.setObjectName("folderTypeDialog")
        self.setWindowTitle(title)
        self.setAccessibleName(title)
        self.setAccessibleDescription(QUESTION_BODY)
        self.setWindowModality(Qt.WindowModality.WindowModal)
        self.setMinimumWidth(520)
        self.resize(640, 10)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(SPACE_XL, SPACE_XL, SPACE_XL, SPACE_XL)
        layout.setSpacing(SPACE_L)

        header = QHBoxLayout()
        header.setSpacing(SPACE_M)
        emblem = QLabel(self)
        emblem.setObjectName("folderTypeEmblem")
        emblem.setFixedSize(40, 40)
        emblem.setAlignment(Qt.AlignmentFlag.AlignCenter)
        emblem.setPixmap(themed_icon("file-question", color=current_palette().accent, size=20).pixmap(20, 20))
        header.addWidget(emblem, 0, Qt.AlignmentFlag.AlignTop)
        titles = QVBoxLayout()
        titles.setSpacing(SPACE_XS)
        self.title = QLabel(title, self)
        self.title.setObjectName("folderTypeTitle")
        self.title.setTextFormat(Qt.TextFormat.PlainText)
        titles.addWidget(self.title)
        self.folder = ElidedLabel(question.path.name, mode=Qt.TextElideMode.ElideMiddle, parent=self)
        self.folder.setObjectName("folderTypeFolder")
        titles.addWidget(self.folder)
        self.path = ElidedLabel(str(question.path), mode=Qt.TextElideMode.ElideMiddle, parent=self)
        self.path.setObjectName("folderTypePath")
        self.path.setToolTip(str(question.path))
        titles.addWidget(self.path)
        header.addLayout(titles, 1)
        chip_text = folder_chip(position)
        self.chip = QLabel(chip_text, self)
        self.chip.setObjectName("folderTypeChip")
        self.chip.setVisible(bool(chip_text))
        header.addWidget(self.chip, 0, Qt.AlignmentFlag.AlignTop)
        layout.addLayout(header)

        self.body = QLabel(QUESTION_BODY, self)
        self.body.setObjectName("folderTypeBody")
        self.body.setTextFormat(Qt.TextFormat.PlainText)
        self.body.setWordWrap(True)
        self.body.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        layout.addWidget(self.body)

        self.table = QTableWidget(len(question.files), len(QUESTION_COLUMNS), self)
        self.table.setObjectName("folderTypeTable")
        self.table.setAccessibleName("MRC files without a type")
        self.table.setHorizontalHeaderLabels([column.upper() for column in QUESTION_COLUMNS])
        self.table.verticalHeader().setVisible(False)
        self.table.setShowGrid(False)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.table.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        heading = self.table.horizontalHeader()
        heading.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for column in range(1, len(QUESTION_COLUMNS)):
            heading.setSectionResizeMode(column, QHeaderView.ResizeMode.ResizeToContents)
            heading.model().setHeaderData(column, Qt.Orientation.Horizontal,
                                          int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter),
                                          Qt.ItemDataRole.TextAlignmentRole)
        heading.setDefaultAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        for row, file in enumerate(question.files):
            for column, text in enumerate(file_cells(file)):
                item = QTableWidgetItem(text)
                if column:
                    item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                else:
                    item.setToolTip(str(file.path))
                self.table.setItem(row, column, item)
        self.table.resizeRowsToContents()
        shown = min(len(question.files), VISIBLE_FILES)
        rows_height = sum(self.table.rowHeight(row) for row in range(shown))
        self.table.setFixedHeight(heading.sizeHint().height() + rows_height + 2 * self.table.frameWidth())
        layout.addWidget(self.table)

        note_row = QHBoxLayout()
        note_row.setSpacing(SPACE_S)
        note_row.addWidget(Glyph("info", "muted", 16, self), 0, Qt.AlignmentFlag.AlignTop)
        self.note = QLabel(QUESTION_NOTE, self)
        self.note.setObjectName("folderTypeNote")
        self.note.setTextFormat(Qt.TextFormat.PlainText)
        self.note.setWordWrap(True)
        self.note.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        note_row.addWidget(self.note, 1)
        layout.addLayout(note_row)

        buttons = QHBoxLayout()
        buttons.setSpacing(SPACE_S)
        self.skip_button = self._button(DO_NOT_LOAD, "folderTypeSkip")
        self.skip_button.clicked.connect(self.reject)
        buttons.addWidget(self.skip_button)
        buttons.addStretch(1)
        self.choice_buttons: dict[ReadAs, QPushButton] = {}
        for label, selection in CHOICES:
            button = self._button(label, "folderTypeChoice")
            button.clicked.connect(lambda _checked=False, selection=selection: self.choose(selection))
            self.choice_buttons[selection] = button
            buttons.addWidget(button)
        layout.addLayout(buttons)
        # No default (D12): the list takes focus, so Enter selects nothing.
        self.table.setFocus(Qt.FocusReason.OtherFocusReason)

    def _button(self, label: str, name: str) -> QPushButton:
        button = QPushButton(label, self)
        button.setObjectName(name)
        button.setAutoDefault(False)
        button.setDefault(False)
        return button

    def choose(self, selection: ReadAs) -> None:
        self.selection = selection
        self.accept()

    def reject(self) -> None:  # noqa: D102 - Esc and closing select "Do not load"
        self.selection = None
        super().reject()

    def showEvent(self, event) -> None:  # noqa: N802 - Qt API
        super().showEvent(event)
        announcement = QAccessibleAnnouncementEvent(self, f"{self.title.text()}. {QUESTION_BODY}")
        announcement.setPoliteness(QAccessible.AnnouncementPoliteness.Polite)
        QAccessible.updateAccessibility(announcement)


def ask_folder_type(parent: QWidget | None, question: FolderQuestion,
                    position: tuple[int, int] = (1, 1)) -> ReadAs | None:
    """Show the pop-up and wait: the selection, or None for "Do not load"."""

    dialog = FolderTypeDialog(question, position, parent)
    try:
        dialog.exec()
        return dialog.selection
    finally:
        dialog.deleteLater()
