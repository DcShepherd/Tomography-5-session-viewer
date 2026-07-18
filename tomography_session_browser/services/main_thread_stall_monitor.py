"""Main-thread stall sampler for the loading-animation budget.

The Qt main thread is supposed to repaint at ~60 fps during a load so the
:class:`~tomography_session_browser.ui.widgets.loading_overlay.LoadingOverlay`
animation stays smooth. Heavy synchronous work scheduled onto the main
thread (project tree rebuilds, big widget construction, eager table
builds) blocks the event loop and produces visible stutter.

This monitor schedules a periodic :class:`QTimer` and watches for late
firings. The difference between the actual firing interval and the
configured interval (minus a small tolerance) is recorded as a stall on
the active :class:`LoadingProfiler`. We keep only the maximum sample,
since one bad stall is what users notice — not the cumulative count of
small ones.

Designed to be cheap: when not started, no Qt object is created; when
started, a single :class:`QTimer` and one Python callback per tick.
"""

from __future__ import annotations

import logging
import time

from tomography_session_browser.services.loading_profiler import LoadingProfiler

LOGGER = logging.getLogger(__name__)

DEFAULT_INTERVAL_MS = 16   # ~60 Hz; matches the loading animation cadence
DEFAULT_TOLERANCE_MS = 4   # Qt timers aren't precise; ignore <= this skew


class MainThreadStallMonitor:
    """Track the worst late-fire delta of a main-thread ``QTimer``.

    Use:

    >>> monitor = MainThreadStallMonitor(profile)
    >>> monitor.start()
    >>> ...                       # run the load
    >>> monitor.stop()

    The monitor is Qt-aware; importing it does not require Qt to be
    available, but :meth:`start` does.
    """

    def __init__(
        self,
        profile: LoadingProfiler | None,
        *,
        interval_ms: int = DEFAULT_INTERVAL_MS,
        tolerance_ms: int = DEFAULT_TOLERANCE_MS,
    ) -> None:
        self._profile = profile
        self._interval_ms = max(1, interval_ms)
        self._tolerance_ms = max(0, tolerance_ms)
        self._timer = None  # type: ignore[assignment]
        self._last_tick_perf: float | None = None
        self._max_stall_ms: float = 0.0
        self._tick_count: int = 0

    @property
    def max_stall_ms(self) -> float:
        return self._max_stall_ms

    @property
    def tick_count(self) -> int:
        return self._tick_count

    def start(self) -> None:
        """Begin sampling. Idempotent: a second ``start`` does nothing."""

        if self._timer is not None or self._profile is None or not self._profile.enabled:
            return
        try:
            from PySide6.QtCore import QTimer
        except ImportError:  # pragma: no cover - Qt is a hard dependency
            LOGGER.debug("Qt unavailable; stall monitor disabled.")
            return
        self._timer = QTimer()
        # Coarse Qt timer type (PreciseTimer) so the OS doesn't snap our
        # interval to a 1 ms tick budget. We're measuring SKEW, not duration,
        # so high precision here matters.
        try:
            self._timer.setTimerType(self._precise_timer_type())
        except Exception:  # pragma: no cover - defensive
            pass
        self._timer.timeout.connect(self._on_tick)
        self._last_tick_perf = time.perf_counter()
        self._timer.start(self._interval_ms)

    def stop(self) -> None:
        """End sampling and record the result into the profile."""

        if self._timer is None:
            return
        try:
            self._timer.stop()
            self._timer.timeout.disconnect(self._on_tick)
        finally:
            self._timer = None
        if self._profile is not None and self._max_stall_ms > 0:
            self._profile.record_stall(self._max_stall_ms)

    def _on_tick(self) -> None:
        now = time.perf_counter()
        previous = self._last_tick_perf
        self._last_tick_perf = now
        self._tick_count += 1
        if previous is None:
            return
        elapsed_ms = (now - previous) * 1000.0
        skew_ms = elapsed_ms - self._interval_ms
        if skew_ms <= self._tolerance_ms:
            return
        if skew_ms > self._max_stall_ms:
            self._max_stall_ms = skew_ms

    @staticmethod
    def _precise_timer_type():
        from PySide6.QtCore import Qt

        return Qt.TimerType.PreciseTimer


__all__ = ["MainThreadStallMonitor", "DEFAULT_INTERVAL_MS", "DEFAULT_TOLERANCE_MS"]
