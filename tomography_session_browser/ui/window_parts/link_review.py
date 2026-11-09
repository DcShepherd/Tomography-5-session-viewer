"""One QWidget owns background decision I/O, canonical publication and UI actions."""
from tomography_session_browser.ui.widgets.link_button import link_button
import logging
from PySide6.QtCore import QThreadPool, QTimer, Slot
from PySide6.QtWidgets import QWidget

from tomography_session_browser.domain.link_confirmations import ConfirmationSnapshot
from tomography_session_browser.presentation.tab_keys import TabKey
from tomography_session_browser.presentation.tomogram_link_review import name_link_rows
from tomography_session_browser.services.link_confirmations import LinkConfirmationStore, verify_confirmation_sources
from tomography_session_browser.ui.loading.tasks import BackgroundTask, TaskSignals
from tomography_session_browser.ui.shell.chrome import TAB_LABELS
from tomography_session_browser.ui.tomograms.link_review import NameLinkReview


class LinkReviewController(QWidget):
    def __init__(self, window):
        super().__init__(window)
        self.hide()
        self.window = window
        self.snapshot = ConfirmationSnapshot()
        self.store = LinkConfirmationStore()
        self.pending = None
        self.busy = False
        self.dialog = None
        self.open_source = None
        self.signals = TaskSignals()
        self.signals.finished.connect(self.finished)
        self.signals.failed.connect(self.failed)
        tab = window.ui.tomogram_tab
        self.review = link_button('Review name-only links', tab.status)
        self.review.clicked.connect(lambda: self.open(tab.current_object().source_id if tab.current_object() else ''))
        tab.status.add_action(self.review)
        self.unlink = link_button('Unlink confirmed name', window.parts.context_panel.widget)
        self.unlink.clicked.connect(self.unlink_current)
        window.parts.context_panel.widget.layout().insertWidget(1, self.unlink)
        self.unlink.hide()
        tab.item_selected.connect(lambda _: QTimer.singleShot(0, self, self.sync))
        window.ui.context_panel.title_changed.connect(lambda _: QTimer.singleShot(0, self, self.sync))
        window.ui.tabs.currentChanged.connect(self.sync)
        window.ui.session_dashboard.name_links_requested.connect(self.open)
        QTimer.singleShot(0, self, self.reload)

    def run(self, work, action):
        if self.busy:
            return
        self.busy = True
        self.sync()
        task = BackgroundTask(work, context=action, signals=self.signals,
            logger=logging.getLogger(__name__), failure_log='Name-link decision storage failed')
        QThreadPool.globalInstance().start(task)

    def reload(self):
        self.run(self.store.read, 'read')

    @Slot(object, object)
    def finished(self, action, snapshot):
        self.busy = False
        if self.window.parts.loading.closed:
            return
        if snapshot.issue:
            self.failed(action, snapshot.issue)
            return
        self.pending = snapshot
        if action == 'write' and self.dialog is not None:
            self.dialog.accept()
        self.adopt()

    @Slot(object, str)
    def failed(self, action, message):
        self.busy = False
        self.open_source = None
        if self.window.parts.loading.closed:
            return
        self.sync()
        self.window.statusBar().showMessage(message)
        if self.dialog is not None and self.dialog.isVisible():
            self.dialog.set_busy(False)
            self.dialog.feedback.setText(message + ' Close and reopen Review to reload saved decisions.')

    def adopt(self):
        parts, state = self.window.parts, self.window.state
        if self.pending is None or parts.loading.closed:
            return
        if state.loading.loading_task_active or parts.viewers.scope_preparation.running is not None:
            QTimer.singleShot(30, self, self.adopt)
            return
        snapshot, self.pending = self.pending, None
        changed = snapshot.records != self.snapshot.records
        self.snapshot = snapshot
        if changed and state.review.workspace.has_sources:
            parts.viewers.capture_viewer_states()
            state.review.workspace = state.review.workspace.with_link_confirmations(snapshot.records)
            state.review.scope = state.review.workspace.rebind_scope(state.review.scope)
            parts.history.prune_history()
            parts.dashboard_host.invalidate()
            parts.scopes.populate_tree()
            parts.viewers.render_viewer_tabs()
            parts.dashboard.render_session_summary(defer_heavy_cards=False)
            parts.context.refresh_context_header()
        self.sync()
        if self.open_source is not None:
            source_id, self.open_source = self.open_source, None
            self.show_review(source_id)

    def rows(self, source_id=''):
        return name_link_rows(self.window.workspace, source_id)

    def open(self, source_id=''):
        if self.busy or self.window.state.loading.loading_task_active:
            self.window.statusBar().showMessage('Wait for source/decision loading before reviewing name links.')
            return
        self.open_source = source_id
        self.run(self.store.read, 'review')

    def show_review(self, source_id):
        if self.dialog is not None:
            self.dialog.close()
        self.dialog = NameLinkReview(self.rows(source_id), self.window)
        self.dialog.decisions_requested.connect(self.submit)
        self.dialog.show()

    def submit(self, changes):
        if self.busy or self.window.state.loading.loading_task_active:
            message = 'Wait for source/decision loading, then review the current pairs before saving.'
            self.window.statusBar().showMessage(message)
            if self.dialog is not None and self.dialog.isVisible():
                self.dialog.set_busy(False)
                self.dialog.feedback.setText(message)
            return
        current = {(row.source_id, row.tomogram_id): row for row in self.rows()}
        if any(current.get((record.source_id, record.tomogram_id)) is None or
               current[(record.source_id, record.tomogram_id)].signature != record.signature or
               current[(record.source_id, record.tomogram_id)].target_key != record.target_key for record in changes):
            self.failed('write', 'The proposed pairs changed. Reopen Review; no decisions were saved.')
            return
        workspace = self.window.workspace
        protected = workspace.source_roots
        snapshot, store = self.snapshot, self.store
        confirmations = tuple(record for record in changes if record.decision == 'confirmed')
        items = tuple(item for source in workspace.processing_sources for item in source.tomograms
                      if any(record.tomogram_id == item.id for record in confirmations))
        targets = tuple(target for target in workspace.acquisition_index.series
                        if any(record.target_key == target.key for record in confirmations))
        def save():
            verify_confirmation_sources(items, targets)
            return store.write(snapshot, changes, protected)
        self.run(save, 'write')

    def sync(self, *_):
        workspace, ui = self.window.workspace, self.window.ui
        current = ui.tomogram_tab.current_object()
        rows = self.rows(current.source_id) if current is not None else ()
        count = sum(row.decision != 'confirmed' for row in rows)
        self.review.setText(f'{count} match by name only · Review' if count else f'{len(rows)} confirmed name links · Review')
        ui.tomogram_tab.status.show_action(self.review, bool(rows))
        self.review.setToolTip("Name-link decisions are being saved." if self.busy else "Review name-only parent links")
        self.unlink.setToolTip("Name-link decisions are being saved." if self.busy else "Remove this confirmed name-only parent link")
        self.review.setEnabled(not self.busy)
        self.unlink.setEnabled(not self.busy)
        value = ui.tomogram_tab.current_object() if ui.tabs.currentIndex() == TAB_LABELS.index(TabKey.TOMOGRAMS) else None
        link = workspace.tomogram_links.get(value.id) if value is not None else None
        self.unlink.setVisible(link is not None and link.step == 'E' and link.status == 'resolved'
                               and self.window.parts.context_panel.describes(value))

    def unlink_current(self):
        value = self.window.ui.tomogram_tab.current_object()
        rows = tuple(row for row in self.rows() if value is not None and row.tomogram_id == value.id)
        if len(rows) == 1 and rows[0].decision == 'confirmed':
            self.submit((rows[0].record('revoked'),))
