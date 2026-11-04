"""One-based intersection editor; the tab retains all geometry operations."""
from __future__ import annotations
from tomography_session_browser.presentation.help_content import LocalBinding, InteractionKind
from PySide6.QtCore import Qt, QSignalBlocker
from PySide6.QtWidgets import QGridLayout, QLabel, QSpinBox, QWidget
from tomography_session_browser.presentation.tomogram_geometry import AXES
from tomography_session_browser.presentation.design_tokens import TOMOGRAM_PLANE_COLOURS


class CoordinateField(QSpinBox):
    def __init__(self, owner: IntersectionCorner):
        super().__init__(owner)
        self.owner = owner

    def keyPressEvent(self, event) -> None:  # noqa: N802
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Escape):
            if event.key() == Qt.Key.Key_Escape:
                self.owner.sync(force=True)
            else:
                self.interpretText()
                self.owner.apply()
            event.accept()
            return
        super().keyPressEvent(event)


class IntersectionCorner(QWidget):
    def __init__(self, tab, parent):
        super().__init__(parent)
        self.tab = tab
        self.setObjectName("tomogramCorner")
        layout = QGridLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        self.fields: dict[int, CoordinateField] = {}
        self.sizes: dict[int, QLabel] = {}
        for row, plane in enumerate(("YZ", "XZ", "XY")):
            index = AXES[plane][2]
            name = "XYZ"[index]
            label = QLabel(name, self)
            label.setStyleSheet(f"color: {TOMOGRAM_PLANE_COLOURS[plane]}; background: transparent;")
            field = CoordinateField(self)
            field.setAccessibleName(f"{name} intersection, one-based. Enter applies; Esc cancels.")
            field.setToolTip("Enter applies the intersection. Esc cancels this edit.")
            field.setKeyboardTracking(False)
            field.setButtonSymbols(QSpinBox.ButtonSymbols.NoButtons)
            field.setMinimumWidth(48)
            size = QLabel(self)
            layout.addWidget(label, row, 0)
            layout.addWidget(field, row, 1)
            layout.addWidget(size, row, 2)
            self.fields[index], self.sizes[index] = field, size

    def sync(self, *, force: bool = False) -> None:
        if not force and any(field.hasFocus() for field in self.fields.values()):
            return
        volume = self.tab.volume()
        if volume is None or volume.grid is None:
            return
        cross = self.tab.state.cross_xyz or tuple(size // 2 for size in volume.grid.shape_xyz)
        for index, field in self.fields.items():
            with QSignalBlocker(field):
                field.setRange(1, volume.grid.shape_xyz[index])
                field.setValue(cross[index] + 1)
                self.sizes[index].setText(f"/ {volume.grid.shape_xyz[index]}")

    def apply(self) -> None:
        self.tab.apply_intersection(tuple(self.fields[index].value() - 1 for index in range(3)))


# Contextual keys handled by this module; consumed by the shared Help sheet.
LOCAL_KEYS = (
    LocalBinding('tomograms.corner.ortho-coordinates-enter', 'Ortho coordinates', InteractionKind.KEYBOARD, ('Enter', 'Return'), 'Apply X/Y/Z edits'),
    LocalBinding('tomograms.corner.ortho-coordinates-esc', 'Ortho coordinates', InteractionKind.KEYBOARD, ('Esc',), 'Cancel the edit'),
)
