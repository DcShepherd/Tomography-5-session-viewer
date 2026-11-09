"""X, Y and Z rotation sliders for a wide canvas toolbar, after IMOD's slicer.

``ObliqueControls`` owns the strip and is the only writer of its positions and
values, so the sliders show the current rotation whatever changed it. The
toolbar's fitter shows either this strip or the Angles cell. A drag scrubs one
rotation increment for each pixel and previews; keys and the wheel move whole
increments and render final frames (owner, 9 Nov 2026).
"""
from __future__ import annotations

from collections.abc import Iterable, Sequence

from PySide6.QtCore import QPoint, QSignalBlocker, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QAction, QActionGroup
from PySide6.QtWidgets import (QHBoxLayout, QLabel, QMenu, QPushButton, QSlider, QStyle, QStyleOptionSlider,
                               QStylePainter, QWidget)

from tomography_session_browser.presentation.design_tokens import CANVAS_CHROME
from tomography_session_browser.presentation.help_content import InteractionKind, LocalBinding
from tomography_session_browser.ui.icons import themed_icon
from tomography_session_browser.ui.widgets.wheel_notches import WheelNotches

#: The slider length the owner chose (Q3, 7 Nov 2026). A drag scrubs by the
#: increment, so the length no longer sets the precision.
SLIDER_LENGTH = 64
#: The keyboard mark clears this long after the last key.
MARK_MS = 800
#: Tenths of a degree from -180.0° to 179.9°: set_angle's [-180, 180), so a drag never fights a wrapped value.
LOWEST, HIGHEST = -1800, 1799
#: The rotation increments, in degrees: the pop-up, the < and > keys and a slider's right-click menu.
ROTATION_INCREMENTS = (.1, .3, 1., 3., 10., 30., 90.)


def shown_tenths(rotation: Iterable[float]) -> tuple[int, ...]:
    """Each angle as shown, in tenths of a degree in [-1800, 1800): 179.96° shows as -180.0°."""
    return tuple(wrapped_tenths(round(value * 10)) for value in rotation)


def wrapped_tenths(tenths: int) -> int:
    """Tenths of a degree in [-1800, 1800), as the angle wraps."""
    return (tenths - LOWEST) % 3600 + LOWEST


def degrees(tenths: int) -> str:
    return f"{tenths / 10:.1f}°"


class RotationSlider(QSlider):
    """One axis in tenths of a degree, moved in whole rotation increments.

    A drag scrubs one increment for each pixel from where the press started and
    wraps past ±180°; the handle shows the angle, so it can move less or more
    than the pointer. A press without a drag only selects the slider. Plain
    arrow keys and the wheel (only when selected) step one increment.
    """

    #: An arrow key changed the value.
    keyed = Signal()
    #: The slider got (True) or lost (False) the focus.
    selected = Signal(bool)
    #: A right-click asked for the increment list, at this global position.
    increment_menu_requested = Signal(QPoint)

    def __init__(self, parent: QWidget) -> None:
        super().__init__(Qt.Orientation.Horizontal, parent)
        self.setRange(LOWEST, HIGHEST)
        self.notches = WheelNotches()
        #: The pointer's x and the value when the left button went down.
        self.press: tuple[float, int] | None = None

    def keyPressEvent(self, event) -> None:  # noqa: N802
        # The tab's commands keep PgUp, PgDown, Home, End, Insert and Shift+arrows.
        before = self.value()
        super().keyPressEvent(event)
        if self.value() != before:
            self.keyed.emit()

    def wheelEvent(self, event) -> None:  # noqa: N802
        if not self.hasFocus():  # Q5: scrolling over the toolbar never turns the volume.
            event.ignore()
            return
        event.accept()
        steps = self.notches.steps(event)
        if steps:
            self.setValue(self.value() + steps * self.singleStep())

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() != Qt.MouseButton.LeftButton:
            super().mousePressEvent(event)
            return
        self.setFocus(Qt.FocusReason.MouseFocusReason)
        self.press = (event.position().x(), self.value())
        event.accept()

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        if self.press is None:
            super().mouseMoveEvent(event)
            return
        start, value = self.press
        pixels = round(event.position().x() - start)
        if pixels and not self.isSliderDown():
            self.setSliderDown(True)  # The drag previews until the release.
        if self.isSliderDown():
            self.setValue(wrapped_tenths(value + pixels * self.singleStep()))
        event.accept()

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if self.press is None or event.button() != Qt.MouseButton.LeftButton:
            super().mouseReleaseEvent(event)
            return
        self.press = None
        if self.isSliderDown():
            self.setSliderDown(False)  # sliderReleased renders the final frame.
        event.accept()

    def paintEvent(self, _event) -> None:  # noqa: N802
        """As QSlider paints, without the style's focus frame: the selected look is the lit handle and letter."""
        painter = QStylePainter(self)
        option = QStyleOptionSlider()
        self.initStyleOption(option)
        option.state &= ~QStyle.StateFlag.State_HasFocus
        option.subControls = QStyle.SubControl.SC_SliderGroove | QStyle.SubControl.SC_SliderHandle
        painter.drawComplexControl(QStyle.ComplexControl.CC_Slider, option)

    def contextMenuEvent(self, event) -> None:  # noqa: N802
        self.increment_menu_requested.emit(event.globalPos())
        event.accept()

    def focusInEvent(self, event) -> None:  # noqa: N802
        super().focusInEvent(event)
        self.selected.emit(True)

    def focusOutEvent(self, event) -> None:  # noqa: N802
        super().focusOutEvent(event)
        self.notches.reset()
        self.selected.emit(False)


class RotationSliders(QWidget):
    """A menu button for the Angles pop-up, then X, Y and Z: a name, a slider and its value."""

    #: The axis, the angle in degrees, and whether the frame is a drag preview.
    changed = Signal(int, float, bool)
    #: A rotation increment, in degrees, chosen from a slider's right-click menu.
    increment_chosen = Signal(float)

    def __init__(self, parent: QWidget, menu: QMenu) -> None:
        super().__init__(parent)
        self.setObjectName("tomogramRotationSliders")
        self.marked: frozenset[int] = frozenset()
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.setInterval(MARK_MS)
        self.timer.timeout.connect(lambda: self.set_marked(frozenset()))
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 6, 0)
        row.setSpacing(4)
        self.menu_button = QPushButton(self)
        self.menu_button.setObjectName("tomogramIconButton")
        self.menu_button.setIcon(themed_icon("rotate-3d", color=CANVAS_CHROME.text, size=16))
        self.menu_button.setIconSize(QSize(16, 16))
        self.menu_button.setAccessibleName("Angle settings")
        self.menu_button.setToolTip("Set the keypad axis, the rotation increment and the angles.")
        self.menu_button.setProperty("menuIndicator", "none")  # The icon is the cue; an arrow clips it in 24 px.
        self.menu_button.setMenu(menu)
        row.addWidget(self.menu_button)
        self.names: list[QLabel] = []
        self.sliders: list[RotationSlider] = []
        self.values: list[QLabel] = []
        for index, axis in enumerate("XYZ"):
            row.addSpacing(6 if index else 2)
            name = QLabel(axis, self)
            slider = RotationSlider(self)
            slider.setAccessibleName(f"{axis} rotation in degrees")
            slider.setToolTip(f"Turn the view about the {axis} axis. Drag: one increment for each pixel. "
                              "Wheel: one increment for each step. Right-click to set the increment, or use < and >.")
            value = QLabel(self)
            value.setObjectName("tomogramRotationValue")
            slider.valueChanged.connect(lambda tenths, i=index, s=slider: self.changed.emit(i, tenths / 10, s.isSliderDown()))
            slider.sliderReleased.connect(lambda i=index, s=slider: self.changed.emit(i, s.value() / 10, False))
            slider.keyed.connect(lambda i=index: self.mark((i,)))
            slider.selected.connect(lambda on, widgets=(name, slider): [restyle(widget, "active", on) for widget in widgets])
            slider.increment_menu_requested.connect(self.show_increment_menu)
            for widget in (name, slider, value):
                row.addWidget(widget)
            self.names.append(name)
            self.sliders.append(slider)
            self.values.append(value)

    def reserve(self, cell: int, value_width: int) -> None:
        """Fix every size, so a new value or the mark never makes a layout request."""
        self.menu_button.setFixedSize(cell, cell)
        for name, slider, value in zip(self.names, self.sliders, self.values):
            name.setFixedSize(name.sizeHint().width(), cell)
            slider.setFixedSize(SLIDER_LENGTH, cell)
            value.setFixedSize(value_width, cell)
        layout = self.layout()
        assert layout is not None
        layout.activate()
        self.setFixedSize(layout.sizeHint().width(), cell)

    def display(self, tenths: Sequence[int], *, enabled: bool) -> None:
        """Show the rotation; only ObliqueControls.sync() calls this."""
        for slider, value, angle in zip(self.sliders, self.values, tenths):
            with QSignalBlocker(slider):
                slider.setValue(angle)
            slider.setEnabled(enabled)
            value.setText(degrees(angle))

    def set_step(self, tenths: int) -> None:
        """One rotation increment per arrow key; the page step matches, so no input jumps further."""
        for slider in self.sliders:
            slider.setSingleStep(tenths)
            slider.setPageStep(tenths)

    def mark(self, axes: Iterable[int]) -> None:
        """Mark the axes that the keyboard turned, until MARK_MS after the last key."""
        axes = frozenset(axes)
        if not axes or not self.isVisible():
            return
        self.timer.start()
        self.set_marked(axes)

    def set_marked(self, axes: frozenset[int]) -> None:
        """Restyle only the axes whose mark changes; every size is fixed, so nothing relayouts."""
        for index in axes ^ self.marked:
            for widget in (self.names[index], self.sliders[index], self.values[index]):
                restyle(widget, "marked", index in axes)
        self.marked = axes

    def show_increment_menu(self, where: QPoint) -> None:
        """The rotation increments, the current one ticked; the menu follows the application theme."""
        menu = QMenu(self.window())
        menu.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        menu.addAction("Rotation increment").setEnabled(False)  # A heading; the Windows style draws no section text.
        group = QActionGroup(menu)
        step = self.sliders[0].singleStep()
        for value in ROTATION_INCREMENTS:
            action = QAction(f"{value:g}°", menu, checkable=True, checked=round(value * 10) == step)
            action.triggered.connect(lambda _checked=False, chosen=value: self.increment_chosen.emit(chosen))
            group.addAction(action)
            menu.addAction(action)
        menu.addSeparator()
        menu.addAction("The < and > keys also change it").setEnabled(False)
        menu.popup(where)


def restyle(widget: QWidget, name: str, on: bool) -> None:
    """Set a style property and repolish; every strip size is fixed, so nothing relayouts."""
    widget.setProperty(name, on)
    widget.style().unpolish(widget)
    widget.style().polish(widget)


# Contextual keys handled by this module; consumed by the shared Help sheet.
LOCAL_KEYS = (
    LocalBinding('tomograms.rotation_sliders.rotation-slider-left', 'Rotation slider', InteractionKind.KEYBOARD,
                 ('Left', 'Right', 'Up', 'Down'), 'Turn the view about this axis by the rotation increment'),
)
