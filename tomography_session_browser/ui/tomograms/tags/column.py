"""Lend the same panel to compact mode; keep drafts and focus during moves."""
from PySide6.QtCore import QEvent, Qt, QPoint
from PySide6.QtWidgets import QFrame, QPushButton, QSplitter, QVBoxLayout, QWidget


class TagsColumn(QSplitter):
    def __init__(self, owner):
        super().__init__(Qt.Orientation.Vertical, owner.tab)
        self.owner, self.tab, self.panel = owner, owner.tab, owner.panel
        self.setObjectName("tomogramListColumn")
        self.setChildrenCollapsible(False)
        self.setHandleWidth(5)
        self.addWidget(self.tab.review_list)
        self.slot = QWidget(self)
        slot_layout = QVBoxLayout(self.slot)
        slot_layout.setContentsMargins(0, 0, 0, 0)
        slot_layout.addWidget(self.panel)
        self.addWidget(self.slot)
        self.setStretchFactor(0, 1)
        self.setStretchFactor(1, 0)
        self.setSizes(self.tab.settings.tomogram_tags_splitter)
        self.splitterMoved.connect(self.remember_sizes)
        self.popup = QFrame(self.tab, Qt.WindowType.Popup)
        self.popup.setObjectName("tomogramTagsPopup")
        popup_layout = QVBoxLayout(self.popup)
        popup_layout.setContentsMargins(0, 0, 0, 0)
        self.popup.installEventFilter(self)
        self.button = QPushButton("Tags", self.tab)
        self.button.setAccessibleName("Open tomogram tags")
        self.button.clicked.connect(self.open_popup)
        self.compact = False
        self.destination = None
        for pane in self.tab.stage.panes.values():
            pane.installEventFilter(self)

    def remember_sizes(self) -> None:
        self.tab.settings.tomogram_tags_splitter = self.sizes()
        self.tab.save_settings(self.tab.settings)

    def sync(self, compact: bool, destination) -> None:
        focus = self.panel.focusWidget()
        self.compact = compact
        self.setVisible(not compact)
        if self.destination is not destination:
            if self.destination is not None:
                self.destination.removeWidget(self.button)
            destination.insertWidget(1, self.button)
            self.destination = destination
        self.button.setVisible(compact)
        if not compact and self.panel.parentWidget() is not self.slot:
            self.popup.hide()
            self.slot.layout().addWidget(self.panel)
            self.panel.show()
            if focus:
                focus.setFocus(Qt.FocusReason.OtherFocusReason)

    def open_popup(self) -> None:
        if not self.compact:
            self.panel.entry.begin()
            return
        self.popup.layout().addWidget(self.panel)
        self.panel.show()
        screen = self.button.screen().availableGeometry()
        self.popup.resize(min(360, screen.width()), min(max(240, self.panel.sizeHint().height()), 420, screen.height()))
        point = self.button.mapToGlobal(QPoint(0, self.button.height()))
        point.setX(max(screen.left(), min(point.x(), screen.right() - self.popup.width())))
        point.setY(max(screen.top(), min(point.y(), screen.bottom() - self.popup.height())))
        self.popup.move(point)
        self.popup.show()

    def dismiss(self) -> None:
        self.panel.entry.cancel()
        self.panel.close_note()
        self.popup.hide()
        self.tab.canvas.setFocus()

    def eventFilter(self, watched, event):
        if watched is self.popup and event.type() == QEvent.Type.Hide and self.compact:
            self.panel.entry.cancel()
            self.panel.close_note()
        elif watched in self.tab.stage.panes.values() and event.type() == QEvent.Type.MouseButtonPress:
            self.panel.entry.cancel()
            self.panel.close_note()
            self.popup.hide()
        return super().eventFilter(watched, event)
