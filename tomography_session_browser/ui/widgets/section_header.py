"""Shared disclosure header; its caller owns the section's reveal mechanism."""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QToolButton, QWidget


class SectionHeader(QToolButton):
    def __init__(self, title: str, parent: QWidget | None = None, *, open_: bool = False) -> None:
        super().__init__(parent)
        self.setObjectName("sectionHeader")
        self.set_title(title)
        self.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.setCheckable(True)
        self.toggled.connect(self._arrow)
        self.setChecked(open_)
        self._arrow(open_)

    def set_title(self, title: str) -> None:
        self.setText(title.upper())
        self.setAccessibleName(title)

    def _arrow(self, open_: bool) -> None:
        self.setArrowType(Qt.ArrowType.DownArrow if open_ else Qt.ArrowType.RightArrow)
