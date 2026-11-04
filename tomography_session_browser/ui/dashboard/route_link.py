"""A borderless area of a summary card that opens one destination.

The inventory entries of the sample card and the Tomograms aside are areas,
not buttons: an icon, a word and a number that open what they count. Click,
Enter and Space activate; with nothing to open the area is inert and takes no
focus. Hover and focus are style (``QFrame#routeLink``), so they never move
the contents.
"""
from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import Qt
from PySide6.QtGui import QKeyEvent, QMouseEvent
from PySide6.QtWidgets import QFrame, QWidget

from tomography_session_browser.presentation.dashboard.models import DashboardFilterModel
from tomography_session_browser.presentation.help_content import InteractionKind, LocalBinding

_ACTIVATE_KEYS = frozenset((Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Space))


class RouteLink(QFrame):
    """Opens ``route`` through ``activate`` when it has rows to open."""

    def __init__(self, route: DashboardFilterModel, activate: Callable[[], None], *,
                 name: str, description: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("routeLink")
        self._route = route
        self._activate = activate
        live = bool(route.count)
        self.setProperty("live", live)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, live)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus if live else Qt.FocusPolicy.NoFocus)
        self.setCursor(Qt.CursorShape.PointingHandCursor if live else Qt.CursorShape.ArrowCursor)
        self.setAccessibleName(name)
        self.setAccessibleDescription(f"{description} Press Enter or Space to open." if live else description)

    def destination(self) -> str:
        return self._route.tab

    def activate(self) -> None:
        if self._route.count:
            self._activate()

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802 - Qt signature
        if event.button() == Qt.MouseButton.LeftButton and self._route.count:
            self.activate()
            event.accept()
            return
        super().mousePressEvent(event)

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802 - Qt signature
        if event.key() in _ACTIVATE_KEYS and self._route.count:
            self.activate()
            event.accept()
            return
        super().keyPressEvent(event)


# Contextual keys handled by this module; consumed by the shared Help sheet.
LOCAL_KEYS = (
    LocalBinding('dashboard.route_link.route-link-enter', 'Summary card entry', InteractionKind.KEYBOARD,
                 ('Enter', 'Return', 'Space'), 'Open the rows that the entry counts'),
)
