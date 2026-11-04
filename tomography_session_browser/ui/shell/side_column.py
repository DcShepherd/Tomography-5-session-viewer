"""One panel shell travels between the right dock and the existing drawer.

Context is the permanent lower panel. A viewer may lend the upper position its
Image settings widget; the viewer retains ownership when that widget is removed.
The shell owns no dock, drawer, settings or focus state.
"""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QSplitter, QWidget


class SideColumn(QSplitter):
    image_dismissed = Signal()

    def __init__(self, context: QWidget, parent: QWidget | None = None) -> None:
        super().__init__(Qt.Orientation.Vertical, parent)
        self.setObjectName("sideColumn")
        self.setChildrenCollapsible(False)
        self.context = context
        self.image_settings: QWidget | None = None
        self.addWidget(context)

    def set_image_settings(self, panel: QWidget | None) -> QWidget | None:
        """Mount a viewer's panel and return the detached, hidden previous panel.

        Detachment never destroys the panel or its editing state. The caller
        must retain/reparent the returned widget before releasing its viewer.
        Hidden panels leave the full height to their visible sibling.
        """
        previous = self.image_settings
        if previous is panel:
            return None
        if panel is self.context:
            raise ValueError("Context cannot also be Image settings")
        if previous is not None:
            previous.hide()
            previous.setParent(None)
        self.image_settings = panel
        if panel is not None:
            self.insertWidget(0, panel)
            panel.show()
            self.setStretchFactor(0, 1)
            self.setStretchFactor(1, 1)
        return previous
