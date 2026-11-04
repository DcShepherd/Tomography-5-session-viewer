"""Rotation, paired pivot state and the 150 ms idle refinement boundary."""
from dataclasses import replace
import numpy as np
from PySide6.QtCore import QSignalBlocker, QSize, QTimer
from PySide6.QtWidgets import QPushButton, QMenu, QWidgetAction, QDoubleSpinBox, QComboBox, QWidget, QFormLayout
from tomography_session_browser.presentation.design_tokens import CANVAS_CHROME
from tomography_session_browser.ui.icons import themed_icon
from tomography_session_browser.services.volume.oblique import AXES, mapping, view_rotation
from tomography_session_browser.services.volume.render import calibrated_spacing
from tomography_session_browser.services.volume.models import RenderResult


class ObliqueControls:
    """Owns the padlock and angle controls; the toolbar groups them."""

    def __init__(self, tab, parent):
        self.tab = tab
        self.locked_text = ""
        self.shown_locked: bool | None = None
        self.interactive = False
        self.increment = 1.
        self.timer = QTimer(tab)
        self.timer.setSingleShot(True)
        self.timer.setInterval(150)
        self.timer.timeout.connect(self.refine)
        # The padlock shows the pivot's state; the toolbar is canvas chrome.
        self.lock = QPushButton(parent)
        self.lock.setObjectName("tomogramLockButton")
        self.lock.setIconSize(QSize(16, 16))
        self.lock.setAccessibleName("Lock the pivot")
        self.lock.setIcon(themed_icon("unlock", color=CANVAS_CHROME.text, size=16))
        self.lock.setCheckable(True)
        self.lock.setToolTip("Lock the point that the view turns around (Ctrl+L). When it is not locked, a click moves this point.")
        self.lock.toggled.connect(self.set_lock)
        # An icon while the view is axis-aligned; the angles themselves once rotated.
        self.angles = QPushButton(parent)
        self.angles.setIcon(themed_icon("rotate-3d", color=CANVAS_CHROME.text, size=16))
        self.angles.setIconSize(QSize(16, 16))
        self.angles.setAccessibleName("Angles")
        self.angles.setToolTip("The tilt of the view, in degrees. The app turns the view about Z, then Y, then X. "
                               "Areas that are not in the volume show no data.")
        # A popup follows the application theme, outside the dark canvas chrome.
        menu = QMenu(tab)
        panel = QWidget(menu)
        form = QFormLayout(panel)
        form.setContentsMargins(12, 12, 12, 12)
        form.setSpacing(8)
        self.axis_choice = QComboBox(menu)
        self.axis_choice.addItems(("Rotate X", "Rotate Y", "Rotate Z"))
        self.axis_choice.setCurrentIndex(tab.state.rotation_axis)
        self.axis_choice.setAccessibleName("Active keypad rotation axis")
        self.axis_choice.currentIndexChanged.connect(self.select_axis)
        form.addRow("Keypad axis", self.axis_choice)
        self.increment_choice = QComboBox(panel)
        for value in (.1, .3, 1., 3., 10., 30., 90.):
            self.increment_choice.addItem(f"{value:g}°", value)
        self.increment_choice.setCurrentIndex(2)
        self.increment_choice.setAccessibleName("Rotation increment")
        self.increment_choice.currentIndexChanged.connect(lambda _index: self.set_increment(self.increment_choice.currentData()))
        form.addRow("Increment", self.increment_choice)
        self.spins = []
        for index, axis in enumerate("XYZ"):
            spin = QDoubleSpinBox(menu)
            spin.setRange(-180, 180)
            spin.setDecimals(1)
            spin.setSingleStep(self.increment)
            spin.setSuffix("°")
            spin.setKeyboardTracking(False)
            spin.setAccessibleName(axis + " rotation in degrees")
            spin.valueChanged.connect(lambda value, i=index: self.set_angle(i, value, preview=False))
            form.addRow(axis + " rotation", spin)
            self.spins.append(spin)
        action = QWidgetAction(menu)
        action.setDefaultWidget(panel)
        menu.addAction(action)
        menu.addAction("Zero rotation", self.reset)
        menu.addAction("Show orientation box", lambda: self.show_box(True))
        self.angles.setMenu(menu)
        for axis, pane in tab.stage.panes.items():
            pane.orientation.rotated.connect(lambda x, y, a=axis: self.drag(a, x, y, preview=False))
            pane.orientation.reset_requested.connect(self.reset)
            pane.orientation.dismissed.connect(lambda: self.show_box(False))
            pane.rotation_requested.connect(lambda x, y, roll, a=axis: self.drag(a, x, y, roll, preview=False))

    def set_lock(self, enabled):
        self.tab.state = replace(self.tab.state, pivot_locked=enabled)
        self.tab.persistence.schedule()
        self.sync()

    def select_axis(self, axis):
        self.tab.state = replace(self.tab.state, rotation_axis=axis)
        self.tab.persistence.schedule()

    def step_active(self, delta):
        self.step(self.tab.state.rotation_axis, delta)

    def zero_active(self):
        if all(value == 0 for index, value in enumerate(self.tab.state.rotation) if index != self.tab.state.rotation_axis):
            self.reset()
        else:
            self.set_angle(self.tab.state.rotation_axis, 0., preview=False)

    def show_box(self, visible=None):
        self.tab.state = replace(self.tab.state, orientation_visible=(not self.tab.state.orientation_visible if visible is None else visible))
        self.tab.persistence.schedule()
        self.sync()

    def sync(self):
        state = self.tab.state
        with QSignalBlocker(self.axis_choice):
            self.axis_choice.setCurrentIndex(state.rotation_axis)
        with QSignalBlocker(self.lock):
            self.lock.setChecked(state.pivot_locked)
        # Restyling an unchanged padlock would relayout and repaint on every rotation input.
        if state.pivot_locked != self.shown_locked:
            self.shown_locked = state.pivot_locked
            self.lock.setIcon(themed_icon("lock" if state.pivot_locked else "unlock", color=CANVAS_CHROME.text, size=16))
            self.lock.setAccessibleName("Unlock the pivot" if state.pivot_locked else "Lock the pivot")
            # Both states occupy the same icon cell; the icon and accessible name change.
            self.lock.setText("")
        text = "" if not any(state.rotation) else "  ".join(f"{axis} {v:.1f}°" for axis, v in zip("XYZ", state.rotation))
        if text != self.angles.text() or self.lock.text() != self.locked_text:
            self.set_angles_text(text)
            self.locked_text = self.lock.text()
            if hasattr(self.tab, "canvas_toolbar"):
                self.tab.canvas_toolbar.compact(self.tab)
        for spin, value in zip(self.spins, state.rotation):
            with QSignalBlocker(spin):
                spin.setValue(value)
        self.sync_display()

    def set_angles_text(self, text):
        """The toolbar reserves the widest angle label before any frame is accepted."""
        self.angles.setText(text)

    def sync_display(self):
        """Update orientation boxes from the displayed frames, without restyling controls."""
        state = self.tab.state
        for axis, pane in self.tab.stage.panes.items():
            frame = pane.frame_owner
            rotation = frame.recipe.rotation if isinstance(frame, RenderResult) else (0., 0., 0.)
            pane.orientation.rotation = rotation
            pane.orientation.axis = axis
            # The box draws the displayed frame's slice, through the reslice's own mapping.
            pane.orientation.recipe = frame.recipe if isinstance(frame, RenderResult) else None
            pane.orientation.volume = self.tab.volume()
            pane.orientation.locked = state.pivot_locked
            pane.orientation_enabled = state.orientation_visible and any(rotation)
            pane.sync_orientation()

    def set_angle(self, index, value, *, preview=True):
        if self.tab.volume() is None:
            return
        self.tab.playback.stop()
        angles = list(self.tab.state.rotation)
        angles[index] = (value+180) % 360-180
        self.apply_angles(tuple(angles), preview=preview)

    def apply_angles(self, angles, *, preview=True):
        if self.tab.volume() is None:
            return
        self.tab.stage.reset_scrubs()
        manual = self.tab.state.levels_mode == "Manual"
        self.tab.state = replace(self.tab.state, rotation=tuple(angles), capture_region=None,
                                black=self.tab.state.black if manual else None,
                                white=self.tab.state.white if manual else None, levels_anchor=None)
        quality_changed = self.interactive != preview
        self.interactive = preview
        self.timer.stop()
        if preview:
            self.timer.start()
        self.sync()
        self.tab.request_render(immediate=not preview, view_only=not quality_changed)

    def step(self, index, delta):
        self.set_angle(index, self.tab.state.rotation[index]+delta, preview=False)

    def drag(self, axis, x, y, roll=False, *, preview=True):
        h, v, n = AXES[axis]
        self.tab.playback.stop()
        angles = self.tab.state.rotation
        if roll:
            angles = view_rotation(angles, n, x)
        else:
            angles = view_rotation(view_rotation(angles, v, x), h, y)
        self.apply_angles(angles, preview=preview)

    def change_increment(self, direction):
        steps = (.1, .3, 1., 3., 10., 30., 90.)
        self.set_increment(steps[max(0, min(len(steps)-1, steps.index(self.increment)+direction))])

    def set_increment(self, value):
        self.increment = value
        with QSignalBlocker(self.increment_choice):
            self.increment_choice.setCurrentIndex(self.increment_choice.findData(value))
        for spin in self.spins:
            spin.setSingleStep(self.increment)
        self.tab.status.message.setText(f"Rotation increment {self.increment:g}°")

    def reset(self):
        self.tab.stage.reset_scrubs()
        self.timer.stop()
        self.interactive = False
        manual = self.tab.state.levels_mode == "Manual"
        self.tab.state = replace(self.tab.state, rotation=(0., 0., 0.), pivot_xyz=None,
                                pivot_grid_xyz=None, black=self.tab.state.black if manual else None,
                                white=self.tab.state.white if manual else None, levels_anchor=None)
        self.sync()
        self.tab.request_render()

    def refine(self):
        self.interactive = False
        # Rotation changes already invalidate Auto levels in apply_angles.
        # Plane scrubbing must retain its established levels through refinement.
        self.tab.request_render(immediate=True)

    def place_pivot(self, reference):
        tab, volume = self.tab, self.tab.volume()
        if tab.state.pivot_locked or volume is None or volume.grid is None:
            return
        state = tab.state
        matrix, offset = mapping(volume, state.rotation, state.pivot_xyz, state.pivot_grid_xyz,
                                 calibrated_spacing(volume, state.spacing_override))
        source = matrix @ np.asarray(reference) + offset
        tab.state = replace(state, pivot_xyz=tuple(float(v) for v in source),
                            pivot_grid_xyz=tuple(float(v) for v in reference))
        self.sync()
