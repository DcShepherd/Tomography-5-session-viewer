"""Right-click outside closes the canvas's open chrome (display only).

While the Positions card or the Overlays panel is open, a right-click outside
them (and outside their buttons and any open menu) closes everything that is
open in one go: an open menu, the card (it closes and shrinks with its reveal)
and the Overlays panel (its button shrinks with it). The click still does what
it does there, for example clear the marker interaction on the canvas.

The application-wide filter is attached only while something is open: a
permanent one costs every event in the app.
"""
from __future__ import annotations

from PySide6.QtCore import QEvent, QObject, Qt
from PySide6.QtWidgets import QApplication, QWidget


class OutsideClose(QObject):
    def __init__(self, view: QWidget) -> None:
        super().__init__(view)
        self.view = view
        self.watching = False

    def follow_controls(self) -> None:
        """Follow the card and the Overlays button, once the canvas controls exist."""

        card = getattr(self.view, "positions_card", None)
        if card is not None:
            card.expanded_changed.connect(lambda _expanded: self.sync())
        button = getattr(self.view, "overlay_button", None)
        if button is not None:
            button.toggled.connect(lambda _checked: self.sync())

    # State --------------------------------------------------------------
    def card_open(self) -> bool:
        card = getattr(self.view, "positions_card", None)
        return card is not None and card.expanded and not card.isHidden()

    def panel_open(self) -> bool:
        button = getattr(self.view, "overlay_button", None)
        return button is not None and button.isChecked()

    def sync(self) -> None:
        """Watch right-clicks only while the card or the panel is open."""

        wanted = self.card_open() or self.panel_open()
        if wanted == self.watching:
            return
        self.watching = wanted
        app = QApplication.instance()
        if app is None:
            return
        if wanted:
            app.installEventFilter(self)
        else:
            app.removeEventFilter(self)

    # Closing ------------------------------------------------------------
    def owning_window(self, widget: QWidget) -> QWidget | None:
        """The window a widget belongs to, through any popup menus it opened from."""

        window = widget.window()
        while window is not None and window.windowType() == Qt.WindowType.Popup:
            parent = window.parentWidget()
            window = parent.window() if parent is not None else None
        return window

    def inside(self, global_pos, watched: QWidget) -> bool:
        """On the card, the panel, their buttons or an open menu: not outside."""

        for popup in (QApplication.activePopupWidget(), watched.window()):
            if (popup is not None and popup.windowType() == Qt.WindowType.Popup and popup.isVisible()
                    and popup.frameGeometry().contains(global_pos)):
                return True
        names = ("positions_card", "overlay_panel", "overlay_button")
        for widget in (getattr(self.view, name, None) for name in names):
            if widget is not None and widget.isVisible() and widget.rect().contains(widget.mapFromGlobal(global_pos)):
                return True
        return False

    def close_all(self) -> None:
        colour_menu = self.view.parts.colour_by.menu
        if colour_menu.isVisible():
            colour_menu.close()
        if self.card_open():
            self.view.parts.overlay_panel.set_atlas_legend_expanded(False)
        if self.panel_open():
            self.view.overlay_button.setChecked(False)

    def eventFilter(self, watched, event) -> bool:  # noqa: N802 - Qt API
        if (event.type() == QEvent.Type.MouseButtonPress and event.button() == Qt.MouseButton.RightButton
                and self.view.isVisible()):
            global_pos = event.globalPosition().toPoint()
            if (isinstance(watched, QWidget) and self.owning_window(watched) is self.view.window()
                    and not self.inside(global_pos, watched)):
                self.close_all()
        return False
