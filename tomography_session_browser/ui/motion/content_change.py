"""Small content replacements in reserved layout slots; the canvas never moves."""
from collections.abc import Callable

from PySide6.QtCore import QEvent, QObject
from PySide6.QtWidgets import QApplication, QWidget
import shiboken6

from tomography_session_browser.ui.motion.fade import PictureFade
from tomography_session_browser.ui.motion.preferences import motion_enabled
from tomography_session_browser.ui.motion.section_reveal import settle_layouts
from tomography_session_browser.ui.motion.tokens import DURATION_MICRO_MS


class ContentTransition(QObject):
    """Crossfade a small slot. New input acts immediately and removes its old picture."""

    def __init__(self, widget: QWidget) -> None:
        super().__init__(widget)
        self.widget = widget
        self.fade: PictureFade | None = None

    def replace(self, change: Callable[[], None]) -> None:
        parent = self.widget.parentWidget()
        animate = (parent is not None and parent.isVisible() and motion_enabled(self.widget)
                   and not self.widget.geometry().isEmpty())
        # Picture the displayed state before retiring an interrupted fade.
        picture = parent.grab(self.widget.geometry()) if animate else None
        self.dismiss()
        change()
        if picture is None or parent is None:
            return
        settle_layouts(self.widget, parent)
        self.fade = PictureFade(parent, self.widget.geometry(), picture, duration_ms=DURATION_MICRO_MS)
        self.fade.start()
        QApplication.instance().installEventFilter(self)

    def dismiss(self) -> None:
        app = QApplication.instance()
        if app is not None:
            app.removeEventFilter(self)
        if self.fade is not None and shiboken6.isValid(self.fade):
            self.fade.dismiss()
        self.fade = None

    def eventFilter(self, watched, event) -> bool:  # noqa: N802 - Qt API
        if not shiboken6.isValid(self.widget):
            self.dismiss()
            return False
        kind = event.type()
        if kind in (QEvent.Type.MouseButtonPress, QEvent.Type.KeyPress, QEvent.Type.Wheel,
                    QEvent.Type.TouchBegin):
            self.dismiss()
        elif kind in (QEvent.Type.Hide, QEvent.Type.Resize) and watched in (self.widget, self.widget.parentWidget()):
            self.dismiss()
        elif self.fade is not None and (not shiboken6.isValid(self.fade) or not self.fade.isVisible()):
            self.dismiss()
        return False
