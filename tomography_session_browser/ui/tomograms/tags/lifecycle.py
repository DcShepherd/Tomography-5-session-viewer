"""Cancellable close preflight, before any widgets or project lanes retire."""
from time import monotonic

from PySide6.QtCore import QTimer, Qt
from PySide6.QtWidgets import QProgressDialog
from tomography_session_browser.presentation.tomogram_tag_messages import RecoveryAction, SaveProjectAs
from tomography_session_browser.services.tomogram_tags.binding import ProjectKey
from tomography_session_browser.ui.tomograms.tags import recovery_dialog

#: A save that finishes sooner closes the window without a dialog flashing first.
PROGRESS_DELAY_S = 0.4


class TagClosePreflight:
    def __init__(self, owner):
        self.owner = owner
        self.active = False
        self.approved = False
        self.discard = set()
        self.progress = None
        self.timer = QTimer(owner)
        self.timer.setInterval(50)
        self.timer.timeout.connect(self.tick)

    def request(self, close) -> bool:
        projects = self.owner.projects
        if self.approved or (not projects.sessions and not self.owner.capture_links.jobs and not self.owner.capture_links.requests):
            return True
        if self.active:
            return False
        self.active, self.close, self.started = True, close, monotonic()
        self.progress = None
        projects.stopping = True
        self.owner.tab.captures.accepting = False
        self.owner.tab.captures.cancel_deferred()
        self.timer.start()
        return False

    def show_progress(self):
        if self.progress is None:
            self.progress = QProgressDialog("Waiting for snapshots and tag projects to finish saving…", "Cancel close", 0, 0, self.owner.tab.window())
            self.progress.setWindowTitle("Save tag projects before closing")
            self.progress.setWindowModality(Qt.WindowModality.WindowModal)
            self.progress.setMinimumDuration(0)
            self.progress.canceled.connect(self.cancel)
            self.progress.show()
        return self.progress

    def hide_progress(self):
        if self.progress is not None:
            self.progress.hide()

    def keep_waiting(self):
        waited = monotonic() - self.started
        if waited > PROGRESS_DELAY_S:
            self.show_progress()
        if waited > 10:
            self.show_progress().setLabelText("Still waiting for storage. You can cancel close; the window and unsaved changes stay available.")

    def cancel(self):
        if not self.active:
            return
        self.active = False
        self.timer.stop()
        self.owner.projects.stopping = False
        self.owner.tab.captures.accepting = True
        self.discard.clear()
        self.hide_progress()

    def tick(self):
        projects = self.owner.projects
        projects.poll()
        if self.owner.tab.captures.pending or projects.tasks or any(s.writer and not s.writer.flush(0) for s in projects.sessions.values()):
            self.keep_waiting()
            return
        for session in projects.sessions.values():
            session.poll()
        unresolved = [s for s in projects.sessions.values() if s.dirty and s not in self.discard and s not in projects.relocations.values()]
        if unresolved or projects.failed_bindings:
            self.timer.stop()
            decision = recovery_dialog.choose_recovery(self.show_progress(), recovery_dialog.recovery_snapshot(projects, unresolved))
            if decision == RecoveryAction.RETRY_ALL:
                self.owner.retry()
            elif isinstance(decision, SaveProjectAs):
                session = projects.sessions.get(decision.project.path)
                if session is None or session.project is None or ProjectKey.at(session.store.path, session.project) != decision.project:
                    self.owner.tab.status.message.setText("The project changed. Review its recovery actions again.")
                    self.cancel()
                    return
                before = set(projects.relocations)
                self.owner.save_as(session)
                if set(projects.relocations) == before:
                    self.cancel()
                    return
            elif decision == RecoveryAction.CLOSE_UNRESOLVED:
                self.discard.update(s for s in projects.sessions.values() if s.dirty)
                projects.failed_bindings.clear()
            else:
                self.cancel()
                return
            if self.active:
                self.timer.start()
            return
        if projects.pending_bindings or any(s.dirty and s not in self.discard for s in projects.sessions.values()):
            self.keep_waiting()
            return
        self.active, self.approved = False, True
        self.timer.stop()
        self.hide_progress()
        # Every filesystem operation is already settled. Only now retire the
        # idle lanes; cancellation before this point never closes one of them.
        for session in projects.sessions.values():
            session.close(None, discard=session in self.discard)
        QTimer.singleShot(0, self.owner, self.close)
