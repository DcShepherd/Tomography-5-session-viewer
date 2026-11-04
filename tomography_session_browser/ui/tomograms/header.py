"""Tomogram relationships use the image tabs' Linked-strip vocabulary and styling."""
from PySide6.QtCore import QEvent, QSize, Signal
from PySide6.QtWidgets import QHBoxLayout, QLabel, QMenu, QPushButton, QSizePolicy, QWidget

from tomography_session_browser.presentation.design_tokens import SPACE_S
from tomography_session_browser.ui.icons import themed_icon
from tomography_session_browser.ui.theme import current_palette
from tomography_session_browser.ui.viewer.constants import NAVIGATION_BUTTONS
from tomography_session_browser.ui.viewer.models import ViewerNavigationAction


class LinkedHeader(QWidget):
    requested = Signal(str)
    changed = Signal()

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setObjectName("viewerLinkedStrip")
        # The strip must be allowed to become narrower than its expanded buttons.
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self.row = QHBoxLayout(self)
        self.row.setContentsMargins(0, 0, 0, 0)
        self.row.setSpacing(SPACE_S)
        self.label = QLabel("LINKED", self)
        self.label.setObjectName("viewerLinkedLabel")
        self.row.addWidget(self.label)
        self.buttons: dict[str, QPushButton] = {}
        self.separators: list[QLabel] = []
        self.menu = QMenu(self)
        self.menu.setToolTipsVisible(True)
        self.menu_button = QPushButton("Go to", parent)
        self.menu_button.setObjectName("viewerNavButton")
        self.menu_button.setMenu(self.menu)
        self.actions = {}
        for key, label in NAVIGATION_BUTTONS:
            if key == "tomograms":
                continue  # The current tomogram is already the strip's destination.
            button = QPushButton(str(label), self)
            button.setObjectName("viewerNavButton")
            button.setAccessibleName(f"Open linked {str(label).lower()}")
            button.clicked.connect(lambda _checked=False, k=key: self.requested.emit(k))
            self.buttons[key] = button
            self.row.addWidget(button)
            separator = QLabel("›", self)
            self.row.addWidget(separator)
            self.separators.append(separator)
            action = self.menu.addAction(str(label))
            action.triggered.connect(lambda _checked=False, k=key: self.requested.emit(k))
            self.actions[key] = action
        self.here = QLabel("this tomogram", self)
        self.here.setObjectName("viewerLinkedHere")
        self.row.addWidget(self.here)
        self.row.addStretch()
        self.apply_icons()
        self.update_actions([], "No linked acquisition")

    def update_actions(self, actions: list[ViewerNavigationAction], reason: str) -> None:
        by_key = {action.key: action for action in actions}
        for key, button in self.buttons.items():
            action = by_key.get(key)
            enabled = action is not None and action.enabled
            tooltip = action.tooltip if action else reason
            button.setEnabled(enabled)
            button.setToolTip(tooltip)
            self.actions[key].setEnabled(enabled)
            self.actions[key].setToolTip(tooltip)
        count = sum(button.isEnabled() for button in self.buttons.values())
        self.menu_button.setText(f"Go to · {count} of {len(self.buttons)}" if count else "No linked items")
        self.menu_button.setEnabled(count > 0)
        explanation = "Go to a related item from the acquisition" if count else "\n".join(dict.fromkeys(
            button.toolTip() for button in self.buttons.values() if button.toolTip()))
        self.menu_button.setAccessibleDescription(f"{count} of {len(self.buttons)} linked destinations available. {explanation}")
        self.menu_button.setToolTip(explanation)
        self.changed.emit()

    def changeEvent(self, event) -> None:  # noqa: N802
        super().changeEvent(event)
        if event.type() in (QEvent.Type.FontChange, QEvent.Type.StyleChange):
            self.changed.emit()

    def adapt(self, width: int, *, short: bool = False, allowed: bool = True) -> None:
        widgets = (self.label, *self.buttons.values(), *self.separators, self.here)
        for widget in widgets:
            widget.ensurePolished()
        self.row.invalidate()
        needed = self.row.sizeHint().width()
        collapse = short or not self.menu_button.isEnabled() or width < needed
        self.setVisible(allowed and not collapse)
        self.menu_button.setVisible(allowed and collapse)

    def apply_icons(self) -> None:
        for button in self.buttons.values():
            button.setIcon(themed_icon("step-out", color=current_palette().icon, size=16))
            button.setIconSize(QSize(16, 16))
