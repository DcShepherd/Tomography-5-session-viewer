"""Capture lifetime is independent of selection and render-request cancellation."""
from __future__ import annotations

from dataclasses import asdict, dataclass, replace
import logging
from pathlib import Path
import time
from threading import Lock
from typing import TYPE_CHECKING

from PySide6.QtCore import QThreadPool, Qt
from tomography_session_browser.ui.file_actions import show_in_folder
from PySide6.QtWidgets import QFileDialog


from tomography_session_browser.parsers.volume_header import fingerprint
from tomography_session_browser.presentation.tomogram_state import TomogramViewState
from tomography_session_browser.presentation.tomogram_render_plan import CaptureIntent
from tomography_session_browser.presentation.tomogram_capture_history import restore_refusal, CaptureEntry
from tomography_session_browser.services.volume.snapshot import CaptureRecord, SnapshotRequest, save_snapshot
from tomography_session_browser.services.output_boundary import require_output_directory
from tomography_session_browser.services.volume.render import native_region
from tomography_session_browser.services.volume.models import RenderRecipe
from tomography_session_browser.services.volume.review_repository import ReviewRepository
from tomography_session_browser.services.volume.review_writer import ReviewWrite, ReviewWriter
from tomography_session_browser.presentation.tomogram_geometry import normal_axis
from tomography_session_browser.ui.loading.tasks import BackgroundTask, TaskSignals

if TYPE_CHECKING:
    from tomography_session_browser.ui.tomograms.tab import TomogramTab


# Reserve recovery capacity for running jobs too: no later failure is evicted.
FAILED_CAPTURES = 8


@dataclass(frozen=True, slots=True)
class CaptureJob:
    request: SnapshotRequest
    state: TomogramViewState
    saved_record: CaptureRecord | None = None
    created_ns: int = 0

    @property
    def identity(self) -> tuple:
        request = self.request
        return (request.row_key, request.volume.id, request.volume.fingerprint,
                request.recipe, request.directory, request.format)


@dataclass(frozen=True, slots=True)
class CaptureOutcome:
    record: CaptureRecord
    history_error: str | None = None


@dataclass(frozen=True, slots=True)
class CaptureFailure:
    job: CaptureJob
    message: str


class CaptureCompletions:
    """Bounded by admitted jobs; destruction can consume without queued signals."""
    def __init__(self):
        self.lock = Lock()
        self.values = []

    def run(self, job, repository, writer):
        try:
            outcome = save_capture_job(job, repository, writer)
        except Exception as exc:
            with self.lock:
                self.values.append((job, str(exc)))
            raise
        with self.lock:
            self.values.append((job, outcome))
        return outcome

    def consume(self, job):
        with self.lock:
            self.values[:] = [(j, value) for j, value in self.values if j is not job]

    def take(self):
        with self.lock:
            values, self.values = self.values, []
        return values


def save_capture_job(job: CaptureJob, repository: ReviewRepository, writer: ReviewWriter) -> CaptureOutcome:
    """Frozen producer work; no widget is retained or read from either worker."""
    record = job.saved_record or save_snapshot(job.request)
    try:
        request = ReviewWrite(repository, "capture", str(record.path), asdict(job.state), {
            "output": str(record.path), "recipe": asdict(job.request.recipe),
            "format": job.request.format, "created_ns": job.created_ns})
        writer.submit(request).result()
    except Exception as exc:
        logging.getLogger(__name__).warning("Snapshot saved, but capture history could not be saved: %s", exc)
        return CaptureOutcome(record, str(exc) or type(exc).__name__)
    return CaptureOutcome(record)


class Captures:
    def __init__(self, tab: TomogramTab) -> None:
        self.tab = tab
        self.signals = TaskSignals()
        self.signals.finished.connect(tab._capture_finished)
        self.signals.failed.connect(tab._capture_failed)
        self.pool = QThreadPool(tab)
        self.pool.setMaxThreadCount(1)
        self.pending: list[CaptureJob] = []
        self.failures: list[CaptureFailure] = []
        self.removing: set[str] = set()
        # The view a Snapshot press asked for while its pixels were still on their way.
        self.deferred: CaptureIntent | None = None
        self.accepting = True
        self.admitted = lambda intent, job: None
        self.cancelled = lambda intent: None
        self.published = lambda job, entry: None
        self.dismissed = lambda job: None
        self.completions = CaptureCompletions()
        tab.image_settings.export_format.currentIndexChanged.connect(tab.update_commands)
        tab.image_settings.sampling.currentIndexChanged.connect(tab.update_commands)

    def block_reason(self) -> str:
        if len(self.pending) >= 2:
            return "Two snapshots are pending. Wait for one to finish."
        if len(self.failures) + len(self.pending) >= FAILED_CAPTURES:
            return "Resolve failed snapshots in Captures before taking another. Viewing remains available."
        if self.ready():
            accepted = self.tab.rendering.accepted
            assert accepted is not None
            recipe = self.output_recipe()
            requests = [(job, "This snapshot is already pending.") for job in self.pending]
            requests.extend((failure.job, "This snapshot needs recovery. Use Retry or Dismiss in Captures.")
                            for failure in self.failures)
            for job, message in requests:
                request = job.request
                if (request.row_key == accepted.job.row_key and request.volume.id == accepted.job.volume.id
                        and request.volume.fingerprint == accepted.job.volume.fingerprint
                        and request.recipe == recipe and request.format == self.tab.image_settings.export_format.currentText()
                        and request.directory == Path(self.tab.settings.tomogram_snapshot_folder or "")):
                    return message
        return ""

    def output_recipe(self, *, native_sampling: bool | None = None) -> RenderRecipe:
        accepted = self.tab.rendering.accepted
        assert accepted is not None
        recipe = accepted.result.recipe
        if native_sampling if native_sampling is not None else self.tab.image_settings.sampling.currentIndex() == 1:
            x0, y0, x1, y1 = native_region(recipe.region)
            if x1 <= x0 or y1 <= y0:
                raise ValueError("No native voxel centres in the visible region. Zoom out before a 1:1 capture.")
            recipe = replace(recipe, output_size=(x1 - x0, y1 - y0), native_sampling=True, pixel_ratio=1.)
        return recipe

    def choose_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(self.tab, "Snapshot folder", self.tab.settings.tomogram_snapshot_folder or "")
        if not folder:
            return
        try:
            require_output_directory(Path(folder), self.tab.protected_roots())
        except (OSError, ValueError) as exc:
            self.tab.status.message.setText(str(exc))
            return
        self.tab.settings.tomogram_snapshot_folder = folder
        self.tab.save_settings(self.tab.settings)
        self.tab.image_settings.refresh_summaries()
        self.tab.update_commands()

    def intent(self) -> CaptureIntent:
        volume = self.tab.volume()
        if volume is None:
            raise ValueError("Select a tomogram before taking a snapshot.")
        folder = self.tab.settings.tomogram_snapshot_folder
        return CaptureIntent(self.tab.state.row_key or "", volume.id, volume.fingerprint,
            self.tab.rendering.plan(refined=True)[0].recipe, self.tab.recipes.levels_revision,
            self.tab.history.generation, self.tab.capture_view_state(), Path(folder) if folder else None,
            self.tab.image_settings.export_format.currentText(), self.tab.image_settings.sampling.currentIndex() == 1)

    def matches(self, intent: CaptureIntent) -> bool:
        volume = self.tab.volume()
        return volume is not None and intent.matches(self.tab.state.row_key or "", volume,
            self.tab.rendering.plan(refined=True)[0].recipe, self.tab.recipes.levels_revision,
            self.tab.history.generation)

    def ready(self) -> bool:
        accepted = self.tab.rendering.accepted
        return (accepted is not None and accepted.complete and accepted.job.generation == self.tab.rendering.generation
                and not self.tab.render_timer.isActive() and not accepted.result.recipe.interactive
                and not self.tab.oblique.timer.isActive())

    def frame_ready(self) -> None:
        """The requested frame arrived: take a snapshot asked for while it was on its way."""
        if self.deferred is None or not self.ready():
            return
        wanted, self.deferred = self.deferred, None
        if not self.matches(wanted):
            self.cancelled(wanted)
            self.tab.status.message.setText("Snapshot not taken: the view changed before the requested plane was shown.")
            return
        self.snapshot(wanted)

    def cancel_deferred(self) -> None:
        wanted, self.deferred = self.deferred, None
        if wanted is not None:
            self.cancelled(wanted)

    def snapshot(self, intent: CaptureIntent | None = None) -> None:
        self.tab.playback.stop()
        try:
            intent = intent or self.intent()
        except ValueError as exc:
            self.tab.status.message.setText(str(exc))
            return
        origin = intent
        if not self.accepting:
            self.cancelled(origin)
            return
        self.cancel_deferred()
        if not self.ready():
            # Snapshot stays available while planes load; it saves the view as
            # requested now, once its pixels are shown, never the outgoing ones.
            if self.tab.volume() is not None:
                self.deferred = intent
                self.tab.status.message.setText("Snapshot will be saved when the requested plane is shown…")
            else:
                self.cancelled(origin)
            return
        accepted = self.tab.rendering.accepted
        assert accepted is not None  # ready() checked it.
        if intent.directory is None:
            self.choose_folder()
            folder = self.tab.settings.tomogram_snapshot_folder
            intent = replace(intent, directory=Path(folder) if folder else None)
        folder = intent.directory
        if not folder:
            self.cancelled(origin)
            return
        # Folder dialogs run a nested event loop: a queued scope/render result
        # must not combine a different pane's recipe with this accepted source.
        if not self.ready() or self.tab.rendering.accepted is not accepted or not self.matches(intent):
            self.cancelled(origin)
            self.tab.status.message.setText("Snapshot not taken: the view changed while choosing the output folder.")
            return
        row = self.tab.review_list.rows.get(accepted.job.row_key)
        if row is None:
            self.cancelled(origin)
            return
        try:
            recipe = self.output_recipe(native_sampling=intent.native_sampling)
            directory = Path(folder).resolve()
        except (OSError, ValueError) as exc:
            self.cancelled(origin)
            self.tab.status.message.setText(str(exc))
            return
        request = SnapshotRequest(accepted.job.row_key, row.tomogram.name, accepted.job.volume,
                                  recipe, directory, self.tab.protected_roots(), intent.format)
        state = replace(intent.state, black=recipe.black, white=recipe.white, levels_anchor=recipe.levels_anchor)
        job = CaptureJob(request, state, created_ns=time.time_ns())
        self.dispatch(job, origin=origin)

    def dispatch(self, job: CaptureJob, failure: CaptureFailure | None = None, *, origin: CaptureIntent | None = None) -> None:
        """All input routes, including retries, share one bounded transaction guard."""
        if failure is not None and failure not in self.failures:
            return
        if reason := self.queue_reason(job):
            if origin is not None:
                self.cancelled(origin)
            self.tab.status.message.setText(reason)
            return
        if failure is None and any(value.job.identity == job.identity for value in self.failures):
            if origin is not None:
                self.cancelled(origin)
            self.tab.status.message.setText("This snapshot needs recovery. Use Retry or Dismiss in Captures.")
            return
        if failure is None and len(self.failures) + len(self.pending) >= FAILED_CAPTURES:
            if origin is not None:
                self.cancelled(origin)
            self.tab.status.message.setText("Resolve failed snapshots in Captures before taking another. Viewing remains available.")
            return
        if failure is not None:
            self.failures.remove(failure)
        # Retry retains the frozen roots and adds roots loaded since the failure.
        roots = tuple(dict.fromkeys((*job.request.protected_roots, *self.tab.protected_roots())))
        job = replace(job, request=replace(job.request, protected_roots=roots,
                                           history=self.tab.persistence.repository.directory, held=self.held_names()))
        self.pending.append(job)
        if origin is not None:
            self.admitted(origin, job)
        request = job.request
        self.tab.status.message.capture(job, f"{'Saving history for' if job.saved_record else 'Saving'} {request.name} · {request.recipe.axis} · {normal_axis(request.recipe.axis)} {request.recipe.plane + 1}…")
        self.tab.update_commands()
        # At most two jobs may be queued; each owns its immutable recipe/source.
        repository, writer = self.tab.persistence.repository, self.tab.persistence.writer
        completions = self.completions
        self.pool.start(BackgroundTask(
            lambda: completions.run(job, repository, writer), context=job, signals=self.signals,
            logger=logging.getLogger(__name__), failure_log="Tomogram snapshot failed"))

    def finished(self, job: CaptureJob, outcome: CaptureOutcome) -> None:
        self.completions.consume(job)
        if job not in self.pending:
            return
        self.pending.remove(job)
        record = outcome.record
        entry = CaptureEntry(record, job.state, created_ns=job.created_ns)
        if job.saved_record is None:
            self.published(job, entry)
        self.tab.history.record_saved(entry, recorded=outcome.history_error is None)
        if outcome.history_error is not None:
            self.failures.append(CaptureFailure(replace(job, saved_record=record), outcome.history_error))
            self.tab.status.message.capture(job, f"Saved {record.path.name}, but history was not saved. Retry history in Captures.")
        else:
            self.tab.status.message.complete(job)
            self.tab.status.announce_saved(record.path.name)
        self.tab.confirm_saved()
        self.tab.image_settings.next_file.refresh(force=True)
        self.tab.update_commands()

    def failed(self, job: CaptureJob, message: str) -> None:
        self.completions.consume(job)
        if job not in self.pending:
            return
        self.pending.remove(job)
        message = message or "The snapshot could not be saved"
        self.failures.append(CaptureFailure(job, message))
        self.tab.status.message.capture(job, f"Snapshot failed for {job.request.name}: {message}. Retry in Captures.")
        self.tab.update_commands()

    def held_names(self) -> frozenset[Path]:
        """Saved images whose history exists only in this window keep their names."""
        records = [entry.record for entry in self.tab.history.unrecorded.values()]
        records.extend(failure.job.saved_record for failure in self.failures if failure.job.saved_record is not None)
        return frozenset(record.path for record in records)

    def queue_reason(self, job: CaptureJob) -> str:
        if len(self.pending) >= 2:
            return "Two snapshots are pending. Wait for one to finish."
        if any(value.identity == job.identity for value in self.pending):
            return "This snapshot is already pending."
        if job.saved_record is not None:
            from tomography_session_browser.services.volume.review_repository import record_id
            if record_id(str(job.saved_record.path)) in self.removing:
                return "Wait for this capture's history removal to finish."
        return ""

    def retry(self, failure: CaptureFailure, *, choose_folder: bool = False) -> None:
        if failure not in self.failures:
            return  # A stale menu or repeated activation cannot enqueue twice.
        job = failure.job
        if choose_folder and job.saved_record is None:
            folder = QFileDialog.getExistingDirectory(self.tab, "Retry snapshot in folder", str(job.request.directory))
            if not folder:
                return
            try:
                roots = (*job.request.protected_roots, *self.tab.protected_roots(), job.request.volume.path.parent)
                directory = require_output_directory(Path(folder), roots)
            except (OSError, ValueError) as exc:
                self.tab.status.message.setText(str(exc))
                return
            job = replace(job, request=replace(job.request, directory=directory))
        # Never read the live canvas, version, sampling, format or output preference.
        self.dispatch(job, failure)

    def dismiss(self, failure: CaptureFailure) -> None:
        if failure not in self.failures:
            return
        self.failures.remove(failure)
        self.dismissed(failure.job)
        self.tab.status.message.setText("Dismissed capture recovery; exported images are kept.")
        self.tab.update_commands()

    def restore(self, entry: CaptureEntry) -> bool:
        request = entry.record.request
        reason = restore_refusal(entry, tuple(self.tab.review_list.rows.values()), verify_source=True)
        if reason:
            self.tab.status.message.setText(reason)
            return False
        self.tab.restore_view_state(replace(entry.state, capture_region=request.recipe.region),
                                    ordering_notice=entry.ordering_notice, captured=True)
        self.tab.image_settings.export_format.setCurrentText(request.format)
        self.tab.image_settings.sampling.setCurrentIndex(1 if request.recipe.native_sampling else 0)
        self.tab.status.message.setText("Restoring captured source and view, including its list filters…"
                                  + (" " + entry.ordering_notice if entry.ordering_notice else ""))
        return True

    def restore_reason(self, entry: CaptureEntry) -> str:
        return restore_refusal(entry, tuple(self.tab.review_list.rows.values()))

    def show_in_folder(self, entry: CaptureEntry) -> bool:
        if not entry.record.path.exists():
            self.tab.status.message.setText("The exported image is missing; its history and captured view are kept.")
        opened = show_in_folder(entry.record.path)
        if not opened:
            self.tab.status.message.setText("The capture folder could not be opened; its history is kept.")
        return opened

    def show_menu(self, row_key: str | None = None) -> None:
        key = row_key if isinstance(row_key, str) else self.tab.state.row_key
        if key not in self.tab.review_list.rows:
            self.tab.history.show_browser()
            return
        menu = self.build_menu(key)
        self.tab.history.popup = menu
        listing = self.tab.review_list
        listing.capture_column.set_open(key)
        item = listing.items_by_key.get(key)
        if item is not None and listing.isVisible():
            point = listing.tree.viewport().mapToGlobal(listing.capture_column.rect(
                listing.tree.visualItemRect(item)).bottomLeft())
        else:
            anchor = listing.compact if listing.compact.isVisible() else self.tab.snapshot_button
            point = anchor.mapToGlobal(anchor.rect().bottomLeft())
        try:
            menu.exec(point)
        finally:
            menu.cancel()
            listing.capture_column.set_open(None)
            self.tab.history.popup = None
            menu.deleteLater()

    def build_menu(self, row_key: str | None = None):
        from tomography_session_browser.ui.tomograms.capture_popup import CapturePopup
        return CapturePopup(self, row_key or self.tab.state.row_key or "")

    def forget(self, entry: CaptureEntry) -> bool:
        if entry.id in self.removing:
            return False
        if any(job.saved_record is not None and job.saved_record.path == entry.record.path for job in self.pending):
            self.tab.status.message.setText("Wait for this capture's history retry to finish before removing it.")
            return False
        try:
            request = ReviewWrite(self.tab.persistence.repository, "capture", entry.id, {}, {}, remove=True)
            future = self.tab.persistence.writer.submit(request)
        except (OSError, ValueError) as exc:
            self.tab.status.message.setText(f"Could not remove capture history: {exc}")
            return False
        self.removing.add(entry.id)
        self.tab.persistence.watch(future, entry)
        return True

    def forgotten(self, entry: CaptureEntry) -> None:
        self.removing.discard(entry.id)
        self.tab.history.forget(entry)
        self.failures[:] = [failure for failure in self.failures
                           if failure.job.saved_record is None or failure.job.saved_record.path != entry.record.path]
        self.tab.status.message.setText(f"Removed {entry.record.path.name} from history; exported image kept.")
        self.tab.update_commands()
