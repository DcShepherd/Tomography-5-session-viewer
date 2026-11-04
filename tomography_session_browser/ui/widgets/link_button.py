"""One construction and availability policy for text-link buttons."""
from tomography_session_browser.presentation.help_content import LocalBinding, InteractionKind
from typing import Literal
from PySide6.QtCore import QEvent, Qt
from PySide6.QtWidgets import QToolButton, QWidget


class LinkButton(QToolButton):
    def __init__(self, text: str = "", parent: QWidget | None = None, *,
                 style: Literal["inline", "table"] = "inline", tooltip: str = "") -> None:
        super().__init__(parent)
        self.setObjectName("inlineLink" if style == "inline" else "tableLink")
        self.setText(text)
        self.setAutoRaise(True)
        self.setToolTip(tooltip)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def keyPressEvent(self, event) -> None:  # noqa: N802
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self.click()
            event.accept()
            return
        super().keyPressEvent(event)

    def changeEvent(self, event) -> None:  # noqa: N802
        super().changeEvent(event)
        if event.type() == QEvent.Type.EnabledChange:
            self.setCursor(Qt.CursorShape.PointingHandCursor if self.isEnabled() else Qt.CursorShape.ArrowCursor)
            if not self.isEnabled() and not self.toolTip():
                self.setToolTip("Unavailable for the current selection.")


link_button = LinkButton


# Contextual keys handled by this module; consumed by the shared Help sheet.
LOCAL_KEYS = (
    LocalBinding('widgets.link_button.text-link-enter', 'Text link', InteractionKind.KEYBOARD, ('Enter', 'Return', 'Space'), 'Activate the focused link'),
)
