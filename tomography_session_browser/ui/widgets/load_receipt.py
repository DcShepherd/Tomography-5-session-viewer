"""Persistent Project-panel result; the load batch owns retries and outcomes."""
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QAccessible, QAccessibleAnnouncementEvent
from PySide6.QtWidgets import (
    QApplication, QFrame, QHBoxLayout, QLabel, QPushButton, QScrollArea,
    QSizePolicy, QVBoxLayout, QWidget,
)

from tomography_session_browser.presentation.design_tokens import SPACE_M, SPACE_S, SPACE_XS
from tomography_session_browser.presentation.load_results import LoadResultContent
from tomography_session_browser.ui.widgets.session_drop import Glyph, GlyphButton


class LoadReceipt(QFrame):
    dismissed = Signal()
    retry_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("dropReceipt")
        self.setAccessibleName("Load result")
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self._details = ""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        head = QHBoxLayout()
        head.setContentsMargins(SPACE_M, SPACE_M, SPACE_S, SPACE_S)
        head.setSpacing(SPACE_S)
        head.addWidget(Glyph("alert", "warn", 18, self), 0, Qt.AlignmentFlag.AlignTop)
        self.title = self.label("", "dropReceiptTitle", self)
        head.addWidget(self.title, 1)
        self.dismiss_button = GlyphButton("x", "Dismiss load result", self)
        self.dismiss_button.clicked.connect(self.dismiss)
        head.addWidget(self.dismiss_button, 0, Qt.AlignmentFlag.AlignTop)
        layout.addLayout(head)
        self.scroll = QScrollArea(self)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll.setMaximumHeight(300)
        self.body = QWidget(self.scroll)
        self.sections = QVBoxLayout(self.body)
        self.sections.setContentsMargins(SPACE_M, SPACE_XS, SPACE_M, SPACE_S)
        self.sections.setSpacing(SPACE_S)
        self.sections.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.scroll.setWidget(self.body)
        layout.addWidget(self.scroll)
        actions = QHBoxLayout()
        actions.setContentsMargins(SPACE_M, SPACE_S, SPACE_M, SPACE_M)
        actions.setSpacing(SPACE_S)
        self.retry_button = QPushButton("Retry", self)
        self.retry_button.setAccessibleName("Retry failed folders")
        self.retry_button.clicked.connect(self.retry_requested)
        self.copy_button = QPushButton("Copy details", self)
        self.copy_button.clicked.connect(self.copy_details)
        actions.addWidget(self.retry_button)
        actions.addWidget(self.copy_button)
        actions.addStretch(1)
        layout.addLayout(actions)
        self.hide()

    @staticmethod
    def label(text: str, name: str, parent: QWidget) -> QLabel:
        label = QLabel(text, parent)
        label.setObjectName(name)
        label.setTextFormat(Qt.TextFormat.PlainText)
        label.setMinimumWidth(0)
        label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        label.setWordWrap(True)
        label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        return label

    def show_receipt(self, content: LoadResultContent) -> None:
        self._details = content.details
        self.title.setText(content.title)
        while self.sections.count():
            item = self.sections.takeAt(0)
            if item.widget() is not None:
                item.widget().hide()
                item.widget().deleteLater()
        for section in content.sections:
            group = QFrame(self.body)
            group.setObjectName("dropReceiptSection")
            rows = QVBoxLayout(group)
            rows.setContentsMargins(0, SPACE_S, 0, SPACE_S)
            rows.setSpacing(SPACE_XS)
            rows.setAlignment(Qt.AlignmentFlag.AlignTop)
            rows.addWidget(self.label(section.title, "dropReceiptSkipped", group))
            if section.detail:
                rows.addWidget(self.label(section.detail, "dropReceiptLines", group))
            for line in section.lines:
                label = self.label(f"{line.name}\n{line.reason}", "dropReceiptLines", group)
                label.setToolTip(line.path)
                rows.addWidget(label)
            self.sections.addWidget(group)
        self.retry_button.setVisible(content.can_retry)
        self.retry_button.setEnabled(content.can_retry)
        self.scroll.verticalScrollBar().setValue(0)
        description = ". ".join((content.title, *(section.title for section in content.sections)))
        self.setAccessibleDescription(description)
        self.show()
        self.fit_body()
        QAccessible.updateAccessibility(QAccessibleAnnouncementEvent(self, description))

    def fit_body(self) -> None:
        width = max(1, self.scroll.viewport().width())
        needed = self.sections.heightForWidth(width)
        if needed < 0:
            needed = self.body.sizeHint().height()
        limit = max(60, min(300, self.window().height() // 3))
        height = min(limit, needed + 2 * self.scroll.frameWidth())
        if self.scroll.height() != height:
            self.scroll.setFixedHeight(height)

    def resizeEvent(self, event) -> None:  # noqa: N802 - QWidget facade
        super().resizeEvent(event)
        if hasattr(self, "sections"):
            self.fit_body()

    def set_running(self) -> None:
        self.retry_button.setEnabled(False)

    def copy_details(self) -> None:
        QApplication.clipboard().setText(self._details)

    def dismiss(self) -> None:
        self.hide()
        self.dismissed.emit()
