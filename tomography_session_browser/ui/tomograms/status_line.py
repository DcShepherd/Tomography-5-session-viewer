"""Independent status, Resume choice and window-local capture recovery."""
from __future__ import annotations

from PySide6.QtCore import QEvent, Qt
from PySide6.QtGui import QAccessible, QAccessibleAnnouncementEvent, QResizeEvent
from PySide6.QtWidgets import QAbstractButton, QHBoxLayout, QMenu, QPushButton, QSizePolicy, QToolButton, QWidget
from tomography_session_browser.ui.widgets.elided_label import ElidedLabel
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


class MessageCell(QWidget):
    """Keep text changes from invalidating the page layout."""

    def __init__(self, parent: QWidget):
        super().__init__(parent)
        self.setFixedHeight(38)
        self.setMinimumWidth(24)
        self.message = StatusMessage(self)

    def resizeEvent(self, event: QResizeEvent) -> None:  # noqa: N802
        super().resizeEvent(event)
        self.message.setGeometry(self.rect())


class StatusLine(QWidget):
    ACTIONS_CAPTION = "Status actions"

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
        self.cell = MessageCell(self)
        self.message = self.cell.message
        row.addWidget(self.cell, 1)
        self.actions: dict[QAbstractButton, bool] = {}
        self.resume = QPushButton("Resume saved view", self)
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
        self.more = QToolButton(self)
        self.more.setText(self.ACTIONS_CAPTION)
        self.more.setAccessibleName(self.ACTIONS_CAPTION)
        self.more.setToolTip("Show status actions")
        self.more.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.menu = QMenu(self.more)
        self.more.setMenu(self.menu)
        self.menu.aboutToShow.connect(self.populate_actions)
        row.addWidget(self.more)
        self.more.hide()
        for button in (self.resume, self.keep, self.recovery, self.tag_save):
            self.add_action(button)

    def add_action(self, button: QAbstractButton) -> None:
        self.actions[button] = False
        self.layout().insertWidget(self.layout().count() - 1, button)
        button.hide()

    def show_action(self, button: QAbstractButton, available: bool) -> None:
        self.actions[button] = available
        self.fit_actions()

    def action_anchor(self, button: QAbstractButton) -> QAbstractButton:
        return self.more if button.isHidden() and not self.more.isHidden() else button

    def populate_actions(self) -> None:
        self.menu.clear()
        for button, available in self.actions.items():
            if available:
                action = self.menu.addAction(button.accessibleName() if button is self.keep else button.text())
                action.setEnabled(button.isEnabled())
                action.setToolTip(button.toolTip())
                action.triggered.connect(lambda _checked, target=button: self.activate_action(target))

    def activate_action(self, button: QAbstractButton) -> None:
        if self.actions[button]:
            button.click()

    def fit_actions(self) -> None:
        shown = tuple(button for button, available in self.actions.items() if available)
        needed = self.cell.minimumWidth() + sum(
            min(button.sizeHint().width(), button.maximumWidth()) + self.layout().spacing() for button in shown)
        overflow = bool(shown) and needed > self.width()
        if overflow:
            metrics = self.more.fontMetrics()
            caption_width = (self.more.sizeHint().width() + metrics.horizontalAdvance(self.ACTIONS_CAPTION)
                             - metrics.horizontalAdvance(self.more.text()))
            budget = self.width() - self.cell.minimumWidth() - self.layout().spacing()
            self.more.setText(self.ACTIONS_CAPTION if budget >= caption_width else "⋯")
        focus_lost = any(button.hasFocus() and (overflow or button not in shown) for button in self.actions)
        more_focused = self.more.hasFocus()
        self.more.setVisible(overflow)
        for button in self.actions:
            button.setVisible(button in shown and not overflow)
        if focus_lost or (more_focused and not overflow):
            target = self.more if overflow else next((button for button in shown if button.isEnabled()), self.tab.canvas)
            target.setFocus(Qt.FocusReason.OtherFocusReason)

    def event(self, event: QEvent) -> bool:
        if event.type() == QEvent.Type.LayoutRequest and hasattr(self, "more"):
            self.fit_actions()
        return super().event(event)

    def refresh(self) -> None:
        count = len(self.tab.captures.failures)
        self.recovery.setText(f"{count} snapshots need recovery · Review")
        self.recovery.setAccessibleName(f"{count} snapshots need recovery. Review")
        self.recovery.setToolTip("Review capture recovery. Exported images are kept.")
        if not count and self.recovery.hasFocus():
            self.tab.canvas.setFocus(Qt.FocusReason.OtherFocusReason)
        self.show_action(self.recovery, bool(count))

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self.fit_actions()

    def announce_saved(self, name: str) -> None:
        event = QAccessibleAnnouncementEvent(self, f"Snapshot saved: {name}")
        event.setPoliteness(QAccessible.AnnouncementPoliteness.Polite)
        QAccessible.updateAccessibility(event)
