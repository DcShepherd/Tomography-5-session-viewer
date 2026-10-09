"""Work run on the thread pool, reported back to the UI thread by signal.

One ``BackgroundTask`` runs any piece of work: it emits ``finished(context,
result)`` or, after logging the exception, ``failed(context, error)``. The
context is whatever the caller needs to act on the answer (a load job, the
report's output path). The factories below build the loader's two tasks.
"""
from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from PySide6.QtCore import QObject, QRunnable, Signal, Slot

from tomography_session_browser.diagnostics.profiling import NULL_PROFILE, LoadingProfiler, active_profile
from tomography_session_browser.presentation.prepared_session import prepare_session_ui_payload
from tomography_session_browser.presentation.workspace import Scope, Workspace
from tomography_session_browser.services.session_loader import SessionLoader
from tomography_session_browser.ui.loading.requests import LoadJob


class TaskSignals(QObject):
    finished = Signal(object, object)
    failed = Signal(object, str)


class LoadProgressSignals(QObject):
    """What the parsing worker is doing, for the loading overlay."""

    status = Signal(str)
    # The loader's stage and what it is reading (plan F6.1).
    stage = Signal(str)
    progress = Signal(str, int, int)


class BackgroundTask(QRunnable):
    def __init__(
        self,
        work: Callable[[], Any],
        *,
        context: Any,
        signals: TaskSignals,
        logger: logging.Logger,
        failure_log: str,
        on_failure: Callable[[], None] | None = None,
    ) -> None:
        super().__init__()
        self._work = work
        self._context = context
        self._signals = signals
        self._logger = logger
        self._failure_log = failure_log
        self._on_failure = on_failure

    @Slot()
    def run(self) -> None:
        try:
            result = self._work()
        except Exception as exc:  # noqa: BLE001 - surfaced to the UI thread
            self._logger.exception(self._failure_log)
            if self._on_failure is not None:
                self._on_failure()
            self._signals.failed.emit(self._context, str(exc))
            return
        self._signals.finished.emit(self._context, result)


def session_load_task(
    job: LoadJob,
    signals: TaskSignals,
    progress: LoadProgressSignals,
    logger: logging.Logger,
) -> BackgroundTask:
    """Parse ``job``'s folder into a ``Session``."""

    path, profile = job.request.path, job.profile

    def work() -> Any:
        # Attach the inspect_mrc cache hooks for the duration of this load so
        # the perf report reflects hits/misses for this specific session.
        from tomography_session_browser.parsers import mrc_parser as _mrc_parser

        previous_hit = _mrc_parser.inspect_cache_hit_hook
        previous_miss = _mrc_parser.inspect_cache_miss_hook
        _mrc_parser.inspect_cache_hit_hook = lambda: profile.increment("parser_cache_hits")
        _mrc_parser.inspect_cache_miss_hook = lambda: profile.increment("parser_cache_misses")
        try:
            progress.stage.emit("parse")
            progress.status.emit("Parsing metadata and MRC headers...")
            with profile.phase("load_session_worker"), active_profile(profile):
                return SessionLoader().load(
                    path,
                    profile=profile,
                    # Emitted from this worker thread; queued to the window.
                    progress=lambda label, current, total: progress.progress.emit(label, current, total),
                )
        finally:
            _mrc_parser.inspect_cache_hit_hook = previous_hit
            _mrc_parser.inspect_cache_miss_hook = previous_miss

    return BackgroundTask(
        work,
        context=job,
        signals=signals,
        logger=logger,
        failure_log=f"Could not load tomography session path={path}",
        on_failure=lambda: profile.emit_perf_report(logger, path=path),
    )


def ui_prepare_task(
    workspace: Workspace,
    scope: Scope,
    signals: TaskSignals,
    logger: logging.Logger,
    profile: LoadingProfiler = NULL_PROFILE,
) -> BackgroundTask:
    """Build the dashboard and tab models for ``scope`` before any widget."""

    def work() -> Any:
        with profile.phase("prepare_ui_payload_worker"), active_profile(profile):
            return prepare_session_ui_payload(workspace, scope, profile=profile)

    return BackgroundTask(
        work,
        context=None,
        signals=signals,
        logger=logger,
        failure_log="Could not prepare tomography session UI payload",
    )
