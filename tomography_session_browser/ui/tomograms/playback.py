"""Accepted-frame paced playback. Deliberate inspection always stops it."""
from tomography_session_browser.presentation.help_content import LocalBinding, InteractionKind
from time import monotonic
from shiboken6 import isValid
from PySide6.QtCore import QEvent, QObject, QSize, Qt, QTimer
from PySide6.QtGui import QActionGroup
from PySide6.QtWidgets import (QApplication, QComboBox, QHBoxLayout, QLabel, QMenu, QPushButton, QSpinBox, QWidget,
                               QWidgetAction)
from tomography_session_browser.presentation.design_tokens import CANVAS_CHROME, SPACE_M, SPACE_XS
from tomography_session_browser.ui.icons import themed_icon


class Playback(QObject):
    def __init__(self, tab) -> None:
        super().__init__(tab)
        self.tab, self.direction, self.advancing = tab, 1, False
        # A round icon button on the canvas band; its name says Play or Pause.
        self.button = QPushButton(tab)
        self.button.setObjectName("tomogramPlay")
        self.button.setIconSize(QSize(14, 14))
        self.button.setToolTip("Play or pause the planes (Space)")
        self.button.clicked.connect(self.toggle)
        self.show_state(False)
        self.speed = QSpinBox(tab)
        self.speed.setRange(5, 30)
        self.speed.setValue(10)
        self.speed.setSuffix("/s")
        self.speed.setAccessibleName("Playback speed, planes per second")
        self.speed.setToolTip("Planes per second, from 5 to 30")
        self.speed.setButtonSymbols(QSpinBox.ButtonSymbols.NoButtons)
        self.speed.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.speed.setFixedWidth(56)
        self.speed.setKeyboardTracking(False)
        self.end = QComboBox(tab)
        self.end.addItems(("Stop at end", "Loop", "Bounce"))
        self.end.setAccessibleName("Playback end behaviour")
        self.end.hide()
        # Rate and end behaviour share one menu, so the band holds one control.
        self.rate_button = QPushButton(tab)
        self.rate_button.setObjectName("tomogramRate")
        self.rate_button.setAccessibleName("Playback rate and end behaviour")
        menu = QMenu(tab)  # Follows the application theme, outside the canvas chrome.
        rate = QWidget(menu)
        row = QHBoxLayout(rate)
        row.setContentsMargins(SPACE_M, SPACE_XS, SPACE_M, SPACE_XS)
        row.addWidget(QLabel("Rate", rate))
        row.addStretch()
        row.addWidget(self.speed)
        action = QWidgetAction(menu)
        action.setDefaultWidget(rate)
        menu.addAction(action)
        menu.addSeparator()
        self.end_actions = QActionGroup(menu)
        for index in range(self.end.count()):
            choice = menu.addAction(self.end.itemText(index))
            choice.setCheckable(True)
            self.end_actions.addAction(choice)
            choice.triggered.connect(lambda _checked=False, index=index: self.end.setCurrentIndex(index))
        self.rate_button.setMenu(menu)
        self.end.currentIndexChanged.connect(self.sync_rate)
        self.speed.valueChanged.connect(self.sync_rate)
        self.sync_rate()
        self.timer = QTimer(self)
        self.timer.setTimerType(Qt.TimerType.PreciseTimer)
        self.timer.timeout.connect(self.tick)
        self.next_due = 0.
        self.catchup = QTimer(self)
        self.catchup.setSingleShot(True)
        self.catchup.timeout.connect(self.tick)
        self.speed.valueChanged.connect(self.speed_changed)

    def sync_rate(self, *_args) -> None:
        self.rate_button.setText(f"{self.speed.value()}/s")
        self.rate_button.setToolTip(f"{self.speed.value()} planes per second, {self.end.currentText().lower()}. "
                                    "Click to change the speed and what occurs at the last plane.")
        for index, choice in enumerate(self.end_actions.actions()):
            choice.setChecked(index == self.end.currentIndex())

    def speed_changed(self, value: int) -> None:
        self.timer.setInterval(round(1000 / value))
        self.next_due = monotonic() + 1 / value

    def toggle(self) -> None:
        if self.timer.isActive():
            self.stop()
        elif self.tab.slider.isEnabled() and self.tab.slider.maximum() > 0:
            self.tab.stage_transition.content.end("playback")
            # Each tick waits for the requested frame, so play may start while one loads.
            self.direction = 1
            self.next_due = monotonic() + 1 / self.speed.value()
            self.timer.start(round(1000 / self.speed.value()))
            app = QApplication.instance()
            assert app is not None
            app.installEventFilter(self)
            self.show_state(True)
            self.tab.update_commands()

    def show_state(self, playing: bool) -> None:
        if not isValid(self.button) or self.button.property("playing") == playing:
            return
        if hasattr(self.tab, "stage"):
            self.tab.stage.reset_scrubs()
        self.button.setIcon(themed_icon("pause" if playing else "play", color=CANVAS_CHROME.text, size=14))
        self.button.setAccessibleName("Pause" if playing else "Play")
        self.button.setProperty("playing", playing)
        self.button.style().unpolish(self.button)
        self.button.style().polish(self.button)

    def stop(self) -> None:
        if not self.timer.isActive() and not self.catchup.isActive():
            return
        app = QApplication.instance()
        if app is not None:
            app.removeEventFilter(self)
        self.timer.stop()
        self.catchup.stop()
        self.show_state(False)
        if self.tab.constructed:
            self.tab.update_commands()

    def dispose(self) -> None:
        self.stop()

    def frame_accepted(self) -> None:
        # A slow frame can finish just after a periodic tick. Continue on the
        # next event-loop turn, without waiting another whole timer interval.
        if self.timer.isActive() and monotonic() >= self.next_due:
            self.catchup.start(0)

    def tick(self) -> None:
        if not self.timer.isActive() or monotonic() < self.next_due:
            return
        tab, accepted = self.tab, self.tab.rendering.accepted
        if not tab.isVisible() or QApplication.activeModalWidget() is not None:
            self.stop()
            return
        if accepted is None or not accepted.complete or tab.render_timer.isActive() or accepted.job.generation != tab.rendering.generation:
            return
        value = tab.slider.value() + self.direction
        maximum = tab.slider.maximum()
        if value < 0 or value > maximum:
            mode = self.end.currentIndex()
            if mode == 0:
                self.stop()
                return
            if mode == 1:
                value = 0 if self.direction > 0 else maximum
            else:
                self.direction *= -1
                value = tab.slider.value() + self.direction
        self.advancing = True
        try:
            self.next_due = monotonic() + 1 / self.speed.value()
            tab.slider.setValue(value)
        finally:
            self.advancing = False

    def eventFilter(self, watched, event) -> bool:  # noqa: N802
        # Qt delivers child-removal events while destroying the owner. Its
        # timers can already be gone; input is the only event we need here.
        if event.type() not in (QEvent.Type.KeyPress, QEvent.Type.MouseButtonPress,
                               QEvent.Type.Wheel, QEvent.Type.FocusIn) or not isValid(self.timer):
            return False
        if event.type() == QEvent.Type.KeyPress and event.key() == Qt.Key.Key_Space and watched in self.tab.stage.panes.values():
            return False
        if (event.type() == QEvent.Type.Wheel and event.modifiers() & Qt.KeyboardModifier.ShiftModifier
                and isinstance(watched, QWidget) and any(
                    watched is pane or pane.isAncestorOf(watched) for pane in self.tab.stage.panes.values())):
            return False  # The pane's eligibility guard rejects scrubbing during playback.
        if self.timer.isActive():
            if isinstance(watched, QWidget) and watched.window() is self.tab.window() and watched is not self.button:
                self.stop()
        return False


LOCAL_KEYS = (
    LocalBinding('playback.inspect-key', 'Playback', InteractionKind.KEYBOARD, ('Any key except Space',), 'Deliberate inspection pauses playback; Space over a pane keeps its play/pause command'),
    LocalBinding('playback.inspect-pointer', 'Playback', InteractionKind.MOUSE, ('Click', 'Wheel'), 'Deliberate inspection pauses playback. Shift+wheel does not step planes while playback runs.'),
)
