"""A load's profiling on the UI thread.

The worker records its own phases. On the UI thread the profile is bound as
the active one (so context-panel updates and the like can record aggregate
phases without being handed it), a stall monitor samples the thread, and the
dashboard's rebuild count and latest build time are kept until the load's
report is written. None of it may break a load: failures are logged at debug.
"""
from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

from tomography_session_browser.diagnostics.profiling import ACTIVE_PROFILE, LoadingProfiler


class LoadInstrumentation:
    def __init__(self, logger: logging.Logger) -> None:
        self._logger = logger
        # Token for the ContextVar binding of the active profile on the
        # main thread. Reset on load completion.
        self._profile_token: Any | None = None
        # Main-thread stall sampler: live for the duration of a load, stopped
        # at completion (success or queue advance). None when no load runs.
        self._stall_monitor: Any | None = None
        #: Dashboard rebuilds and the latest widget build, for this load only.
        self.dashboard_rebuilds = 0
        self.last_dashboard_build_ms = 0

    def reset_counters(self) -> None:
        self.dashboard_rebuilds = 0
        self.last_dashboard_build_ms = 0

    def bind(self, profile: LoadingProfiler) -> None:
        """Bind ``profile`` as the main thread's active loader profile.

        ``unbind`` must run on the same thread.
        """

        self.unbind()
        self._profile_token = ACTIVE_PROFILE.set(profile)

    def unbind(self) -> None:
        token = self._profile_token
        if token is None:
            return
        try:
            ACTIVE_PROFILE.reset(token)
        except Exception:  # pragma: no cover - defensive
            self._logger.debug("Could not unbind main-thread profile", exc_info=True)
        finally:
            self._profile_token = None

    def attach_stall_monitor(self, profile: LoadingProfiler) -> None:
        """Start a ``MainThreadStallMonitor`` for ``profile``, stopping any
        earlier one first."""

        self.detach_stall_monitor()
        try:
            from tomography_session_browser.ui.main_thread_stall_monitor import (
                MainThreadStallMonitor,
            )

            monitor = MainThreadStallMonitor(profile)
            monitor.start()
            self._stall_monitor = monitor
        except Exception:  # pragma: no cover - perf must not break loads
            self._logger.debug("Could not start stall monitor", exc_info=True)
            self._stall_monitor = None

    def detach_stall_monitor(self) -> None:
        if self._stall_monitor is None:
            return
        try:
            self._stall_monitor.stop()
        except Exception:  # pragma: no cover - defensive
            self._logger.debug("Could not stop stall monitor", exc_info=True)
        self._stall_monitor = None

    def fold_into(self, profile: LoadingProfiler) -> None:
        """Add the UI thread's dashboard metrics to ``profile``'s report.

        Additive: worker phases are never overwritten.
        """

        try:
            if self.last_dashboard_build_ms > 0:
                profile.add_phase(
                    "build_dashboard_widget",
                    self.last_dashboard_build_ms / 1000.0,
                    note="latest set_model() call",
                )
            if self.dashboard_rebuilds:
                profile.set_counter("dashboard_rebuilds", self.dashboard_rebuilds)
        except Exception:  # pragma: no cover - perf reporting must never break a load
            self._logger.debug("Could not fold main-thread perf into profile", exc_info=True)

    @staticmethod
    def record_loading_animation(profile: LoadingProfiler, snapshot: Mapping[str, Any]) -> None:
        """Add the loading overlay's frame timing to ``profile``."""

        max_interval_ms = float(snapshot.get("max_interval_ms", 0.0))
        profile.add_phase(
            "loading_animation_timer",
            max_interval_ms / 1000.0,
            count=int(snapshot.get("tick_count", 0)),
            note=(
                f"target_ms={snapshot.get('target_interval_ms', 0)} "
                f"median_ms={snapshot.get('median_interval_ms', 0)} "
                f"max_ms={snapshot.get('max_interval_ms', 0)} "
                f"dropped_frames={snapshot.get('dropped_frames', 0)} "
                f"renderer={snapshot.get('renderer', '')}"
            ),
        )
