"""One running query and one latest pending query per QWidget receiver."""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
import logging
from threading import Event
from typing import Any

from PySide6.QtCore import QThreadPool

from tomography_session_browser.ui.loading.tasks import BackgroundTask, TaskSignals


@dataclass(frozen=True, slots=True)
class HistoryJob:
    work: Callable[[Callable[[], bool]], Any]
    cancelled: Event = field(default_factory=Event, compare=False)
    stamp: tuple[int, int] = (0, 0)


class HistoryLoader:
    def __init__(self, pool: QThreadPool) -> None:
        self.pool = pool
        self.signals = TaskSignals()
        self.running: HistoryJob | None = None
        self.pending: HistoryJob | None = None
        self.latest: HistoryJob | None = None

    @property
    def loading(self) -> bool:
        return self.latest is not None

    def request(self, work: Callable[[Callable[[], bool]], Any], *, stamp: tuple[int, int] = (0, 0)) -> HistoryJob:
        job = HistoryJob(work, stamp=stamp)
        self.latest = job
        if self.running is not None:
            self.running.cancelled.set()
            if self.pending is not None:
                self.pending.cancelled.set()
            self.pending = job
        else:
            self.start(job)
        return job

    def start(self, job: HistoryJob) -> None:
        self.running = job

        def work():
            try:
                return job.work(job.cancelled.is_set)
            except InterruptedError:
                return None

        self.pool.start(BackgroundTask(work, context=job, signals=self.signals,
            logger=logging.getLogger(__name__), failure_log="Tomogram history query failed"))

    def retire(self, job: HistoryJob) -> bool:
        if self.running is not job:
            return False
        publish = self.latest is job and not job.cancelled.is_set()
        self.running = None
        if publish:
            self.latest = None
        next_job, self.pending = self.pending, None
        if next_job is not None:
            self.start(next_job)
        return publish

    def cancel(self) -> None:
        for job in (self.running, self.pending):
            if job is not None:
                job.cancelled.set()
        self.pending = self.latest = None
