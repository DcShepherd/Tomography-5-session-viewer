"""Expose viewer tooltips on keyboard focus, with Escape taking one layer."""
from tomography_session_browser.presentation.help_content import LocalBinding, InteractionKind
from PySide6.QtCore import QEvent, QObject, QPoint, Qt, Slot, QTimer
from PySide6.QtWidgets import QApplication, QAbstractButton, QAbstractSpinBox, QComboBox, QFrame, QLabel, QVBoxLayout, QWidget
from shiboken6 import isValid


class KeyboardTooltips(QObject):
    def __init__(self, tab) -> None:
        super().__init__(tab)
        self.tab = tab
        self.tab_intent = False
        self.intent_timer = QTimer(self)
        self.intent_timer.setSingleShot(True)
        self.intent_timer.timeout.connect(self.cancel_traversal)
        self.current = None
        # Own the keyboard tip. Qt's global QToolTip uses an ephemeral parentless
        # QLabel, which can disappear during window retirement/re-entrant events.
        self.popup = QFrame(tab, Qt.WindowType.ToolTip)
        self.popup.setObjectName("tomogramKeyboardTip")
        layout = QVBoxLayout(self.popup)
        layout.setContentsMargins(8, 8, 8, 8)
        self.label = QLabel(self.popup)
        self.label.setWordWrap(True)
        self.label.setMaximumWidth(360)
        layout.addWidget(self.label)
        app = QApplication.instance()
        assert app is not None
        app.installEventFilter(self)
        tab.destroyed.connect(self.detach)

    def eventFilter(self, watched, event) -> bool:  # noqa: N802
        # QWidget destruction can send focus events after the tab wrapper has
        # become invalid but before Qt destroys this child filter. Never call
        # back into that partially retired widget tree.
        if not isValid(self.tab) or not isValid(self.popup) or not isValid(self.tab.image_settings):
            return False
        if event.type() not in (QEvent.Type.FocusIn, QEvent.Type.FocusOut, QEvent.Type.ShortcutOverride, QEvent.Type.KeyPress, QEvent.Type.MouseButtonPress):
            return False
        if not isinstance(watched, QWidget):
            return super().eventFilter(watched, event)
        if event.type() == QEvent.Type.KeyPress:
            self.cancel_traversal()
            if event.key() in (Qt.Key.Key_Tab, Qt.Key.Key_Backtab) and not event.modifiers() & (Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.AltModifier | Qt.KeyboardModifier.MetaModifier):
                self.tab_intent = True
                self.intent_timer.start(0)
        elif event.type() == QEvent.Type.MouseButtonPress:
            self.cancel_traversal()
        traversal = self.tab_intent if event.type() == QEvent.Type.FocusIn else False
        if event.type() == QEvent.Type.FocusIn:
            self.cancel_traversal()
        belongs = self.tab.isAncestorOf(watched) or self.tab.image_settings.isAncestorOf(watched)
        if belongs and self.current is not None and event.type() in (QEvent.Type.ShortcutOverride, QEvent.Type.KeyPress) and event.key() == Qt.Key.Key_Escape and self.popup.isVisible():
            if event.type() == QEvent.Type.KeyPress:
                self.popup.hide()
                self.current = None
            event.accept()
            return True
        if belongs and traversal and event.type() == QEvent.Type.FocusIn and event.reason() in (Qt.FocusReason.TabFocusReason, Qt.FocusReason.BacktabFocusReason):
            if isinstance(watched, (QAbstractButton, QAbstractSpinBox, QComboBox)) and watched.toolTip():
                self.label.setText(watched.toolTip())
                self.popup.adjustSize()
                point = watched.mapToGlobal(QPoint(0, watched.height()))
                area = watched.screen().availableGeometry()
                point.setX(max(area.left(), min(point.x(), area.right()-self.popup.width())))
                point.setY(max(area.top(), min(point.y(), area.bottom()-self.popup.height())))
                self.popup.move(point)
                self.popup.show()
                self.current = watched
        elif watched is self.current and event.type() == QEvent.Type.FocusOut:
            self.popup.hide()
            self.current = None
        return super().eventFilter(watched, event)

    def cancel_traversal(self) -> None:
        self.tab_intent = False
        self.intent_timer.stop()

    @Slot()
    def detach(self) -> None:
        """Detach without touching widgets, including during owner destruction."""
        app = QApplication.instance()
        if app is not None:
            app.removeEventFilter(self)

    def dispose(self) -> None:
        self.detach()
        if self.current is not None:
            self.popup.hide()
            self.current = None


LOCAL_KEYS = (
    LocalBinding('tooltip.traverse', 'Tomograms controls', InteractionKind.KEYBOARD, ('Tab', 'Shift+Tab'), 'Move between controls and show their keyboard tooltip'),
    LocalBinding('tooltip.close', 'Keyboard tooltip', InteractionKind.KEYBOARD, ('Esc',), 'Close the tooltip before changing the underlying view'),
)
