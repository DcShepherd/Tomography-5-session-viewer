"""Independent status, Resume choice and window-local capture recovery."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QAccessible, QAccessibleAnnouncementEvent
from PySide6.QtWidgets import QHBoxLayout, QPushButton, QSizePolicy, QWidget
from tomography_session_browser.ui.widgets.elided_label import ElidedLabel
from tomography_session_browser.ui.widgets.elided_button import ElidedButton
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from tomography_session_browser.ui.tomograms.captures import CaptureJob


class StatusMessage(ElidedLabel):
    """A completion clears only the message produced by its own capture job."""
    def __init__(self, parent: QWidget):
        self.owner: object = None
        super().__init__("", parent=parent)
        self.setObjectName("tomogramFeedback")

    def setText(self, text: str) -> None:  # noqa: N802
        self.owner = None
        super().setText(text)
        self.setAccessibleDescription(text)

    def note(self, text: str) -> None:
        if text or self.owner == "render":
            self.setText(text)
            self.owner = "render"

    def capture(self, job: CaptureJob, text: str) -> None:
        self.setText(text)
        self.owner = ("capture", job.created_ns, job.identity)

    def complete(self, job: CaptureJob) -> None:
        if self.owner == ("capture", job.created_ns, job.identity):
            self.setText("")


class StatusLine(QWidget):
    def __init__(self, tab):
        super().__init__(tab)
        self.tab = tab
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setMinimumWidth(0)
        # Match the version segments; hidden choices never change stage height.
        self.setFixedHeight(38)
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(8)
        self.message = StatusMessage(self)
        self.message.setFixedHeight(38)
        row.addWidget(self.message, 1)
        self.resume = ElidedButton("Resume saved view", self)
        self.resume.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        self.keep = QPushButton("×", self)
        self.keep.setAccessibleName("Keep current view")
        self.keep.setFixedWidth(28)
        self.recovery = QPushButton(self)
        self.recovery.setObjectName("secondaryAction")
        self.recovery.setFlat(True)
        self.recovery.clicked.connect(lambda: tab.history.show_browser())
        self.tag_save = QPushButton(self)
        self.tag_save.setObjectName("secondaryAction")
        self.tag_save.setAccessibleName("Review tag project save failures")
        for button in (self.resume, self.keep, self.recovery, self.tag_save):
            row.addWidget(button)
            button.hide()

    def refresh(self) -> None:
        count = len(self.tab.captures.failures)
        self.recovery.setText(f"{count} snapshots need recovery · Review" if self.width() >= 480 else f"{count} · Review")
        self.recovery.setAccessibleName(f"{count} snapshots need recovery. Review")
        self.recovery.setToolTip("Review capture recovery. Exported images are kept.")
        if not count and self.recovery.hasFocus():
            self.tab.canvas.setFocus(Qt.FocusReason.OtherFocusReason)
        self.recovery.setVisible(bool(count))

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        if hasattr(self.tab, "captures"):
            self.refresh()

    def announce_saved(self, name: str) -> None:
        event = QAccessibleAnnouncementEvent(self, f"Snapshot saved: {name}")
        event.setPoliteness(QAccessible.AnnouncementPoliteness.Polite)
        QAccessible.updateAccessibility(event)
