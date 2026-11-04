"""Window-owned history context, recent cache and bounded metadata query lane."""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Literal
from PySide6.QtCore import QThreadPool

from tomography_session_browser.presentation.tomogram_capture_history import (CaptureEntry, HistoryContext,
                                                                            CapturePage, capture_page, scope_history)
from tomography_session_browser.ui.tomograms.history_loading import HistoryJob, HistoryLoader


@dataclass(frozen=True, slots=True)
class CountSnapshot:
    total: int
    rows: Mapping[str, int]


class CaptureHistory:
    def __init__(self, tab) -> None:
        self.tab = tab
        self.pool = QThreadPool(tab)
        self.pool.setMaxThreadCount(1)
        self.loader = HistoryLoader(self.pool)
        self.loader.signals.finished.connect(tab._history_finished)
        self.loader.signals.failed.connect(tab._history_failed)
        self.recent_loader = HistoryLoader(self.pool)
        self.recent_loader.signals.finished.connect(tab._history_finished)
        self.recent_loader.signals.failed.connect(tab._history_failed)
        self.generation = 0
        self.revision = 0
        self.entries: list[CaptureEntry] = []
        self.scope_choice_revision = 0
        self.browser = None
        self.contact_dialog = None
        self.popup = None
        self.unrecorded: dict[str, CaptureEntry] = {}
        self.opened_revision: dict[str, int] = {}
        self.count_snapshot: CountSnapshot | None = None
        self.pending_counts: HistoryJob | None = None
        self.refresh_error = ""

    def begin_counts(self, job: HistoryJob) -> None:
        """Dispatch grants publication rights only to a full-scope request."""
        self.pending_counts = job
        self.refresh_error = ""
        self.publish_review_counts(None)
        self.tab.slider.suspend_marks()
        self.tab.update_commands()
        self.tab.review_metadata_changed.emit()

    def complete_counts(self, job: HistoryJob, page: CapturePage, *,
                        recent: Literal["replace", "merge", "keep"] = "keep") -> None:
        if self.pending_counts is not job or job.stamp != self.stamp() or job.cancelled.is_set():
            return
        self.count_snapshot = CountSnapshot(page.total, MappingProxyType(dict(page.counts)))
        self.pending_counts, self.refresh_error = None, ""
        self.publish_review_counts(dict(page.counts))
        if recent != "keep":
            values = {} if recent == "replace" else {entry.id: entry for entry in self.entries}
            values.update((entry.id, entry) for entry in page.entries)
            self.entries[:] = sorted(values.values(), key=lambda entry: (entry.created_ns, entry.id))[-200:]
        self.tab.update_commands()

        self.tab.review_metadata_changed.emit()

    def fail_counts(self, job: HistoryJob, message: str) -> None:
        if self.pending_counts is job and job.stamp == self.stamp():
            self.pending_counts = None
            self.refresh_error = message or "Capture history is unavailable"
            self.publish_review_counts(None, self.refresh_error)
            self.tab.update_commands()
            self.tab.review_metadata_changed.emit()

    def publish_review_counts(self, counts: Mapping[str, int] | None, note: str = "") -> None:
        self.tab.review_state.capture_counts((row.tomogram for row in self.tab.payload.rows),
                                              self.tab.payload.sources, counts, note)

    def cancel_counts(self, job: HistoryJob | None, *, replace: bool = False) -> None:
        if job is not None and self.pending_counts is job:
            self.pending_counts = None
            if replace and not self.tab.retired and job.stamp == self.stamp():
                self.refresh_recent()
            else:
                self.tab.update_commands()

    @property
    def loading(self) -> bool:
        return self.loader.loading or self.recent_loader.loading or self.pending_counts is not None

    def stamp(self) -> tuple[int, int]:
        return self.generation, self.revision

    def refresh_recent(self) -> None:
        stamp = self.stamp()
        repository, context, extra = self.tab.persistence.repository, self.context(), self.extras()
        writer = self.tab.persistence.writer
        job = self.recent_loader.request(lambda cancelled: writer.read(lambda: capture_page(
            repository, context, limit=200, extra=extra, cancelled=cancelled)), stamp=stamp)
        self.begin_counts(job)

    def refresh_captures(self) -> None:
        self.tab.slider.refresh_marks()
        # An open full-scope browser already queries the authoritative total.
        # Reuse that read instead of serially scanning the same records twice.
        if self.browser is not None and self.browser.isVisible():
            self.recent_loader.cancel()
            self.browser.refresh_page()
        else:
            self.refresh_recent()
        if self.contact_dialog is not None and self.contact_dialog.isVisible():
            self.contact_dialog.refresh_page()
        if self.popup is not None and self.popup.isVisible():
            self.popup.refresh_page()

    def context(self, keys: frozenset[str] | None = None) -> HistoryContext:
        return HistoryContext(tuple(row for key, row in self.tab.review_list.rows.items() if keys is None or key in keys),
                              self.tab.protected_roots())

    def extras(self):
        return tuple(entry.metadata() for entry in self.unrecorded.values())

    def load_scope(self, *, invalidate: bool = True) -> None:
        if invalidate:
            self.generation += 1
            if self.popup is not None:
                self.popup.cancel()
                self.popup.close()
            self.count_snapshot, self.pending_counts = None, None
            self.refresh_error = ""
            self.recent_loader.cancel()
            self.scope_choice_revision = self.tab.persistence.choice_revision
        generation = self.generation
        revision = self.revision
        repository, context, extra = self.tab.persistence.repository, self.context(), self.extras()
        writer = self.tab.persistence.writer
        self.opened_revision = {key: value for key, value in self.opened_revision.items()
                                if key in self.tab.review_list.rows}
        revisions = dict(self.opened_revision)
        choice = self.scope_choice_revision
        self.tab.persistence.history_loading = True
        self.tab.persistence.history_error = ""
        if invalidate:
            self.tab.persistence.offered_state = None
            self.tab.persistence.resume_reason = ""
        self.tab.persistence.sync_resume_controls()
        self.entries[:] = [entry for entry in self.entries
                                       if (entry.state.row_key, entry.state.volume_id) in context.identities]
        job = self.loader.request(lambda cancelled: (revisions, choice, writer.read(lambda: scope_history(
            repository, context, extra=extra, cancelled=cancelled))), stamp=(generation, revision))
        self.begin_counts(job)
        if invalidate and self.browser is not None:
            self.browser.scope_changed()
        if invalidate and self.contact_dialog is not None:
            self.contact_dialog.scope_changed()

    def finished(self, job: HistoryJob, result) -> None:
        if self.recent_loader.running is job:
            if self.recent_loader.retire(job) and result is not None:
                if job.stamp == self.stamp():
                    self.complete_counts(job, result, recent="replace")
                else:
                    self.refresh_recent()
            return
        if not self.loader.retire(job) or result is None:
            return
        generation, revision = job.stamp
        revisions, choice, snapshot = result
        if generation != self.generation:
            return
        if revision != self.revision:
            self.load_scope(invalidate=False)
            return
        self.tab.persistence.history_loading = False
        self.complete_counts(job, snapshot.captures, recent="replace")
        self.tab.persistence.apply_history(snapshot, revisions, choice)

    def failed(self, job: HistoryJob, message: str) -> None:
        if self.recent_loader.running is job:
            if self.recent_loader.retire(job):
                if job.stamp != self.stamp():
                    self.refresh_recent()
                    return
                self.fail_counts(job, message)
                self.tab.status.message.setText("Capture history could not be refreshed. Use Refresh to retry.")
            return
        if not self.loader.retire(job):
            return
        if job.stamp != self.stamp():
            self.load_scope(invalidate=False)
            return
        self.tab.persistence.history_loading = False
        self.tab.persistence.history_error = message or "History is unavailable"
        self.fail_counts(job, message)
        self.tab.persistence.resume_label = "History unavailable"
        self.tab.persistence.resume_reason = self.tab.persistence.history_error
        self.tab.persistence.sync_resume_controls()
        self.tab.update_commands()

    def record_saved(self, entry: CaptureEntry, *, recorded: bool) -> None:
        self.revision += 1
        if recorded:
            self.unrecorded.pop(entry.id, None)
        else:
            self.unrecorded[entry.id] = entry
            while len(self.unrecorded) > 200:
                del self.unrecorded[next(iter(self.unrecorded))]
        values = {value.id: value for value in self.entries}
        values[entry.id] = entry
        self.entries[:] = sorted(values.values(), key=lambda e: (e.created_ns, e.id))[-200:]
        # A newly published record may have missed a query's directory snapshot.
        # Refresh the recent cache without changing the Resume user choice.
        self.refresh_captures()

    def forget(self, entry: CaptureEntry) -> None:
        self.revision += 1
        self.unrecorded.pop(entry.id, None)
        self.entries[:] = [value for value in self.entries if value.id != entry.id]
        for dialog in (self.browser, self.contact_dialog):
            if dialog is not None and dialog.isVisible():
                dialog.set_selected(entry.id, False)
        self.refresh_captures()

    def show_browser(self) -> None:
        from tomography_session_browser.ui.tomograms.history_dialog import CaptureHistoryDialog
        if self.browser is None:
            self.browser = CaptureHistoryDialog(self)
        else:
            self.browser.refresh_page()
        self.browser.show()
        self.browser.raise_()
        self.browser.activateWindow()

    def refresh_controls(self) -> None:
        if self.browser is not None:
            self.browser.refresh_export()

    def refresh_theme(self) -> None:
        for dialog in (self.browser, self.contact_dialog):
            if dialog is not None:
                for row in dialog.row_widgets.values():
                    row.refresh_theme()
        if self.popup is not None:
            for row in self.popup.rows:
                row.refresh_theme()

    def removal_failed(self, identity: str, message: str) -> None:
        if self.browser is not None and self.browser.selected_id == identity:
            self.browser.detail.setText(message)

    def shutdown(self) -> None:
        self.pending_counts = None
        if self.popup is not None:
            self.popup.cancel()
            self.popup.close()
        self.loader.cancel()
        self.recent_loader.cancel()
        for dialog in (self.browser, self.contact_dialog):
            if dialog is not None:
                dialog.reject()
        self.pool.waitForDone()
