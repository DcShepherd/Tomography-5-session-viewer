"""The Atlas tab's Colour by control: the mode menu, the colour key and the card's notice (batch 4).

The Colour by button in the Positions card's header opens a ``RowMenu`` of the
modes ``AtlasOverlayState.offered`` gives, asked when the menu opens. A mode with
no data is a greyed choice in its place, its reason in a tooltip. Choosing a mode is one ``publish``: one
frame with the new colouring and one redraw.

The colour key (``ColourKey``) shows while the card shows and a value mode is
set, so Clean image hides both. When a mode cannot colour the markers shown,
the state returns to Status and the card shows why for a moment.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtCore import QPoint, Qt, QTimer
from PySide6.QtWidgets import QHBoxLayout, QRadioButton, QWidget

from tomography_session_browser.presentation.atlas_colouring import AtlasColouring, ColourMode, mode_name, mode_short_name
from tomography_session_browser.ui.viewer.colour_key import ColourKey
from tomography_session_browser.ui.widgets.row_menu import MenuRow, RowMenu

if TYPE_CHECKING:
    from tomography_session_browser.ui.viewer.tab_selection import TabSelection

#: How long the card says why a mode returned to Status.
NOTICE_MS = 8000


class UnavailableMode(QWidget):
    """A colour mode the atlas has no data for: a greyed choice whose tooltip says why.

    The row takes the focus (the radio inside is disabled and cannot), so the
    menu's arrow keys stop on it and a screen reader reads its reason.
    """

    def __init__(self, name: str, reason: str, parent: QWidget) -> None:
        super().__init__(parent)
        self.setObjectName("positionsColourUnavailable")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.reason = f"Not available. {reason}"
        self.setToolTip(self.reason)
        self.setAccessibleName(name)
        self.setAccessibleDescription(self.reason)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.choice = QRadioButton(name, self)
        self.choice.setObjectName("positionsColourChoice")
        self.choice.setEnabled(False)
        self.choice.setToolTip(self.reason)
        layout.addWidget(self.choice)


class ColourBy:
    def __init__(self, view: QWidget, *, selection: TabSelection, atlas_lod: bool) -> None:
        self.view = view
        self.selection = selection
        self.atlas_lod = atlas_lod
        self.menu = RowMenu(view)
        self.menu.setObjectName("positionsColourMenu")
        #: The menu's choices by mode; they live while the menu does.
        self.choices: dict[ColourMode, QRadioButton] = {}
        self.key = ColourKey(view)
        self.key.highlighted.connect(self.highlight)
        #: Whether the key fits between the scale bar and the zoom stack (``place``).
        self.key_fits = True
        #: Where ``place`` last put the key, so a key that changes height stays on the baseline.
        self.placed: tuple[int, int, int] | None = None
        self.notice_timer = QTimer(view)
        self.notice_timer.setSingleShot(True)
        self.notice_timer.setInterval(NOTICE_MS)
        self.notice_timer.timeout.connect(lambda: self.card_notice(None))

    def card_notice(self, text: str | None) -> None:
        card = getattr(self.view, "positions_card", None)
        if card is not None:
            card.set_notice(text)

    # Key hover (batch 5) -------------------------------------------------
    def highlight(self, ids: frozenset[str] | None) -> None:
        """The key points at ``ids``: the marker layer dims the other batch glyphs."""

        viewer = getattr(self.view, "viewer", None)
        if viewer is not None:
            viewer.parts.marker_layer.set_highlight(ids)

    def clear_highlight(self) -> None:
        """Show every glyph in full, as before an export."""

        self.key.set_current(None)
        self.highlight(None)

    # State --------------------------------------------------------------
    def sync(self) -> None:
        """Set the card's Colour by button and the key's visibility from the state."""

        card = getattr(self.view, "positions_card", None)
        if card is None or not self.atlas_lod:
            return
        state = self.selection.atlas_overlay
        card.set_colour_text(mode_name(state.mode) if state is not None else None,
                             mode_short_name(state.mode) if state is not None else None)
        if state is not None and state.notice:
            self.card_notice(f"Colour by returned to Status. {state.notice}")
            state.notice = None
            self.notice_timer.start()
        wanted = self.key.colouring is not None and not card.isHidden() and self.key_fits
        if self.key.isHidden() == wanted:
            self.key.setVisible(wanted)
            if wanted:
                self.key.raise_()

    def show(self, colouring: AtlasColouring | None) -> None:
        """The frame just drawn has ``colouring``: the key follows it."""

        height = self.key.height()
        self.key.set_colouring(colouring)
        if self.placed is not None and self.key.height() != height:
            self.place(*self.placed)
            return
        self.sync()

    def place(self, left: int, right: int, baseline: int) -> None:
        """Centre the key between ``left`` and ``right``, its bottom on ``baseline``."""

        self.placed = (left, right, baseline)
        self.key.set_available_width(right - left)
        self.key_fits = right - left >= self.key.minimumWidth() and right - left >= 150
        centre = (left + right) // 2
        self.key.move(centre - self.key.width() // 2, baseline - self.key.height())
        self.sync()

    # Menu ---------------------------------------------------------------
    def open_menu(self, anchor: QWidget) -> None:
        state = self.selection.atlas_overlay
        layer = self.view.viewer.parts.marker_layer
        if state is None or layer.input_markers is None:
            return
        self.menu.reset()
        self.choices = {}
        current = None
        for mode, reason in state.offered(layer.input_markers):
            if reason is not None:
                # Greyed, in its place, with the reason in its tooltip. The arrow
                # keys stop on it so a screen reader reads why; Enter does nothing.
                self.menu.add_row(MenuRow(mode, UnavailableMode(mode_name(mode), reason, self.menu), lambda: ()))
                continue
            choice = QRadioButton(mode_name(mode), self.menu)
            choice.setObjectName("positionsColourChoice")
            choice.setAutoExclusive(False)
            choice.setChecked(mode == state.mode)
            choice.setToolTip(f"Colour the batch positions by {mode_name(mode).lower()}")
            choice.clicked.connect(lambda _checked=False, value=mode: self.choose(value))
            self.choices[mode] = choice
            action = self.menu.add_row(MenuRow(mode, choice, lambda: (), activate=lambda value=mode: self.choose(value)))
            if mode == state.mode:
                current = action
        hint = self.menu.sizeHint()
        self.menu.popup(anchor.mapToGlobal(QPoint(0, -hint.height())))
        rows = [action for action in self.menu.choosable() if action in self.menu.row_actions]
        if rows:
            self.menu.focus_entry(current if current is not None else rows[0])

    def recolour(self) -> None:
        """The data behind the mode changed (tags, batch 6): one publish of the item shown."""

        if self.selection.current_value is not None:
            self.selection.publish(self.selection.current_value)
        self.sync()

    def choose(self, mode: ColourMode) -> None:
        state = self.selection.atlas_overlay
        if state is None:
            return
        for value, choice in self.choices.items():
            choice.setChecked(value == mode)
        self.menu.close()
        self.card_notice(None)
        if state.set_mode(mode):
            self.selection.publish(self.selection.current_value)
        self.sync()
