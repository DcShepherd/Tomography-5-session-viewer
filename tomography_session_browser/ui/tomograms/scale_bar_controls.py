"""Compact scale-bar controls; the renderer also owns snapshot appearance.

The rows follow 3dmod's Scale Bar dialog: colour, corner and inset, length
(Auto minimum or Exact), thickness, then the label and where it sits. Colour
is a row of swatches, the corner a 2 × 2 picker and sizes are sliders with
their value beside them. Exact length appears only once Exact is chosen.
"""
from dataclasses import replace

from PySide6.QtCore import QRectF, QSignalBlocker, Qt
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import (QAbstractButton, QButtonGroup, QColorDialog, QDoubleSpinBox, QGridLayout,
                               QHBoxLayout, QLabel, QLineEdit, QSlider, QSpinBox, QToolButton, QVBoxLayout, QWidget)

from tomography_session_browser.presentation.design_tokens import SPACE_S, SPACE_XS
from tomography_session_browser.services.volume.scale_bar import BAR_COLOURS, ScaleBarStyle
from tomography_session_browser.ui.theme import current_palette
from tomography_session_browser.ui.tomograms.image_settings import inline_row, plain_field, share_width
from tomography_session_browser.ui.tomograms.layout_controls import Versions
from tomography_session_browser.ui.widgets.switch import Switch

CORNERS = (("tl", "Top left"), ("tr", "Top right"), ("bl", "Bottom left"), ("br", "Bottom right"))
#: A field wide enough for three digits.
INSET_WIDTH = 40


class Swatch(QAbstractButton):
    """A round colour choice; the custom swatch shows the chosen colour, or + before one is chosen."""

    def __init__(self, colour: str | None, name: str, parent: QWidget) -> None:
        super().__init__(parent)
        self.colour = colour
        self.setCheckable(True)
        self.setFixedSize(26, 26)
        self.setAccessibleName(name)
        self.setToolTip(name)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt API
        palette = current_palette()
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        selected = self.isChecked() or self.hasFocus()
        painter.setPen(QPen(QColor(palette.accent if selected else palette.border_strong), 2))
        painter.setBrush(QColor(self.colour) if self.colour else Qt.BrushStyle.NoBrush)
        painter.drawEllipse(QRectF(3, 3, 20, 20))
        if self.colour is None:
            painter.setPen(QPen(QColor(palette.text), 1.5))
            painter.drawLine(9, 13, 17, 13)
            painter.drawLine(13, 9, 13, 17)
        painter.end()


def unit(text: str, parent: QWidget) -> QLabel:
    label = QLabel(text, parent)
    label.setObjectName("tomogramUnit")
    return label


class ScaleBarControls(QWidget):
    def __init__(self, tab, parent):
        super().__init__(parent)
        self.tab = tab
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(SPACE_S)

        swatches = QHBoxLayout()
        swatches.setSpacing(SPACE_XS)
        self.colours = QButtonGroup(self)
        self.swatches = [Swatch(colour, name, self) for name, colour in BAR_COLOURS]
        self.custom = Swatch(None, "Select a colour", self)
        for index, swatch in enumerate((*self.swatches, self.custom)):
            self.colours.addButton(swatch, index)
            swatches.addWidget(swatch)
        swatches.addStretch()  # Without it the spare width spreads around the label too.
        self.colours.idClicked.connect(self.colour_changed)
        layout.addLayout(inline_row("Colour", swatches, parent=self))

        picker = QWidget(self)
        picker.setObjectName("tomogramCornerPicker")
        grid = QGridLayout(picker)
        grid.setContentsMargins(SPACE_XS, SPACE_XS, SPACE_XS, SPACE_XS)
        grid.setSpacing(SPACE_XS)
        self.corners = QButtonGroup(self)
        self.corner_buttons: dict[str, QToolButton] = {}
        for index, (key, name) in enumerate(CORNERS):
            cell = QToolButton(picker)
            cell.setObjectName("tomogramCornerCell")
            cell.setCheckable(True)
            cell.setFixedSize(28, 20)
            cell.setAccessibleName(f"Scale bar {name.lower()}")
            cell.setToolTip(name)
            self.corners.addButton(cell, index)
            self.corner_buttons[key] = cell
            grid.addWidget(cell, index // 2, index % 2)
        self.corners.idClicked.connect(self.corner_changed)
        corner_row = QHBoxLayout()
        corner_row.addWidget(picker)
        corner_row.addStretch()
        layout.addLayout(inline_row("Corner", corner_row, parent=self))

        # 3dmod's indentation: the distance from the image edges, in screen pixels.
        self.inset_x, self.inset_y = QSpinBox(self), QSpinBox(self)
        inset = QHBoxLayout()
        inset.setSpacing(SPACE_XS)
        for axis, field in (("X", self.inset_x), ("Y", self.inset_y)):
            field.setRange(0, 200)
            field.setKeyboardTracking(False)
            field.setButtonSymbols(QSpinBox.ButtonSymbols.NoButtons)
            field.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            field.setFixedWidth(INSET_WIDTH)
            field.setAccessibleName(f"Scale bar inset {axis}")
            field.setToolTip(f"Distance from the {'left or right' if axis == 'X' else 'top or bottom'} edge of the image, in screen pixels")
            field.valueChanged.connect(self.changed)
            inset.addWidget(QLabel(axis, self))
            inset.addWidget(field)
            inset.addSpacing(SPACE_XS)
        inset.addWidget(unit("px", self))
        inset.addStretch()
        layout.addLayout(inline_row("Inset", inset, parent=self))

        self.length = Versions(self)
        self.length.addItems(("Auto", "Exact"))
        self.length.setAccessibleName("Scale bar length")
        self.length.setItemData(0, "Use the shortest length of 1, 2 or 5 × 10ⁿ nm that is not shorter than Min length",
                                Qt.ItemDataRole.ToolTipRole)
        self.length.setItemData(1, "Use the length that you type", Qt.ItemDataRole.ToolTipRole)
        self.length.rebuild()
        self.length.hide()
        share_width(self.length)
        self.length.currentIndexChanged.connect(self.changed)
        layout.addLayout(inline_row("Length", self.length.segments, parent=self))
        self.exact = QDoubleSpinBox(self)
        self.exact.setDecimals(2)
        self.exact.setRange(.01, 1e9)
        self.exact.setValue(100)
        plain_field(self.exact)
        self.exact.setKeyboardTracking(False)
        self.exact.setAccessibleName("Scale bar exact length")
        self.exact.setToolTip("The bar length in nanometres. If the bar is too long for the view, the app shows a message. It does not make the bar shorter.")
        self.exact.valueChanged.connect(self.changed)
        self.exact_row = QWidget(self)
        self.exact_row.setLayout(inline_row("Exact length", self.exact, unit("nm", self.exact_row), parent=self.exact_row, stretch=True))
        self.exact_row.layout().setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.exact_row)

        self.values: dict[QSlider, QLabel] = {}
        self.minimum = self.slider(layout, "Min length", 30, 300, "px", "The shortest length of the bar on the screen")
        self.thickness = self.slider(layout, "Thickness", 1, 20, "px", "The thickness of the bar on the screen")
        self.minimum_row = self.minimum.parentWidget()

        self.text = QLineEdit(self)
        self.text.setMaxLength(80)
        self.text.setPlaceholderText("Distance")
        self.text.setAccessibleName("Scale bar label text")
        self.text.setToolTip("Type a different label, or keep it empty to show the distance. Snapshots always record the distance.")
        self.text.editingFinished.connect(self.changed)
        self.label = Switch(self, name="Show scale bar label")
        self.label.setToolTip("Show the label")
        self.label.toggled.connect(self.changed)
        label_row = inline_row("Label", self.text, parent=self)
        label_row.setStretchFactor(self.text, 1)
        label_row.addWidget(self.label)
        layout.addLayout(label_row)
        self.label_place = Versions(self)
        self.label_place.addItems(("Below", "Above"))
        self.label_place.setAccessibleName("Scale bar label position")
        self.label_place.setItemData(0, "Put the label below the bar, as in 3dmod", Qt.ItemDataRole.ToolTipRole)
        self.label_place.setItemData(1, "Put the label above the bar", Qt.ItemDataRole.ToolTipRole)
        self.label_place.rebuild()
        self.label_place.hide()
        share_width(self.label_place)
        self.label_place.currentIndexChanged.connect(self.changed)
        layout.addLayout(inline_row("Label side", self.label_place.segments, parent=self))
        self.font_size = self.slider(layout, "Text size", 9, 36, "pt", "The size of the label text, in points")
        self.sync(ScaleBarStyle())

    def slider(self, layout: QVBoxLayout, title: str, low: int, high: int, units: str, tip: str) -> QSlider:
        row = QWidget(self)
        control = QSlider(Qt.Orientation.Horizontal, row)
        control.setRange(low, high)
        control.setAccessibleName("Scale bar " + title.lower())
        control.setToolTip(tip)
        value = QLabel(row)
        value.setObjectName("tomogramSliderValue")
        value.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        value.setFixedWidth(value.fontMetrics().horizontalAdvance("300 px") + 4)
        value.setProperty("units", units)
        self.values[control] = value
        row.setLayout(inline_row(title, control, value, parent=row))
        row.layout().setContentsMargins(0, 0, 0, 0)
        # Keys step at once; a drag publishes on release so it does not queue renders.
        control.valueChanged.connect(lambda number: value.setText(f"{number} {units}"))
        control.valueChanged.connect(lambda _number: None if control.isSliderDown() else self.changed())
        control.sliderReleased.connect(self.changed)
        layout.addWidget(row)
        return control

    def sync(self, style):
        controls = (self.colours, self.corners, self.length, self.minimum, self.exact, self.thickness, self.label,
                    self.text, self.font_size, self.inset_x, self.inset_y, self.label_place)
        blockers = [QSignalBlocker(control) for control in controls]
        preset = next((index for index, (_, colour) in enumerate(BAR_COLOURS) if colour == style.colour), None)
        self.custom.colour = None if preset is not None else style.colour
        self.custom.setToolTip("Select a colour" if preset is not None else f"Colour {style.colour}. Click to select a different colour.")
        self.colours.button(len(BAR_COLOURS) if preset is None else preset).setChecked(True)
        # A dragged bar sits at no corner: no cell is selected until one is chosen.
        self.corners.setExclusive(False)
        for key, cell in self.corner_buttons.items():
            cell.setChecked(style.position is None and key == style.corner)
        self.corners.setExclusive(True)
        self.corner_buttons["br"].parentWidget().setToolTip(
            "You moved the bar on the image. Click a corner to put it there." if style.position is not None
            else "Click a corner, or drag the bar on the image")
        self.inset_x.setValue(style.inset_x)
        self.inset_y.setValue(style.inset_y)
        self.length.setCurrentIndex(int(style.exact_nm is not None))
        self.length.sync()
        self.minimum.setValue(style.minimum_width)
        if style.exact_nm is not None:
            self.exact.setValue(style.exact_nm)
        self.exact_row.setVisible(style.exact_nm is not None)
        self.minimum_row.setVisible(style.exact_nm is None)
        self.thickness.setValue(style.thickness)
        self.label.setChecked(style.label)
        if not self.text.hasFocus():
            self.text.setText(style.text)
        self.label_place.setCurrentIndex(int(style.label_above))
        self.label_place.sync()
        self.font_size.setValue(style.text_size)
        for control in (self.text, self.label_place.segments, self.font_size):
            control.setEnabled(style.label)
        del blockers
        for control, value in self.values.items():
            value.setText(f"{control.value()} {value.property('units')}")
        self.update()

    def changed(self, *_args):
        old = self.tab.state.scale_bar
        new = replace(old, minimum_width=self.minimum.value(), exact_nm=self.exact.value() if self.length.currentIndex() else None,
                      thickness=self.thickness.value(), label=self.label.isChecked(), text=self.text.text(),
                      text_size=self.font_size.value(), inset_x=self.inset_x.value(), inset_y=self.inset_y.value(),
                      label_above=bool(self.label_place.currentIndex()))
        if new != old:
            self.tab.recipes.change(scale_bar=new)

    def corner_changed(self, index):
        self.tab.recipes.change(scale_bar=replace(self.tab.state.scale_bar, corner=CORNERS[index][0], position=None))

    def colour_changed(self, index):
        colour = BAR_COLOURS[index][1] if index < len(BAR_COLOURS) else None
        if colour is None:
            self.tab.playback.stop()
            selected = QColorDialog.getColor(QColor(self.tab.state.scale_bar.colour), self, "Scale bar colour")
            if not selected.isValid():
                self.sync(self.tab.state.scale_bar)
                return
            colour = selected.name()
        self.tab.recipes.change(scale_bar=replace(self.tab.state.scale_bar, colour=colour))
