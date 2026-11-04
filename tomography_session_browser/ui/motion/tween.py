"""Tween components for the motion."""
from __future__ import annotations
from collections.abc import Callable
from PySide6.QtWidgets import QWidget
from tomography_session_browser.ui.motion.easing import ease_out_cubic
from tomography_session_browser.ui.motion.preferences import motion_enabled
from tomography_session_browser.ui.motion.ticker import TICKER


class Tween:
    """A value running 0 → 1 over a fixed time on the shared ticker, eased.

    For fire-and-forget fades, where a spring's long tail would only cost
    frames. It has the ticker's interface (``step``, ``settled``), so it
    shares the one frame timer with the springs. With motion off it jumps
    to 1 at once.
    """

    def __init__(
        self,
        duration_ms: float,
        *,
        on_update: Callable[[float], None] | None = None,
        on_finished: Callable[[], None] | None = None,
        widget: QWidget | None = None,
        easing: Callable[[float], float] = ease_out_cubic,
    ) -> None:
        self.duration_s = max(1e-3, duration_ms / 1000.0)
        self.elapsed = 0.0
        self.value = 0.0
        self._on_update = on_update
        self._on_finished = on_finished
        self._widget = widget
        self._easing = easing

    @property
    def settled(self) -> bool:
        return self.elapsed >= self.duration_s

    @property
    def is_animating(self) -> bool:
        return TICKER.contains(self)

    def start(self) -> None:
        self.elapsed = 0.0
        self.value = 0.0
        if not motion_enabled(self._widget):
            self.finish()
            return
        TICKER.add(self)

    def step(self, dt: float) -> None:
        self.elapsed += max(0.0, dt)
        self.value = self._easing(min(1.0, self.elapsed / self.duration_s))
        if self._on_update is not None:
            self._on_update(self.value)

    def finish(self) -> None:
        """Jump to the end now."""

        TICKER.remove(self)
        self.elapsed = self.duration_s
        self.value = 1.0
        if self._on_update is not None:
            self._on_update(1.0)
        self.notify_settled()

    def stop(self) -> None:
        TICKER.remove(self)

    def notify_settled(self) -> None:
        if self._on_finished is not None:
            self._on_finished()

