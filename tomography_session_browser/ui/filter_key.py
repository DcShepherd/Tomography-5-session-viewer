"""The one key that puts the caret in the current filter (D12, Batch 4c).

The window's action and every filter's tooltip read it from here, so the
tooltip cannot name a key that the action does not use.
"""

from PySide6.QtGui import QKeySequence

FILTER_KEY = "Ctrl+F"
FILTER_ACTION_TEXT = "Filter the current list"
FILTER_HELP_ID = "shell.filter.focus"


def with_filter_key(tooltip: str) -> str:
    """``tooltip`` with the key at its end: "… (Ctrl+F)"."""

    return f"{tooltip} ({QKeySequence(FILTER_KEY).toString(QKeySequence.SequenceFormat.NativeText)})"
