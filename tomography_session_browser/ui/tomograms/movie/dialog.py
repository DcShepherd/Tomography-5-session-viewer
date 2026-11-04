"""The window-modal movie dialog (plan §1.2–1.6, board 3b): the frame preview and timeline on the left, the settings
on the right in four cards. Each card's line says its settings; one card at a time opens to show its controls. Every
text it shows comes from the presenter, from one set of settings.

Only the Export button exports. No push button is a default button, so Enter in a field applies its value and
nothing else."""
from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING

import numpy as np
from PySide6.QtCore import QSignalBlocker, QSize, Qt, QTimer
from PySide6.QtWidgets import (QAbstractSpinBox, QComboBox, QDialog, QFileDialog, QFrame, QHBoxLayout, QLabel, QLineEdit,
                               QPushButton, QScrollArea, QSizePolicy, QSlider, QToolButton, QVBoxLayout, QWidget)

from tomography_session_browser.presentation.design_tokens import (SPACE_L, SPACE_M, SPACE_S, SPACE_XL, SPACE_XS,
                                                                   SPACE_XXL)
from tomography_session_browser.presentation.tomogram_movie import (ACTIVE, FFMPEG_NOTE, FRAMINGS, INFORMATION, NO_FFMPEG,
                                                                    NO_PIXEL_SIZE, ORTHO, ORTHO_REASON, SECTIONS,
                                                                    FrozenView, MovieAssessment, MovieSettings, assess,
                                                                    automatic, change_sweep, change_view, extension,
                                                                    rate_hint, readout, step_hint)
from tomography_session_browser.services.movie.compose import NORMALS
from tomography_session_browser.services.movie.models import (COMPRESSION, COMPRESSION_HINTS, FRAMES_ONLY, SAVES,
                                                              MovieFailure)
from tomography_session_browser.services.movie.sizing import SHAPES, SIZES
from tomography_session_browser.services.movie.timeline import (BOUNCE, HOLD_RANGE, SINGLE_PASS, ChosenHold,
                                                                hold_here_reason, unused_reason)
from tomography_session_browser.services.output_boundary import require_output_directory
from tomography_session_browser.ui.dashboard.layout import FlowLayout
from tomography_session_browser.ui.icons import themed_icon
from tomography_session_browser.ui.theme import current_palette
from tomography_session_browser.ui.tomograms.image_settings import note
from tomography_session_browser.ui.tomograms.movie.dialog_parts import (CARD_BORDER, CONTROL_HEIGHT, LABEL_COLUMN,
                                                                        FramePreview, HoldChip, MovieSection, PictureChip,
                                                                        RateField, Seconds, Segments, SettingRow, hint,
                                                                        pane_dots, plane_field)
from tomography_session_browser.ui.tomograms.movie.timeline_bar import TimelineBar
from tomography_session_browser.ui.widgets.elided_label import ElidedLabel
from tomography_session_browser.ui.widgets.link_button import LinkButton
from tomography_session_browser.ui.widgets.switch import Switch

if TYPE_CHECKING:
    from tomography_session_browser.ui.tomograms.movie.owner import MovieExport

FROZEN_NOTE = "The movie uses the view as it was when this dialog opened. Changes in the viewer do not change it."
NO_HOLDS = "To hold on another frame, move the timeline to it and click Hold here."
CLIP_TOOLTIP = "Save 3 seconds from the timeline position as a test MP4, then open it in your video player"
CHECKING = "Not available yet: the app is checking for FFmpeg."
PRESS_O = "Press O in the viewer first."
SHAPE_HINT = "Extra space is black. The image is not stretched or cut."
SWEEP_LABELS = {"XY": "XY · Z", "XZ": "XZ · Y", "YZ": "YZ · X"}
#: The preview waits this long after the last change before it asks for a frame.
PREVIEW_DELAY_MS = 60
#: Board 3b: a 1200 × 1000 dialog with a 552 px settings column; the preview column takes the rest.
DIALOG_SIZE, SETTINGS_WIDTH, PREVIEW_MIN_WIDTH = QSize(1200, 1000), 552, 400
TITLE_WIDTH, FROZEN_WIDTH = 220, 400
#: The section that is open when the dialog opens: its values change most from movie to movie.
OPEN_AT_START = "Timing"


def outlined(text: str, parent: QWidget, tip: str, name: str = "movieStepButton") -> QPushButton:
    """An outlined button; its height is in the style sheet, which overrides a height set here."""
    button = QPushButton(text, parent)
    button.setObjectName(name)
    button.setToolTip(tip)
    button.setAutoDefault(False)
    return button


def same_place(hold: ChosenHold, other: ChosenHold) -> bool:
    """One hold at any time: a chip keeps the hold it was made with, and its time changes after that."""
    return (hold.plane, hold.direction) == (other.plane, other.direction)


def field_height(*fields: QWidget) -> None:
    for field in fields:
        field.setFixedHeight(CONTROL_HEIGHT)


class MovieDialog(QDialog):
    def __init__(self, owner: MovieExport, view: FrozenView, settings: MovieSettings, start_note: str = "") -> None:
        super().__init__(owner.tab.window())
        self.owner, self.view, self.settings = owner, view, settings
        #: What of the tomogram's last settings could not apply to this view.
        self.start_note = start_note
        self.setObjectName("movieDialog")
        self.setWindowTitle("Export movie")
        self.setAccessibleName("Export movie")
        self.setWindowModality(Qt.WindowModality.WindowModal)
        self.index = 0
        self.plan: MovieAssessment | None = None
        self.chips_for: tuple = ()
        self.preview_timer = QTimer(self)
        self.preview_timer.setSingleShot(True)
        self.preview_timer.setInterval(PREVIEW_DELAY_MS)
        self.preview_timer.timeout.connect(self.request_frame)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        outer.addLayout(self.build_header())
        body = QHBoxLayout()
        body.setContentsMargins(SPACE_XL, SPACE_L, SPACE_XL, SPACE_L)
        body.setSpacing(SPACE_XXL)
        body.addWidget(self.build_preview(), 1)
        body.addWidget(self.build_settings())
        outer.addLayout(body, 1)
        outer.addWidget(self.build_footer())
        # A default (or auto-default) push button is what Enter in a field clicks: there is none.
        for button in self.findChildren(QPushButton):
            button.setAutoDefault(False)
            button.setDefault(False)
        self.sync_controls()
        self.refresh()
        self.fit_to_screen()
        self.opened_with = self.settings

    @property
    def changed(self) -> bool:
        """Whether the user changed a setting. The FFmpeg check can change Save: that does not count."""
        return self.settings != self.opened_with

    # Building.
    def build_header(self) -> QHBoxLayout:
        header = QHBoxLayout()
        header.setContentsMargins(SPACE_XL, SPACE_L + SPACE_XS, SPACE_XL, 0)
        header.setSpacing(SPACE_XL)
        left = QVBoxLayout()
        left.setSpacing(SPACE_XS)
        title = QLabel("Export movie", self)
        title.setObjectName("messageSheetTitle")
        left.addWidget(title)
        source = QHBoxLayout()
        source.setSpacing(SPACE_S)
        icon = QLabel(self)
        icon.setPixmap(themed_icon("volume", color=current_palette().text_muted, size=16).pixmap(16, 16))
        source.addWidget(icon)
        name = ElidedLabel(f"{self.view.tomogram} · {self.view.volume.version}", mode=Qt.TextElideMode.ElideMiddle,
                           parent=self)
        name.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        source.addWidget(name, 1)
        left.addLayout(source)
        header.addLayout(left, 1)
        frozen = hint(self, FROZEN_NOTE)
        frozen.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignTop)
        frozen.setFixedWidth(FROZEN_WIDTH)
        header.addWidget(frozen, 0, Qt.AlignmentFlag.AlignTop)
        return header

    def build_preview(self) -> QWidget:
        holder = QWidget(self)
        holder.setMinimumWidth(PREVIEW_MIN_WIDTH)
        column = QVBoxLayout(holder)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(SPACE_M)
        self.preview = FramePreview(holder)
        column.addWidget(self.preview, 1)
        track = QVBoxLayout()
        track.setSpacing(SPACE_XS)
        self.bar = TimelineBar(holder)
        self.bar.moved.connect(self.move_to)
        track.addWidget(self.bar)
        self.scrubber = QSlider(Qt.Orientation.Horizontal, holder)
        self.scrubber.setObjectName("movieScrubber")
        self.scrubber.setAccessibleName("Movie frame")
        self.scrubber.valueChanged.connect(self.move_to)
        track.addWidget(self.scrubber)
        column.addLayout(track)
        row = QHBoxLayout()
        row.setSpacing(SPACE_S)
        # A readout that does not fit elides: it never pushes the buttons beside it out of the column.
        self.readout = ElidedLabel("", mode=Qt.TextElideMode.ElideRight, parent=holder)
        self.readout.setObjectName("movieReadout")
        self.readout.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        row.addWidget(self.readout, 1)
        self.hold_here = outlined("Hold here", holder, "", "movieHoldHere")
        self.hold_here.setIcon(themed_icon("pause", color=current_palette().accent, size=12))
        self.hold_here.setIconSize(QSize(12, 12))
        self.hold_here.clicked.connect(self.add_hold)
        row.addWidget(self.hold_here)
        row.addSpacing(SPACE_XS)
        for label, where in (("First", 0), ("Middle", 1), ("Last", 2)):
            button = outlined(label, holder, f"Show the {label.lower()} frame")
            button.clicked.connect(lambda _checked=False, where=where: self.jump(where))
            row.addWidget(button)
        column.addLayout(row)
        self.notes = QVBoxLayout()
        self.notes.setSpacing(SPACE_XS)
        column.addLayout(self.notes)
        return holder

    def build_settings(self) -> QScrollArea:
        scroll = self.settings_scroll = QScrollArea(self)
        scroll.setObjectName("movieSettingsScroll")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        content = QWidget(scroll)
        content.setObjectName("movieSettings")
        layout = self.settings_layout = QVBoxLayout(content)
        layout.setContentsMargins(0, 0, SPACE_S, 0)
        layout.setSpacing(SPACE_S)
        self.sections: list[MovieSection] = []
        builds = (self.build_planes, self.build_timing, self.build_picture, self.build_file)
        for title, build in zip(SECTIONS, builds, strict=True):
            section = MovieSection(title, content, open_=title == OPEN_AT_START)
            build(section.body, section.layout_)
            section.line.toggled.connect(lambda open_, section=section: self.section_toggled(section, open_))
            section.opened.connect(lambda section=section: self.show_section(section))
            layout.addWidget(section)
            self.sections.append(section)
        layout.addStretch()
        scroll.setWidget(content)
        # One column down all four cards, as wide as the longest label in this font: sentences start where controls do.
        content.ensurePolished()
        self.rows = content.findChildren(SettingRow)
        names = [section.line.name for section in self.sections]
        column = max(LABEL_COLUMN, *(row.caption.sizeHint().width() for row in self.rows),
                     *(name.sizeHint().width() for name in names))
        for row in self.rows:
            row.set_column(column)
        for name in names:
            name.setFixedWidth(column)
        return scroll

    def section_toggled(self, section: MovieSection, open_: bool) -> None:
        """One section at a time: the open one closes as another opens, each on its own spring."""
        if open_:
            for other in self.sections:
                if other is not section:
                    other.line.setChecked(False)

    def show_section(self, section: MovieSection) -> None:
        """All of the section if it fits; otherwise its top, because its line is what closes it again."""
        self.settings_scroll.ensureWidgetVisible(section, 0, 0)
        self.settings_scroll.ensureWidgetVisible(section.line, 0, CARD_BORDER)

    def fit_settings(self) -> None:
        """Board 3b's settings width; wider, never narrower, when the texts in this font need it.

        A row in a closed section counts too, so opening a section never moves the column."""
        scroll = self.settings_scroll
        padding = self.sections[0].padding
        widths = [SETTINGS_WIDTH]
        for row in self.rows:
            if not row.isHidden():
                # A changed text has not reached the row's cached size yet: activate brings it up to date.
                row.outer.activate()
                widths.append(row.minimumSizeHint().width() + padding)
        margins = self.settings_layout.contentsMargins()
        needed = max(widths) + margins.left() + margins.right() + scroll.verticalScrollBar().sizeHint().width()
        if needed > scroll.minimumWidth():
            scroll.setFixedWidth(needed)

    def build_planes(self, parent: QWidget, layout: QVBoxLayout) -> None:
        view = self.view
        self.view_choice = Segments(parent, (f"Active pane ({view.axis})", ORTHO), "View",
                                    tips=(f"Export the {view.axis} pane", "Export the three panes of the Ortho view"))
        items: list[QWidget] = [self.view_choice.segments]
        if view.layout != "Ortho":
            self.view_choice.enable(1, False, ORTHO_REASON)
            items.append(hint(parent, PRESS_O))
        self.view_choice.currentIndexChanged.connect(self.view_changed)
        layout.addWidget(SettingRow(parent, "View", *items))
        self.sweep_choice = Segments(parent, tuple(SWEEP_LABELS.values()), "Sweep",
                                     tips=tuple(f"Sweep the {axis} pane through {NORMALS[axis]}" for axis in SWEEP_LABELS),
                                     dots=pane_dots(SWEEP_LABELS))
        self.sweep_choice.currentIndexChanged.connect(self.sweep_changed)
        self.sweep_row = SettingRow(parent, "Sweep", self.sweep_choice.segments)
        layout.addWidget(self.sweep_row)
        self.first, self.last = plane_field(parent, "From plane"), plane_field(parent, "To plane")
        first_current = LinkButton("Current", parent, tooltip="Use the plane that the viewer showed")
        last_current = LinkButton("Current", parent, tooltip="Use the plane that the viewer showed")
        first_current.clicked.connect(lambda: self.first.setValue(self.view.current(self.settings.sweep) + 1))
        last_current.clicked.connect(lambda: self.last.setValue(self.view.current(self.settings.sweep) + 1))
        self.first.valueChanged.connect(lambda value: self.change_timeline(first=value - 1))
        self.last.valueChanged.connect(lambda value: self.change_timeline(last=value - 1))
        swap = QToolButton(parent)
        swap.setObjectName("movieSwap")
        swap.setIcon(themed_icon("swap", color=current_palette().text, size=14))
        swap.setIconSize(QSize(14, 14))
        swap.setFixedSize(CONTROL_HEIGHT, CONTROL_HEIGHT)
        swap.setAccessibleName("Swap From and To")
        swap.setToolTip("Exchange From and To. The sweep then goes the other way.")
        swap.clicked.connect(self.swap)
        self.of_planes = hint(parent, wrap=False)
        from_label, to_label = hint(parent, "From", wrap=False), hint(parent, "to", wrap=False)
        from_label.setBuddy(self.first)
        to_label.setBuddy(self.last)
        layout.addWidget(SettingRow(parent, "Planes", from_label, self.first, first_current, to_label, self.last,
                                    last_current, swap, self.of_planes))
        self.step = plane_field(parent, "Step")
        self.step.setMinimum(1)
        self.step.valueChanged.connect(lambda value: self.change_timeline(step=value))
        self.step_hint = hint(parent)
        layout.addWidget(SettingRow(parent, "Step", self.step, self.step_hint))

    def build_timing(self, parent: QWidget, layout: QVBoxLayout) -> None:
        self.rate = RateField(parent)
        self.rate.valueChanged.connect(lambda value: self.change_timeline(fps=value))
        self.rate_note = hint(parent)
        layout.addWidget(SettingRow(parent, "Rate", self.rate, self.rate_note))
        self.end = Segments(parent, (SINGLE_PASS, BOUNCE), "End",
                            tips=("Sweep once, from From to To", "Sweep to To, then back to From"))
        self.end.currentIndexChanged.connect(lambda index: self.change_timeline(end=self.end.itemText(index)))
        self.loop_ready = Switch(parent, name="Loop-ready")
        self.loop_ready.setToolTip("Leave out the last frame, so a looped movie does not stop on its first plane")
        self.loop_ready.toggled.connect(lambda checked: self.change_timeline(loop_ready=checked))
        self.loop_label = QLabel("Loop-ready", parent)
        self.loop_label.setBuddy(self.loop_ready)
        layout.addWidget(SettingRow(parent, "End", self.end.segments, SPACE_XS, self.loop_ready, self.loop_label))
        self.start_hold = Seconds(parent, "Start hold", *HOLD_RANGE)
        self.turn_hold = Seconds(parent, "Turn hold", *HOLD_RANGE)
        self.end_hold = Seconds(parent, "End hold", *HOLD_RANGE)
        captions = []
        for caption, field, key in (("Start", self.start_hold, "start_hold"), ("Turn", self.turn_hold, "turn_hold"),
                                    ("End", self.end_hold, "end_hold")):
            label = hint(parent, caption, wrap=False)
            label.setBuddy(field)
            captions += [label, field]
            field.valueChanged.connect(lambda value, key=key: self.change_timeline(**{key: value}))
        self.turn_label = captions[2]
        hold_row = SettingRow(parent, "Hold", *captions, hint(parent, "seconds", wrap=False))
        self.chips = FlowLayout(spacing=SPACE_S)
        hold_row.lines.addLayout(self.chips)
        self.no_holds = hint(parent, NO_HOLDS)
        hold_row.lines.addWidget(self.no_holds)
        layout.addWidget(hold_row)

    def build_picture(self, parent: QWidget, layout: QVBoxLayout) -> None:
        self.framing = Segments(parent, FRAMINGS, "Framing",
                                tips=("Keep the zoom, pan, orientation and pane proportions that the viewer shows",
                                      "Show the full planes"))
        self.framing.currentIndexChanged.connect(lambda index: self.change(framing=FRAMINGS[index]))
        layout.addWidget(SettingRow(parent, "Framing", self.framing.segments))
        self.size_choice = Segments(parent, tuple(SIZES), "Size")
        self.size_choice.currentIndexChanged.connect(lambda index: self.change(size=self.size_choice.itemText(index)))
        self.size_note = hint(parent)
        layout.addWidget(SettingRow(parent, "Size", self.size_choice.segments, self.size_note))
        self.shape = QComboBox(parent)
        self.shape.addItems(tuple(SHAPES))
        self.shape.setAccessibleName("Shape")
        self.shape.setItemData(0, "The shape of the view", Qt.ItemDataRole.ToolTipRole)
        self.shape.currentIndexChanged.connect(lambda index: self.change(shape=self.shape.itemText(index)))
        field_height(self.shape)
        layout.addWidget(SettingRow(parent, "Shape", self.shape, hint(parent, SHAPE_HINT)))
        self.scale_bar = PictureChip("Scale bar", parent, "Draw a scale bar")
        self.names = PictureChip("Plane names", parent, "Show the name of each pane")
        self.numbers = PictureChip("Plane numbers", parent, "Show the plane number of the sweeping pane")
        self.lines = PictureChip("Intersection lines", parent, "Draw where the other planes cross each pane")
        self.title_on = PictureChip("Title", parent, "Show a title above the planes")
        for chip, key in ((self.scale_bar, "scale_bar"), (self.names, "names"), (self.numbers, "numbers"),
                          (self.lines, "lines"), (self.title_on, "title_on")):
            chip.toggled.connect(lambda checked, key=key: self.change(**{key: checked}))
        self.title_text = QLineEdit(parent)
        self.title_text.setAccessibleName("Title text")
        self.title_text.setPlaceholderText("Title text")
        self.title_text.setMaxLength(120)
        self.title_text.setFixedWidth(TITLE_WIDTH)
        self.title_text.textChanged.connect(lambda text: self.change(title=text))
        field_height(self.title_text)
        shown = FlowLayout(spacing=SPACE_S)
        for widget in (self.scale_bar, self.names, self.numbers, self.lines, self.title_on, self.title_text):
            shown.addWidget(widget)
        show_row = SettingRow(parent, "Show", shown, top=True)
        show_row.caption.setBuddy(self.scale_bar)
        if not self.view.calibrated:
            self.scale_bar.setEnabled(False)
            self.scale_bar.setToolTip(NO_PIXEL_SIZE)
            show_row.lines.addWidget(note(parent, NO_PIXEL_SIZE))
        layout.addWidget(show_row)

    def build_file(self, parent: QWidget, layout: QVBoxLayout) -> None:
        self.save = Segments(parent, SAVES, "Save", tips=("Save an MP4 movie", "Save an MP4 movie and its frames as PNG files",
                                                          "Save the frames as PNG files only"))
        self.save.currentIndexChanged.connect(lambda index: self.change(save=SAVES[index]))
        save_row = SettingRow(parent, "Save", self.save.segments)
        self.ffmpeg_note = note(parent)
        self.ffmpeg_note.setObjectName("movieNote")
        self.ffmpeg_note.setProperty("kind", "warning")
        save_row.lines.addWidget(self.ffmpeg_note)
        layout.addWidget(save_row)
        self.compression = QComboBox(parent)
        self.compression.addItems(tuple(COMPRESSION))
        self.compression.setAccessibleName("Compression")
        self.compression.currentIndexChanged.connect(lambda index: self.change(compression=self.compression.itemText(index)))
        field_height(self.compression)
        self.compression_hint = hint(parent)
        self.compression_row = SettingRow(parent, "Compression", self.compression, self.compression_hint)
        layout.addWidget(self.compression_row)
        # A path cannot wrap: it elides in the middle, and its tooltip has it in full.
        self.folder_text = ElidedLabel("", mode=Qt.TextElideMode.ElideMiddle, parent=parent)
        self.folder_text.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.folder_kind = hint(parent, "The snapshot folder", wrap=False)
        change = outlined("Change…", parent, "Select the folder for the movie", "movieOutlineButton")
        change.clicked.connect(self.choose_folder)
        folder_row = SettingRow(parent, "Folder", self.folder_text, self.folder_kind, change)
        self.folder_error = note(parent)
        self.folder_error.setObjectName("movieNote")
        self.folder_error.setProperty("kind", "error")
        self.folder_error.hide()
        folder_row.lines.addWidget(self.folder_error)
        layout.addWidget(folder_row)
        self.name = QLineEdit(parent)
        self.name.setAccessibleName("Name")
        self.name.setMaxLength(160)
        self.name.textChanged.connect(lambda text: self.change(name=text))
        field_height(self.name)
        self.extension = hint(parent, wrap=False)
        name_row = SettingRow(parent, "Name", self.name, self.extension)
        # With PNG frames only: what the folder holds and about how much it takes. No other line says this.
        self.path_text = ElidedLabel("", mode=Qt.TextElideMode.ElideMiddle, parent=parent)
        self.path_text.setObjectName("movieHint")
        self.path_text.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        name_row.lines.addWidget(self.path_text)
        self.estimate = hint(parent)
        name_row.lines.addWidget(self.estimate)
        layout.addWidget(name_row)

    def build_footer(self) -> QFrame:
        footer = QFrame(self)
        footer.setObjectName("movieFooter")
        row = QHBoxLayout(footer)
        row.setContentsMargins(SPACE_XL, SPACE_M, SPACE_XL, SPACE_M)
        row.setSpacing(SPACE_S)
        self.clip = QPushButton("Preview clip", footer)
        self.clip.setIcon(themed_icon("play", color=current_palette().text, size=12))
        self.clip.setIconSize(QSize(12, 12))
        self.clip.clicked.connect(lambda: self.owner.preview_clip(self.view, self.settings, self.index))
        row.addWidget(self.clip)
        row.addStretch()
        cancel = QPushButton("Cancel", footer)
        cancel.clicked.connect(self.reject)
        row.addWidget(cancel)
        self.export = QPushButton("Export movie", footer)
        self.export.setObjectName("primaryButton")
        self.export.clicked.connect(self.accept_export)
        row.addWidget(self.export)
        return footer

    def fit_to_screen(self) -> None:
        """Board 3's size where the screen has room for it; otherwise the screen's free area."""
        screen = self.owner.tab.window().screen().availableGeometry()
        minimum = self.minimumSizeHint()
        self.resize(max(minimum.width(), min(DIALOG_SIZE.width(), screen.width() - SPACE_XXL)),
                    max(minimum.height(), min(DIALOG_SIZE.height(), screen.height() - SPACE_XXL)))

    # Settings.
    def sync_controls(self) -> None:
        """Put the settings into the controls without feedback; refresh() does the rest."""
        settings, timing = self.settings, self.settings.timeline
        planes = self.view.planes(settings.sweep)
        blockers = [QSignalBlocker(widget) for widget in (
            self.view_choice, self.sweep_choice, self.first, self.last, self.step, self.rate, self.end, self.loop_ready,
            self.start_hold, self.turn_hold, self.end_hold, self.framing, self.size_choice, self.shape, self.scale_bar,
            self.names, self.numbers, self.lines, self.title_on, self.title_text, self.save, self.compression, self.name)]
        self.view_choice.setCurrentIndex(1 if settings.view == ORTHO else 0)
        self.sweep_row.setVisible(settings.view == ORTHO)
        self.sweep_choice.setCurrentIndex(tuple(SWEEP_LABELS).index(settings.sweep))
        for field in (self.first, self.last):
            field.setRange(1, planes)
        self.first.setValue(timing.first + 1)
        self.last.setValue(timing.last + 1)
        self.of_planes.setText(f"of {planes}")
        self.step.setMaximum(max(1, planes - 1))
        self.step.setValue(timing.step)
        self.rate.setValue(timing.fps)
        self.end.setCurrentIndex(1 if timing.bounce else 0)
        self.loop_ready.setChecked(timing.loop_ready)
        self.start_hold.setValue(timing.start_hold)
        self.turn_hold.setValue(timing.turn_hold)
        self.end_hold.setValue(timing.end_hold)
        self.framing.setCurrentIndex(FRAMINGS.index(settings.framing))
        self.size_choice.setCurrentIndex(tuple(SIZES).index(settings.size))
        self.shape.setCurrentIndex(tuple(SHAPES).index(settings.shape))
        for chip, checked in ((self.scale_bar, settings.scale_bar), (self.names, settings.names),
                              (self.numbers, settings.numbers), (self.lines, settings.lines),
                              (self.title_on, settings.title_on)):
            chip.setChecked(checked)
            chip.mark(checked)
        self.title_text.setText(settings.title)
        self.save.setCurrentIndex(SAVES.index(settings.save))
        self.compression.setCurrentIndex(tuple(COMPRESSION).index(settings.compression))
        self.name.setText(settings.name)
        del blockers
        for versions in (self.view_choice, self.sweep_choice, self.end, self.framing, self.size_choice, self.save):
            versions.sync()

    def change(self, **changes) -> None:
        self.settings = replace(self.settings, **changes)
        self.refresh()

    def change_timeline(self, **changes) -> None:
        self.settings = replace(self.settings, timeline=replace(self.settings.timeline, **changes))
        self.refresh()

    def view_changed(self, index: int) -> None:
        try:
            self.settings = change_view(self.settings, self.view, ORTHO if index == 1 else ACTIVE)
        except ValueError as exc:
            self.owner.tab.status.message.setText(str(exc))
        self.index = 0
        self.sync_controls()
        self.refresh()

    def sweep_changed(self, index: int) -> None:
        self.settings = change_sweep(self.settings, self.view, tuple(SWEEP_LABELS)[index])
        self.index = 0
        self.sync_controls()
        self.refresh()

    def swap(self) -> None:
        timing = self.settings.timeline
        self.settings = replace(self.settings, timeline=replace(timing, first=timing.last, last=timing.first))
        self.sync_controls()
        self.refresh()

    def choose_folder(self) -> None:
        """As for snapshots: a folder inside a loaded source is refused when it is chosen, with the reason."""
        folder = QFileDialog.getExistingDirectory(self, "Movie folder", self.settings.folder)
        if not folder:
            return
        try:
            require_output_directory(Path(folder), self.owner.tab.protected_roots())
        except (OSError, ValueError) as exc:
            self.folder_error.setText(str(exc))
            self.folder_error.show()
            return
        self.folder_error.hide()
        self.change(folder=folder)

    # Holds at chosen frames.
    def add_hold(self) -> None:
        timeline = self.plan.timeline if self.plan is not None else None
        if timeline is None or hold_here_reason(timeline, self.index):
            return
        frame = timeline.frames[timeline.base(self.index)]
        self.change_timeline(chosen=(*self.settings.timeline.chosen, ChosenHold(frame.plane, frame.direction)))

    def change_hold(self, hold: ChosenHold, seconds: float) -> None:
        chosen = tuple(replace(item, seconds=seconds) if same_place(item, hold) else item
                       for item in self.settings.timeline.chosen)
        self.change_timeline(chosen=chosen)

    def remove_hold(self, hold: ChosenHold) -> None:
        self.change_timeline(chosen=tuple(item for item in self.settings.timeline.chosen if not same_place(item, hold)))

    def go_to_hold(self, hold: ChosenHold) -> None:
        timeline = self.plan.timeline if self.plan is not None else None
        if timeline is not None and (index := timeline.frame_of(hold.plane, hold.direction)) is not None:
            self.move_to(index)

    def rebuild_chips(self) -> None:
        timing = self.settings.timeline
        unused = self.plan.timeline.unused if self.plan is not None and self.plan.timeline is not None else ()
        key = (self.settings.sweep, tuple((hold.plane, hold.direction) for hold in timing.chosen), unused)
        if key == self.chips_for:
            return
        self.chips_for = key
        while (item := self.chips.takeAt(0)) is not None:
            if (widget := item.widget()) is not None:
                widget.deleteLater()
        normal = NORMALS[self.settings.sweep]
        for hold in timing.chosen:
            self.chips.addWidget(HoldChip(self, hold, unused_reason(hold, timing, normal) if hold in unused else ""))
        self.no_holds.setVisible(not timing.chosen)

    # The timeline and the preview.
    def jump(self, where: int) -> None:
        timeline = self.plan.timeline if self.plan is not None else None
        if timeline is not None:
            self.move_to((0, (timeline.count - 1) // 2, timeline.count - 1)[where])

    def move_to(self, index: int) -> None:
        timeline = self.plan.timeline if self.plan is not None else None
        if timeline is None:
            return
        index = max(0, min(timeline.count - 1, index))
        changed = index != self.index
        self.index = index
        with QSignalBlocker(self.scrubber):
            self.scrubber.setValue(index)
        self.bar.set_timeline(timeline, index)
        self.readout.setText(readout(self.view, self.settings, timeline, index))
        reason = hold_here_reason(timeline, index)
        self.hold_here.setEnabled(not reason)
        self.hold_here.setToolTip(reason or "Hold this frame for 1 s. Change the time under Timing.")
        if changed:
            self.preview_timer.start()

    def request_frame(self) -> None:
        if self.plan is not None and self.plan.timeline is not None and self.plan.geometry is not None and not self.plan.errors:
            self.owner.request_frame(self.view, self.settings, self.index)

    def show_frame(self, _generation: int, index: int, frame: np.ndarray) -> None:
        if index == self.index:
            self.preview.set_frame(frame)

    def frame_failed(self, _generation: int, failure: MovieFailure) -> None:
        self.preview.set_message(f"No preview: {failure.message}")

    def encoder_checked(self) -> None:
        if self.owner.movie_available() is False and self.settings.save != FRAMES_ONLY:
            self.settings = replace(self.settings, save=FRAMES_ONLY)
            self.opened_with = replace(self.opened_with, save=FRAMES_ONLY)
            self.sync_controls()
        self.refresh()

    # Everything the dialog shows, from one set of settings.
    def refresh(self) -> None:
        settings, view = self.settings, self.view
        available = self.owner.movie_available()
        plan = self.plan = assess(view, settings, movie_available=available is not False)
        missing = available is False
        for index in (0, 1):
            self.save.enable(index, not missing, NO_FFMPEG)
        self.ffmpeg_note.setText(FFMPEG_NOTE if missing else "")
        self.ffmpeg_note.setVisible(missing)
        self.lines.setVisible(settings.view == ORTHO)  # Only an Ortho movie has other planes to cross.
        self.title_text.setVisible(settings.title_on)
        timing = settings.timeline
        for widget in (self.turn_label, self.turn_hold, self.loop_ready, self.loop_label):
            widget.setVisible(timing.bounce)  # Only a Bounce movie turns, and only it can be loop-ready.
        self.turn_hold.setToolTip("Hold the turning plane")
        self.end_hold.setEnabled(timing.end_available)
        self.end_hold.setToolTip("Hold the last frame" if timing.end_available
                                 else "A loop-ready movie has no last plane to hold. Use Start or Turn.")
        self.start_hold.setToolTip("Hold the first frame")
        self.step_hint.setText(step_hint(plan.timeline, timing.step))
        self.rate_note.setText(rate_hint(view, timing.fps))
        self.size_note.setText(plan.geometry.scale_note if plan.geometry is not None else "")
        frames_only = settings.save == FRAMES_ONLY
        self.compression_row.setVisible(not frames_only)
        self.compression_hint.setText(COMPRESSION_HINTS[settings.compression])
        snapshot = self.owner.tab.settings.tomogram_snapshot_folder or ""
        self.folder_text.setText(settings.folder or "No folder selected")
        self.folder_text.setToolTip(settings.folder)
        self.folder_kind.setVisible(bool(settings.folder) and settings.folder == snapshot)
        self.name.setPlaceholderText(automatic(view, settings))
        self.extension.setText(extension(settings))
        self.path_text.setText(plan.path_text)
        self.path_text.setVisible(bool(plan.path_text))
        self.estimate.setText(plan.estimate)
        self.estimate.setVisible(bool(plan.estimate))
        for section, line in zip(self.sections, plan.lines, strict=True):
            section.line.show_line(line)
        self.show_notes(plan)
        self.rebuild_chips()
        checking = available is None and not frames_only
        blocked = plan.blocked or (CHECKING if checking else "")
        self.export.setText("Save frames" if frames_only else "Export movie")
        self.export.setEnabled(not blocked)
        self.export.setToolTip(blocked)
        clip_reason = (NO_FFMPEG if missing else CHECKING if available is None else plan.blocked if plan.errors else "")
        self.clip.setVisible(not frames_only)  # The test clip is an MP4.
        self.clip.setEnabled(not clip_reason and plan.timeline is not None)
        self.clip.setToolTip(clip_reason or CLIP_TOOLTIP)
        self.fit_settings()
        timeline = plan.timeline
        self.scrubber.setEnabled(timeline is not None)
        self.hold_here.setEnabled(False)
        if timeline is None:
            self.bar.set_timeline(None, 0)
            self.readout.setText("")
            self.preview.set_message(plan.blocked or "No preview")
            return
        with QSignalBlocker(self.scrubber):
            self.scrubber.setRange(0, timeline.count - 1)
        self.index = min(self.index, timeline.count - 1)
        self.move_to(self.index)
        if plan.errors:
            self.preview.set_message(plan.errors[0])
        else:
            self.preview_timer.start()

    def show_notes(self, plan: MovieAssessment) -> None:
        while (item := self.notes.takeAt(0)) is not None:
            if (widget := item.widget()) is not None:
                widget.deleteLater()
        for kind, text in (*(((INFORMATION, self.start_note),) if self.start_note else ()), *plan.notes):
            if text == FFMPEG_NOTE:
                continue  # Said once, beside Save.
            label = QLabel(text, self)
            label.setObjectName("movieNote")
            label.setProperty("kind", kind)
            label.setWordWrap(True)
            label.setAccessibleDescription(kind)
            self.notes.addWidget(label)

    def accept_export(self) -> None:
        if self.export.isEnabled():
            self.accept()

    def reject(self) -> None:
        # Cancel keeps the settings, and Escape moves no focus: a value still being typed counts too.
        for field in self.findChildren(QAbstractSpinBox):
            field.interpretText()
        self.rate.commit()
        self.preview_timer.stop()
        super().reject()

    def accept(self) -> None:
        self.preview_timer.stop()
        super().accept()
