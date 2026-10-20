"""Viewer header for the image viewer."""
from __future__ import annotations
import re
from typing import Any
from PySide6.QtCore import Qt
from PySide6.QtGui import QFontMetrics
from PySide6.QtWidgets import QHBoxLayout, QLabel, QMenu, QPushButton, QSizePolicy, QWidget
from tomography_session_browser.presentation.viewer_items import STATUS_ROW_TYPES, chips_for_item as _chips_for_item
from tomography_session_browser.services.item_status import display_status_label
from tomography_session_browser.services.navigation import tab_label_for_object
from tomography_session_browser.ui.icons import themed_icon
from tomography_session_browser.ui.motion.content_change import ContentTransition
from tomography_session_browser.presentation.design_tokens import SPACE_XS, SPACE_S
from tomography_session_browser.ui.fit_first import COMPACT_VIEWER_HEIGHT_PX
from tomography_session_browser.ui.theme import TIER_SUPPORTING, apply_tier
from tomography_session_browser.ui.widgets.elided_label import ElidedLabel, WrappedElidedLabel
from tomography_session_browser.ui.viewer.exposure_chooser import ExposureChooser
from tomography_session_browser.ui.viewer.constants import HEADER_TITLE_MIN_PX, IMAGE_HIERARCHY, JUMP_DESCRIPTIONS, JUMP_ICONS, JUMP_STEP_IN, JUMP_STEP_OUT, LINKED_HERE_TEXT, LIST_PRIMARY_ROLE, NAVIGATION_ACTION_NAMES, NAVIGATION_BUTTONS, NAVIGATION_TARGET_TABS
from tomography_session_browser.ui.viewer.list_rows import is_status_chip, status_chip_tooltip, status_tone
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from tomography_session_browser.ui.viewer.viewer_tab import ViewerTab


#: The reason line wraps onto this many lines before eliding.
REASON_MAX_LINES = 1
REASON_SEPARATOR = "  ·  "


def jump_direction(value: Any, key: str) -> str | None:
    """Whether a jump from ``value`` to the ``key`` tab steps out or in.

    ``JUMP_STEP_OUT`` towards the Atlas, ``JUMP_STEP_IN`` towards the tilt
    series (``IMAGE_HIERARCHY``); ``None`` when either end is not on it.
    """

    source = tab_label_for_object(value)
    target = NAVIGATION_TARGET_TABS.get(key)
    if source not in IMAGE_HIERARCHY or target not in IMAGE_HIERARCHY or source == target:
        return None
    return JUMP_STEP_OUT if IMAGE_HIERARCHY.index(target) < IMAGE_HIERARCHY.index(source) else JUMP_STEP_IN


class ViewerHeader:
    """Viewer header operations for one ViewerTab."""

    def __init__(self, view: ViewerTab) -> None:
        self.view = view

    def create_linked_strip(self, header: QHBoxLayout) -> None:
        self.linked_strip = QWidget(self.view)
        self.linked_strip.setObjectName("viewerLinkedStrip")
        self.linked_layout = QHBoxLayout(self.linked_strip)
        self.linked_layout.setContentsMargins(0, 0, 0, 0)
        self.linked_layout.setSpacing(SPACE_XS)
        self.linked_label = QLabel("LINKED", self.linked_strip)
        self.linked_label.setObjectName("viewerLinkedLabel")
        # The item shown, in its place on the path ("this tilt series").
        self.linked_here = QLabel("", self.linked_strip)
        self.linked_here.setObjectName("viewerLinkedHere")
        self.linked_here.setVisible(False)
        self.linked_here_index: int | None = None
        self.linked_separators: list[QLabel] = []
        self.linked_strip.setVisible(False)
        # The areas of the batch position shown, as chips under the strip (B5.1):
        # it only ever shows on the tab that has them.
        self.exposure_chooser = ExposureChooser(self.view)
        chooser_policy = self.exposure_chooser.sizePolicy()
        chooser_policy.setRetainSizeWhenHidden(self.show_exposure_chooser)
        self.exposure_chooser.setSizePolicy(chooser_policy)
        self.exposure_chooser.area_chosen.connect(self.view.parts.tab_selection.choose_exposure_area)
        self.exposure_chooser.choice_cleared.connect(self.view.parts.tab_selection.marker_selection_cleared)
        # Why a jump is unavailable, in the resolver's words, readable without
        # hovering. It sits under the strip, not in it, so it stays when the
        # strip folds into "Go to".
        self.linked_reason = WrappedElidedLabel("", max_lines=REASON_MAX_LINES, parent=self.view)
        self.linked_reason.setObjectName("viewerLinkedReason")
        apply_tier(self.linked_reason, TIER_SUPPORTING)
        # QLabel's empty-text size differs from its text layout on native Qt.
        # Keep the explanation's line box even when every jump is available.
        self.linked_reason.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.linked_reason.setVisible(False)
        self.reason_transition = ContentTransition(self.linked_reason)

        self.navigation_buttons: dict[str, QPushButton] = {}
        # Each button's arrow: a step out or in from the item shown.
        self.navigation_icon_names: dict[str, str] = {}
        if self.navigation_actions_for is not None:
            for key, label in NAVIGATION_BUTTONS:
                button = QPushButton(label, self.linked_strip)
                button.setObjectName("viewerNavButton")
                button.setIcon(themed_icon("arrow-right", size=14))
                button.setAccessibleName(f"Open linked {label.lower()}")
                button.setVisible(False)
                button.clicked.connect(lambda _checked=False, key=key: self.view._navigation_button_clicked(key))
                self.navigation_buttons[key] = button

        # F1.8: when the strip cannot hold every button, they collapse into
        # this single menu instead of the row clipping at both ends.
        self.navigation_available: dict[str, bool] = {}
        self.navigation_menu_button = QPushButton("Go to", self.view)
        self.navigation_menu_button.setObjectName("viewerNavButton")
        self.navigation_menu_button.setIcon(themed_icon("arrow-right", size=14))
        self.navigation_menu_button.setAccessibleName("Go to a linked item")
        navigation_menu = QMenu(self.navigation_menu_button)
        navigation_menu.setToolTipsVisible(True)
        navigation_menu.aboutToShow.connect(self.view._populate_navigation_menu)
        self.navigation_menu_button.setMenu(navigation_menu)
        self.navigation_menu_button.setVisible(False)
        self.view._arrange_linked_strip(None)
        # Chips that do not fit are summarised by one "+N" chip.
        self.overflow_chip = QLabel("", self.view)
        # A count of folded readouts, so a count badge (harmonisation D3).
        self.overflow_chip.setObjectName("viewerChipMore")
        self.overflow_chip.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.overflow_chip.setVisible(False)
        header.addWidget(self.overflow_chip)
        # Folded, the jumps are this one button at the end of the title row, and the
        # strip's row is gone (hidden otherwise).
        header.addWidget(self.navigation_menu_button)
        self.header_layout = header

    def create_title(self) -> QHBoxLayout:
        header = QHBoxLayout()
        header.setSpacing(8)
        self.view.header_title = ElidedLabel("No item selected", parent=self.view)
        self.view.header_title.setObjectName("viewerTitle")
        self.view.header_title.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        header.addWidget(self.view.header_title, stretch=1)
        self.view.header_chips: list[QLabel] = []

        return header

    def set_header_chips(self, values: list[str]) -> None:
        layout = getattr(self, "header_layout", None)
        if layout is None:
            return
        for chip in self.view.header_chips:
            for index in range(layout.count()):
                item = layout.itemAt(index)
                if item is not None and item.widget() is chip:
                    layout.takeAt(index)
                    break
            chip.deleteLater()
        self.view.header_chips = []
        previous_was_readout = False
        for index, value in enumerate(values):
            chip = QLabel(value, self.view)
            is_status = is_status_chip(value)
            chip.setObjectName("viewerChipStatus" if is_status else "viewerChip")
            # Readouts are unboxed text; a thin rule separates consecutive
            # ones (harmonisation B2). The text itself is unchanged.
            chip.setProperty("separated", previous_was_readout and not is_status)
            previous_was_readout = not is_status
            if is_status:
                # The status chip used to be painted with the accent colour
                # whatever the status was, so a failed tilt series showed a
                # calm sage chip in the header while the list row beside it
                # showed a red one. It takes the list-row badge's tone, which
                # the stylesheet colours, so it follows a theme switch.
                chip.setProperty("tone", status_tone(value))
            chip.setAlignment(Qt.AlignmentFlag.AlignCenter)
            chip.setToolTip(status_chip_tooltip(value) if is_status else value)
            layout.insertWidget(1 + index, chip)
            self.view.header_chips.append(chip)
        self.view._fit_header()

    def header_available_width(self) -> int | None:
        """Width the header row can use, or ``None`` before a real layout.

        An unshown tab has a placeholder geometry; fitting against it would
        collapse the header of every tab that has never been looked at.
        """

        if not self.view.isVisible() or self.view.width() <= 0:
            return None
        margins = self.view.layout().contentsMargins() if self.view.layout() is not None else None
        horizontal = (margins.left() + margins.right()) if margins is not None else 0
        return self.view.width() - horizontal

    def fit_header(self) -> None:
        """Keep the viewer header without clipping (plan F1.8).

        The title, its chips and up to four navigation buttons sat in one
        fixed row; in a narrow workspace the row overflowed and clipped at both
        ends while the title shrank to "…". Degrade in order until it fits:

        1. the jumps have a row of their own (the Linked strip, B1); when it cannot
           hold the buttons they fold into one "Go to · n of m" menu, which moves
           to the end of the title row and takes the strip's row with it (D24),
           and the menu's items keep each action's enabled state and reason;
        2. on the title row, trailing chips fold into a "+N" chip listing them
           (the first chip, the status, always stays), making room for that
           button first; the title keeps a readable floor and is the last thing
           to give way.
        """

        if not hasattr(self, "navigation_menu_button"):
            return
        compact = self.view.height() < COMPACT_VIEWER_HEIGHT_PX
        layout = self.view.layout()
        # Controls carry their own padding; short pages need no extra row gaps.
        layout.setSpacing(0 if compact else SPACE_S)
        inset = SPACE_XS if compact else SPACE_S
        layout.setContentsMargins(inset, inset, inset, inset)
        self.linked_reason.setFixedHeight(self.linked_reason.heightForWidth(self.linked_reason.width()))
        self.exposure_chooser.set_compact(compact)
        available_keys = [key for key in self.navigation_buttons if self.navigation_available.get(key)]
        labels = ", ".join(self.navigation_buttons[key].text() for key in available_keys)
        # How many of the jumps the item has are open, from the same plan the buttons read.
        usable = sum(1 for key in available_keys if self.navigation_buttons[key].isEnabled())
        self.navigation_menu_button.setText(f"Go to \u00b7 {usable} of {len(available_keys)}")
        self.navigation_menu_button.setToolTip(f"Go to a linked item: {labels}")
        self.navigation_menu_button.setAccessibleDescription(f"{usable} of {len(available_keys)} available. Linked items: {labels}")
        collapse = False
        hidden = 0
        width = self.view._header_available_width()
        if width is not None:
            button_widths = [self.navigation_buttons[key].sizeHint().width() for key in available_keys]
            if button_widths:
                strip_spacing = max(0, self.linked_layout.spacing())
                separator_width = self.linked_separators[0].sizeHint().width() if self.linked_separators else 0
                here = self.linked_here.sizeHint().width() if self.linked_here_index is not None else 0
                items = len(button_widths) + (1 if here else 0)
                strip_needed = (
                    self.linked_label.sizeHint().width()
                    + SPACE_XS
                    + sum(button_widths)
                    + here
                    + separator_width * max(0, items - 1)
                    + strip_spacing * (2 * items)
                )
                collapse = compact or strip_needed > width
            spacing = max(0, self.header_layout.spacing())
            title_text = self.view.header_title.text_full()
            title_width = min(
                self.view.header_title.fontMetrics().horizontalAdvance(title_text) + 8,
                HEADER_TITLE_MIN_PX,
            )
            chip_widths = [chip.sizeHint().width() for chip in self.view.header_chips]
            overflow_width = QFontMetrics(self.overflow_chip.font()).horizontalAdvance("+9") + 24
            menu_width = self.navigation_menu_button.sizeHint().width() if collapse else 0

            def needed(shown_chips: int) -> int:
                chips = chip_widths[:shown_chips]
                extra = overflow_width if shown_chips < len(chip_widths) else 0
                items = 1 + len(chips) + (1 if extra else 0) + (1 if menu_width else 0)
                return title_width + sum(chips) + extra + menu_width + spacing * max(0, items - 1)

            while hidden < len(chip_widths) - 1 and needed(len(chip_widths) - hidden) > width:
                hidden += 1

        for key, button in self.navigation_buttons.items():
            button.setVisible(bool(self.navigation_available.get(key)) and not collapse)
        self.navigation_menu_button.setVisible(collapse)
        self.linked_strip.setVisible(bool(available_keys) and not collapse)
        self.view._sync_linked_separators(collapse)
        shown = len(self.view.header_chips) - hidden
        for index, chip in enumerate(self.view.header_chips):
            chip.setVisible(index < shown)
        folded = [chip.text() for chip in self.view.header_chips[shown:]]
        if folded:
            self.overflow_chip.setText(f"+{len(folded)}")
            self.overflow_chip.setToolTip("\n".join(folded))
            self.overflow_chip.setAccessibleName(f"{len(folded)} more: {', '.join(folded)}")
            # Keep "+N" directly after the last visible chip, and Go to after that.
            self.header_layout.removeWidget(self.overflow_chip)
            self.header_layout.insertWidget(1 + len(self.view.header_chips), self.overflow_chip)
        self.overflow_chip.setVisible(bool(folded))

    def populate_navigation_menu(self) -> None:
        menu = self.navigation_menu_button.menu()
        menu.clear()
        for key, button in self.navigation_buttons.items():
            if not self.navigation_available.get(key):
                continue
            label = button.text() if button.isEnabled() else f"{button.text()} (unavailable)"
            action = menu.addAction(button.icon(), label)
            action.setEnabled(button.isEnabled())
            action.setToolTip(button.toolTip())
            action.triggered.connect(lambda _checked=False, key=key: self.view._navigation_button_clicked(key))

    def set_header_title(self, text: str) -> None:
        if self.view.header_title.text_full() != text:
            self.view.header_title.setText(text)
            self.view.header_title_changed.emit(text)

    def update_header(self, value: Any) -> None:
        self.view._set_header_title(self.view._header_title_for(value))
        chips = _chips_for_item(value)
        # The row's own status word and its summary, for every type that has
        # them: the header says what the row says, in the same words. A batch
        # position's chip used to be its raw parser status ("Acquired") while
        # its row said "Failed".
        if isinstance(value, STATUS_ROW_TYPES) and self.view.parts.list_pane.item_status_for is not None:
            row = self.view.parts.list_pane.item_status_for(value)
            if row.summary:
                chips.insert(0, row.summary)
            if row.status:
                chips.insert(0, display_status_label(row.status))
        self.view._set_header_chips(chips)
        self.view._update_navigation_actions(value)

    def header_title_for(self, value: Any) -> str:
        """Title the header with the same label the list row shows.

        ``Atlas`` carries no name of its own, so ``_title_for_item`` could
        only return the literal word "Atlas" while every other tab titled
        itself with the selected item. The owning tab already resolved a
        real label (e.g. "Specimen_alpha") to populate the list; reuse it. An object
        deliberately omitted from the list takes that same label provider.
        """

        row = self.view.parts.list_pane.rows.row_for_value(value)
        label = str(row.data(0, LIST_PRIMARY_ROLE) or "") if row is not None else ""
        return label or self.view.parts.tab_selection.label_for(value)

    def update_navigation_actions(self, value: Any | None) -> None:
        if not self.navigation_buttons:
            return
        actions = (
            self.navigation_actions_for(value)
            if value is not None and self.navigation_actions_for is not None
            else []
        )
        by_key = {action.key: action for action in actions}
        self.show_unavailable_reasons(actions)
        self.view._arrange_linked_strip(value)
        any_visible = False
        for key, button in self.navigation_buttons.items():
            action = by_key.get(key)
            if action is None:
                self.navigation_available[key] = False
                continue
            button.setText(action.label.lstrip("↗ ").strip())
            button.setEnabled(action.enabled)
            button.setToolTip(action.tooltip)
            direction = jump_direction(value, key)
            icon_name = JUMP_ICONS.get(direction, "arrow-right")
            if self.navigation_icon_names.get(key) != icon_name:
                self.navigation_icon_names[key] = icon_name
                button.setIcon(themed_icon(icon_name, size=14))
            # The visible face stays terse — these sit in a dense strip. The
            # verb-plus-destination phrasing goes where it has room, and where
            # a screen reader will actually reach it. A disabled action states
            # its reason here too, not only on hover; the arrow's direction is
            # said as well as drawn.
            button.setAccessibleName(NAVIGATION_ACTION_NAMES.get(action.key, action.label))
            description = (
                action.tooltip
                if action.enabled or action.selection_required
                else f"Unavailable. {action.tooltip}".strip()
            )
            step = JUMP_DESCRIPTIONS.get(direction)
            button.setAccessibleDescription(f"{description} {step}".strip() if step else description)
            self.navigation_available[key] = True
            any_visible = True
        if not any_visible:
            for key in self.navigation_buttons:
                self.navigation_available[key] = False
        # Visibility is decided by the header fit: expanded buttons, or the
        # collapsed "Go to" menu when the row cannot hold them.
        self.view._fit_header()

    def show_unavailable_reasons(self, actions: list[Any]) -> None:
        """One line under the strip giving each distinct reason a jump is inert.

        Several disabled jumps commonly share one cause, so each reason is
        stated once. The buttons' own accessible descriptions carry the same
        words; the tooltip repeats them as a list.
        """

        seen: set[str] = set()

        def distinct_reasons(selection_required: bool) -> list[str]:
            reasons = []
            for action in actions:
                if action.enabled or action.selection_required != selection_required:
                    continue
                fresh = []
                for clause in re.split(r"(?<=[.!?])\s+", action.tooltip.strip()):
                    if clause and clause not in seen:
                        seen.add(clause)
                        fresh.append(clause)
                if fresh:
                    reasons.append(" ".join(fresh))
            return reasons

        hints = distinct_reasons(True)
        reasons = distinct_reasons(False)
        parts = hints
        if reasons:
            parts.append(f"Unavailable: {REASON_SEPARATOR.join(reasons)}")
        label = self.linked_reason
        text = REASON_SEPARATOR.join(parts)
        if label.text_full() != text:
            self.reason_transition.replace(lambda: label.setText(text))
        label.setToolTip("\n".join(dict.fromkeys(action.tooltip.strip() for action in actions
                                             if not action.enabled and action.tooltip.strip())))
        # A relationship-bearing item keeps one line even when every jump is
        # available. The empty line paints nothing; selecting a neighbour or
        # an exposure changes its words without moving the canvas.
        label.setVisible(bool(actions))

    def arrange_linked_strip(self, value: Any | None) -> None:
        """Lay out the strip: the jumps in tab order, the item shown in its place.

        ``Atlas › Overview › Search map › this tile › Batch › Tilt series``:
        steps out sit before the item, steps in after it. Display only; the
        buttons, their states and what they open are unchanged.
        """

        source = tab_label_for_object(value) if value is not None else None
        here_index = IMAGE_HIERARCHY.index(source) if source in IMAGE_HIERARCHY else None
        if here_index == self.linked_here_index and self.linked_layout.count():
            return
        self.linked_here_index = here_index
        while self.linked_layout.count():
            self.linked_layout.takeAt(0)
        ordered: list[tuple[float, QWidget]] = [
            (float(IMAGE_HIERARCHY.index(NAVIGATION_TARGET_TABS[key])), button)
            for key, button in self.navigation_buttons.items()
        ]
        if here_index is not None:
            self.linked_here.setText(LINKED_HERE_TEXT.get(source, "this item"))
            ordered.append((float(here_index), self.linked_here))
        ordered.sort(key=lambda item: item[0])
        while len(self.linked_separators) < len(ordered):
            separator = QLabel("\u203a", self.linked_strip)
            separator.setObjectName("viewerLinkedSeparator")
            separator.setVisible(False)
            self.linked_separators.append(separator)
        self.linked_layout.addWidget(self.linked_label)
        self.linked_layout.addSpacing(SPACE_XS)
        self.linked_order: list[tuple[QLabel, QWidget]] = []
        for separator, (_index, widget) in zip(self.linked_separators, ordered):
            self.linked_layout.addWidget(separator)
            self.linked_layout.addWidget(widget)
            self.linked_order.append((separator, widget))
        self.linked_layout.addStretch(1)

    def sync_linked_separators(self, collapsed: bool) -> None:
        """A "›" shows between two shown items; collapsed, only "Go to" shows."""

        self.linked_here.setVisible(self.linked_here_index is not None and not collapsed)
        seen = False
        for separator, widget in getattr(self, "linked_order", []):
            shown = not widget.isHidden()
            separator.setVisible(shown and seen)
            seen = seen or shown

    def navigation_button_clicked(self, key: str) -> None:
        if self.view.parts.tab_selection.current_value is None or self.on_navigation_requested is None:
            return
        self.on_navigation_requested(self.view.parts.tab_selection.current_value, key)

