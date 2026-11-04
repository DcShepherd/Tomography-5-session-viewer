"""Shared prompt shell and three concrete inputs; operations retain their owners."""
from collections.abc import Hashable
from typing import Generic, TypeVar

from PySide6.QtCore import Qt
from PySide6.QtGui import QAccessible, QAccessibleAnnouncementEvent
from PySide6.QtWidgets import (QApplication, QComboBox, QDialog, QFrame, QHBoxLayout,
                               QLabel, QLineEdit, QPlainTextEdit, QPushButton, QScrollArea,
                               QSizePolicy, QVBoxLayout, QWidget)
from shiboken6 import isValid

from tomography_session_browser.domain.display_names import count_phrase
from tomography_session_browser.presentation.design_tokens import SPACE_M, SPACE_S
from tomography_session_browser.presentation.messages import (
    ActionPrompt, ActionRole, Cancelled, ItemAccepted, ItemChoice, MessageAction,
    MessageContent, TextAccepted,
)
from tomography_session_browser.ui.widgets.elided_label import ElidedLabel
from tomography_session_browser.ui.widgets.link_button import link_button
from tomography_session_browser.ui.widgets.section_header import SectionHeader

Identity = TypeVar("Identity", bound=Hashable)


class MessageSheet(QDialog, Generic[Identity]):
    """A scrollable body, explicit safe default/Escape and fixed action order."""

    def __init__(self, parent: QWidget | None, prompt: ActionPrompt[Identity]):
        super().__init__(parent)
        self.prompt = prompt
        self.selected_action = prompt.escape_action_id
        self.previous_focus = QApplication.focusWidget()
        self.setObjectName("messageSheet")
        self.setWindowTitle(prompt.content.title)
        self.setAccessibleName(prompt.content.title)
        self.setAccessibleDescription(prompt.content.consequence)
        self.setWindowModality(Qt.WindowModality.WindowModal)
        self.resize(560, 360)
        self.setMinimumWidth(360)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(SPACE_M, SPACE_M, SPACE_M, SPACE_M)
        layout.setSpacing(SPACE_M)
        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        layout.addWidget(scroll, 1)
        body = QWidget(scroll)
        self.body = QVBoxLayout(body)
        self.body.setContentsMargins(0, 0, 0, 0)
        self.body.setSpacing(SPACE_S)
        scroll.setWidget(body)
        self.title = QLabel(prompt.content.title, body)
        self.title.setObjectName("messageSheetTitle")
        self.title.setTextFormat(Qt.TextFormat.PlainText)
        self.title.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.title.setWordWrap(True)
        self.body.addWidget(self.title)
        self.consequence = QLabel(prompt.content.consequence, body)
        self.consequence.setTextFormat(Qt.TextFormat.PlainText)
        self.consequence.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.consequence.setWordWrap(True)
        self.body.addWidget(self.consequence)
        for item in prompt.content.items[:5]:
            label = ElidedLabel(item, mode=Qt.TextElideMode.ElideMiddle, parent=body)
            label.setTextFormat(Qt.TextFormat.PlainText)
            label.setAccessibleName(item)
            self.body.addWidget(label)
        if len(prompt.content.items) > 5:
            self.body.addWidget(QLabel("and " + count_phrase(len(prompt.content.items) - 5, "more", "more"), body))
        self.inputs = QVBoxLayout()
        self.body.addLayout(self.inputs)
        details_row = QHBoxLayout()
        self.disclosure = SectionHeader("Details", body)
        self.copy_button = link_button("Copy details", body)
        details_row.addWidget(self.disclosure)
        details_row.addStretch(1)
        details_row.addWidget(self.copy_button)
        self.body.addLayout(details_row)
        self.details = QPlainTextEdit(prompt.content.details, body)
        self.details.setReadOnly(True)
        self.details.setAccessibleName("Message details")
        self.details.setMaximumHeight(180)
        self.details.hide()
        self.disclosure.toggled.connect(self.details.setVisible)
        self.copy_button.clicked.connect(lambda: QApplication.clipboard().setText(self.copy_text()))
        self.body.addWidget(self.details)
        self.body.addStretch(1)
        safe = QHBoxLayout()
        destructive = QHBoxLayout()
        layout.addLayout(safe)
        self.buttons: dict[Identity, QPushButton] = {}
        ordered = sorted(prompt.actions, key=lambda a: (a.role == ActionRole.DESTRUCTIVE,
            a.id != prompt.default_action_id, a.role == ActionRole.PRIMARY))
        for action in ordered:
            button = QPushButton(action.label, self)
            button.setAutoDefault(False)
            button.setDefault(action.id == prompt.default_action_id)
            button.setObjectName("messageDestructive" if action.role == ActionRole.DESTRUCTIVE else
                                 "primaryButton" if action.id == prompt.default_action_id else "messageAction")
            button.clicked.connect(lambda _checked=False, identity=action.id: self.choose(identity))
            self.buttons[action.id] = button
            (destructive if action.role == ActionRole.DESTRUCTIVE else safe).addWidget(button)
        safe.addStretch(1)
        if any(a.role == ActionRole.DESTRUCTIVE for a in ordered):
            line = QFrame(self)
            line.setObjectName("messageDivider")
            line.setFrameShape(QFrame.Shape.HLine)
            layout.addWidget(line)
            layout.addLayout(destructive)
        self.finished.connect(self.restore_focus)
        self.buttons[prompt.default_action_id].setFocus()

    def copy_text(self) -> str:
        return MessageContent(self.title.text(), self.consequence.text(), self.prompt.content.items,
                              self.details.toPlainText()).copy_text()

    def choose(self, identity: Identity) -> None:
        if identity not in self.buttons:
            raise ValueError("Unknown message action.")
        self.selected_action = identity
        self.accept()

    def reject(self) -> None:
        self.selected_action = self.prompt.escape_action_id
        super().reject()

    def restore_focus(self, _result: int) -> None:
        previous = self.previous_focus
        if previous is not None and isValid(previous) and previous.isVisible() and previous.isEnabled():
            previous.window().activateWindow()
            previous.setFocus(Qt.FocusReason.OtherFocusReason)

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        announcement = QAccessibleAnnouncementEvent(self, self.prompt.content.title + ". " + self.prompt.content.consequence)
        announcement.setPoliteness(QAccessible.AnnouncementPoliteness.Polite)
        QAccessible.updateAccessibility(announcement)


def choose_action(parent: QWidget | None, prompt: ActionPrompt[Identity]) -> Identity:
    sheet = MessageSheet(parent, prompt)
    try:
        sheet.exec()
        return sheet.selected_action
    finally:
        sheet.deleteLater()


def request_text(parent: QWidget | None, content: MessageContent, *, label: str, value: str = "",
                 accept_label: str = "Save") -> TextAccepted | Cancelled:
    sheet = MessageSheet(parent, ActionPrompt(content, (MessageAction("accept", accept_label),
        MessageAction("cancel", "Cancel")), "accept", "cancel"))
    field = QLineEdit(value, sheet)
    field.setAccessibleName(label)
    heading = QLabel(label, sheet)
    heading.setBuddy(field)
    sheet.inputs.addWidget(heading)
    sheet.inputs.addWidget(field)
    field.selectAll()
    field.setFocus()
    try:
        sheet.exec()
        return TextAccepted(field.text()) if sheet.selected_action == "accept" else Cancelled()
    finally:
        sheet.deleteLater()


def choose_item(parent: QWidget | None, content: MessageContent, choices: tuple[ItemChoice[Identity], ...], *,
                label: str, accept_label: str = "Choose") -> ItemAccepted[Identity] | Cancelled:
    if not choices or len({c.id for c in choices}) != len(choices):
        raise ValueError("Item choices require unique identities and at least one item.")
    sheet = MessageSheet(parent, ActionPrompt(content, (MessageAction("accept", accept_label),
        MessageAction("cancel", "Cancel")), "accept", "cancel"))
    combo = QComboBox(sheet)
    combo.setAccessibleName(label)
    for choice in choices:
        combo.addItem(choice.label)
    combo.setPlaceholderText("Choose…")
    combo.setCurrentIndex(-1)
    sheet.buttons["accept"].setEnabled(False)
    combo.currentIndexChanged.connect(lambda index: sheet.buttons["accept"].setEnabled(index >= 0))
    heading = QLabel(label, sheet)
    heading.setBuddy(combo)
    sheet.inputs.addWidget(heading)
    sheet.inputs.addWidget(combo)
    combo.setFocus()
    try:
        sheet.exec()
        return ItemAccepted(choices[combo.currentIndex()].id) if sheet.selected_action == "accept" and combo.currentIndex() >= 0 else Cancelled()
    finally:
        sheet.deleteLater()
