"""The exposure-area chooser: one chip per area of the batch position shown (D4, D9).

An exposure area is chosen on the image, by clicking its marker, and then the
Linked strip's Tilt series jump follows from it. The chips are the same choice
from the keyboard and without hunting for a small marker on the image: a chip
does what a click on that area's marker does, and clicking the chosen chip does
what clicking empty canvas does (it clears the choice). It only shows where an
area can be chosen, the Batch positions tab (D9), and only while the position
has areas.

The chooser says nothing about where the jump goes or why it cannot: that is
the Linked strip's, and the line under it.
"""
from __future__ import annotations

from collections.abc import Sequence

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QToolButton, QVBoxLayout, QWidget

from tomography_session_browser.presentation.design_tokens import SPACE_S, SPACE_XS
from tomography_session_browser.presentation.exposure_areas import ExposureArea
from tomography_session_browser.ui.theme import TIER_SUPPORTING, apply_tier
from tomography_session_browser.ui.motion.content_change import ContentTransition
from tomography_session_browser.ui.widgets.elided_label import WrappedElidedLabel

class ExposureChip(QToolButton):
    """A chip whose checked state is the chooser's alone.

    A chip chooses on press, as a click on the area's marker does: on release a
    whole click later, nothing showed for as long as the button was held. So
    the release must not toggle it back; ``set_selected`` sets it.
    """

    def nextCheckState(self) -> None:  # noqa: N802 - Qt API
        pass


class ExposureChooser(QFrame):
    """Numbered chips, one per exposure area, one of them chosen at most."""

    #: A chip was pressed: the marker id of its area.
    area_chosen = Signal(str)
    #: The chosen chip was pressed again.
    choice_cleared = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("viewerExposureChooser")
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setAccessibleName("Exposure area")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(SPACE_S, SPACE_XS, SPACE_S, SPACE_XS)
        outer.setSpacing(SPACE_XS)
        layout = QHBoxLayout()
        self._row = layout
        outer.addLayout(layout)
        layout.setSpacing(SPACE_S)
        label = QLabel("Exposure area", self)
        label.setObjectName("viewerExposureLabel")
        apply_tier(label, TIER_SUPPORTING)
        layout.addWidget(label)
        self._chips = QWidget(self)
        self._chips_layout = QHBoxLayout(self._chips)
        self._chips_layout.setContentsMargins(0, 0, 0, 0)
        self._chips_layout.setSpacing(SPACE_XS)
        self._chips.setMinimumHeight(28)  # the styled chip's 26 px content and border
        layout.addWidget(self._chips)
        layout.addStretch(1)
        self.image_note = WrappedElidedLabel("", max_lines=1, parent=self)
        self.image_note.setObjectName("viewerExposureImageNote")
        apply_tier(self.image_note, TIER_SUPPORTING)
        note_policy = self.image_note.sizePolicy()
        note_policy.setRetainSizeWhenHidden(True)
        self.image_note.setSizePolicy(note_policy)
        outer.addWidget(self.image_note)
        self._areas: tuple[ExposureArea, ...] = ()
        self._buttons: dict[str, QToolButton] = {}
        self._selected: str | None = None
        self._compact = False
        self.transition = ContentTransition(self)
        self.setVisible(False)

    def set_compact(self, compact: bool) -> None:
        """On a short page the same image note sits beside the chips."""
        if compact == self._compact:
            return
        self._compact = compact
        target = self._row if compact else self.layout()
        target.addWidget(self.image_note, 1 if compact else 0)
        if compact:
            self._row.takeAt(2)  # the spacer belongs only to the two-row layout
        else:
            self._row.addStretch(1)
        outer = self.layout()
        outer.setContentsMargins(SPACE_S, 0 if compact else SPACE_XS, SPACE_S, 0 if compact else SPACE_XS)

    def areas(self) -> tuple[ExposureArea, ...]:
        return self._areas

    def selected(self) -> str | None:
        return self._selected

    def set_areas(self, areas: Sequence[ExposureArea]) -> None:
        """Show these areas; the chips are rebuilt only when they changed."""

        areas = tuple(areas)
        if areas != self._areas:
            self.transition.replace(lambda: self.replace_areas(areas))
        self.set_selected(self._selected)

    def replace_areas(self, areas: tuple[ExposureArea, ...]) -> None:
        self._areas = areas
        for button in self._buttons.values():
            self._chips_layout.removeWidget(button)
            button.hide()
            button.deleteLater()
        self._buttons = {}
        for area in areas:
            button = ExposureChip(self._chips)
            button.setObjectName("exposureChip")
            button.setProperty("offImage", area.on_image is False)
            button.setCheckable(True)
            button.setText(area.label)
            description = f"{area.status} · {area.image_note}"
            button.setToolTip(f"{area.title} · {description}")
            button.setAccessibleName(area.title)
            button.setAccessibleDescription(description)
            button.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
            button.pressed.connect(lambda marker_id=area.marker_id: self._pressed(marker_id))
            self._chips_layout.addWidget(button)
            self._buttons[area.marker_id] = button
        notes = [f"{area.label}: {area.image_note}" for area in areas if area.on_image is not True]
        self.image_note.setText(" · ".join(notes))
        self.image_note.setVisible(bool(notes))
        self.setVisible(bool(areas))

    def set_selected(self, marker_id: str | None) -> None:
        """Mark the chip of ``marker_id`` chosen (none, if it is not one of the areas)."""

        self._selected = marker_id if marker_id in self._buttons else None
        for chip_id, button in self._buttons.items():
            button.setChecked(chip_id == self._selected)

    def _pressed(self, marker_id: str) -> None:
        if marker_id == self._selected:
            self.set_selected(None)
            self.choice_cleared.emit()
        else:
            self.set_selected(marker_id)
            self.area_chosen.emit(marker_id)
