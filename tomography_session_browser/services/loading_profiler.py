"""Lightweight phase timing and counters for the staged session loader.

Used to diagnose where session loading and UI preparation spend their time
without imposing measurable overhead when ``enabled`` is False.

Activate the developer perf report (stderr) by setting the
``TOMOAPP_PERF=1`` environment variable (or any non-empty, non-zero value).
The report is also written to the rotating diagnostics log file at INFO
level regardless of the env var, via :meth:`LoadingProfiler.log_summary`.
"""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
import logging
import os
import sys
import threading
import time
from typing import Iterator, Mapping


PERF_ENV_VAR = "TOMOAPP_PERF"


def perf_mode_enabled() -> bool:
    """Return True when the developer perf report should be emitted to stderr.

    Controlled by the ``TOMOAPP_PERF`` environment variable. ``0``, empty, and
    case-insensitive ``false`` are treated as disabled.
    """

    value = os.environ.get(PERF_ENV_VAR, "")
    if not value:
        return False
    return value.strip().lower() not in {"0", "false", "no", "off"}


@dataclass(slots=True)
class LoadingProfilePhase:
    name: str
    elapsed_s: float
    thread: str
    count: int = 0
    ui_updates: int = 0
    note: str = ""


# Counter names exposed via :meth:`LoadingProfiler.increment`. Adding new
# counters here is preferred over scattering ad-hoc attributes, so the report
# format and tests can rely on a stable set.
_COUNTER_NAMES = (
    "dashboard_rebuilds",
    "defocus_model_builds",
    "dose_model_builds",
    "timeline_model_builds",
    "searchmap_model_builds",
    "mrc_image_loads",
    "parser_cache_hits",
    "parser_cache_misses",
)


# Mapping from internal phase names to the headline summary labels that the
# perf brief asks us to surface. The first match wins; phases not listed
# still appear in the full per-phase listing below the headline.
_HEADLINE_GROUPS: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "Folder scan",
        (
            "validate_session_folder",
            "classify_layout",
            "discover_search_maps",
            "discover_overviews",
        ),
    ),
    (
        "XML/DM parse",
        (
            "parse_session_xml",
            "parse_sample_xml",
            "parse_sample_session_xml",
        ),
    ),
    ("MDOC parse", ("parse_mdoc",)),
    (
        "MRC header parse",
        (
            "parse_overview_mrc_headers",
            "parse_mrc_header",
            "parse_mrc_extended_header",
        ),
    ),
    ("Tilt validation", ("tilt_series_validation",)),
    ("Dashboard model", ("prepare_dashboard_model",)),
    ("Defocus model", ("build_defocus_model",)),
    ("Dose model", ("build_dose_model",)),
    ("Timeline model", ("build_timeline_model",)),
    ("Search map model", ("build_search_map_model",)),
    ("Dashboard widget build", ("build_dashboard_widget",)),
    ("Marker generation", ("build_markers",)),
    ("Image load", ("load_preview_image", "load_mrc_slice")),
    ("Context-panel update", ("update_context_panel",)),
    ("Project tree rebuild", ("populate_project_tree",)),
)


@dataclass(slots=True)
class LoadingProfiler:
    """Lightweight phase timing for the staged session loader."""

    enabled: bool = True
    phases: list[LoadingProfilePhase] = field(default_factory=list)
    ui_update_count: int = 0
    counters: dict[str, int] = field(default_factory=dict)
    # Worst observed main-thread stall (timer-fire delta minus expected
    # interval), in milliseconds. ``None`` means no stall data captured.
    max_main_thread_stall_ms: float | None = None

    @contextmanager
    def phase(self, name: str, *, count: int = 0, note: str = "") -> Iterator[None]:
        if not self.enabled:
            yield
            return
        started = time.perf_counter()
        thread_name = _thread_label()
        try:
            yield
        finally:
            self.phases.append(
                LoadingProfilePhase(
                    name=name,
                    elapsed_s=time.perf_counter() - started,
                    thread=thread_name,
                    count=count,
                    note=note,
                )
            )

    def add_phase(
        self,
        name: str,
        elapsed_s: float,
        *,
        count: int = 0,
        ui_updates: int = 0,
        note: str = "",
    ) -> None:
        if not self.enabled:
            return
        self.phases.append(
            LoadingProfilePhase(
                name=name,
                elapsed_s=elapsed_s,
                thread=_thread_label(),
                count=count,
                ui_updates=ui_updates,
                note=note,
            )
        )

    def record_ui_update(self, count: int = 1) -> None:
        self.ui_update_count += count

    def increment(self, counter: str, amount: int = 1) -> None:
        """Bump a named counter (e.g. ``dashboard_rebuilds``).

        Unknown counters are accepted and stored verbatim so callers can add
        ad-hoc counters in a hot fix without an explicit registration step.
        """

        if not self.enabled:
            return
        self.counters[counter] = self.counters.get(counter, 0) + amount

    def record_stall(self, stall_ms: float) -> None:
        """Record a main-thread stall sample. Keeps the maximum observed."""

        if not self.enabled or stall_ms <= 0:
            return
        if self.max_main_thread_stall_ms is None or stall_ms > self.max_main_thread_stall_ms:
            self.max_main_thread_stall_ms = stall_ms

    # ------------------------------------------------------------------
    # Reporting

    def total_for_phases(self, phase_names: tuple[str, ...]) -> float:
        names = set(phase_names)
        return sum(phase.elapsed_s for phase in self.phases if phase.name in names)

    def format_report(self, *, path: str = "") -> str:
        """Return a human-readable performance report.

        The first section is the headline summary requested by the developer
        brief (one row per scientific phase, plus counters and the worst
        UI-thread stall). The second section is the full per-phase listing
        from :meth:`log_summary` so nothing is hidden.
        """

        lines: list[str] = ["Loading performance report"]
        if path:
            lines.append(f"path: {path}")
        for label, phase_names in _HEADLINE_GROUPS:
            seconds = self.total_for_phases(phase_names)
            if seconds <= 0:
                lines.append(f"{label:<26}n/a")
            else:
                lines.append(f"{label:<26}{seconds:6.2f} s")

        stall_text = (
            f"{self.max_main_thread_stall_ms:6.0f} ms"
            if self.max_main_thread_stall_ms is not None
            else "n/a"
        )
        lines.append(f"{'Max UI-thread stall':<26}{stall_text}")

        for counter in _COUNTER_NAMES:
            if counter in self.counters:
                lines.append(f"{counter.replace('_', ' ').capitalize():<26}{self.counters[counter]}")

        if self.phases:
            lines.append("")
            lines.append("Per-phase detail (in order):")
            for phase in self.phases:
                suffix_parts: list[str] = []
                if phase.count:
                    suffix_parts.append(f"{phase.count} items")
                if phase.ui_updates:
                    suffix_parts.append(f"{phase.ui_updates} UI updates")
                if phase.note:
                    suffix_parts.append(phase.note)
                warning_threshold_s = (
                    0.1 if phase.name == "loading_animation_timer" else 0.05
                )
                warning = (
                    " WARNING"
                    if phase.thread == "main thread" and phase.elapsed_s >= warning_threshold_s
                    else ""
                )
                detail = f" ({', '.join(suffix_parts)})" if suffix_parts else ""
                lines.append(
                    f"  - {phase.name}: {phase.elapsed_s:6.3f} s on {phase.thread}{detail}{warning}"
                )

        if self.ui_update_count:
            marker = " WARNING" if self.ui_update_count > 120 else ""
            lines.append(f"total UI updates: {self.ui_update_count}{marker}")

        return "\n".join(lines)

    def log_summary(self, logger: logging.Logger, *, path: str = "") -> None:
        if not self.enabled or not self.phases:
            return
        logger.info(self.format_report(path=path))

    def emit_perf_report(self, logger: logging.Logger | None = None, *, path: str = "") -> None:
        """Log the summary, and additionally print it to stderr if
        :func:`perf_mode_enabled` returns True.

        Use this at session-load completion. Safe to call when the profiler
        has no phases (silent no-op).
        """

        if not self.enabled or not self.phases:
            return
        report = self.format_report(path=path)
        if logger is not None:
            logger.info(report)
        if perf_mode_enabled():
            # Print, not log, so it lands on stderr regardless of logger
            # configuration and is visible to a developer running the app
            # from a terminal.
            print(report, file=sys.stderr, flush=True)


def _thread_label() -> str:
    current = threading.current_thread()
    if current is threading.main_thread():
        return "main thread"
    return current.name or "worker thread"


# Module-level "active profile" channel for code that runs deep inside the
# parsers and would otherwise need to thread a ``profile`` argument through
# many call sites. The values are aggregate (sum of all per-file
# microseconds for that phase name) so the report stays compact rather than
# growing one phase entry per parsed file.
_ACTIVE_PROFILE: ContextVar["LoadingProfiler | None"] = ContextVar(
    "tomoapp_active_profile", default=None
)
# Guards mutations to aggregate phase entries — if parsers ever run on
# multiple threads (future parallelisation) we must not lose updates to the
# same phase entry. The lock is contended only during loads, never in
# steady-state UI work.
_AGGREGATE_LOCK = threading.Lock()


@contextmanager
def active_profile(profile: "LoadingProfiler | None") -> Iterator["LoadingProfiler | None"]:
    """Bind ``profile`` as the active loader profile for the current async
    context. Parsers can then call :func:`record_aggregate_phase` without
    receiving the profile as an argument.

    Nested calls stack correctly via the ContextVar token.
    """

    token = _ACTIVE_PROFILE.set(profile)
    try:
        yield profile
    finally:
        _ACTIVE_PROFILE.reset(token)


def current_profile() -> "LoadingProfiler | None":
    """Return the currently active loader profile, if any."""

    return _ACTIVE_PROFILE.get()


def record_aggregate_phase(name: str, elapsed_s: float, *, count: int = 1) -> None:
    """Accumulate ``elapsed_s`` under the named phase on the active profile.

    Designed to be called many times per load (once per file parsed); the
    profiler folds them into a single phase entry whose ``elapsed_s`` is the
    sum and whose ``count`` is the number of files. Cheap when no profile is
    active.
    """

    profile = _ACTIVE_PROFILE.get()
    if profile is None or not profile.enabled or elapsed_s <= 0:
        return
    with _AGGREGATE_LOCK:
        # Look for an existing aggregate entry to add into. Linear scan is
        # acceptable: there are O(10) phase entries at any one time and
        # aggregate updates already amortise across hundreds of files each.
        for phase in profile.phases:
            if phase.name == name and phase.note == "aggregate":
                phase.elapsed_s += elapsed_s
                phase.count += count
                return
        profile.phases.append(
            LoadingProfilePhase(
                name=name,
                elapsed_s=elapsed_s,
                thread=_thread_label(),
                count=count,
                note="aggregate",
            )
        )


__all__ = [
    "LoadingProfilePhase",
    "LoadingProfiler",
    "PERF_ENV_VAR",
    "active_profile",
    "current_profile",
    "perf_mode_enabled",
    "record_aggregate_phase",
]
