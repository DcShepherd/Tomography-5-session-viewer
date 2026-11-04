"""Spring components for the motion."""
from __future__ import annotations
from collections.abc import Callable
import math
from PySide6.QtWidgets import QWidget
from tomography_session_browser.ui.motion.preferences import motion_enabled
from tomography_session_browser.ui.motion.ticker import TICKER
from tomography_session_browser.ui.motion.tokens import MOTION_SMALL, SpringSpec


SETTLE_DISTANCE = 1e-3


SETTLE_VELOCITY = 1e-2


class Spring:
    """A value that moves toward a target like a damped spring.

    Retargetable and velocity-preserving: :meth:`set_target` during motion
    continues from the current value and velocity. ``on_update`` runs every
    step; ``on_settled`` once the value comes to rest on the target.
    """

    def __init__(
        self,
        value: float = 0.0,
        *,
        spec: SpringSpec = MOTION_SMALL,
        on_update: Callable[[float], None] | None = None,
        on_settled: Callable[[], None] | None = None,
        widget: QWidget | None = None,
        settle_distance: float = SETTLE_DISTANCE,
        settle_velocity: float = SETTLE_VELOCITY,
    ) -> None:
        self.value = float(value)
        self.target = float(value)
        self.velocity = 0.0
        self.spec = spec
        self._on_update = on_update
        self._on_settled = on_settled
        self._widget = widget
        # How close counts as at rest, in the value's own units. A spring that
        # moves pixels should stop within half a pixel: the default ran slides
        # on for 200 ms of frames that moved nothing visible.
        self._settle_distance = settle_distance
        self._settle_velocity = settle_velocity

    @property
    def settled(self) -> bool:
        return (
            abs(self.value - self.target) < self._settle_distance
            and abs(self.velocity) < self._settle_velocity
        )

    @property
    def is_animating(self) -> bool:
        return TICKER.contains(self)

    def set_target(self, target: float, *, animate: bool = True) -> None:
        self.target = float(target)
        if not animate or not motion_enabled(self._widget):
            self.jump_to(self.target)
            return
        if self.settled:
            return
        TICKER.add(self)

    def reset(self, value: float) -> None:
        """Rest at ``value`` without animating or notifying.

        For when something else has already moved what the spring drives (a
        Fit click during a wheel zoom), so the spring only needs to agree.
        """

        TICKER.remove(self)
        self.value = self.target = float(value)
        self.velocity = 0.0

    def jump_to(self, value: float) -> None:
        """Move instantly (reduced motion, or a change that must not animate)."""

        TICKER.remove(self)
        self.value = self.target = float(value)
        self.velocity = 0.0
        if self._on_update is not None:
            self._on_update(self.value)
        self.notify_settled()

    def step(self, dt: float) -> None:
        # Semi-implicit Euler in small substeps: stable for these stiffnesses
        # even when a stalled frame hands us the maximum step.
        stiffness = self.spec.stiffness
        damping = 2.0 * self.spec.damping_ratio * math.sqrt(stiffness)
        remaining = max(0.0, dt)
        while remaining > 0.0:
            h = min(remaining, 1.0 / 240.0)
            acceleration = -stiffness * (self.value - self.target) - damping * self.velocity
            self.velocity += acceleration * h
            self.value += self.velocity * h
            remaining -= h
        if self.settled:
            self.value = self.target
            self.velocity = 0.0
        if self._on_update is not None:
            self._on_update(self.value)

    def notify_settled(self) -> None:
        if self._on_settled is not None:
            self._on_settled()

