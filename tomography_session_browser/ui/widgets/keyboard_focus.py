"""The focus reasons that earn a keyboard ring on a painted widget."""
from PySide6.QtCore import Qt


def keyboard_focus_reason(reason: Qt.FocusReason) -> bool:
    return reason in (Qt.FocusReason.TabFocusReason, Qt.FocusReason.BacktabFocusReason,
                      Qt.FocusReason.ShortcutFocusReason)
