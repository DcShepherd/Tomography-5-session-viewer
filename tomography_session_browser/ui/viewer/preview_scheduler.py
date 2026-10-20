"""Bounded preview work with foreground priority and GUI-owned publication."""
from __future__ import annotations
from collections import OrderedDict
from dataclasses import dataclass, field
from pathlib import Path
import logging
import time
from typing import Callable
from PySide6.QtCore import QObject, QRunnable, Qt, QThreadPool, Signal, Slot
from PySide6.QtGui import QImage, QImageReader
from tomography_session_browser.parsers.mrc_parser import MrcPreviewSource
from tomography_session_browser.ui.viewer.preview_cache import (
    CachedPreview,
    MRC_PREVIEW_CACHE,
    PreviewCacheKey,
    THUMBNAIL_CACHE,
    preview_cache_key,
)

LOGGER = logging.getLogger("tomography_session_browser.ui.image_viewer")

PreviewKey = tuple[Path, int, int]

#: Thumbnails waiting for the lane's one thread. A contact sheet asks for more
#: as these finish (``thumbnail_room``), so it never queues a whole project.
THUMBNAIL_QUEUE_LIMIT = 32


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


def cached_thumbnail(key: PreviewKey) -> CachedPreview | None:
    """The same for a thumbnail, from the thumbnails' own cache."""
    return THUMBNAIL_CACHE.get(preview_cache_key(*key))


def _read_raster(path: Path, bound: int) -> QImage:
    """``path`` as an image whose longest side is at most ``bound`` (0: as saved).

    The scaled size is set before ``read()``, so the decoder produces the small
    image; decoding a full atlas picture and shrinking it afterwards is the
    cost a thumbnail lane exists to avoid.
    """

    if bound <= 0:
        return QImage(str(path))
    reader = QImageReader(str(path))
    native = reader.size()
    if native.isValid() and max(native.width(), native.height()) > bound:
        reader.setScaledSize(native.scaled(bound, bound, Qt.AspectRatioMode.KeepAspectRatio))
    return reader.read()


def decode_preview(key: PreviewKey, *, thumbnail: bool = False) -> PreviewResult:
    """Decode ``key``; a thumbnail's raster is read at its requested size."""

    path, index, size = key
    cache = THUMBNAIL_CACHE if thumbnail else MRC_PREVIEW_CACHE
    fingerprint = preview_cache_key(path, index, size)
    cached = cache.get(fingerprint)
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
        image = _read_raster(path, size if thumbnail else 0)
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
    #: Runs on the thumbnail lane, into the thumbnails' cache.
    thumbnail: bool = False
    queued_at: float = field(default_factory=time.perf_counter)


class PreviewSignals(QObject):
    completed = Signal(object, object)


class PreviewTask(QRunnable):
    def __init__(self, job: PreviewJob, signals: PreviewSignals,
                 current_generation: Callable[[], int] | None = None) -> None:
        super().__init__()
        self.job, self.signals = job, signals
        self.current_generation = current_generation

    @Slot()
    def run(self) -> None:
        started = time.perf_counter()
        try:
            if self.current_generation is not None and self.job.generation != self.current_generation():
                # Reset while this waited for its thread: nobody wants it, and
                # a thumbnail is not worth a decode to be thrown away.
                raise RuntimeError("Preview request is stale")
            result = decode_preview(self.job.key, thumbnail=True) if self.job.thumbnail else decode_preview(self.job.key)
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

    Thumbnails are a third kind of work and run on a pool of their own, one
    decode at a time and only while no foreground request is waiting. They are
    on their own pool because the global one also serves loading and scope
    preparation: a thumbnail there could hold the thread a foreground decode
    then waits for. Their pictures are small and bounded at the decoder, and
    they publish to their own cache.
    """

    #: A queued thumbnail started or finished, so the queue has room again.
    thumbnail_room = Signal()

    def __init__(self) -> None:
        super().__init__()
        self.signals = PreviewSignals()
        self.signals.completed.connect(self.completed)
        self.pool = QThreadPool.globalInstance()
        self.generation = 0
        self.jobs: OrderedDict[PreviewKey, PreviewJob] = OrderedDict()
        self.active: set[PreviewJob] = set()
        self.thumbnail_pool = QThreadPool()
        self.thumbnail_pool.setMaxThreadCount(1)
        self.thumbnails: OrderedDict[PreviewKey, PreviewJob] = OrderedDict()
        self.thumbnail_tasks: dict[PreviewJob, PreviewTask] = {}

    def cancel(self, client: object) -> None:
        for key, job in list(self.jobs.items()):
            job.listeners.pop(client, None)
            if not job.listeners and not job.speculative and job not in self.active:
                del self.jobs[key]
        for key, thumbnail in list(self.thumbnails.items()):
            thumbnail.listeners.pop(client, None)
            if not thumbnail.listeners and thumbnail not in self.thumbnail_tasks:
                del self.thumbnails[key]

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

    def request_thumbnail(self, key: PreviewKey, *, client: object,
                          receive: Callable[[CachedPreview], None], urgent: bool = False) -> bool:
        """Queue ``key`` for ``client`` on the thumbnail lane; False when the queue is full.

        A client has one request: asking again replaces its last. ``urgent``
        (a tile scrolled into view) moves a queued thumbnail to the front. A
        refused request is the caller's to repeat when ``thumbnail_room`` is
        emitted.
        """

        self.cancel(client)
        job = self.thumbnails.get(key)
        if job is None:
            if sum(1 for queued in self.thumbnails.values() if queued not in self.thumbnail_tasks) >= THUMBNAIL_QUEUE_LIMIT:
                return False
            job = PreviewJob(key, self.generation, thumbnail=True)
            self.thumbnails[key] = job
        job.listeners[client] = receive
        if urgent and job not in self.thumbnail_tasks:
            self.thumbnails.move_to_end(key, last=False)
        self.start_pending()
        return True

    def start_pending(self) -> None:
        while len(self.active) < 2:
            queued = [job for job in self.jobs.values() if job not in self.active]
            foreground = next((job for job in queued if job.listeners), None)
            job = foreground or (queued[0] if queued and not self.active else None)
            if job is None:
                break
            self.active.add(job)
            self.pool.start(PreviewTask(job, self.signals))
        self.start_thumbnail()

    def start_thumbnail(self) -> None:
        """Start the next queued thumbnail, unless one runs or a foreground request waits."""

        if self.thumbnail_tasks:
            return
        if any(job.listeners and job not in self.active for job in self.jobs.values()):
            return
        job = next(iter(self.thumbnails.values()), None)
        if job is None:
            return
        task = PreviewTask(job, self.signals, lambda: self.generation)
        self.thumbnail_tasks[job] = task
        self.thumbnail_pool.start(task)
        self.thumbnail_room.emit()

    @Slot(object, object)
    def completed(self, job: PreviewJob, result: PreviewResult | Exception) -> None:
        if job.thumbnail:
            self.thumbnail_tasks.pop(job, None)
            queue, cache = self.thumbnails, THUMBNAIL_CACHE
        else:
            self.active.discard(job)
            queue, cache = self.jobs, MRC_PREVIEW_CACHE
        if queue.get(job.key) is job:
            del queue[job.key]
        if job.generation == self.generation:
            if isinstance(result, PreviewResult):
                preview = result.preview
                if not preview.image.isNull():
                    cache.put(result.key, preview)
            else:
                preview = CachedPreview(QImage(), 0, [str(result)], 0)
            for receive in tuple(job.listeners.values()):
                receive(preview)
        self.start_pending()
        if job.thumbnail:
            self.thumbnail_room.emit()

    def is_idle(self) -> bool:
        """No decode is queued (speculative ones included) or running.

        The benchmarks wait on this, not on the global thread pool alone: a
        decode on another pool leaves that pool looking idle.
        """

        return not (self.jobs or self.active or self.thumbnails or self.thumbnail_tasks)

    def reset(self) -> None:
        """Forget queued work and silence running work, without waiting for it.

        This runs on the GUI thread during a session load. A decode already
        running finishes on its worker and ``completed`` discards its result,
        because the generation has moved on. A thumbnail handed to the pool
        but not yet running sees the new generation and skips its decode, so
        the pool needs no ``clear()``: it holds no queue of its own, only the
        one task being started, and a cleared task would never report.
        """

        self.generation += 1
        self.jobs.clear()
        for job in self.active:
            job.listeners.clear()
        self.thumbnails.clear()
        for job in self.thumbnail_tasks:
            job.listeners.clear()

    def shutdown(self) -> None:
        """Application exit: start nothing more, then wait out the running decode.

        Only here may the GUI thread wait. A decode has no safe cancellation
        point, so exit waits for the at most one thumbnail in progress.
        """

        self.reset()
        self.thumbnail_pool.waitForDone()


_scheduler: PreviewScheduler | None = None


def preview_scheduler() -> PreviewScheduler:
    global _scheduler
    if _scheduler is None:
        _scheduler = PreviewScheduler()
    return _scheduler


def forget_thumbnail_client(client: object) -> None:
    """Withdraw ``client``'s requests; a scheduler never created has none."""

    if _scheduler is not None:
        _scheduler.cancel(client)


def shutdown_preview_scheduler() -> None:
    """Application exit: a scheduler that was never used has nothing to wait for."""

    if _scheduler is not None:
        _scheduler.shutdown()
