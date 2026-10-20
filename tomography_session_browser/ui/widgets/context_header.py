"""Compact "where am I?" strip shown above the tab workspace (plan item C1).

Renders a :class:`ContextStack` on one line: the scopes above what the page
titles, and, on a viewer tab, the review scope with its project badge as the
last step. The tab names the section and the page titles the item, so neither
is repeated. Below it, a second row
carries borrowed Atlas context or an unresolved
relationship. Why a jump is unavailable is the viewer's to say, under its
Linked strip (``ui/viewer/viewer_header.py``).

All colour comes from theme tokens via object names, never inline hex, so both
palettes stay correct and `tests/test_theme_style_guide.py` keeps passing.
"""

from __future__ import annotations

from PySide6.QtCore import QEvent, QSize, Qt
from PySide6.QtWidgets import (
    QBoxLayout,
    QHBoxLayout,
    QLabel,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from tomography_session_browser.presentation.context_stack import CRUMB_SEPARATOR, ContextStack
from tomography_session_browser.ui.theme import (
    TIER_LANDMARK,
    TIER_PRIMARY,
    TIER_SUPPORTING,
    apply_tier,
)
from tomography_session_browser.ui.widgets.elided_label import ElidedLabel, WrappedElidedLabel

EMPTY_SCOPE_TEXT = "No session loaded"

#: The notes row wraps onto this many lines before eliding.
NOTES_MAX_LINES = 2
NOTE_SEPARATOR = "  ·  "

#: The scope keeps this much room before it starts to elide, so the crumb tail
#: is what gives way in a cramped workspace.
SCOPE_MIN_WIDTH_PX = 160


class _ScopeLabel(ElidedLabel):
    """The scope: keeps up to ``SCOPE_MIN_WIDTH_PX`` before it elides, but
    never more room than its own text needs.

    A flat 160 px floor left a shorter scope such as ``Collection_demo (LS)``
    sitting in a wider box, with a visible gap before ``› Section`` (plan
    C10). The floor now follows the text up to that cap, so a long name still
    cannot widen the window, and a short one sits flush against its crumbs.
    """

    def __init__(self) -> None:
        super().__init__("", tooltip=False, prefer_full_width=True)
        self._update_floor()

    def setText(self, text: str) -> None:  # noqa: N802 - Qt API
        super().setText(text)
        self._update_floor()

    def changeEvent(self, event) -> None:  # noqa: N802 - Qt API
        super().changeEvent(event)
        # The sheet's font lands on polish; the floor must follow it.
        if event.type() in (QEvent.Type.FontChange, QEvent.Type.StyleChange):
            self._update_floor()

    def minimumSizeHint(self) -> QSize:  # noqa: N802 - Qt API
        # A layout sizes an item by the larger of its hint and its *minimum*
        # hint, and QLabel's minimum hint stayed at the width of an earlier,
        # longer text, which kept the gap open. The floor is the minimum.
        return QSize(self.minimumWidth(), super().minimumSizeHint().height())

    def _update_floor(self) -> None:
        # Safe from ElidedLabel's constructor, which calls setText: the hint
        # needs only state that constructor has already set.
        self.setMinimumWidth(min(SCOPE_MIN_WIDTH_PX, self.sizeHint().width()))
        self.updateGeometry()


class ContextHeader(QWidget):
    """One-glance statement of scope, section, selection and view state."""


    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("contextHeader")
        self._stack = ContextStack()
        # Kept separately because ElidedLabel.text() returns the *elided*
        # rendering, which callers and tests must not have to reason about.
        self._scope_text = ""
        self._crumb_text = ""
        self._notes_text = ""
        # Hidden for want of anything to say (see ``_refresh``).
        self._collapsed = False
        # The width given by ``fit_to_width``; None until the window says.
        self._available_width: int | None = None

        outer = QVBoxLayout(self)
        outer.setContentsMargins(12, 6, 12, 6)
        outer.setSpacing(2)

        self._narrow = False
        crumb_row = QHBoxLayout()
        crumb_row.setContentsMargins(0, 0, 0, 0)
        # No gap of its own: on one line the crumb text starts with the spaced
        # separator (" › "), and 8 px more made the first step wider than the
        # rest (plan C10). Stacked, the lines sit directly under each other.
        crumb_row.setSpacing(0)
        self._crumb_row = crumb_row

        # Elided, like the rest of the strip. A plain QLabel sized itself to
        # the full text, so a long linked-group or session name widened the
        # header — and with it the window's minimum width — instead of
        # shortening. It keeps a generous floor so that when space does run
        # out the crumb tail gives way first: the scope answers "what am I
        # even looking at?" and must never be the thing squeezed out.
        # The ancestors read first, coarsest to finest, and elide from the left
        # so the step nearest the scope is the last to go.
        self.crumb_label = ElidedLabel("", mode=Qt.TextElideMode.ElideLeft, tooltip=False, prefer_full_width=True)
        self.crumb_label.setObjectName("contextCrumbs")
        apply_tier(self.crumb_label, TIER_PRIMARY)
        self.crumb_label.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred)
        crumb_row.addWidget(self.crumb_label)

        self.scope_label = _ScopeLabel()
        self.scope_label.setObjectName("contextScope")
        apply_tier(self.scope_label, TIER_LANDMARK)
        self.scope_label.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred)
        crumb_row.addWidget(self.scope_label)
        crumb_row.addStretch(1)
        outer.addLayout(crumb_row)

        note_row = QHBoxLayout()
        note_row.setContentsMargins(0, 0, 0, 0)
        note_row.setSpacing(8)

        # Two lines before eliding: the unavailable-action reasons must be
        # readable inline (navigation contract), and a single elided line cut
        # them off after the first one.
        self.notes_label = WrappedElidedLabel("", max_lines=NOTES_MAX_LINES)
        self.notes_label.setObjectName("contextNotes")
        apply_tier(self.notes_label, TIER_SUPPORTING)
        self.notes_label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        note_row.addWidget(self.notes_label, stretch=1)

        self._note_row = note_row
        outer.addLayout(note_row)

        self.setAccessibleName("Review context")
        self._refresh()

    # -- public API ---------------------------------------------------------

    def set_narrow(self, narrow: bool) -> None:
        """Recompose for a cramped workspace.

        The scope is the one thing that must never be squeezed out — it is the
        answer to "what am I even looking at?". In narrow mode the crumb tail
        moves onto its own line beneath it rather than competing for the same row.
        """

        if narrow == self._narrow:
            return
        self._narrow = narrow
        self._crumb_row.setDirection(
            QBoxLayout.Direction.TopToBottom if narrow else QBoxLayout.Direction.LeftToRight
        )
        self._refresh()

    def is_narrow(self) -> bool:
        return self._narrow

    def fit_to_width(self, available: int) -> None:
        """Stack the crumbs under the scope only when one line cannot hold both.

        This replaces a fixed workspace breakpoint (plan F3, D1), under which
        the header took a second line on any laptop even when the scope and a
        short crumb trail fitted with room to spare. It is re-checked whenever
        the crumbs change, because a long selection name can tip the balance.
        """

        self._available_width = available
        self._refit()

    def _one_line_width(self) -> int:
        margins = self.layout().contentsMargins()
        crumbs = self.crumb_label.fontMetrics().horizontalAdvance(self._crumb_text) + 6 if self._crumb_text else 0
        scope = self.scope_label.sizeHint().width() if not self._stack.titles_scope else 0
        return margins.left() + margins.right() + scope + crumbs

    def _refit(self) -> None:
        if self._available_width is not None:
            self.set_narrow(self._one_line_width() > self._available_width)

    def set_context_stack(self, stack: ContextStack | None) -> None:
        self._stack = stack or ContextStack()
        self._refresh()

    def context_stack(self) -> ContextStack:
        return self._stack

    def scope_text(self) -> str:
        """The full scope name, before elision."""

        return self._scope_text

    def crumb_text(self) -> str:
        """The full trail of ancestors, before elision."""

        return self._crumb_text

    def notes_text(self) -> str:
        """The full view-state text, before elision."""

        return self._notes_text

    # -- rendering ----------------------------------------------------------

    def _refresh(self) -> None:
        stack = self._stack

        scope = stack.scope_label or EMPTY_SCOPE_TEXT
        self._scope_text = scope
        self.scope_label.setText(scope)
        self.scope_label.setToolTip(scope)
        # On the Session tab the page's own title is the scope: the header
        # stops at its ancestors rather than say the name a second time.
        self.scope_label.setVisible(not stack.titles_scope)

        # The ancestor trail is complete on its own, including when the scope
        # is on a different line or titled by the Summary page.
        ancestors = CRUMB_SEPARATOR.join(stack.ancestors)
        self._crumb_text = ancestors
        self.crumb_label.setText(self._crumb_text)
        self.crumb_label.setToolTip(stack.to_text())
        self.crumb_label.setVisible(bool(ancestors))

        notes = list(stack.view_state_notes())
        text = NOTE_SEPARATOR.join(notes)
        self._notes_text = text
        self.notes_label.setText(text)
        self.notes_label.setToolTip("\n".join(notes))
        self.notes_label.setVisible(bool(text))
        # Nothing to say (a top-level group on its Session tab): no empty strip.
        collapsed = not (ancestors or not stack.titles_scope or text)
        if collapsed != self._collapsed:
            self._collapsed = collapsed
            self.setVisible(not collapsed)

        self.setAccessibleDescription(
            " ".join(part for part in (stack.to_text(), text, stack.filter_summary) if part) or EMPTY_SCOPE_TEXT
        )
        # New crumbs may no longer fit on the scope's line, or may now fit.
        self._refit()
