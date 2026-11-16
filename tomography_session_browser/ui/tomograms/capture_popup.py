"""The capture row menu: bounded row history, restore, recovery and Delete; transactions stay with Captures."""
from __future__ import annotations
from tomography_session_browser.domain.display_names import count_phrase
from tomography_session_browser.presentation.help_content import LocalBinding, InteractionKind
from PySide6.QtCore import Qt, Slot

from tomography_session_browser.presentation.tomogram_capture_history import CapturePage, capture_page
from tomography_session_browser.presentation.tomogram_versions import review_versions
from tomography_session_browser.ui.tomograms.capture_rows import CaptureRow, RecoveryRow
from tomography_session_browser.ui.tomograms.history_loading import HistoryJob, HistoryLoader
from tomography_session_browser.ui.widgets.row_menu import MenuRow, RowMenu
from tomography_session_browser.ui.tomograms.thumbnail_admission import CaptureThumbnailAdmission


class CapturePopup(RowMenu):
    def __init__(self, captures, row_key: str) -> None:
        super().__init__(captures.tab)
        self.captures, self.tab, self.row_key = captures, captures.tab, row_key
        self.entries = {}
        self.rows = []
        self.thumbnails = CaptureThumbnailAdmission(self, self.visible_thumbnails)
        self.loader = HistoryLoader(self.tab.history.pool)
        self.loader.signals.finished.connect(self._loaded)
        self.loader.signals.failed.connect(self._failed)
        self.setMaximumWidth(540)
        self.aboutToHide.connect(self.cancel)
        self.refresh_page()

    def cancel(self) -> None:
        self.loader.cancel()
        self.thumbnails.stop()

    def visible_thumbnails(self):
        return tuple(row.widget.thumbnail for action, row in self.row_actions.items() if isinstance(row.widget, CaptureRow)
                     and self.rect().intersects(self.actionGeometry(action)) and not row.widget.visibleRegion().isEmpty())

    def capture_row(self, panel: CaptureRow) -> None:
        def restore() -> None:
            if panel.restore_button.isEnabled():
                panel.restore_button.click()
        self.add_row(MenuRow(panel.entry.id, panel, lambda: panel.controls, restore))

    def recovery_row(self, recovery: RecoveryRow) -> None:
        def enter() -> None:
            # A recovery row has no default action: Enter moves into its buttons.
            action = next(action for action, row in self.row_actions.items() if row.widget is recovery)
            controls = self.usable_controls(self.row_actions[action])
            if controls:
                self.focus_entry(action, controls[0])
        self.add_row(MenuRow(recovery.failure.job, recovery, lambda: recovery.controls, enter))

    def row_key_pressed(self, key, action, row) -> bool:
        if key == Qt.Key.Key_Delete and row is not None and isinstance(row.widget, CaptureRow):
            button = row.widget.remove_button
            if button.isEnabled() and button.isVisible():
                button.click()
            return True
        return False

    def build(self, page=None, error="") -> None:
        self.thumbnails.set_clients(())
        self.rows.clear()
        anchor = self.reset()
        row = self.tab.review_list.rows.get(self.row_key)
        name = row.tomogram.name if row else "Tomogram outside this scope"
        counts = self.tab.history.count_snapshot
        total = page.total if page is not None else counts.rows.get(self.row_key) if counts else None
        self.addAction(f"{name} · {count_phrase(total, 'capture') if total is not None else '… captures'}").setEnabled(False)
        for failure in self.captures.failures:
            if failure.job.request.row_key != self.row_key:
                continue
            recovery = RecoveryRow(failure, self)
            recovery.set_retry_reason(self.captures.queue_reason(failure.job))
            recovery.retry_requested.connect(self.retry)
            recovery.folder_requested.connect(self.select_folder)
            recovery.dismissed.connect(self.dismiss)
            self.recovery_row(recovery)
        self.entries = {entry.id: entry for entry in page.entries} if page is not None else {}
        for entry in self.entries.values():
            panel = CaptureRow(entry, self, show_version=len(review_versions(row.tomogram)) > 1)
            panel.set_restore_reason(self.captures.restore_reason(entry))
            panel.activated.connect(self.restore)
            panel.shown.connect(self.show_folder)
            panel.removed.connect(self.remove)
            target = self.tab.tags.target()
            panel.tag_button.setVisible(self.tab.tags.editable() and target is not None and target.row.id == entry.record.request.row_key)
            panel.tag_requested.connect(lambda _, e=entry: self.tab.tags.capture_links.link_existing(e))
            self.rows.append(panel)
            self.capture_row(panel)
        if error:
            self.footer("refresh", "History unavailable · Refresh", self.refresh_page).setToolTip(error)
        elif page is None:
            self.note("Loading capture history…")
        elif not page.entries:
            self.note("No captures of this tomogram")
        elif page.total > len(page.entries):
            self.note(f"Newest {len(page.entries)} of {page.total} shown")
        self.addSeparator()
        self.footer("all", "All captures…", self.show_all)
        contact = self.footer("contact", "Contact sheet (PDF)…", self.contact_sheet)
        contact.setEnabled(bool(self.tab.review_list.ordered_keys) and self.tab.contact_sheet.pending is None)
        if self.tab.contact_sheet.pending is not None:
            action = self.footer("cancel", "Cancel contact sheet export", self.tab.contact_sheet.cancel)
            action.setEnabled(not self.tab.contact_sheet.cancelled.is_set())
        self.restore_anchor(anchor)
        self.thumbnails.set_clients(tuple(row.thumbnail for row in self.rows))

    def refresh_page(self) -> None:
        repository = self.tab.persistence.repository
        writer = self.tab.persistence.writer
        context = self.tab.history.context(frozenset({self.row_key}))
        extra = self.tab.history.extras()
        if not self.actions():
            self.build()
        self.loader.request(lambda cancelled: writer.read(lambda: capture_page(
            repository, context, limit=12, extra=extra, cancelled=cancelled)), stamp=self.tab.history.stamp())

    @Slot(object, object)
    def _loaded(self, job: HistoryJob, page: CapturePage | None) -> None:
        if not self.loader.retire(job) or page is None or self.tab.retired:
            return
        if job.stamp != self.tab.history.stamp():
            self.refresh_page()
            return
        self.build(page)

    @Slot(object, str)
    def _failed(self, job: HistoryJob, message: str) -> None:
        if self.loader.retire(job) and not self.tab.retired:
            self.build(error=message)

    def restore(self, identity: str) -> None:
        entry = self.entries.get(identity)
        self.close()
        if entry is not None:
            self.captures.restore(entry)

    def show_folder(self, identity: str) -> None:
        entry = self.entries.get(identity)
        self.close()
        if entry is not None:
            self.captures.show_in_folder(entry)

    def remove(self, identity: str) -> None:
        entry = self.entries.get(identity)
        if entry is not None:
            self.captures.forget(entry)

    def show_all(self) -> None:
        self.close()
        self.tab.history.show_browser()

    def contact_sheet(self) -> None:
        self.close()
        self.tab.contact_sheet.choose()

    def retry(self, failure) -> None:
        self.close()
        self.captures.retry(failure)

    def select_folder(self, failure) -> None:
        self.close()
        self.captures.retry(failure, choose_folder=True)

    def dismiss(self, failure) -> None:
        self.close()
        self.captures.dismiss(failure)


# Contextual keys handled by this module; consumed by the shared Help sheet.
# Traversal keys are shared by every row menu (``row_menu.LOCAL_KEYS``).
LOCAL_KEYS = (
    LocalBinding('tomograms.capture_popup.captures-menu-delete', 'Captures menu', InteractionKind.KEYBOARD, ('Delete',), 'Remove capture history'),
)
