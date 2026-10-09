"""Ticker components for the motion."""
from __future__ import annotations
import time
from PySide6.QtCore import QObject, Qt, QTimer
from PySide6.QtWidgets import QApplication
import shiboken6
from tomography_session_browser.ui.motion.contracts import Animation
from tomography_session_browser.ui.motion.tokens import FRAME_INTERVAL_MS, MAX_STEP_S


class Ticker(QObject):
    """One frame timer for every active animation; idle when nothing moves."""

    def __init__(self) -> None:
        super().__init__()
        self._active: list[Animation] = []
        self._timer: QTimer | None = None
        self._last_tick = 0.0

    @property
    def is_running(self) -> bool:
        return self._timer is not None and self._timer.isActive()

    @property
    def active_count(self) -> int:
        return len(self._active)

    def add(self, spring: Animation) -> None:
        if spring in self._active:
            return
        self._active.append(spring)
        if not self.is_running:
            self._start()

    def remove(self, spring: Animation) -> None:
        if spring in self._active:
            self._active.remove(spring)
        if not self._active:
            self._stop()

    def contains(self, animation: Animation) -> bool:
        return animation in self._active

    def advance(self, dt: float) -> None:
        """Step every active spring by ``dt`` seconds (also used by tests)."""

        for spring in list(self._active):
            # One animation's update or finish may stop another further down
            # this frame's list (a smooth scroll moving a tree's scroll bar
            # ends its branch reveal). A stopped animation belongs to whoever
            # stopped it: it is neither stepped nor settled here.
            if spring not in self._active:
                continue
            spring.step(dt)
            if spring.settled and spring in self._active:
                self._active.remove(spring)
                spring.notify_settled()
        if not self._active:
            self._stop()

    def _start(self) -> None:
        if QApplication.instance() is None:
            return
        if self._timer is None:
            self._timer = QTimer(self)
            self._timer.setTimerType(Qt.TimerType.PreciseTimer)
            self._timer.setInterval(FRAME_INTERVAL_MS)
            self._timer.timeout.connect(self._tick)
        self._last_tick = time.perf_counter()
        self._timer.start()

    def _stop(self) -> None:
        # At exit Qt may delete the timer before a widget whose spring is
        # still moving is destroyed (and resets that spring).
        if self._timer is not None and shiboken6.isValid(self._timer):
            self._timer.stop()

    def _tick(self) -> None:
        now = time.perf_counter()
        dt = min(MAX_STEP_S, max(0.0, now - self._last_tick))
        self._last_tick = now
        self.advance(dt)


TICKER = Ticker()

