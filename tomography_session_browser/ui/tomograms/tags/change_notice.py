"""Independent panel feedback for a saved source comparison."""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFrame, QLabel, QVBoxLayout

from tomography_session_browser.presentation.design_tokens import SPACE_S
from tomography_session_browser.presentation.tomogram_tag_changes import ChangeNotice
from tomography_session_browser.ui.widgets.link_button import link_button
from tomography_session_browser.ui.widgets.literal_text import literal_tooltip


class ChangeNoticeFrame(QFrame):
    def __init__(self, parent):
        super().__init__(parent)
        self.setObjectName("tagChangeNotice")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(SPACE_S, SPACE_S, SPACE_S, SPACE_S)
        self.title, self.body = QLabel(self), QLabel(self)
        self.title.setObjectName("inlineNoticeTitle")
        for label in (self.title, self.body):
            label.setTextFormat(Qt.TextFormat.PlainText)
            label.setWordWrap(True)
            layout.addWidget(label)
        self.review = link_button("Review…", self, tooltip="Review the recorded and loaded file facts")
        layout.addWidget(self.review, alignment=Qt.AlignmentFlag.AlignLeft)
        self.hide()

    def render(self, notice: ChangeNotice | None) -> None:
        self.setVisible(notice is not None)
        if notice is None:
            return
        self.title.setText(notice.title)
        self.body.setText(notice.body)
        self.body.setToolTip(literal_tooltip(notice.details))
        self.body.setAccessibleDescription(notice.details)
        tone = "warn" if notice.warn else "neutral"
        if self.property("tone") != tone:
            self.setProperty("tone", tone)
            self.style().unpolish(self)
            self.style().polish(self)
