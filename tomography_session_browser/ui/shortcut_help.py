"""Thin adapter over live Qt bindings and the existing local-handler catalogue."""
from collections.abc import Mapping
from importlib import import_module

from PySide6.QtCore import QObject, Qt
from PySide6.QtGui import QAction, QKeySequence, QShortcut
from PySide6.QtWidgets import QWidget

from tomography_session_browser.presentation.help_content import (
    BindingScope, HelpBinding, HelpPage, InteractionKind, normalise_bindings,
)
from tomography_session_browser.presentation.tab_keys import TabKey
from tomography_session_browser.ui.local_keys import LOCAL_KEY_MODULES

QT_SCOPES = {
    Qt.ShortcutContext.WindowShortcut: (BindingScope.WINDOW, "This window"),
    Qt.ShortcutContext.WidgetShortcut: (BindingScope.CONTROL, "Focused control"),
    Qt.ShortcutContext.WidgetWithChildrenShortcut: (BindingScope.CONTROL, "Focused control and its children"),
    Qt.ShortcutContext.ApplicationShortcut: (BindingScope.APPLICATION, "Application"),
}


def shortcut_rows(window: QWidget, tabs: Mapping[str, QWidget]) -> tuple[HelpBinding, ...]:
    rows: list[HelpBinding] = []
    for binding in (*window.findChildren(QAction), *window.findChildren(QShortcut)):
        sequences = binding.shortcuts() if isinstance(binding, QAction) else binding.keys()
        tokens = tuple(key.toString(QKeySequence.SequenceFormat.NativeText) for key in sequences if not key.isEmpty())
        if not tokens:
            continue
        identity = binding.property("helpBindingId")
        if not isinstance(identity, str) or not identity:
            raise ValueError(f"A live {type(binding).__name__} has no semantic Help identity: {tokens}")
        binding_parent = binding.parent()
        parent: QObject | None = binding_parent
        eligible = frozenset(TabKey)
        owning_tab = None
        while parent is not None:
            owner = next((key for key, tab in tabs.items() if parent is tab), None)
            if owner is not None:
                eligible = frozenset((TabKey(owner),))
                owning_tab = tabs[owner]
                break
            parent = parent.parent()
        context = binding.shortcutContext() if isinstance(binding, QAction) else binding.context()
        scope, label = QT_SCOPES[context]
        if context == Qt.ShortcutContext.WidgetWithChildrenShortcut and owning_tab is not None and binding_parent is owning_tab:
            scope, label = BindingScope.TAB, "This tab and its controls"
        if scope == BindingScope.CONTROL and isinstance(binding_parent, QWidget):
            name = binding_parent.accessibleName()
            if name:
                label = f"{label} · {name}"
        description = binding.text().replace("&", "") if isinstance(binding, QAction) else binding.whatsThis()
        rows.append(HelpBinding(identity, eligible, label, scope, InteractionKind.KEYBOARD, tokens,
                                description or "Control shortcut"))
    for module_name, eligible in LOCAL_KEY_MODULES.items():
        module = import_module(f"tomography_session_browser.ui.{module_name}")
        rows.extend(HelpBinding(local.identity, eligible, local.context, BindingScope.LOCAL,
                                local.kind, local.tokens, local.description) for local in module.LOCAL_KEYS)
    return normalise_bindings(rows)


def show_help(window: QWidget, *, tabs: Mapping[str, QWidget], current: str,
              page: HelpPage = HelpPage.KEYBOARD, load_action: QAction | None = None):
    from tomography_session_browser.ui.help_window import HelpWindow
    help_window = window.findChild(HelpWindow, "helpWindow")
    if help_window is None:
        help_window = HelpWindow(window, load_action=load_action)
        action = window.findChild(QAction, "shortcutHelpAction")
        if action is not None:
            help_window.addAction(action)  # The same F1 owner in both top-level windows.
    help_window.open_page(page, TabKey(current), shortcut_rows(window, tabs))
    help_window.show()
    help_window.raise_()
    help_window.activateWindow()
    return help_window


def request_shortcut_help(tab: QWidget) -> None:
    action = tab.window().findChild(QAction, "shortcutHelpAction")
    if action is not None:
        action.trigger()
    else:
        show_help(tab, tabs={TabKey.TOMOGRAMS: tab}, current=TabKey.TOMOGRAMS)
