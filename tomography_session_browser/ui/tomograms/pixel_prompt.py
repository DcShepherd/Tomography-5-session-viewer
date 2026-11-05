"""A prompt docked above the plane band while a volume has no usable pixel size.

It offers recorded values as suggestions and applies the same display-only,
per-version override as Image settings › Calibration. Later hides it for this
version until the next session; the source header is never changed.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDoubleSpinBox, QFrame, QHBoxLayout, QLabel, QPushButton, QToolButton, QVBoxLayout, QWidget

from tomography_session_browser.presentation.design_tokens import SPACE_M, SPACE_S, SPACE_XS
from tomography_session_browser.presentation.tomogram_calibration import header_note, needs_pixel_size, pixel_size_suggestions
from tomography_session_browser.ui.widgets.elided_label import ElidedLabel

if TYPE_CHECKING:
    from tomography_session_browser.ui.tomograms.tab import TomogramTab


class PixelSizePrompt(QFrame):
    def __init__(self, tab: TomogramTab) -> None:
        super().__init__(tab)
        self.tab = tab
        self.setObjectName("tomogramPixelPrompt")
        self.setAccessibleName("Pixel size missing")
        self.later: set[str] = set()
        self.volume_id: str | None = None
        column = QVBoxLayout(self)
        column.setContentsMargins(SPACE_M, SPACE_S, SPACE_M, SPACE_S)
        column.setSpacing(SPACE_XS)
        top = QHBoxLayout()
        title = QLabel("Check pixel size", self)
        title.setObjectName("tomogramPromptTitle")
        self.note = QLabel(self)
        self.note.setObjectName("tomogramPromptNote")
        later = QPushButton("Later", self)
        later.setObjectName("tomogramPromptAction")
        later.setToolTip("Close this message. It shows again when you open the session again.")
        later.clicked.connect(self.dismiss)
        top.addWidget(title)
        top.addWidget(self.note, 1)
        top.addWidget(later)
        column.addLayout(top)
        self.values = QHBoxLayout()
        self.values.setSpacing(SPACE_S)
        self.spacing = QDoubleSpinBox(self)
        self.spacing.setRange(.001, 100000)
        self.spacing.setDecimals(3)
        self.spacing.setValue(1.)
        self.spacing.setButtonSymbols(QDoubleSpinBox.ButtonSymbols.NoButtons)
        self.spacing.setAccessibleName("Pixel size in ångström")
        self.spacing.setFixedWidth(80)
        self.values.addWidget(self.spacing)
        self.values.addWidget(QLabel("Å", self))
        self.suggestions = QWidget(self)
        self.suggestion_row = QHBoxLayout(self.suggestions)
        self.suggestion_row.setContentsMargins(0, 0, 0, 0)
        self.suggestion_row.setSpacing(SPACE_XS)
        self.values.addWidget(self.suggestions, 1)
        self.several = QPushButton("Apply to several…", self)
        self.several.setObjectName("tomogramPromptOutline")
        self.several.setToolTip("Use this voxel size for other versions that need one. You review the list before "
                                "the app changes a value. The MRC files do not change.")
        self.several.clicked.connect(self.open_review)
        self.several.hide()
        self.values.addWidget(self.several)
        apply = QPushButton("Apply to this version", self)
        apply.setObjectName("tomogramPromptPrimary")
        apply.setToolTip("Use this voxel size for this version in the app. The MRC file does not change.")
        apply.clicked.connect(self.apply)
        self.values.addWidget(apply)
        column.addLayout(self.values)
        self.hide()

    def sync(self) -> None:
        tab, volume = self.tab, self.tab.volume()
        # Wait for storage: a value saved for this file must not flash this prompt first.
        show = (tab.pixel_sizes.loaded and needs_pixel_size(volume, tab.pixel_sizes.values.value_for(volume) if volume else None)
                and volume is not None and volume.id not in self.later)
        if show and volume is not None and volume.id != self.volume_id:
            self.volume_id = volume.id
            self.note.setText(header_note(volume))
            while self.suggestion_row.count():
                item = self.suggestion_row.takeAt(0)
                if item is not None and item.widget() is not None:
                    item.widget().deleteLater()
            row = tab.current_object()
            for text, value in pixel_size_suggestions(row, volume) if row is not None else ():
                button = QPushButton(text, self.suggestions)
                button.setObjectName("tomogramPromptSuggestion")
                button.setToolTip("Put this recorded value in the field. Click Apply to use it.")
                button.clicked.connect(lambda _checked=False, value=value: self.spacing.setValue(value))
                self.suggestion_row.addWidget(button)
            self.suggestion_row.addStretch()
        if show != self.isVisible():
            self.setVisible(show)

    def apply(self) -> None:
        self.tab.pixel_sizes.confirm(self.spacing.value())
        self.sync()

    def open_review(self) -> None:
        self.spacing.interpretText()
        self.tab.pixel_sizes.open_review(self.spacing.value())

    def dismiss(self) -> None:
        if self.volume_id is not None:
            self.later.add(self.volume_id)
        self.sync()


class PixelSizeApplied(QFrame):
    """One line after a bulk Apply: the value, the count and Undo (mockup board 3)."""

    def __init__(self, tab: TomogramTab) -> None:
        super().__init__(tab)
        self.tab = tab
        self.setObjectName("tomogramPixelApplied")
        row = QHBoxLayout(self)
        row.setContentsMargins(SPACE_M, SPACE_XS, SPACE_S, SPACE_XS)
        row.setSpacing(SPACE_S)
        self.text = ElidedLabel("", mode=Qt.TextElideMode.ElideRight, parent=self, tooltip=False)
        self.undo_button = QPushButton("Undo", self)
        self.undo_button.setObjectName("tomogramAppliedUndo")
        self.undo_button.setToolTip("Remove this value from these versions. Values you set before or after it do not change.")
        self.undo_button.clicked.connect(lambda: tab.pixel_sizes.undo())
        self.close_button = QToolButton(self)
        self.close_button.setObjectName("tomogramInsetClose")
        self.close_button.setText("×")
        self.close_button.setAccessibleName("Close this message")
        self.close_button.clicked.connect(lambda: tab.pixel_sizes.dismiss())
        row.addWidget(self.text, 1)
        row.addWidget(self.undo_button)
        row.addWidget(self.close_button)
        self.hide()

    def show_text(self, text: str, *, undo: bool = False, warn: bool = False, tooltip: str = "") -> None:
        self.text.setText(text)
        self.text.setToolTip(tooltip or text)
        self.text.setProperty("tone", "warn" if warn else "")
        self.text.style().unpolish(self.text)
        self.text.style().polish(self.text)
        self.undo_button.setVisible(undo)
        self.close_button.setToolTip("Close this message. After you close it, you cannot undo this action." if undo
                                     else "Close this message.")
        self.show()
