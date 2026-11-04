"""Persistent inline feedback; its owner supplies and scopes the actual controls."""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QFrame, QHBoxLayout, QLabel, QPlainTextEdit, QSizePolicy, QVBoxLayout

from tomography_session_browser.presentation.messages import MessageContent
from tomography_session_browser.ui.widgets.link_button import link_button
from tomography_session_browser.ui.widgets.section_header import SectionHeader


class InlineNotice(QFrame):
    def __init__(self, parent):
        super().__init__(parent)
        self.setObjectName("inlineNotice")
        self.content = MessageContent("", "")
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        layout = QVBoxLayout(self)
        self.title = QLabel(self)
        self.title.setObjectName("inlineNoticeTitle")
        self.consequence = QLabel(self)
        for label in (self.title, self.consequence):
            label.setTextFormat(Qt.TextFormat.PlainText)
            label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
            label.setWordWrap(True)
            layout.addWidget(label)
        self.actions = QHBoxLayout()
        layout.addLayout(self.actions)
        details_row = QHBoxLayout()
        details = SectionHeader("Details", self)
        copy = link_button("Copy details", self)
        details_row.addWidget(details)
        details_row.addStretch()
        details_row.addWidget(copy)
        layout.addLayout(details_row)
        self.details = QPlainTextEdit(self)
        self.details.setReadOnly(True)
        self.details.setMaximumHeight(100)
        self.details.hide()
        details.toggled.connect(self.details.setVisible)
        copy.clicked.connect(lambda: QApplication.clipboard().setText(self.copy_text()))
        layout.addWidget(self.details)
        self.hide()

    def set_content(self, content: MessageContent) -> None:
        if content != self.content:
            self.content = content
            self.title.setText(content.title)
            self.consequence.setText(content.consequence)
            self.details.setPlainText("\n\n".join(filter(None, ("\n".join(content.items), content.details))))
        self.show()
        self.fit_text()

    def fit_text(self) -> None:
        # A splitter needs the frame's wrapped minimum. Pinning individual
        # labels would retain a long error's height after shorter feedback.
        layout = self.layout()
        layout.invalidate()
        height = max(layout.totalMinimumSize().height(), layout.totalHeightForWidth(self.width()))
        if self.minimumHeight() != height:
            self.setMinimumHeight(height)

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self.fit_text()

    def copy_text(self) -> str:
        return self.content.copy_text()
