"""Small Context primitives; values and paths come from typed presenters."""
from tomography_session_browser.ui.widgets.link_button import link_button
from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QMenu, QScrollArea, QSizePolicy, QToolButton, QVBoxLayout, QWidget

from tomography_session_browser.presentation.design_tokens import SPACE_S, SPACE_XS
from tomography_session_browser.presentation.tomogram_context import ContextFile, ContextValue
from tomography_session_browser.ui.file_actions import add_path_actions, copy_text
from tomography_session_browser.ui.motion import SmoothScroller
from tomography_session_browser.ui.tomograms.folding_section import FoldingSection
from tomography_session_browser.ui.widgets.elided_label import ElidedLabel
from tomography_session_browser.ui.widgets.metadata_callout import MetadataCallout
from tomography_session_browser.ui.widgets.metadata_text import display_value


def text_label(text: str, parent: QWidget, name: str = 'metaPlain') -> QLabel:
    label = QLabel(display_value(text), parent)
    label.setTextFormat(Qt.TextFormat.PlainText)
    label.setObjectName(name)
    label.setWordWrap(True)
    label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
    return label


def section(title: str, parent: QWidget) -> QLabel:
    return text_label(title.upper(), parent, 'metaSection')


def copy_button(value: str, parent: QWidget, *, label: str = 'Copy') -> QToolButton:
    button = link_button(parent=parent)
    button.setText(label)
    button.setToolTip(value)
    button.setAccessibleName(label)
    button.setAccessibleDescription(value)
    button.clicked.connect(lambda: copy_text(value))
    return button


def value_row(value: ContextValue, parent: QWidget) -> QWidget:
    row = QWidget(parent)
    layout = QVBoxLayout(row)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(SPACE_XS)
    line = QHBoxLayout()
    line.setContentsMargins(0, 0, 0, 0)
    key = text_label(value.label, row, 'metaKey')
    key.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
    line.addWidget(key)
    label = text_label(value.display, row, 'metaValue')
    label.setToolTip(value.detail)
    label.setAccessibleDescription(value.detail)
    label.setProperty('valueState', value.state)
    line.addWidget(label, 1)
    layout.addLayout(line)
    if value.qualifier and value.state != 'recorded':
        layout.addWidget(text_label(value.qualifier, row, 'metaValueDetail'))
    return row


def evidence_row(value: ContextValue, parent: QWidget) -> QWidget:
    """Keep provenance paths whole in copying/help, without forcing the pane wide."""
    row = QWidget(parent)
    layout = QVBoxLayout(row)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(SPACE_XS)
    layout.addWidget(text_label(value.label + ': ' + value.display, row, 'metaKey'))
    for evidence in value.evidence:
        definition = ElidedLabel(evidence.definition, parent=row, prefer_full_width=True)
        definition.setAccessibleName('Definition: ' + evidence.definition)
        layout.addWidget(definition)
        if (evidence.value, evidence.unit) != (value.value, value.unit):
            layout.addWidget(text_label('Recorded: ' + evidence.value + ' ' + evidence.unit, row))
        layout.addWidget(file_row(ContextFile('Source', Path(evidence.source)), row))
        layout.addWidget(text_label('Field: ' + evidence.field, row))
        if evidence.qualifier:
            layout.addWidget(text_label(evidence.qualifier, row))
    if value.qualifier and value.qualifier not in {evidence.qualifier for evidence in value.evidence}:
        layout.addWidget(text_label(value.qualifier, row))
    return row


def file_row(file: ContextFile, parent: QWidget) -> QWidget:
    row = QWidget(parent)
    layout = QHBoxLayout(row)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(SPACE_XS)
    layout.addWidget(QLabel(file.label, row))
    path = ElidedLabel(file.path.name or str(file.path), parent=row, prefer_full_width=True)
    path.setToolTip(str(file.path))
    path.setAccessibleName(str(file.path))
    layout.addWidget(path, 1)
    actions = link_button(parent=row)
    # U+2026 is in the UI font; '⋯' fell back to a symbol font as three
    # pixel-sized dots above the text line.
    actions.setText('…')
    actions.setAccessibleName(f'File actions for {file.label}: {file.path}')
    actions.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
    actions.setProperty("menuIndicator", "none")
    menu = QMenu(actions)
    add_path_actions(menu, file.path, file.path.name)
    if not file.path.is_absolute():
        menu.actions()[0].setEnabled(False)
        menu.actions()[0].setToolTip('The source folder is not loaded; this recorded path is relative.')
    actions.setMenu(menu)
    layout.addWidget(actions)
    return row


class ContextScroll(QScrollArea):
    def __init__(self, parent: QWidget, warning_tone: Callable[[str], str]) -> None:
        super().__init__(parent)
        self.warning_tone = warning_tone
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setWidgetResizable(True)
        SmoothScroller(self)
        self.body = QWidget(self)
        self.body.setMinimumWidth(0)
        self.layout_ = QVBoxLayout(self.body)
        self.layout_.setContentsMargins(0, SPACE_XS, SPACE_S, SPACE_S)
        self.layout_.setSpacing(SPACE_S)
        self.setWidget(self.body)
        self.folds: dict[str, bool] = {}

    def clear(self) -> None:
        self.folds.update({fold.toggle.accessibleName(): fold.toggle.isChecked() for fold in self.body.findChildren(FoldingSection)})
        while self.layout_.count():
            item = self.layout_.takeAt(0)
            if item.widget():
                item.widget().hide()
                item.widget().deleteLater()

    def fold(self, title: str) -> FoldingSection:
        fold = FoldingSection(title, self.body, open_=self.folds.get(title, False))
        # Keep the preferred text width; override Qt's full-text minimum so a
        # larger platform font can shrink within the Context column.
        fold.toggle.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        fold.toggle.setMinimumWidth(SPACE_XS)
        self.layout_.addWidget(fold)
        return fold

    def warnings(self, values: tuple[str, ...]) -> None:
        for value in values:
            self.layout_.addWidget(MetadataCallout(value, self.warning_tone(value), self.body))
