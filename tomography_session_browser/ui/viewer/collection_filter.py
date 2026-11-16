"""The Atlas tab's collection filter: which data collections' batch positions draw.

It replaces the Data collections fold of the Overlays panel. The choices are
the same and go through the same path (``on_visibility_changed``, then the
markers are built anew). Two controls open the one menu:

- the From button on the Positions card, while the atlas has more than one
  collection;
- the "From: n of m" chip at the bottom left, while a collection is hidden and
  the card is hidden or closed, so a filter never acts unseen. The chip is not
  part of an image export.

The menu is a ``RowMenu``: a check box for each collection, then Show all.
"""
from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from PySide6.QtCore import QPoint
from PySide6.QtWidgets import QCheckBox, QPushButton, QWidget

from tomography_session_browser.ui.viewer.models import AtlasCollectionOverlayOption
from tomography_session_browser.ui.viewer.overlay_swatches import with_swatch
from tomography_session_browser.ui.widgets.row_menu import MenuRow, RowMenu

if TYPE_CHECKING:
    from tomography_session_browser.ui.viewer.tab_selection import TabSelection


class CollectionFilter:
    def __init__(self, view: QWidget, *, selection: TabSelection, atlas_lod: bool) -> None:
        self.view = view
        self.selection = selection
        self.atlas_lod = atlas_lod
        self.options_for: Callable[[Any], list[AtlasCollectionOverlayOption]] | None = None
        self.on_visibility_changed: Callable[[str, bool], None] | None = None
        self.options: list[AtlasCollectionOverlayOption] = []
        #: The menu's check boxes by collection key; they live while the menu does.
        self.checks: dict[str, QCheckBox] = {}
        self.menu = RowMenu(view)
        self.menu.setObjectName("positionsFromMenu")
        self.chip = QPushButton("", view)
        self.chip.setObjectName("positionsFromChip")
        self.chip.setToolTip("Some data collections are hidden. Choose the collections whose batch positions show")
        self.chip.clicked.connect(lambda: self.open_menu(self.chip))
        self.chip.setVisible(False)

    # Options ------------------------------------------------------------
    def set_provider(
        self,
        provider: Callable[[Any], list[AtlasCollectionOverlayOption]] | None,
        on_visibility_changed: Callable[[str, bool], None] | None,
    ) -> None:
        self.options_for = provider
        self.on_visibility_changed = on_visibility_changed
        self.update(self.selection.current_value)

    def update(self, value: Any) -> None:
        """Build the menu for ``value``'s collections."""

        if not self.atlas_lod:
            return
        self.options = (
            self.options_for(value) if self.options_for is not None and value is not None else []
        )
        self.menu.reset()
        self.checks = {}
        for option in self.options:
            box = with_swatch(QCheckBox(option.label, self.menu), "collection")
            box.setObjectName("positionsFromCheck")
            box.setChecked(option.visible)
            box.setToolTip(f"Show batch positions from {option.label}")
            box.toggled.connect(lambda _checked, key=option.key: self.toggled(key))
            self.checks[option.key] = box
            self.menu.add_row(MenuRow(option.key, box, lambda: (), activate=box.toggle))
        if self.options:
            self.menu.addSeparator()
            self.menu.footer("show_all", "Show all", self.show_all)
        self.sync()

    # Changes ------------------------------------------------------------
    def toggled(self, key: str) -> None:
        box = self.checks.get(key)
        if box is None:
            return
        if self.on_visibility_changed is not None:
            self.on_visibility_changed(key, box.isChecked())
        # The Atlas's markers follow its visible collections: build them anew.
        self.selection.forget_markers()
        self.selection.refresh_marker_selection()
        self.sync()

    def show_all(self) -> None:
        for box in self.checks.values():
            if not box.isChecked():
                box.setChecked(True)

    # Display ------------------------------------------------------------
    def shown_count(self) -> int:
        return sum(box.isChecked() for box in self.checks.values())

    def text(self) -> str:
        total = len(self.checks)
        shown = self.shown_count()
        return "From: all" if shown == total else f"From: {shown} of {total}"

    def sync(self) -> None:
        """Set the From button and the chip from the choices and the card."""

        card = getattr(self.view, "positions_card", None)
        if card is None:
            return
        several = len(self.checks) > 1
        card.set_from_text(self.text() if several else None)
        filtering = bool(self.checks) and self.shown_count() < len(self.checks)
        card_shows_it = not card.isHidden() and card.expanded
        chip_wanted = filtering and not card_shows_it
        self.chip.setText(self.text())
        if self.chip.isHidden() == chip_wanted:
            self.chip.setVisible(chip_wanted)

    def open_menu(self, anchor: QWidget) -> None:
        if not self.checks:
            return
        hint = self.menu.sizeHint()
        self.menu.popup(anchor.mapToGlobal(QPoint(0, -hint.height())))
        rows = [action for action in self.menu.choosable() if action in self.menu.row_actions]
        if rows:
            self.menu.focus_entry(rows[0])
