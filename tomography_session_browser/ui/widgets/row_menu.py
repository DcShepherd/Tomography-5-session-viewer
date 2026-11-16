"""Keyboard traversal shared by the row menus (captures, movies, the Atlas From menu).

The base knows rows only through a small registration: a stable key, the
row's widget, its ordered buttons and what Enter does on the row itself. It
never knows what a row is. Footers are plain menu actions with their own keys.
"""
from __future__ import annotations

from collections.abc import Callable, Hashable
from dataclasses import dataclass

from PySide6.QtCore import QEvent, Qt
from PySide6.QtGui import QAction
from PySide6.QtWidgets import QAbstractButton, QApplication, QMenu, QWidget, QWidgetAction

from tomography_session_browser.presentation.help_content import InteractionKind, LocalBinding

#: Keys that the menu keeps from window shortcuts while it is open. Delete is kept
#: too, so that it never reaches the window: only a menu that removes rows uses it.
MENU_KEYS = frozenset((Qt.Key.Key_Tab, Qt.Key.Key_Backtab, Qt.Key.Key_Up, Qt.Key.Key_Down,
    Qt.Key.Key_Left, Qt.Key.Key_Right, Qt.Key.Key_Return, Qt.Key.Key_Enter,
    Qt.Key.Key_Space, Qt.Key.Key_Delete, Qt.Key.Key_Escape))


@dataclass(frozen=True, slots=True)
class FooterKey:
    """A footer's key; never equal to a row key."""
    name: str


@dataclass(frozen=True, slots=True, eq=False)
class MenuRow:
    key: Hashable
    widget: QWidget
    controls: Callable[[], tuple[QAbstractButton, ...]]
    #: Enter or Space on the row itself; None when the row has no default action.
    activate: Callable[[], None] | None = None


@dataclass(frozen=True, slots=True)
class FocusAnchor:
    key: Hashable
    control: int | None


class RowMenu(QMenu):
    def __init__(self, parent) -> None:
        super().__init__(parent)
        self.row_actions: dict[QAction, MenuRow] = {}
        self.action_keys: dict[QAction, Hashable] = {}
        self.setToolTipsVisible(True)

    # Building -----------------------------------------------------------
    def reset(self) -> FocusAnchor | None:
        """Clear the menu for a rebuild; returns where the focus was."""
        anchor = self.anchor()
        self.row_actions.clear()
        self.action_keys.clear()
        self.clear()
        return anchor

    def add_row(self, row: MenuRow) -> QAction:
        action = QWidgetAction(self)
        action.setDefaultWidget(row.widget)
        self.addAction(action)
        self.row_actions[action] = row
        self.action_keys[action] = row.key
        for child in (row.widget, *row.controls()):
            child.installEventFilter(self)
        return action

    def footer(self, name: str, text: str, callback) -> QAction:
        action = self.addAction(text, callback)
        self.action_keys[action] = FooterKey(name)
        return action

    def note(self, text: str) -> QAction:
        """A line of text that cannot be chosen."""
        action = self.addAction(text)
        action.setEnabled(False)
        return action

    # Focus --------------------------------------------------------------
    def usable_controls(self, row: MenuRow) -> list[QAbstractButton]:
        return [button for button in row.controls() if button.isVisible() and button.isEnabled()]

    def choosable(self) -> list[QAction]:
        return [action for action in self.actions()
                if action.isEnabled() and action.isVisible() and not action.isSeparator()]

    def current_entry(self) -> QAction | None:
        focused = QApplication.focusWidget()
        for action, row in self.row_actions.items():
            if focused is row.widget or (focused is not None and row.widget.isAncestorOf(focused)):
                return action
        return self.activeAction()

    def anchor(self) -> FocusAnchor | None:
        action = self.current_entry()
        if action not in self.action_keys:
            return None
        row = self.row_actions.get(action)
        controls = row.controls() if row is not None else ()
        focused = QApplication.focusWidget()
        return FocusAnchor(self.action_keys[action], controls.index(focused) if focused in controls else None)

    def focus_entry(self, action: QAction | None, control: QAbstractButton | None = None) -> None:
        self.setActiveAction(action)
        row = self.row_actions.get(action) if action is not None else None
        target = control or (row.widget if row is not None else None) or self
        target.setFocus(Qt.FocusReason.PopupFocusReason)

    def restore_anchor(self, anchor: FocusAnchor | None) -> None:
        """The same row and button if still usable; else that row, a footer, a row, then the menu itself.

        Nothing is activated while the focus falls back.
        """
        if anchor is None or not self.isVisible():
            return
        for action, key in self.action_keys.items():
            if key != anchor.key or not action.isEnabled() or not action.isVisible():
                continue
            row = self.row_actions.get(action)
            controls = row.controls() if row is not None else ()
            control = controls[anchor.control] if anchor.control is not None and anchor.control < len(controls) else None
            self.focus_entry(action, control if control is not None and control.isVisible() and control.isEnabled() else None)
            return
        usable = self.choosable()
        footers = [action for action in usable if action in self.action_keys and action not in self.row_actions]
        rows = [action for action in usable if action in self.row_actions]
        fallback = (footers or rows or [None])[0]
        if fallback is None:
            self.setActiveAction(None)
            self.setFocus(Qt.FocusReason.PopupFocusReason)
        else:
            self.focus_entry(fallback)

    def focusNextPrevChild(self, forward: bool) -> bool:  # noqa: N802
        targets: list[QAction | QAbstractButton] = []
        for action in self.choosable():
            targets.append(action)
            row = self.row_actions.get(action)
            if row is not None:
                targets.extend(self.usable_controls(row))
        if not targets:
            return False
        focused = QApplication.focusWidget()
        current = focused if focused in targets else self.current_entry()
        index = targets.index(current) if current in targets else (-1 if forward else 0)
        target = targets[(index + (1 if forward else -1)) % len(targets)]
        if isinstance(target, QAction):
            self.focus_entry(target)
        else:
            action = next(action for action, row in self.row_actions.items() if target in row.controls())
            self.focus_entry(action, target)
        return True

    # Keys ---------------------------------------------------------------
    def eventFilter(self, watched, event) -> bool:  # noqa: N802
        if event.type() == QEvent.Type.ShortcutOverride and event.key() in MENU_KEYS:
            event.accept()
            return True
        if event.type() == QEvent.Type.KeyPress and event.key() in MENU_KEYS:
            self.keyPressEvent(event)
            return True
        return super().eventFilter(watched, event)

    def event(self, event) -> bool:
        if event.type() == QEvent.Type.ShortcutOverride and event.key() in MENU_KEYS:
            event.accept()
            return True
        return super().event(event)

    def row_key_pressed(self, key: int, action: QAction | None, row: MenuRow | None) -> bool:
        """A menu's own keys on a row (for example Delete); True when handled."""
        return False

    def keyPressEvent(self, event) -> None:  # noqa: N802
        key = event.key()
        action = self.current_entry()
        row = self.row_actions.get(action)
        if key in (Qt.Key.Key_Tab, Qt.Key.Key_Backtab):
            self.focusNextPrevChild(key == Qt.Key.Key_Tab and not event.modifiers() & Qt.KeyboardModifier.ShiftModifier)
        elif key in (Qt.Key.Key_Up, Qt.Key.Key_Down):
            entries = self.choosable()
            index = entries.index(action) if action in entries else (-1 if key == Qt.Key.Key_Down else 0)
            if entries:
                self.focus_entry(entries[(index + (1 if key == Qt.Key.Key_Down else -1)) % len(entries)])
        elif key in (Qt.Key.Key_Left, Qt.Key.Key_Right) and row is not None:
            controls = self.usable_controls(row)
            self.focus_entry(action, controls[0] if key == Qt.Key.Key_Right and controls else None)
        elif key in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Space):
            focused = QApplication.focusWidget()
            if row is not None and focused in row.controls():
                if focused.isEnabled():
                    focused.click()
            elif row is not None:
                if row.activate is not None:
                    row.activate()
            elif action is not None and action.isEnabled():
                self.close()
                action.trigger()
        elif key == Qt.Key.Key_Escape:
            self.close()
        elif key == Qt.Key.Key_Delete:
            self.row_key_pressed(key, action, row)
        else:
            return super().keyPressEvent(event)
        event.accept()


# Contextual keys handled by this module; consumed by the shared Help sheet.
LOCAL_KEYS = (
    LocalBinding('widgets.row_menu.row-menu-up', 'Row menus', InteractionKind.KEYBOARD, ('Up', 'Down'), 'Select a row'),
    LocalBinding('widgets.row_menu.row-menu-tab', 'Row menus', InteractionKind.KEYBOARD, ('Tab', 'Shift+Tab'), 'Move through row actions'),
    LocalBinding('widgets.row_menu.row-menu-right', 'Row menus', InteractionKind.KEYBOARD, ('Right',), 'Enter row actions'),
    LocalBinding('widgets.row_menu.row-menu-left', 'Row menus', InteractionKind.KEYBOARD, ('Left',), 'Return to the row'),
    LocalBinding('widgets.row_menu.row-menu-enter', 'Row menus', InteractionKind.KEYBOARD, ('Enter', 'Return', 'Space'), 'Activate the focused action'),
    LocalBinding('widgets.row_menu.row-menu-esc', 'Row menus', InteractionKind.KEYBOARD, ('Esc',), 'Close the menu'),
)
