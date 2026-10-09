"""Bounded preview work with foreground priority and GUI-owned publication."""
from __future__ import annotations
from collections import OrderedDict
from dataclasses import dataclass, field
from pathlib import Path
import logging
import time
from typing import Callable
from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal, Slot
from PySide6.QtGui import QImage
from tomography_session_browser.parsers.mrc_parser import MrcPreviewSource
from tomography_session_browser.ui.viewer.preview_cache import CachedPreview, MRC_PREVIEW_CACHE, PreviewCacheKey, preview_cache_key

LOGGER = logging.getLogger("tomography_session_browser.ui.image_viewer")

PreviewKey = tuple[Path, int, int]


@dataclass(slots=True)
class PreviewResult:
    key: PreviewCacheKey
    preview: CachedPreview


def cached_preview(key: PreviewKey) -> CachedPreview | None:
    """A preview already decoded for ``key``, for the GUI thread to install at once.

    One ``stat`` fingerprints the source, as the worker does, so a file changed
    since it was decoded is a miss.
    """
    return MRC_PREVIEW_CACHE.get(preview_cache_key(*key))


def decode_preview(key: PreviewKey) -> PreviewResult:
    path, index, size = key
    fingerprint = preview_cache_key(path, index, size)
    cached = MRC_PREVIEW_CACHE.get(fingerprint)
    if cached is not None:
        return PreviewResult(fingerprint, cached)
    if path.suffix.lower() == ".mrc":
        source = MrcPreviewSource(path)
        preview = source.get_frame_preview(index, max_size=size)
        image = QImage()
        if preview.image is not None:
            gray = preview.image.convert("L")
            image = QImage(gray.tobytes(), gray.width, gray.height, gray.width,
                           QImage.Format.Format_Grayscale8).copy()
        result = CachedPreview(image, preview.slice_count, preview.warnings, image.sizeInBytes(), max(source.inspection.nx, source.inspection.ny))
    else:
        image = QImage(str(path))
        warnings = [f"Could not load image preview: {path}"] if image.isNull() else []
        result = CachedPreview(image, 1, warnings, image.sizeInBytes())
    # A source changed during decode: never cache or display a mixed version.
    if preview_cache_key(path, index, size) != fingerprint:
        raise OSError(f"Preview source changed while reading: {path}")
    return PreviewResult(fingerprint, result)


@dataclass(eq=False, slots=True)
class PreviewJob:
    key: PreviewKey
    generation: int
    listeners: dict[object, Callable[[CachedPreview], None]] = field(default_factory=dict)
    speculative: bool = False
    queued_at: float = field(default_factory=time.perf_counter)


class PreviewSignals(QObject):
    completed = Signal(object, object)


class PreviewTask(QRunnable):
    def __init__(self, job: PreviewJob, signals: PreviewSignals) -> None:
        super().__init__()
        self.job, self.signals = job, signals

    @Slot()
    def run(self) -> None:
        started = time.perf_counter()
        try:
            result = decode_preview(self.job.key)
        except Exception as exc:
            result = exc
        LOGGER.debug("Preview decode path=%s frame=%s queue_ms=%.1f decode_ms=%.1f",
                     self.job.key[0], self.job.key[1], (started - self.job.queued_at) * 1000,
                     (time.perf_counter() - started) * 1000)
        try:
            self.signals.completed.emit(self.job, result)
        except RuntimeError:
            LOGGER.debug("Dropped preview completion during application shutdown")


class PreviewScheduler(QObject):
    """At most two decodes and eight queued prefetches; latest work per client.

    Workers read independent images. Only this GUI-thread owner publishes to
    the cache and subscribers. A workspace reset invalidates running results.
    """
    def __init__(self) -> None:
        super().__init__()
        self.signals = PreviewSignals()
        self.signals.completed.connect(self.completed)
        self.pool = QThreadPool.globalInstance()
        self.generation = 0
        self.jobs: OrderedDict[PreviewKey, PreviewJob] = OrderedDict()
        self.active: set[PreviewJob] = set()

    def cancel(self, client: object) -> None:
        for key, job in list(self.jobs.items()):
            job.listeners.pop(client, None)
            if not job.listeners and not job.speculative and job not in self.active:
                del self.jobs[key]

    def request(self, key: PreviewKey, *, client: object | None = None,
                receive: Callable[[CachedPreview], None] | None = None) -> None:
        if client is not None:
            self.cancel(client)
            # The reviewer has moved on. Keep a matching prefetch to promote,
            # but discard queued speculation before it consumes decoder time.
            for old_key, old_job in list(self.jobs.items()):
                if old_key != key and old_job.speculative and old_job not in self.active:
                    del self.jobs[old_key]
        job = self.jobs.get(key)
        if job is None:
            job = PreviewJob(key, self.generation, speculative=client is None)
            self.jobs[key] = job
        if client is not None and receive is not None:
            job.listeners[client] = receive
            job.speculative = False
        speculative = [key for key, value in self.jobs.items() if value.speculative and value not in self.active]
        for old in speculative[:-8]:
            del self.jobs[old]
        self.start_pending()

    def start_pending(self) -> None:
        while len(self.active) < 2:
            queued = [job for job in self.jobs.values() if job not in self.active]
            foreground = next((job for job in queued if job.listeners), None)
            job = foreground or (queued[0] if queued and not self.active else None)
            if job is None:
                return
            self.active.add(job)
            self.pool.start(PreviewTask(job, self.signals))

    @Slot(object, object)
    def completed(self, job: PreviewJob, result: PreviewResult | Exception) -> None:
        self.active.discard(job)
        if self.jobs.get(job.key) is job:
            del self.jobs[job.key]
        if job.generation == self.generation:
            if isinstance(result, PreviewResult):
                preview = result.preview
                if not preview.image.isNull():
                    MRC_PREVIEW_CACHE.put(result.key, preview)
            else:
                preview = CachedPreview(QImage(), 0, [str(result)], 0)
            for receive in tuple(job.listeners.values()):
                receive(preview)
        self.start_pending()

    def reset(self) -> None:
        self.generation += 1
        self.jobs.clear()
        for job in self.active:
            job.listeners.clear()


_scheduler: PreviewScheduler | None = None


def preview_scheduler() -> PreviewScheduler:
    global _scheduler
    if _scheduler is None:
        _scheduler = PreviewScheduler()
    return _scheduler
