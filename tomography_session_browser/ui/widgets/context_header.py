"""Compact "where am I?" strip shown above the tab workspace (plan item C1).

Renders a :class:`ContextStack` on one line: the review scope with its project
badge, the entity section, and the exact selection. Below it, a second row
carries anything currently narrowing or annotating what is on screen — an
active filter with a Clear action, borrowed Atlas context, an unresolved
relationship, or the reason an offered action is unavailable.

The reason row matters as much as the crumbs: before this widget, a disabled
navigation action explained itself only in a hover tooltip, which is invisible
to keyboard users and easy to miss.

All colour comes from theme tokens via object names, never inline hex, so both
palettes stay correct and `tests/test_theme_style_guide.py` keeps passing.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QBoxLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from tomography_session_browser.ui.context_stack import CRUMB_SEPARATOR, ContextStack
from tomography_session_browser.ui.theme import (
    TIER_LANDMARK,
    TIER_PRIMARY,
    TIER_SUPPORTING,
    apply_tier,
)
from tomography_session_browser.ui.widgets.elided_label import ElidedLabel

EMPTY_SCOPE_TEXT = "No session loaded"

#: The scope keeps this much room before it starts to elide, so the crumb tail
#: is what gives way in a cramped workspace.
SCOPE_MIN_WIDTH_PX = 160


class ContextHeader(QWidget):
    """One-glance statement of scope, section, selection and view state."""

    filter_clear_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("contextHeader")
        self._stack = ContextStack()
        self._reasons: tuple[str, ...] = ()
        # Kept separately because ElidedLabel.text() returns the *elided*
        # rendering, which callers and tests must not have to reason about.
        self._scope_text = ""
        self._crumb_text = ""
        self._notes_text = ""

        outer = QVBoxLayout(self)
        outer.setContentsMargins(12, 6, 12, 6)
        outer.setSpacing(2)

        self._narrow = False
        crumb_row = QHBoxLayout()
        crumb_row.setContentsMargins(0, 0, 0, 0)
        crumb_row.setSpacing(8)
        self._crumb_row = crumb_row

        # Elided, like the rest of the strip. A plain QLabel sized itself to
        # the full text, so a long linked-group or session name widened the
        # header — and with it the window's minimum width — instead of
        # shortening. It keeps a generous floor so that when space does run
        # out the crumb tail gives way first: the scope answers "what am I
        # even looking at?" and must never be the thing squeezed out.
        self.scope_label = ElidedLabel("", tooltip=False)
        self.scope_label.setObjectName("contextScope")
        apply_tier(self.scope_label, TIER_LANDMARK)
        self.scope_label.setMinimumWidth(SCOPE_MIN_WIDTH_PX)
        self.scope_label.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred)
        crumb_row.addWidget(self.scope_label)

        self.crumb_label = ElidedLabel("")
        self.crumb_label.setObjectName("contextCrumbs")
        apply_tier(self.crumb_label, TIER_PRIMARY)
        self.crumb_label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        crumb_row.addWidget(self.crumb_label, stretch=1)
        outer.addLayout(crumb_row)

        note_row = QHBoxLayout()
        note_row.setContentsMargins(0, 0, 0, 0)
        note_row.setSpacing(8)

        self.notes_label = ElidedLabel("")
        self.notes_label.setObjectName("contextNotes")
        apply_tier(self.notes_label, TIER_SUPPORTING)
        self.notes_label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        note_row.addWidget(self.notes_label, stretch=1)

        self.clear_filter_button = QPushButton("Clear filter")
        self.clear_filter_button.setObjectName("contextClearFilter")
        self.clear_filter_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.clear_filter_button.setFlat(True)
        self.clear_filter_button.clicked.connect(self.filter_clear_requested.emit)
        self.clear_filter_button.setVisible(False)
        note_row.addWidget(self.clear_filter_button)
        self._note_row = note_row
        outer.addLayout(note_row)

        self.setAccessibleName("Review context")
        self._refresh()

    # -- public API ---------------------------------------------------------

    def set_narrow(self, narrow: bool) -> None:
        """Recompose for a cramped workspace.

        The scope is the one thing that must never be squeezed out — it is the
        answer to "what am I even looking at?". In narrow mode the crumb tail
        moves onto its own line beneath it rather than competing for the same
        row, and the Clear action keeps its full hit target while its label
        shortens.
        """

        if narrow == self._narrow:
            return
        self._narrow = narrow
        self._crumb_row.setDirection(
            QBoxLayout.Direction.TopToBottom if narrow else QBoxLayout.Direction.LeftToRight
        )
        self._crumb_row.setSpacing(0 if narrow else 8)
        self.clear_filter_button.setText("Clear" if narrow else "Clear filter")
        self._refresh()

    def is_narrow(self) -> bool:
        return self._narrow

    def set_context_stack(self, stack: ContextStack | None) -> None:
        self._stack = stack or ContextStack()
        self._refresh()

    def set_unavailable_reasons(self, reasons: tuple[str, ...] | list[str]) -> None:
        """Explain, inline, why offered actions are currently unavailable.

        Deduplicated and order-preserving, because several disabled actions
        commonly share one cause and repeating it is noise.
        """

        seen: dict[str, None] = {}
        for reason in reasons:
            text = (reason or "").strip()
            if text:
                seen.setdefault(text, None)
        self._reasons = tuple(seen)
        self._refresh()

    def context_stack(self) -> ContextStack:
        return self._stack

    def scope_text(self) -> str:
        """The full scope name, before elision."""

        return self._scope_text

    def crumb_text(self) -> str:
        """The full crumb tail, before elision."""

        return self._crumb_text

    def notes_text(self) -> str:
        """The full view-state and unavailability text, before elision."""

        return self._notes_text

    # -- rendering ----------------------------------------------------------

    def _refresh(self) -> None:
        stack = self._stack

        scope = stack.scope_label or EMPTY_SCOPE_TEXT
        self._scope_text = scope
        self.scope_label.setText(scope)
        self.scope_label.setToolTip(scope)

        # The scope already has its own emphasised label, so the crumb trail
        # carries only what follows it.
        tail = [part for part in (stack.section, stack.selection_label) if part]
        crumbs = CRUMB_SEPARATOR.join(tail)
        # Stacked under the scope in narrow mode, so drop the leading separator
        # that only makes sense when the two sit on one line.
        if not crumbs:
            self._crumb_text = ""
        elif self._narrow:
            self._crumb_text = crumbs
        else:
            self._crumb_text = f"{CRUMB_SEPARATOR}{crumbs}"
        self.crumb_label.setText(self._crumb_text)
        self.crumb_label.setToolTip(stack.to_text())

        notes = list(stack.view_state_notes())
        notes.extend(f"Unavailable: {reason}" for reason in self._reasons)
        text = "   ·   ".join(notes)
        self._notes_text = text
        self.notes_label.setText(text)
        self.notes_label.setToolTip("\n".join(notes))
        self.notes_label.setVisible(bool(text))
        self.clear_filter_button.setVisible(stack.has_filter)

        self.setAccessibleDescription(
            " ".join(part for part in (stack.to_text(), text) if part) or EMPTY_SCOPE_TEXT
        )
