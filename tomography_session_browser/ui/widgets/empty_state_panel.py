"""Renders an :class:`EmptyState` as a real panel (plan item V1).

The six variants in ``ui/empty_states.py`` were modelled and tested in E1, but
only the viewer's empty *title* consumed them — so ``load_failed`` and
``missing_preview``, both fully specified, could never actually be seen. This
is the surface they were missing.

Restrained by design: a heading, a sentence, the evidence that survives the
emptiness, one primary button and any secondary links. No illustration, no
oversized chrome, no added whitespace — the app is a dense review tool and an
empty state is not an occasion for decoration.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from tomography_session_browser.ui.empty_states import (
    KIND_LOAD_FAILED,
    EmptyState,
)
from tomography_session_browser.ui.theme import (
    TIER_LANDMARK,
    TIER_PRIMARY,
    TIER_SUPPORTING,
    apply_tier,
)


class EmptyStatePanel(QWidget):
    """A state-specific empty experience with exactly one primary action."""

    action_requested = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("emptyStatePanel")
        self._state: EmptyState | None = None

        outer = QVBoxLayout(self)
        outer.setContentsMargins(24, 20, 24, 20)
        outer.setSpacing(6)
        outer.addStretch(1)

        self.title_label = QLabel("", self)
        self.title_label.setObjectName("emptyStateTitle")
        apply_tier(self.title_label, TIER_LANDMARK)
        self.title_label.setWordWrap(True)
        self.title_label.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        outer.addWidget(self.title_label)

        self.detail_label = QLabel("", self)
        self.detail_label.setObjectName("emptyStateDetail")
        apply_tier(self.detail_label, TIER_PRIMARY)
        self.detail_label.setWordWrap(True)
        self.detail_label.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        outer.addWidget(self.detail_label)

        # What survives the emptiness. A reviewer should never be told only
        # what is absent.
        self.evidence_label = QLabel("", self)
        self.evidence_label.setObjectName("emptyStateEvidence")
        apply_tier(self.evidence_label, TIER_SUPPORTING)
        self.evidence_label.setWordWrap(True)
        self.evidence_label.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        self.evidence_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        outer.addWidget(self.evidence_label)

        button_row = QHBoxLayout()
        button_row.setSpacing(8)
        button_row.addStretch(1)
        self.primary_button = QPushButton("", self)
        self.primary_button.setObjectName("primaryAction")
        self.primary_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.primary_button.clicked.connect(self._emit_primary)
        button_row.addWidget(self.primary_button)
        self._secondary_row = button_row
        self._secondary_buttons: list[QPushButton] = []
        button_row.addStretch(1)
        outer.addLayout(button_row)
        outer.addStretch(2)

        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.set_state(None)

    # -- public API ---------------------------------------------------------

    def set_state(self, state: EmptyState | None) -> None:
        self._state = state
        for button in self._secondary_buttons:
            self._secondary_row.removeWidget(button)
            button.deleteLater()
        self._secondary_buttons = []

        if state is None:
            self.title_label.setText("")
            self.detail_label.setText("")
            self.evidence_label.setText("")
            self.primary_button.setVisible(False)
            self.setAccessibleName("")
            return

        self.title_label.setText(state.title)
        self.detail_label.setText(state.detail)
        self.detail_label.setVisible(bool(state.detail))
        self.evidence_label.setText("\n".join(state.evidence))
        self.evidence_label.setVisible(bool(state.evidence))

        if state.primary is not None:
            self.primary_button.setText(state.primary.label)
            self.primary_button.setVisible(True)
        else:
            # Two variants offer no primary action on purpose: an unresolved
            # relationship has no destination, and a preview with no readable
            # metadata has nothing to show.
            self.primary_button.setVisible(False)

        insert_at = self._secondary_row.count() - 1
        for action in state.secondary:
            button = QPushButton(action.label, self)
            button.setObjectName("secondaryAction")
            button.setFlat(True)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.clicked.connect(
                lambda _checked=False, command=action.command: self._emit(command)
            )
            self._secondary_row.insertWidget(insert_at, button)
            self._secondary_buttons.append(button)
            insert_at += 1

        self.setAccessibleName(state.title)
        self.setAccessibleDescription(
            " ".join(part for part in (state.detail, *state.evidence) if part)
        )
        # A failed load is an interruption, not a background condition: move to
        # the recovery action so it can be reached without hunting for it.
        if state.kind == KIND_LOAD_FAILED:
            self._focus_primary_action()

    def state(self) -> EmptyState | None:
        return self._state

    def secondary_labels(self) -> list[str]:
        return [button.text() for button in self._secondary_buttons]

    # -- actions ------------------------------------------------------------

    def copyable_details(self) -> str:
        """The text ``copy_details`` offers, for whoever owns the clipboard."""

        return self._state.copyable_details if self._state is not None else ""

    def _emit_primary(self) -> None:
        if self._state is not None and self._state.primary is not None:
            self._emit(self._state.primary.command)

    def _emit(self, command: str) -> None:
        # The panel does not touch the clipboard. It used to write the text
        # *and* emit, while the window separately announced the copy, so two
        # files half-owned one action. The panel offers the text; the window
        # performs and reports the copy.
        self.action_requested.emit(command)

    def _focus_primary_action(self) -> None:
        """Put focus on the recovery action when a failure panel appears.

        A keyboard user is otherwise left with focus wherever it happened to be
        when the load failed, with no indication that Retry now exists.
        """

        if self.primary_button.isVisible() and self.primary_button.isEnabled():
            self.primary_button.setFocus(Qt.FocusReason.OtherFocusReason)
