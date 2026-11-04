"""One shortcut definition, with an explicit editor/dialog eligibility check."""
from __future__ import annotations

from dataclasses import dataclass
from collections.abc import Callable
from typing import TYPE_CHECKING

from PySide6.QtCore import Qt
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import QApplication, QAbstractButton, QAbstractItemView, QAbstractSpinBox, QComboBox, QLineEdit, QTextEdit, QPlainTextEdit

if TYPE_CHECKING:
    from tomography_session_browser.ui.tomograms.tab import TomogramTab


@dataclass(frozen=True, slots=True)
class Command:
    identity: str
    label: str
    shortcut: str
    invoke: Callable[[], None]
    aliases: tuple[str, ...] = ()


VERSION_COMMANDS = (("Previous version", "1", -1), ("Next version", "2", 1))


def version_tooltip() -> str:
    return "; ".join(f"{label} ({key})" for label, key, _ in VERSION_COMMANDS) + ". Main-row keys; NumPad rotates."


def step_version(tab: TomogramTab, delta: int) -> None:
    index = tab.versions.currentIndex() + delta
    if 0 <= index < tab.versions.count():
        tab.versions.setCurrentIndex(index)


def input_available(tab: TomogramTab) -> bool:
    """Editors and transient windows retain their own input."""
    if QApplication.activeModalWidget() is not None or QApplication.activePopupWidget() is not None:
        return False
    widget = QApplication.focusWidget()
    while widget is not None and widget is not tab:
        if isinstance(widget, (QLineEdit, QAbstractSpinBox, QTextEdit, QPlainTextEdit)):
            return False
        widget = widget.parentWidget()
    return True


def install_commands(tab: TomogramTab) -> tuple[QAction, ...]:
    definitions = (
        Command("tomograms.add-a-tag", "Add a tag", "#", tab.tags.begin),
        Command("tomograms.snapshot", "Snapshot", "Ctrl+S", tab.captures.snapshot),
        Command("tomograms.snapshot-folder", "Snapshot folder", "Ctrl+Shift+S", tab.captures.choose_folder),
        Command("tomograms.export-movie", "Export movie…", "Ctrl+E", tab.movie.open),
        Command("tomograms.captures-of-this-tomogram", "Captures of this tomogram", "C", tab.captures.show_menu),
        Command("tomograms.next-tomogram", "Next tomogram", "]", lambda: tab.step_tomogram(1)),
        Command("tomograms.previous-tomogram", "Previous tomogram", "[", lambda: tab.step_tomogram(-1)),
        *(Command(f"tomograms.version.{delta}", label, key, lambda delta=delta: step_version(tab, delta)) for label, key, delta in VERSION_COMMANDS),
        Command("tomograms.next-plane", "Next plane", "PgUp", lambda: tab.step_plane(1)),
        Command("tomograms.previous-plane", "Previous plane", "PgDown", lambda: tab.step_plane(-1)),
        Command("tomograms.fit", "Fit", "F", tab.fit),
        Command("tomograms.image-settings", "Image settings", "I", tab.image_button.toggle),
        Command("tomograms.single-or-ortho", "Single or Ortho", "O", lambda: tab.change_layout("Single" if tab.state.layout == "Ortho" else "Ortho")),
        Command("tomograms.focus", "Focus", "Ctrl+M", tab.focus_button.toggle),
        Command("tomograms.navigator", "Navigator", "N", tab.toggle_navigator),
        Command("tomograms.pivot-lock", "Pivot lock", "Ctrl+L", tab.oblique.lock.toggle),
        Command("tomograms.orientation-box", "Orientation box", "B", tab.oblique.show_box),
        Command("tomograms.zero-active-rotation-axis", "Zero active rotation axis", "Num+0", tab.oblique.zero_active),
        Command("tomograms.smaller-rotation-increment", "Smaller rotation increment", "<", lambda: tab.oblique.change_increment(-1)),
        Command("tomograms.larger-rotation-increment", "Larger rotation increment", ">", lambda: tab.oblique.change_increment(1)),
        Command("tomograms.view-rotation-left", "View rotation left", "Shift+Num+4", lambda: tab.oblique.drag(tab.state.axis, -tab.oblique.increment, 0, preview=False)),
        Command("tomograms.view-rotation-right", "View rotation right", "Shift+Num+6", lambda: tab.oblique.drag(tab.state.axis, tab.oblique.increment, 0, preview=False)),
        Command("tomograms.view-rotation-up", "View rotation up", "Shift+Num+8", lambda: tab.oblique.drag(tab.state.axis, 0, tab.oblique.increment, preview=False)),
        Command("tomograms.view-rotation-down", "View rotation down", "Shift+Num+2", lambda: tab.oblique.drag(tab.state.axis, 0, -tab.oblique.increment, preview=False)),
        Command("tomograms.view-rotation-clockwise", "View rotation clockwise", "Shift+Num+9", lambda: tab.oblique.drag(tab.state.axis, tab.oblique.increment, 0, True, preview=False)),
        Command("tomograms.view-rotation-anticlockwise", "View rotation anticlockwise", "Shift+Num+3", lambda: tab.oblique.drag(tab.state.axis, -tab.oblique.increment, 0, True, preview=False)),
        Command("tomograms.rotate-active-axis-minus-0-1", "Rotate active axis -0.1°", "Num+4", lambda: tab.oblique.step_active(-.1)),
        Command("tomograms.rotate-active-axis-plus-0-1", "Rotate active axis +0.1°", "Num+6", lambda: tab.oblique.step_active(.1)),
        Command("tomograms.rotate-active-axis-minus-0-5", "Rotate active axis -0.5°", "Num+2", lambda: tab.oblique.step_active(-.5), ("Num+Down",)),
        Command("tomograms.rotate-active-axis-plus-0-5", "Rotate active axis +0.5°", "Num+8", lambda: tab.oblique.step_active(.5)),
        Command("tomograms.rotate-active-axis-minus-15", "Rotate active axis -15°", "Num+1", lambda: tab.oblique.step_active(-15), ("Num+End",)),
        Command("tomograms.rotate-active-axis-plus-15", "Rotate active axis +15°", "Num+3", lambda: tab.oblique.step_active(15)),
        Command("tomograms.rotate-view-left", "Rotate view left", "Shift+Left", lambda: tab.oblique.drag(tab.state.axis, -tab.oblique.increment, 0, preview=False)),
        Command("tomograms.rotate-view-right", "Rotate view right", "Shift+Right", lambda: tab.oblique.drag(tab.state.axis, tab.oblique.increment, 0, preview=False)),
        Command("tomograms.rotate-view-up", "Rotate view up", "Shift+Up", lambda: tab.oblique.drag(tab.state.axis, 0, tab.oblique.increment, preview=False)),
        Command("tomograms.rotate-view-down", "Rotate view down", "Shift+Down", lambda: tab.oblique.drag(tab.state.axis, 0, -tab.oblique.increment, preview=False)),
        Command("tomograms.rotate-in-plane-clockwise", "Rotate in plane clockwise", "Shift+PgUp", lambda: tab.oblique.drag(tab.state.axis, tab.oblique.increment, 0, True, preview=False)),
        Command("tomograms.rotate-in-plane-anticlockwise", "Rotate in plane anticlockwise", "Shift+PgDown", lambda: tab.oblique.drag(tab.state.axis, -tab.oblique.increment, 0, True, preview=False)),
        Command("tomograms.ortho-lines", "Ortho lines", "L", tab.lines_button.toggle),
        Command("tomograms.filters", "Filters", "G", tab.recipes.toggle_filters),
        Command("tomograms.undo-image-change", "Undo image change", "Ctrl+Z", tab.recipes.undo),
        Command("tomograms.autocontrast", "Autocontrast", "Shift+A", tab.image_settings.auto_levels),
        Command("tomograms.invert", "Invert", "F11", tab.image_settings.invert.toggle),
        Command("tomograms.play-or-pause", "Play or pause", "Space", tab.playback.toggle),
        Command("tomograms.first-plane", "First plane", "End", lambda: tab.slider.setValue(0)),
        Command("tomograms.last-plane", "Last plane", "Home", lambda: tab.slider.setValue(tab.slider.maximum())),
        Command("tomograms.middle-plane", "Middle plane", "Insert", lambda: tab.slider.setValue(tab.slider.maximum() // 2)),
        Command("tomograms.help", "Help", "?", tab.show_help),
        Command("tomograms.zoom-in", "Zoom in", "=", lambda: tab.zoom(1.2)),
        Command("tomograms.zoom-out", "Zoom out", "-", lambda: tab.zoom(1 / 1.2)),
    )
    actions = []
    for definition in definitions:
        action = QAction(definition.label, tab)
        action.setProperty("helpBindingId", definition.identity)
        action.setShortcuts([QKeySequence(key) for key in (definition.shortcut, *definition.aliases)])
        action.setShortcutContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        # Navigation follows the operating system's held-key cadence. Keep
        # toggles, captures and dialogs single-shot even when their key is held.
        action.setAutoRepeat(definition.shortcut in ("PgUp", "PgDown"))

        def invoke(_checked=False, command=definition) -> None:
            if not input_available(tab):
                return
            widget = QApplication.focusWidget()
            while widget is not None and widget is not tab:
                if command.shortcut == "Space" and isinstance(widget, (QAbstractButton, QAbstractItemView, QComboBox)):
                    return
                if command.shortcut in ("1", "2", "Home", "End", "Insert", "PgUp", "PgDown") and isinstance(widget, (QAbstractItemView, QComboBox)):
                    return
                widget = widget.parentWidget()
            command.invoke()

        action.triggered.connect(invoke)
        tab.addAction(action)
        tab.image_settings.addAction(action)
        actions.append(action)
    return tuple(actions)
