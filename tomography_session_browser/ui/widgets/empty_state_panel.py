"""Illustrated empty states: one centred group, semantic evidence and safe actions.

Decoration gives way before content in a short viewport. Long explanations and
actions scroll together; sprites and asides never enter copied diagnostics.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal, QEvent, QSize
from PySide6.QtWidgets import (
    QAbstractButton,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QToolButton,
    QVBoxLayout,
    QWidget,
    QFrame, QGridLayout, QBoxLayout, QLayout, QScrollArea,
)

from tomography_session_browser.presentation.design_tokens import CANVAS_CHROME
from tomography_session_browser.presentation.empty_states import (
    COMMAND_COPY_DETAILS,
    KIND_LOAD_FAILED,
    EmptyState,
    DECORATIONS,
)
from tomography_session_browser.ui.widgets.empty_sprite import EmptySprite
from tomography_session_browser.ui.theme import current_palette
from tomography_session_browser.ui.icons import themed_icon
from tomography_session_browser.ui.widgets.elided_label import ElidedLabel
from tomography_session_browser.ui.widgets.link_button import link_button
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
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self._state: EmptyState | None = None

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        self.scroll = QScrollArea(self)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll.viewport().installEventFilter(self)
        self.content = QWidget(self.scroll)
        self.scroll.setWidget(self.content)
        outer.addWidget(self.scroll)
        outer = self.group_layout = QVBoxLayout(self.content)
        outer.setSizeConstraint(QLayout.SizeConstraint.SetNoConstraint)
        outer.setContentsMargins(24, 20, 24, 20)
        outer.setSpacing(6)
        outer.addStretch(46)

        self.body = QWidget(self)
        self.body.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self._stacked = None
        grid = self.body_grid = QGridLayout(self.body)
        grid.setSizeConstraint(QLayout.SizeConstraint.SetNoConstraint)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setColumnStretch(1, 1)
        self.sprite = EmptySprite(self.body)
        grid.addWidget(self.sprite, 0, 0, Qt.AlignmentFlag.AlignBottom)
        self.card = QFrame(self.body)
        self.card.setObjectName("emptyStateCard")
        card_layout = QVBoxLayout(self.card)
        card_layout.setContentsMargins(24, 20, 24, 20)
        card_layout.setSpacing(8)
        grid.addWidget(self.card, 0, 1)
        outer.addWidget(self.body, 0, Qt.AlignmentFlag.AlignHCenter)

        self.title_label = QLabel("", self)
        self.title_label.setObjectName("emptyStateTitle")
        apply_tier(self.title_label, TIER_LANDMARK)
        self.title_label.setWordWrap(True)
        self.title_label.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        card_layout.addWidget(self.title_label)

        self.detail_label = QLabel("", self)
        self.detail_label.setObjectName("emptyStateDetail")
        apply_tier(self.detail_label, TIER_PRIMARY)
        self.detail_label.setWordWrap(True)
        self.detail_label.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        card_layout.addWidget(self.detail_label)

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
        card_layout.addWidget(self.evidence_label)

        # Where it was looked for, as it is, and a button to copy it.
        self.path_row = QWidget(self)
        path_layout = QHBoxLayout(self.path_row)
        path_layout.setContentsMargins(0, 0, 0, 0)
        path_layout.setSpacing(4)
        path_layout.addStretch(1)
        # A path is long and has no spaces to wrap at: it gives way in the middle, and
        # the button copies the whole of it.
        self.path_label = ElidedLabel("", mode=Qt.TextElideMode.ElideMiddle, parent=self.path_row, prefer_full_width=True)
        self.path_label.setObjectName("emptyStatePath")
        apply_tier(self.path_label, TIER_SUPPORTING)
        path_layout.addWidget(self.path_label)
        self.copy_button = QToolButton(self.path_row)
        self.copy_button.setObjectName("emptyStateCopy")
        self.copy_button.setIcon(themed_icon("copy", color=current_palette().text_muted, size=14))
        self.copy_button.setToolTip("Copy the path")
        self.copy_button.setAccessibleName("Copy the path")
        self.copy_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.copy_button.clicked.connect(lambda: self._emit(COMMAND_COPY_DETAILS))
        path_layout.addWidget(self.copy_button)
        path_layout.addStretch(1)
        self.path_row.setVisible(False)
        card_layout.addWidget(self.path_row)
        self.aside = QLabel(self.card)
        self.aside.setObjectName("emptyStateAside")
        self.aside.setWordWrap(True)
        self.aside.setTextFormat(Qt.TextFormat.PlainText)
        for label in (self.title_label, self.detail_label, self.evidence_label):
            label.setAlignment(Qt.AlignmentFlag.AlignLeft)
            label.setTextFormat(Qt.TextFormat.PlainText)

        button_row = QHBoxLayout()
        button_row.setSpacing(8)
        self.primary_button = QPushButton("", self.card)
        # The one primary recipe, shared with the dialogs (#primaryButton).
        self.primary_button.setObjectName("primaryButton")
        self.primary_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.primary_button.clicked.connect(self._emit_primary)
        button_row.addWidget(self.primary_button)
        self._secondary_row = button_row
        self._secondary_buttons: list[QAbstractButton] = []
        button_row.addStretch(1)
        card_layout.addLayout(button_row)
        card_layout.addWidget(self.aside)
        outer.addStretch(54)

        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.set_state(None)


    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._adapt()

    def minimumSizeHint(self) -> QSize:  # noqa: N802
        return QSize(1, 1)

    def eventFilter(self, watched, event) -> bool:  # noqa: N802
        if watched is self.scroll.viewport() and event.type() == QEvent.Type.Resize and hasattr(self, "sprite"):
            self._adapt()
        return super().eventFilter(watched, event)

    def changeEvent(self, event) -> None:  # noqa: N802
        super().changeEvent(event)
        if event.type() == QEvent.Type.StyleChange and hasattr(self, "copy_button"):
            self.copy_button.setIcon(themed_icon("copy", color=current_palette().text_muted, size=14))
        if event.type() in (QEvent.Type.StyleChange, QEvent.Type.FontChange) and hasattr(self, '_secondary_buttons'):
            self._adapt()

    def _adapt(self) -> None:
        decoration = DECORATIONS.get(self._state.kind) if self._state else None
        margin = 8 if self.height() < 400 else 24
        self.group_layout.setContentsMargins(margin, margin, margin, margin)
        available = max(1, self.scroll.viewport().width() - 2 * margin)
        card_width = min(available, max(420, min(560, round(.3 * self.width()))))
        self.card.setFixedWidth(card_width)
        side = 80 if self.width() < 540 else max(128, min(240, round(.2 * self.height())))
        if self.height() < 360:
            side = min(side, max(1, self.height() // 5))
        self.sprite.configure(decoration.sprite if decoration and self.height() >= 240 else "", side)
        side = self.sprite.logical_side if not self.sprite.isHidden() else 0
        narrow = available < card_width + side + 24
        self.body.setFixedWidth(card_width if narrow or not side else card_width + side + 24)
        self.body_grid.setHorizontalSpacing(24 if side else 0)
        if narrow != self._stacked:
            self._stacked = narrow
            self.body_grid.removeWidget(self.sprite)
            self.body_grid.removeWidget(self.card)
            self.body_grid.addWidget(self.sprite, 0, 0, 1, 2 if narrow else 1,
                                     Qt.AlignmentFlag.AlignHCenter if narrow else Qt.AlignmentFlag.AlignBottom)
            self.body_grid.addWidget(self.card, 1 if narrow else 0, 0 if narrow else 1, 1, 2 if narrow else 1)
        buttons = [self.primary_button, *self._secondary_buttons]
        required = sum(button.sizeHint().width() + 8 for button in buttons if not button.isHidden())
        self._secondary_row.setDirection(QBoxLayout.Direction.TopToBottom if required > card_width - 48
                                         else QBoxLayout.Direction.LeftToRight)
        card_layout = self.card.layout()
        card_layout.invalidate()
        self.card.setFixedHeight(max(card_layout.totalMinimumSize().height(),
                                    card_layout.totalHeightForWidth(card_width)))
        self.body_grid.invalidate()
        height = max(self.body_grid.minimumSize().height(), self.body_grid.heightForWidth(self.body.width()))
        self.body.setFixedHeight(max(1, height))
        self.content.setMinimumHeight(height + 2 * margin)
        room = max(0, self.scroll.viewport().height() - 2 * margin - height)
        top = max(0, round(.46 * self.scroll.viewport().height() - margin - height / 2))
        self.group_layout.setStretch(0, min(room, top))
        self.group_layout.setStretch(2, max(0, room - top))

    # -- public API ---------------------------------------------------------

    def set_state(self, state: EmptyState | None) -> None:
        self._state = state
        decoration = DECORATIONS.get(state.kind) if state else None
        self.aside.setText(decoration.aside if decoration else "")
        self.aside.setVisible(decoration is not None)
        for button in self._secondary_buttons:
            self._secondary_row.removeWidget(button)
            button.hide()
            button.deleteLater()
        self._secondary_buttons = []

        if state is None:
            self.title_label.setText("")
            self.detail_label.setText("")
            self.evidence_label.setText("")
            self.path_row.setVisible(False)
            self.primary_button.setVisible(False)
            self.setAccessibleName("")
            self.setAccessibleDescription("")
            self._adapt()
            return

        self.title_label.setText(state.title)
        self.detail_label.setText(state.detail)
        self.detail_label.setVisible(bool(state.detail))
        self.evidence_label.setText("\n".join(state.evidence))
        self.evidence_label.setVisible(bool(state.evidence))
        self.path_label.setText(state.path)
        self.path_label.setToolTip(state.path)
        copy_label = "Copy the path" if state.copyable_details == state.path else "Copy details"
        self.copy_button.setToolTip(copy_label)
        self.copy_button.setAccessibleName(copy_label)
        self.path_row.setVisible(bool(state.path))

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
            button = link_button(action.label, self.card)
            button.clicked.connect(
                lambda _checked=False, command=action.command: self._emit(command)
            )
            self._secondary_row.insertWidget(insert_at, button)
            # Qt otherwise queues the new button's first show. Include it now
            # when choosing the row direction and the fixed composition height.
            button.show()
            self._secondary_buttons.append(button)
            insert_at += 1

        self.setAccessibleName(state.title)
        self.setAccessibleDescription(
            " ".join(part for part in (state.detail, *state.evidence) if part)
        )
        self._adapt()
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
