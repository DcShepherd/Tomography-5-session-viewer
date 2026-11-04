"""Image recipes and stock controls; calibration remains explicit and volume-local.

Long units use full-width rows; sliders sit on one row with their label and
value, labels in a fixed column, switches right. Snapshot, Scale bar,
Calibration and Memory fold, and say their choice while folded.
"""
from __future__ import annotations
from tomography_session_browser.presentation.help_content import LocalBinding, InteractionKind

from math import floor, isfinite, log10
from pathlib import Path
from typing import TYPE_CHECKING

from PySide6.QtCore import QSignalBlocker, Qt, QTimer
from PySide6.QtWidgets import (QAbstractButton, QDoubleSpinBox, QFrame, QHBoxLayout, QLabel, QLayout, QPushButton,
                               QScrollArea, QSizePolicy, QSlider, QSpinBox, QToolButton, QVBoxLayout, QWidget)
from tomography_session_browser.ui.tomograms.histogram import HistogramControl
from tomography_session_browser.ui.tomograms.folding_section import FoldingSection
from tomography_session_browser.ui.tomograms.layout_controls import Versions
from tomography_session_browser.ui.tomograms.snapshot_preview import NextFilePreview
from tomography_session_browser.ui.widgets.elided_label import ElidedLabel
from tomography_session_browser.ui.widgets.switch import Switch
from tomography_session_browser.services.volume.memory import VOLUME_MEMORY, MIB
from tomography_session_browser.services.volume.store import resident_key
from tomography_session_browser.services.volume.render import native_region
from tomography_session_browser.services.volume.scale_bar import BAR_COLOURS

from tomography_session_browser.presentation.design_tokens import SPACE_L, SPACE_M, SPACE_S, SPACE_XS
from tomography_session_browser.presentation.tomogram_calibration import CALIBRATION_SCOPE_NOTE

if TYPE_CHECKING:
    from tomography_session_browser.ui.tomograms.tab import TomogramTab

#: Typed values retain a compact width; long controls use the full next row.
FIELD_WIDTH = 72
#: One label column, so one-row controls line up down the panel.
LABEL_WIDTH = 88
#: The lowpass slider's logarithmic range, in cycles per pixel.
LOWPASS_LOW, LOWPASS_HIGH = .01, .5
#: The filter values a Reset puts back in the (switched off) fields.
LOWPASS_DEFAULT, HIGHPASS_DEFAULT = .1, 5.


class LevelField(QDoubleSpinBox):
    """A native intensity limit to four significant figures; the tooltip has full precision.

    Unedited text maps back to the exact value, so tabbing through never
    rounds a level (and never switches the contrast to Manual).
    """

    def textFromValue(self, value: float) -> str:  # noqa: N802 - Qt API
        digits = 0 if value == 0 or not isfinite(value) else max(0, min(self.decimals(), 3 - floor(log10(abs(value)))))
        text = self.locale().toString(value, "f", digits)
        return text if self.isGroupSeparatorShown() else text.replace(self.locale().groupSeparator(), "")

    def valueFromText(self, text: str) -> float:  # noqa: N802 - Qt API
        if text == self.textFromValue(self.value()):
            return self.value()
        return super().valueFromText(text)


def inline_row(label: str, *items: QWidget | QLayout, parent: QWidget, stretch: bool = False) -> QHBoxLayout:
    """Label, control and value on one row; a slider (or ``stretch``) takes the spare width."""
    row = QHBoxLayout()
    row.setSpacing(SPACE_S)
    caption = QLabel(label, parent)
    caption.setFixedWidth(LABEL_WIDTH)
    row.addWidget(caption)
    grows = (QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Expanding)
    for item in items:
        if isinstance(item, QLayout):
            row.addLayout(item, 1)
        else:
            row.addWidget(item, 1 if isinstance(item, QSlider) or item.sizePolicy().horizontalPolicy() in grows else 0)
    if stretch:
        row.addStretch()
    return row


def plain_field(control: QDoubleSpinBox) -> None:
    """A narrow typed value: no step arrows to hide its digits; wheel and keys still step."""
    control.setButtonSymbols(QDoubleSpinBox.ButtonSymbols.NoButtons)
    control.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
    control.setFixedWidth(FIELD_WIDTH)


def share_width(choice: Versions) -> None:
    """A segmented track takes the row's width and never widens the panel."""
    choice.segments.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
    for button in choice.buttons.buttons():
        button.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)


def heading(text: str, parent: QWidget) -> QLabel:
    label = QLabel(text.upper(), parent)
    label.setObjectName("imageSectionTitle")
    return label


def under_control(widget: QWidget) -> QHBoxLayout:
    """A line that starts under the control column, not under the labels."""
    row = QHBoxLayout()
    row.setContentsMargins(LABEL_WIDTH + SPACE_S, 0, 0, 0)
    row.addWidget(widget, 1)
    return row


def rule(parent: QWidget) -> QFrame:
    line = QFrame(parent)
    line.setObjectName("tomogramSectionRule")
    line.setFixedHeight(1)
    return line


def note(parent: QWidget, text: str = "") -> QLabel:
    label = QLabel(text, parent)
    label.setObjectName("tomogramNote")
    label.setWordWrap(True)
    return label


def control_row(label: str, *widgets: QWidget, parent: QWidget, unit: str | None = None,
                switch: QAbstractButton | None = None) -> QVBoxLayout:
    """Give long units, sliders and segmented choices a full-width control row."""
    outer = QVBoxLayout()
    outer.setSpacing(SPACE_XS)
    row = QHBoxLayout()
    row.setSpacing(SPACE_S)
    caption = QLabel(label, parent)
    row.addWidget(caption)
    separate = unit is not None or any(isinstance(widget, (QSlider, QFrame)) for widget in widgets)
    outer.addLayout(row)
    if separate:
        row.addStretch()
        if switch is not None:
            switch.setAccessibleName(switch.accessibleName() or label)
            row.addWidget(switch)
        row = QHBoxLayout()
        row.setSpacing(SPACE_S)
        outer.addLayout(row)
    elif widgets:
        row.addStretch()
    for widget in widgets:
        row.addWidget(widget, 1 if isinstance(widget, (QSlider, QFrame, ElidedLabel)) else 0)
    if unit is not None:
        units = QLabel(unit, parent)
        units.setObjectName("tomogramUnit")
        row.addWidget(units)
    if (separate or not widgets) and not any(isinstance(widget, QSlider) for widget in widgets):
        row.addStretch()
    if switch is not None and not separate:
        switch.setAccessibleName(switch.accessibleName() or label)
        row.addWidget(switch)
    return outer


class ImageSettings(QWidget):
    def keyPressEvent(self, event) -> None:  # noqa: N802
        if event.key() == Qt.Key.Key_Escape:
            self.tab.image_button.setChecked(False)
            event.accept()
            return
        super().keyPressEvent(event)

    def __init__(self, tab: TomogramTab) -> None:
        super().__init__(tab)
        self.tab = tab
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        title_row = QHBoxLayout()
        title_row.setContentsMargins(SPACE_M, SPACE_S, SPACE_M, 0)
        title = QLabel("IMAGE SETTINGS", self)
        title.setObjectName("tomogramPanelTitle")
        title_row.addWidget(title)
        title_row.addStretch()
        # Undo and Reset act on every setting in the panel, so they sit in its header.
        self.undo_button, self.reset_button = QPushButton("Undo", self), QPushButton("Reset", self)
        self.undo_button.clicked.connect(lambda: tab.recipes.undo())
        self.reset_button.clicked.connect(self.reset)
        for button in (self.undo_button, self.reset_button):
            button.setObjectName("secondaryAction")
            title_row.addWidget(button)
        self.close_button = QToolButton(self)
        self.close_button.setText("×")
        self.close_button.setAccessibleName("Close Image settings")
        self.close_button.setToolTip("Close Image settings (Esc)")
        self.close_button.clicked.connect(lambda: tab.image_button.setChecked(False))
        title_row.addWidget(self.close_button)
        outer.addLayout(title_row)
        scroll = QScrollArea(self)
        self.scroll = scroll
        scroll.setObjectName("tomogramImageScroll")
        scroll.setWidgetResizable(True)
        content = QWidget(scroll)
        content.setObjectName("tomogramImageContent")
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setWidget(content)
        outer.addWidget(scroll)
        layout = QVBoxLayout(content)
        layout.setContentsMargins(SPACE_M, SPACE_M, SPACE_M, SPACE_L)
        layout.setSpacing(SPACE_S)

        # Filters: one row each (value, unit, switch); lowpass also has a slider under its value.
        filters_title = QHBoxLayout()
        filters_title.addWidget(heading("Filters", self))
        filters_title.addStretch()
        # A key cap that looks like a button is one: it does what G does.
        self.filters_key = QToolButton(self)
        self.filters_key.setText("G")
        self.filters_key.setObjectName("tomogramKeyHint")
        self.filters_key.setAccessibleName("Stop or start the filters")
        self.filters_key.setToolTip("Stop or start the filters (G)")
        self.filters_key.clicked.connect(lambda: tab.recipes.toggle_filters())
        filters_title.addWidget(self.filters_key)
        layout.addLayout(filters_title)
        self.lowpass_on, self.highpass_on = Switch(self, name="Lowpass"), Switch(self, name="Highpass")
        self.lowpass_on.setToolTip("Use the lowpass filter")
        self.highpass_on.setToolTip("Use the highpass filter")
        self.lowpass = QDoubleSpinBox(self)
        self.lowpass.setDecimals(3)
        self.lowpass.setRange(.001, .5)
        self.lowpass.setValue(LOWPASS_DEFAULT)
        self.lowpass.setSingleStep(.01)
        self.highpass = QDoubleSpinBox(self)
        self.highpass.setRange(.1, 10000)
        self.highpass.setValue(HIGHPASS_DEFAULT)
        self.lowpass.setToolTip("Remove structures that are smaller than this. The value is the sigma of a 2D Gaussian filter, in cycles "
                                "per pixel. A smaller value gives a smoother image.")
        self.highpass.setToolTip("Remove slow changes in brightness across the image. The value is the radius of a 2D "
                                 "Gaussian filter, in Fourier pixels.")
        # The lowpass slider is logarithmic over 0.01–0.5 cycles/px; it publishes on release.
        self.lowpass_slider = QSlider(Qt.Orientation.Horizontal, self)
        self.lowpass_slider.setRange(0, 100)
        self.lowpass_slider.setAccessibleName("Lowpass sigma")
        self.lowpass_slider.setToolTip(self.lowpass.toolTip())
        self.lowpass_slider.setEnabled(False)
        self.lowpass_slider.valueChanged.connect(self.lowpass_dragged)
        self.lowpass_slider.sliderReleased.connect(self.filter_changed)
        self.lowpass_unit, self.highpass_unit = QLabel("cycles/px", self), QLabel("Fourier px", self)
        for switch, control, unit, label in ((self.lowpass_on, self.lowpass, self.lowpass_unit, "Lowpass"),
                                             (self.highpass_on, self.highpass, self.highpass_unit, "Highpass")):
            plain_field(control)
            control.setFixedWidth(64)
            control.setKeyboardTracking(False)
            control.setEnabled(False)
            unit.setObjectName("tomogramUnit")
            switch.toggled.connect(control.setEnabled)
            switch.toggled.connect(self.filter_changed)
            control.valueChanged.connect(self.filter_changed)
            row = inline_row(label, control, unit, parent=self, stretch=True)
            row.addWidget(switch)
            layout.addLayout(row)
            if control is self.lowpass:
                self.lowpass_on.toggled.connect(self.lowpass_slider.setEnabled)
                self.lowpass.valueChanged.connect(self.sync_lowpass_slider)
                slider_row = QHBoxLayout()
                slider_row.setContentsMargins(LABEL_WIDTH + SPACE_S, 0, 0, 0)
                slider_row.addWidget(self.lowpass_slider)
                layout.addLayout(slider_row)
        self.filter_units = note(self)
        layout.addWidget(self.filter_units)
        layout.addSpacing(SPACE_XS)
        layout.addWidget(rule(self))

        # Contrast: Relevel and Undo by the heading; mode, histogram, limits, adjustments, switches.
        contrast_title = QHBoxLayout()
        contrast_title.setSpacing(SPACE_XS)
        contrast_title.addWidget(heading("Contrast", self))
        contrast_title.addStretch()
        self.relevel_button = QPushButton("Relevel", self)
        self.relevel_button.setObjectName("secondaryAction")
        self.relevel_button.clicked.connect(self.auto_levels)
        contrast_title.addWidget(self.relevel_button)
        layout.addLayout(contrast_title)
        self.mode = Versions(self)
        self.mode.addItems(("Auto", "±3σ", "Manual"))
        for index, explanation in enumerate((
                "Set black and white from the 0.5th and 99.5th percentiles of this plane",
                "Set black and white to the mean ± 3 standard deviations of this plane",
                "Type black and white, or drag the B and W lines on the histogram")):
            self.mode.setItemData(index, explanation, Qt.ItemDataRole.ToolTipRole)
        self.mode.setToolTip("How the app sets black and white. Auto and ±3σ measure one plane and keep those levels "
                             "until you click Relevel.")
        self.mode.currentTextChanged.connect(self.mode_changed)
        self.mode.rebuild()
        self.mode.buttons.button(1).setAccessibleName("Mean plus or minus 3 standard deviations")
        share_width(self.mode)
        self.mode.hide()
        layout.addWidget(self.mode.segments)
        self.histogram = HistogramControl(self)
        self.histogram.limits_changed.connect(self.histogram_levels)
        layout.addWidget(self.histogram)
        # Both limits on one row under the histogram, without step arrows.
        self.black, self.white = LevelField(self), LevelField(self)
        levels = QHBoxLayout()
        levels.setSpacing(SPACE_S)
        for index, (name, control) in enumerate((("Black", self.black), ("White", self.white))):
            control.setRange(-1e20, 1e20)
            control.setDecimals(6)
            plain_field(control)
            control.setKeyboardTracking(False)
            control.setAccessibleName(name + " level")
            control.valueChanged.connect(self.manual_levels)
            control.editingFinished.connect(self.refresh_committed_levels)
            if index:
                levels.addStretch()
            levels.addWidget(QLabel(name, self))
            levels.addWidget(control)
        layout.addLayout(levels)
        self.brightness, self.contrast = QDoubleSpinBox(self), QDoubleSpinBox(self)
        self.brightness.setRange(-100, 100)
        self.brightness.setSuffix(" %")
        self.contrast.setRange(.1, 10)
        self.contrast.setValue(1)
        self.contrast.setSingleStep(.1)
        # Sliders drag the same values; the fields stay the typeable, exact home.
        self.brightness_slider, self.contrast_slider = QSlider(Qt.Orientation.Horizontal, self), QSlider(Qt.Orientation.Horizontal, self)
        self.brightness_slider.setRange(-100, 100)
        self.contrast_slider.setRange(-100, 100)  # log10 contrast × 100: 1.00 sits in the middle.
        self.brightness_slider.setAccessibleName("Brightness")
        self.contrast_slider.setAccessibleName("Contrast")
        self.brightness_slider.valueChanged.connect(lambda value: self.brightness.setValue(value))
        self.contrast_slider.valueChanged.connect(lambda value: self.contrast.setValue(10 ** (value / 100)))
        for control in (self.brightness, self.contrast):
            plain_field(control)
            control.setFixedWidth(64)
            control.setKeyboardTracking(False)
            control.valueChanged.connect(self.adjustment_changed)
            control.valueChanged.connect(self.sync_sliders)
        layout.addLayout(inline_row("Brightness", self.brightness_slider, self.brightness, parent=self))
        layout.addLayout(inline_row("Contrast", self.contrast_slider, self.contrast, parent=self))
        self.brightness.setToolTip("Make the image lighter or darker")
        self.contrast.setToolTip("Increase or decrease the difference between light and dark. 1.00 makes no change.")
        self.brightness_slider.setToolTip(self.brightness.toolTip())
        self.contrast_slider.setToolTip(self.contrast.toolTip())
        self.invert = Switch(self)
        self.invert.setToolTip("Show dark as light and light as dark (F11)")
        self.invert.toggled.connect(self.filter_changed)
        self.smooth = Switch(self)
        self.smooth.setChecked(True)
        self.smooth.toggled.connect(self.display_changed)
        self.smooth.setToolTip("Show a smooth image when you zoom in. Set to off to see each voxel as a square.")
        layout.addLayout(control_row("Invert", parent=self, switch=self.invert))
        layout.addLayout(control_row("Smooth pixels", parent=self, switch=self.smooth))
        layout.addWidget(rule(self))

        # Snapshot settings; the action itself is the toolbar's camera (Ctrl+S).
        self.snapshot_section = FoldingSection("Snapshot", self)
        self.export_format = Versions(self)
        self.export_format.addItems(("PNG", "TIFF", "JPEG"))
        self.export_format.setAccessibleName("Snapshot format")
        self.export_format.rebuild()
        self.export_format.hide()
        # Both sizes save the same part of the plane: what is in view.
        self.sampling = Versions(self)
        self.sampling.setAccessibleName("Snapshot size")
        self.sampling.addItems(("Screen", "Native"))
        self.sampling.setItemData(0, "Save the view with the pixels that you see on the screen", Qt.ItemDataRole.ToolTipRole)
        self.sampling.setItemData(1, "Save the same part of the view with one pixel for each voxel. "
                                     "The zoom does not change the number of pixels.", Qt.ItemDataRole.ToolTipRole)
        self.sampling.setToolTip("The number of pixels in the saved image. The two alternatives save only the part of the plane "
                                 "that is in view.")
        self.sampling.rebuild()
        self.sampling.hide()
        share_width(self.export_format)
        share_width(self.sampling)
        body = self.snapshot_section.layout_
        body.addLayout(inline_row("Format", self.export_format.segments, parent=self))
        body.addLayout(inline_row("Size", self.sampling.segments, parent=self))
        self.size_note = note(self)
        body.addLayout(under_control(self.size_note))
        # The folder's own name in full; where it is, elided in the middle, under it.
        self.folder_name = ElidedLabel("", mode=Qt.TextElideMode.ElideRight, parent=self)
        self.folder_name.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.folder_label = ElidedLabel("", mode=Qt.TextElideMode.ElideMiddle, parent=self)
        self.folder_label.setObjectName("tomogramNote")
        self.folder_label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        tab.folder_button.setObjectName("secondaryAction")
        tab.folder_button.clicked.connect(lambda: QTimer.singleShot(0, self, self.refresh_summaries))
        body.addLayout(inline_row("Folder", self.folder_name, tab.folder_button, parent=self))
        body.addLayout(under_control(self.folder_label))
        self.next_file = NextFilePreview(tab, self)
        body.addLayout(inline_row("Next file", self.next_file, parent=self))
        for control in (self.export_format, self.sampling):
            control.currentIndexChanged.connect(self.refresh_summaries)
        layout.addWidget(self.snapshot_section)
        layout.addWidget(rule(self))

        self.scale_section = FoldingSection("Scale bar", self)
        self.show_bar = Switch(self)
        self.show_bar.setChecked(True)
        self.show_bar.setToolTip("Show the scale bar on the image and in snapshots")
        self.show_bar.toggled.connect(self.display_changed)
        self.show_bar.toggled.connect(self.refresh_summaries)
        self.scale_section.layout_.addLayout(control_row("Show bar", parent=self, switch=self.show_bar))
        from tomography_session_browser.ui.tomograms.scale_bar_controls import ScaleBarControls
        self.scale_controls = ScaleBarControls(tab, self)
        self.scale_section.layout_.addWidget(self.scale_controls)
        self.show_bar.toggled.connect(self.scale_controls.setEnabled)
        layout.addWidget(self.scale_section)
        layout.addWidget(rule(self))

        # Calibration: overrides are display-only and per version.
        self.calibration_section = FoldingSection("Calibration", self)
        self.calibration_note = note(self)
        self.spacing = LevelField(self)
        self.spacing.setRange(0.000001, 1000000)
        self.spacing.setDecimals(6)
        plain_field(self.spacing)
        self.spacing.setKeyboardTracking(False)
        self.spacing.setAccessibleName("Isotropic voxel spacing")
        self.spacing.setToolTip("The voxel size, the same in X, Y and Z")
        self.spacing.valueChanged.connect(self.refresh_actions)
        confirm = self.confirm_button = QPushButton("Confirm", self)
        confirm.setToolTip("Use this voxel size for this version in the app. The MRC file does not change.")
        confirm.clicked.connect(self.confirm_spacing)
        undo = self.header_button = QPushButton("Use header", self)
        undo.setObjectName("secondaryAction")
        undo.clicked.connect(self.use_header)
        calibration = self.calibration_section.layout_
        calibration.addWidget(self.calibration_note)
        unit = QLabel("Å/voxel", self)
        unit.setObjectName("tomogramUnit")
        calibration.addLayout(inline_row("Spacing", self.spacing, unit, parent=self, stretch=True))
        buttons = QHBoxLayout()
        buttons.setContentsMargins(LABEL_WIDTH + SPACE_S, 0, 0, 0)
        buttons.addWidget(confirm)
        buttons.addWidget(undo)
        buttons.addStretch()
        calibration.addLayout(buttons)
        self.calibration_scope_note = note(self, CALIBRATION_SCOPE_NOTE)
        calibration.addWidget(self.calibration_scope_note)
        layout.addWidget(self.calibration_section)
        layout.addWidget(rule(self))

        self.memory_section = FoldingSection("Memory", self)
        self.memory_share = QSpinBox(self)
        self.memory_share.setRange(10, 60)
        self.memory_share.setSuffix(" % of RAM")
        self.memory_share.setValue(tab.settings.tomogram_memory_percent)
        self.memory_share.setKeyboardTracking(False)
        self.memory_share.setButtonSymbols(QSpinBox.ButtonSymbols.NoButtons)
        self.memory_share.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.memory_share.setFixedWidth(self.memory_share.fontMetrics().horizontalAdvance("60 % of RAM") + 24)
        self.memory_share.setToolTip("The part of the computer memory that tomograms can use. If free memory is low, "
                                     "the app uses less.")
        self.memory_share.valueChanged.connect(self.memory_changed)
        self.memory_usage = note(self)
        clear_cache = QPushButton("Release idle planes", self)
        clear_cache.setObjectName("secondaryAction")
        clear_cache.setToolTip("Remove the planes that are not on the screen from memory")
        clear_cache.clicked.connect(self.clear_cache)
        memory = self.memory_section.layout_
        memory.addLayout(inline_row("Limit", self.memory_share, parent=self, stretch=True))
        memory.addWidget(self.memory_usage)
        memory.addWidget(clear_cache, 0, Qt.AlignmentFlag.AlignLeft)
        layout.addWidget(self.memory_section)
        layout.addStretch()
        self.refresh_summaries()

    def refresh_summaries(self, *_args) -> None:
        """What a folded section would show, said once in its heading."""
        self.snapshot_section.summary.setText(f"{self.export_format.currentText()} · {self.sampling.currentText()}")
        folder = self.tab.settings.tomogram_snapshot_folder or ""
        path = Path(folder)
        self.folder_name.setText((path.name or folder) if folder else "Not chosen")
        self.folder_name.setToolTip(folder or "You select a folder when you save the first snapshot")
        self.folder_label.setText(str(path.parent) if folder else "")
        self.folder_label.setToolTip(folder)
        self.folder_label.setVisible(bool(folder))
        self.tab.folder_button.setText("Change…" if folder else "Choose…")
        self.refresh_size_note()
        style = self.tab.state.scale_bar
        colour = next((name for name, value in BAR_COLOURS if value == style.colour), "Custom")
        self.scale_section.summary.setText(f"{colour} · {'Auto' if style.exact_nm is None else f'{style.exact_nm:g} nm'}"
                                           if self.show_bar.isChecked() else "Hidden")
        volume = self.tab.volume()
        from tomography_session_browser.services.volume.render import calibrated_spacing
        spacing = calibrated_spacing(volume, self.tab.state.spacing_override) if volume is not None else None
        source = "confirmed" if self.tab.state.spacing_override is not None else "header"
        self.calibration_section.summary.setText(
            "No volume" if volume is None else f"{spacing[0]:g} Å/voxel · {source}" if spacing and len(set(spacing)) == 1
            else "Anisotropic · header" if spacing else "Unresolved")
        self.refresh_memory_summary()

    def refresh_memory_summary(self) -> None:
        """The Memory heading's only writer, so it reads the same after every change."""
        volume = self.tab.volume()
        in_memory = volume is not None and VOLUME_MEMORY.contains(resident_key(volume))
        self.memory_section.summary.setText(f"{self.memory_share.value()} % of RAM · {'in memory' if in_memory else 'streaming'}")

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        self.refresh_summaries()
        self.next_file.refresh(force=True)
        # Hidden panels do not request per-plane histogram work. Opening the
        # panel refreshes the current frame, including when geometry is unchanged.
        if getattr(self.tab, "constructed", False):
            self.tab.request_render()

    def refresh_size_note(self, *_args) -> None:
        """Say how many pixels each choice saves now, so Screen and Native need no explanation."""
        rendering = getattr(self.tab, "rendering", None)
        accepted = rendering.accepted if rendering is not None else None
        if accepted is None:
            self.size_note.setText("")
            return
        recipe = accepted.result.recipe
        if self.sampling.currentIndex() == 1:
            x0, y0, x1, y1 = native_region(recipe.region)
            width, height = x1 - x0, y1 - y0
            self.size_note.setText(f"{width} × {height} px, one pixel for each voxel" if width > 0 and height > 0
                                   else "No whole voxel is in view. Zoom out.")
        else:
            width, height = recipe.raster_size
            self.size_note.setText(f"{width} × {height} px, as on the screen")

    def refresh_actions(self, *_args) -> None:
        """A button with nothing to do is off, and its tooltip says why."""
        tab, state = self.tab, self.tab.state
        loaded = tab.volume() is not None
        default = tab.recipes.is_default()
        self.reset_button.setEnabled(loaded and not default)
        self.reset_button.setToolTip("All values are at their defaults" if default else
                                     "Set the filters, contrast and scale bar to their defaults. The voxel size does not change.")
        undo = tab.recipes.can_undo()
        self.undo_button.setEnabled(undo)
        self.undo_button.setToolTip("Undo the last image change (Ctrl+Z)" if undo else "There is no image change to undo")
        self.refresh_relevel()
        override = state.spacing_override
        self.header_button.setEnabled(loaded and override is not None)
        self.header_button.setToolTip("Use the voxel size from the MRC header" if override is not None
                                      else "The app uses the voxel size from the MRC header")
        self.confirm_button.setEnabled(loaded and self.spacing.value() != override)

    def refresh_relevel(self) -> None:
        state = self.tab.state
        # Manual levels come from no plane; Auto and ±3σ wait for the first frame's anchor.
        manual = state.levels_mode == "Manual"
        relevel = self.tab.volume() is not None and (manual or state.levels_anchor not in (None, (state.axis, state.plane)))
        self.relevel_button.setEnabled(relevel)
        self.relevel_button.setToolTip(
            "Set Auto levels from this plane (Shift+A)" if manual
            else f"Set the {state.levels_mode} levels again from this plane (Shift+A)" if relevel
            else "The levels come from this plane. Go to a different plane to set them again.")

    def sync_sliders(self, *_args) -> None:
        with QSignalBlocker(self.brightness_slider), QSignalBlocker(self.contrast_slider):
            self.brightness_slider.setValue(round(self.brightness.value()))
            self.contrast_slider.setValue(round(100 * log10(max(self.contrast.value(), .1))))

    def sync_state(self) -> None:
        state = self.tab.state
        controls = (self.lowpass_on, self.highpass_on, self.lowpass, self.highpass, self.invert, self.show_bar, self.smooth,
                    self.mode, self.brightness, self.contrast)
        blockers = [QSignalBlocker(control) for control in controls]
        self.lowpass_on.setChecked(state.lowpass > 0)
        self.highpass_on.setChecked(state.highpass > 0)
        if state.lowpass:
            self.lowpass.setValue(state.lowpass)
        if state.highpass:
            self.highpass.setValue(state.highpass)
        self.lowpass.setEnabled(state.lowpass > 0)
        self.lowpass_slider.setEnabled(state.lowpass > 0)
        self.sync_lowpass_slider()
        self.highpass.setEnabled(state.highpass > 0)
        self.invert.setChecked(state.invert)
        self.show_bar.setChecked(state.show_bar)
        self.scale_controls.setEnabled(state.show_bar)
        self.scale_controls.sync(state.scale_bar)
        self.smooth.setChecked(state.smooth)
        self.mode.setCurrentText(state.levels_mode)
        self.mode.sync()
        self.black.setEnabled(state.black is not None or state.levels_mode == "Manual")
        self.white.setEnabled(state.white is not None or state.levels_mode == "Manual")
        self.brightness.setValue(state.brightness * 100)
        self.contrast.setValue(state.contrast)
        del blockers
        self.sync_sliders()
        self.update_filter_units()
        volume = self.tab.volume()
        self.calibration_section.toggle.setChecked(bool(volume is not None and volume.warnings))
        self.refresh_summaries()
        self.refresh_actions()

    def update_filter_units(self) -> None:
        """The lowpass unit says the matching wavelength once the spacing is known."""
        from tomography_session_browser.services.volume.render import calibrated_spacing, plane_spacing
        volume = self.tab.volume()
        spacing = calibrated_spacing(volume, self.tab.state.spacing_override) if volume else None
        sigma = self.lowpass.value()
        if spacing:
            sx, sy = plane_spacing(spacing, self.tab.state.axis)
            wavelength = f"≈ {sx / sigma:.0f} Å" if sx == sy else f"≈ {sx / sigma:.0f} × {sy / sigma:.0f} Å"
            self.lowpass_unit.setText(wavelength)
            self.lowpass_unit.setToolTip(f"Sigma {sigma:g} cycles per pixel removes structures smaller than approximately {sx / sigma:.0f} Å"
                                         if sx == sy else f"Sigma {sigma:g} cycles per pixel: {sx / sigma:g} × {sy / sigma:g} Å")
        else:
            self.lowpass_unit.setText("cycles/px")
            self.lowpass_unit.setToolTip("Sigma in cycles per pixel")
        self.filter_units.setText("The voxel size is not known, so the filters use pixel units.")
        self.filter_units.setVisible(self.lowpass_on.isChecked() and not spacing)

    @staticmethod
    def lowpass_position(sigma: float) -> int:
        return round(max(0., min(1., log10(max(sigma, LOWPASS_LOW) / LOWPASS_LOW) / log10(LOWPASS_HIGH / LOWPASS_LOW))) * 100)

    def sync_lowpass_slider(self, *_args) -> None:
        with QSignalBlocker(self.lowpass_slider):
            self.lowpass_slider.setValue(self.lowpass_position(self.lowpass.value()))

    def lowpass_dragged(self, position: int) -> None:
        """Dragging shows the value; releasing (or a key step) publishes it."""
        with QSignalBlocker(self.lowpass):
            self.lowpass.setValue(round(LOWPASS_LOW * (LOWPASS_HIGH / LOWPASS_LOW) ** (position / 100), 3))
        self.update_filter_units()
        if not self.lowpass_slider.isSliderDown():
            self.filter_changed()

    def filter_changed(self, *_args) -> None:
        self.tab.recipes.change(relevel=True, lowpass=self.lowpass.value() if self.lowpass_on.isChecked() else 0.,
            highpass=self.highpass.value() if self.highpass_on.isChecked() else 0., invert=self.invert.isChecked())
        self.update_filter_units()
        self.tab.request_render()

    def display_changed(self, *_args) -> None:
        self.tab.recipes.change(show_bar=self.show_bar.isChecked(), smooth=self.smooth.isChecked())

    def sync_levels(self, black: float | None, white: float | None) -> None:
        changed = (self.black.isEnabled() != (black is not None or self.tab.state.levels_mode == "Manual") or
                   self.white.isEnabled() != (white is not None or self.tab.state.levels_mode == "Manual"))
        self.black.setEnabled(black is not None or self.tab.state.levels_mode == "Manual")
        self.white.setEnabled(white is not None or self.tab.state.levels_mode == "Manual")
        for control, value in ((self.black, black if black is not None else 0.), (self.white, white if white is not None else 1.)):
            editor = control.lineEdit()
            if editor is not None and editor.isModified():
                continue  # An accepted background plane must not replace a draft.
            if control.value() == value:
                continue
            changed = True
            with QSignalBlocker(control):
                control.setValue(value)
            control.setToolTip(f"{control.accessibleName()}: {control.value():.8g}, in the units of the MRC file")
        if changed:
            self.refresh_actions()

    def refresh_committed_levels(self) -> None:
        for control in (self.black, self.white):
            editor = control.lineEdit()
            if editor is not None:
                editor.setModified(False)
        self.sync_levels(self.tab.state.black, self.tab.state.white)

    def manual_levels(self) -> None:
        if self.white.value() <= self.black.value():
            self.tab.status.message.setText("White level must be greater than black level.")
            return
        self.tab.recipes.change(black=self.black.value(), white=self.white.value(), levels_mode="Manual")

    def reset(self) -> None:
        """Reset the recipe, and put the default values back in the switched-off filter fields."""
        self.tab.recipes.reset()
        for control, value in ((self.lowpass, LOWPASS_DEFAULT), (self.highpass, HIGHPASS_DEFAULT)):
            with QSignalBlocker(control):
                control.setValue(value)
        self.sync_lowpass_slider()
        self.update_filter_units()

    def auto_levels(self) -> None:
        self.tab.recipes.change(relevel=True, force=True, levels_mode="Auto" if self.mode.currentText() == "Manual" else self.mode.currentText())

    def mode_changed(self, mode: str) -> None:
        self.tab.recipes.change(relevel=mode != "Manual", levels_mode=mode)

    def adjustment_changed(self, *_args) -> None:
        self.tab.recipes.change(brightness=self.brightness.value() / 100, contrast=self.contrast.value())

    def histogram_levels(self, black: float, white: float) -> None:
        self.tab.recipes.change(black=black, white=white, levels_mode="Manual")

    def memory_changed(self, value: int) -> None:
        self.tab.playback.stop()
        VOLUME_MEMORY.configure(value)
        self.tab.settings.tomogram_memory_percent = value
        self.tab.save_settings(self.tab.settings)
        self.update_memory()

    def update_memory(self) -> None:
        with QSignalBlocker(self.memory_share):
            self.memory_share.setValue(VOLUME_MEMORY.percent)
        self.tab.settings.tomogram_memory_percent = VOLUME_MEMORY.percent
        memory = VOLUME_MEMORY.snapshot()
        self.memory_usage.setText(f"In use {memory['live']/MIB:.1f} MiB · cache {memory['resident']/MIB:.1f} MiB · limit {memory['limit']/MIB:.0f} MiB")
        self.refresh_memory_summary()

    def clear_cache(self) -> None:
        VOLUME_MEMORY.clear_unpinned()
        self.update_memory()

    def confirm_spacing(self) -> None:
        self.tab.recipes.change(spacing_override=self.spacing.value(), linked_scale=None)
        self.update_filter_units()
        self.refresh_actions()
        self.tab.request_render()

    def use_header(self) -> None:
        self.tab.recipes.change(spacing_override=None, linked_scale=None)
        self.update_filter_units()
        self.refresh_actions()
        self.tab.request_render()


# Contextual keys handled by this module; consumed by the shared Help sheet.
LOCAL_KEYS = (
    LocalBinding('tomograms.image_settings.image-settings-esc', 'Image settings', InteractionKind.KEYBOARD, ('Esc',), 'Close Image settings'),
)
