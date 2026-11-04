"""Offer scalar resume only after explicit source intake; never auto-load data."""
from dataclasses import asdict, replace
from concurrent.futures import Future
import logging

from PySide6.QtCore import QTimer, Qt
from tomography_session_browser.ui.loading.tasks import TaskSignals
from tomography_session_browser.presentation.tomogram_state import TomogramViewState
from tomography_session_browser.presentation.tomogram_versions import review_versions
from tomography_session_browser.parsers.volume_header import fingerprint
from tomography_session_browser.services.volume.review_repository import ReviewRepository
from tomography_session_browser.services.volume.review_writer import ReviewWrite, ReviewWriter
from tomography_session_browser.presentation.tomogram_capture_history import CaptureEntry


class ReviewPersistence:
    def __init__(self, tab) -> None:
        self.tab = tab
        self.repository = ReviewRepository()
        self.writer = ReviewWriter()
        self.signals = TaskSignals()
        self.signals.finished.connect(tab._review_written)
        self.signals.failed.connect(tab._review_write_failed)
        self.local_opened: dict[str, bool] = {}
        self.watched: set[Future] = set()
        self.offered_state: TomogramViewState | None = None
        self.resume_reason = ""
        self.resume_label = "Resume saved view"
        self.resume_ordering_notice = ""
        self.history_loading = False
        self.history_error = ""
        self.choice_revision = 0
        self.timer = QTimer(tab)
        self.timer.setSingleShot(True)
        self.timer.setInterval(500)
        self.timer.timeout.connect(self.save_view)
        tab.review_list.opened_changed.connect(self.save_opened)

    def bind_payload(self, payload) -> None:
        old_sources = {source.id: source for source in self.tab.payload.sources}
        sources = {source.id: source for source in payload.sources}
        unchanged = {row.tomogram.id for row in payload.rows
                     if old_sources.get(row.tomogram.source_id) == sources[row.tomogram.source_id]}
        self.local_opened = {key: value for key, value in self.local_opened.items() if key in unchanged}
        self.tab.review_list.opened = {row.tomogram.id for row in payload.rows
            if self.tab.review_state.row(row.tomogram, sources[row.tomogram.source_id]).opened is True}

    @property
    def resume_state(self) -> TomogramViewState | None:
        return self.offered_state if not self.resume_reason else None

    def schedule(self) -> None:
        self.timer.start()

    def write(self, kind, identity, state, **extra) -> None:
        try:
            request = ReviewWrite(self.repository, kind, identity, asdict(state), extra)
            self.watch(self.writer.submit(request), request)
        except (OSError, ValueError) as exc:
            logging.getLogger(__name__).warning("Could not save tomogram review state: %s", exc)
            self.tab.status.message.setText("Review state could not be saved; the current view remains usable.")

    def watch(self, future: Future, context) -> None:
        if future in self.watched:
            return
        self.watched.add(future)
        signals = self.signals
        watched = self.watched

        def done(result):
            try:
                result.result()
            except Exception as exc:
                signals.failed.emit(context, str(exc))
            else:
                signals.finished.emit(context, None)
            finally:
                watched.discard(result)

        future.add_done_callback(done)

    def finished(self, context, _result) -> None:
        if isinstance(context, CaptureEntry):
            self.tab.captures.forgotten(context)

    def failed(self, context, message: str) -> None:
        if isinstance(context, CaptureEntry):
            self.tab.captures.removing.discard(context.id)
            detail = f"Could not remove capture history: {message}"
            self.tab.status.message.setText(detail)
            self.tab.history.removal_failed(context.id, detail)
        else:
            logging.getLogger(__name__).warning("Could not save tomogram review state: %s", message)
            self.tab.status.message.setText("Review state could not be saved; the current view remains usable.")

    def save_view(self) -> None:
        self.timer.stop()
        if self.offered_state is not None or self.history_loading or self.history_error:
            return
        state = self.tab.capture_view_state()
        if state.row_key and state.volume_id:
            self.write("view", state.volume_id, state, opened=state.row_key in self.tab.review_list.opened)

    def save_opened(self, key: str, opened: bool) -> None:
        self.local_opened[key] = opened
        self.local_opened = {key: value for key, value in self.local_opened.items()
                             if key in self.tab.review_list.rows}
        self.tab.history.opened_revision[key] = self.tab.history.opened_revision.get(key, 0) + 1
        row = self.tab.review_list.rows.get(key)
        source = next((source for source in self.tab.payload.sources if row is not None and source.id == row.tomogram.source_id), None)
        if row is not None and source is not None:
            self.tab.review_state.opened(row.tomogram, source, opened)
        if row is not None and row.tomogram.volumes:
            volume = row.tomogram.volumes[0]
            self.write("opened", key, TomogramViewState(key, volume.id,
                volume_signature=(volume.fingerprint.size, volume.fingerprint.mtime_ns)), opened=opened)

    def offer(self) -> None:
        self.tab.history.load_scope()

    def apply_history(self, snapshot, revisions: dict[str, int], choice: int) -> None:
        if choice == self.choice_revision:
            self.offered_state = snapshot.offered_state
            self.resume_ordering_notice = snapshot.ordering_notice
            self.resume_label, self.resume_reason = "Resume saved view", ""
            if self.offered_state is not None:
                self.resume_label, self.resume_reason = self.source_problem(self.offered_state)
        sources = {source.id: source for source in self.tab.payload.sources}
        updates = dict(snapshot.opened)
        for key, row in self.tab.review_list.rows.items():
            # A missing record is a verified unopened row, not a failed read.
            opened = self.local_opened.get(key, key in self.tab.review_list.opened
                if revisions.get(key, 0) != self.tab.history.opened_revision.get(key, 0) else updates.get(key, False))
            self.tab.review_state.opened(row.tomogram, sources[row.tomogram.source_id], opened)
            if opened:
                self.tab.review_list.opened.add(key)
            else:
                self.tab.review_list.opened.discard(key)
        self.sync_resume_controls()
        self.tab.review_list.rebuild()
        self.tab.update_commands()
        self.tab.review_metadata_changed.emit()

    def resume(self) -> None:
        state = self.resume_state
        if state is None:
            return
        # Recheck only this explicitly loaded source on the user's Resume action.
        # A changed/missing file must not change the currently inspected row.
        self.resume_label, self.resume_reason = self.source_problem(state, check_disk=True)
        if self.resume_reason:
            self.sync_resume_controls()
            return
        self.offered_state = None
        self.choice_revision += 1
        self.sync_resume_controls()
        self.tab.restore_view_state(state, ordering_notice=self.resume_ordering_notice)

    def keep_current(self) -> None:
        self.choice_revision += 1
        self.history_loading = False
        self.history_error = ""
        self.resume_reason = ""
        self.offered_state = None
        self.sync_resume_controls()
        self.save_view()

    def source_problem(self, state: TomogramViewState, *, check_disk: bool = False) -> tuple[str, str]:
        row = self.tab.review_list.rows.get(state.row_key)
        volume = next((v for v in review_versions(row.tomogram) if v.id == state.volume_id), None) if row else None
        if volume is None:
            return "Resume unavailable", "The saved source is not available in this scope. The current view was kept."
        detail = f"Saved view: {row.tomogram.name} · {volume.version}\n{volume.path}\n"
        if state.volume_signature is None:
            return "Resume unavailable", detail + "The saved source fingerprint is unavailable. The current view was kept."
        signature = volume.fingerprint
        if check_disk:
            try:
                signature = fingerprint(volume.path)
            except (OSError, ValueError):
                return "Resume: source unavailable", detail + "The source is missing or unreadable. The current view was kept."
        if state.volume_signature != (signature.size, signature.mtime_ns):
            return "Resume: source changed", detail + "The source size or modification time changed. The current view was kept."
        return "Resume saved view", ""

    def sync_resume_controls(self) -> None:
        # This surface stays separate from capture/render feedback. Repeated
        # scope offers update it in place, without posting messages or taking focus.
        visible = self.offered_state is not None or bool(self.history_error)
        self.tab.status.resume.setText(self.resume_label)
        self.tab.status.resume.setEnabled(not self.resume_reason)
        detail = self.resume_reason or "Restore the latest saved view for a source in this scope."
        if self.resume_ordering_notice:
            detail += "\n" + self.resume_ordering_notice
        self.tab.status.resume.setToolTip(detail)
        self.tab.status.resume.setAccessibleDescription(detail)
        self.tab.status.keep.setToolTip("Keep this view and allow it to replace the saved view. Source files are unchanged.")
        if not visible and (self.tab.status.resume.hasFocus() or self.tab.status.keep.hasFocus()):
            self.tab.canvas.setFocus(Qt.FocusReason.OtherFocusReason)
        self.tab.status.resume.setVisible(visible)
        self.tab.status.keep.setVisible(visible)
