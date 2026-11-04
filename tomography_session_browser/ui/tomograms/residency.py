"""One optional source producer; QWidget facade receives all completions."""
from dataclasses import dataclass, field
import logging
from threading import Event

from PySide6.QtCore import QThreadPool
from tomography_session_browser.domain.tomograms import Volume
from tomography_session_browser.services.volume.memory import VOLUME_MEMORY
from tomography_session_browser.services.volume.resident import load_resident
from tomography_session_browser.services.volume.store import resident_key
from tomography_session_browser.ui.loading.tasks import BackgroundTask, TaskSignals


@dataclass(frozen=True, slots=True)
class ResidentJob:
    volume: Volume
    cancelled: Event = field(default_factory=Event, compare=False)


class ResidentSource:
    def __init__(self, tab):
        self.tab = tab
        self.volume = None
        self.running = None
        self.pending = None
        self.closed = False
        self.pool = QThreadPool(tab)
        self.pool.setMaxThreadCount(1)
        self.signals = TaskSignals()
        self.signals.finished.connect(tab._resident_finished)
        self.signals.failed.connect(tab._resident_failed)
        tab.destroyed.connect(self.close)

    def select(self, volume: Volume | None) -> None:
        if volume != self.volume:
            with VOLUME_MEMORY.lock:
                if self.running is not None:
                    self.running.cancelled.set()
                if self.volume is not None:
                    VOLUME_MEMORY.retire(resident_key(self.volume))
            self.pending = None
            self.volume = volume
        self.ensure()

    def ensure(self) -> None:
        if self.closed or self.volume is None or self.volume.grid is None:
            return
        if VOLUME_MEMORY.contains(resident_key(self.volume)):
            return
        if self.running is not None and self.running.volume == self.volume and not self.running.cancelled.is_set():
            return
        self.pending = ResidentJob(self.volume)
        if self.running is None:
            self.start_next()

    def start_next(self) -> None:
        if self.closed or self.pending is None:
            return
        job, self.pending = self.pending, None
        self.running = job
        def read():
            try:
                return load_resident(job.volume, cancelled=job.cancelled.is_set)
            except (OSError, ValueError, RuntimeError):
                return False  # Optional admission cannot make scientific review fail.
        self.pool.start(BackgroundTask(read, context=job, signals=self.signals,
                                     logger=logging.getLogger(__name__), failure_log="Optional source read failed"))

    def finished(self, job, _result) -> None:
        if self.running is not job:
            return
        self.running = None
        self.tab.image_settings.update_memory()
        if self.pending is not None:
            self.start_next()

    def close(self, *_args) -> None:
        self.closed = True
        self.pending = None
        with VOLUME_MEMORY.lock:
            if self.running is not None:
                self.running.cancelled.set()
            if self.volume is not None:
                VOLUME_MEMORY.retire(resident_key(self.volume))
