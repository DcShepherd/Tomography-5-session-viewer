"""Bounded history pages shared by Browse captures and contact-sheet selection."""
from __future__ import annotations
from tomography_session_browser.domain.display_names import count_phrase
from tomography_session_browser.ui.widgets.link_button import link_button
from collections import Counter
from math import ceil

from PySide6.QtCore import Qt, Slot, QSignalBlocker
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import QCheckBox, QDialog, QDialogButtonBox, QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QPushButton, QScrollArea, QVBoxLayout, QWidget

from tomography_session_browser.presentation.tomogram_capture_history import CapturePage, capture_page
from tomography_session_browser.presentation.tomogram_report_scope import MAX_CONTACT_CAPTURES, normalise_contact_sheet
from tomography_session_browser.presentation.tomogram_versions import review_versions
from tomography_session_browser.services.volume.snapshot import SnapshotRequest
from tomography_session_browser.ui.tomograms.history_loading import HistoryJob, HistoryLoader
from tomography_session_browser.ui.widgets.elided_label import ElidedLabel
from tomography_session_browser.ui.tomograms.capture_rows import CaptureRow, RecoveryRow
from tomography_session_browser.ui.tomograms.thumbnail_admission import CaptureThumbnailAdmission


PAGE_SIZE = 50
GROUP_ROLE = Qt.ItemDataRole.UserRole + 1


class CaptureHistoryDialog(QDialog):
    def __init__(self, history=None, *, allowed_keys: frozenset[str] | None = None,
                 candidates: tuple[SnapshotRequest, ...] = (), parent=None) -> None:
        super().__init__(history.tab if history is not None else parent)
        self.history, self.allowed_keys = history, allowed_keys
        self.selecting = allowed_keys is not None
        self.candidates = candidates
        self.selected_requests: dict[str, SnapshotRequest] = {}
        self.queried_selection: frozenset[str] = frozenset()
        self.requests: dict[str, SnapshotRequest] = {}
        self.entries = {}
        self.row_widgets = {}
        self.checkboxes = {}
        self.group_checks = {}
        self.page_counts = {}
        self.recovery_token = None
        self.selected_id: str | None = None
        self.cursors: list[tuple[int, str] | None] = [None]
        self.next_cursor = None
        self.invalidated = False
        self.setWindowTitle("Tomogram contact sheet" if self.selecting else "Captures")
        self.resize(660, 620 if self.selecting else 520)
        layout = QVBoxLayout(self)
        label = QLabel("Choose the captures to put on one PDF." if self.selecting else
                       "Captures from the loaded sources in this scope. Removing history keeps the exported image.", self)
        label.setWordWrap(True)
        layout.addWidget(label)
        self.selection_bar = QWidget(self)
        selection_layout = QHBoxLayout(self.selection_bar)
        selection_layout.setContentsMargins(0, 0, 0, 0)
        self.select_all_button = link_button(parent=self)
        self.select_all_button.setText('Select all on this page')
        self.clear_button = link_button(parent=self)
        self.clear_button.setText('Clear')
        self.clear_button.setToolTip('Clear the selection across all pages')
        self.clear_button.setAccessibleName('Clear selected captures across all pages')
        for button in (self.select_all_button, self.clear_button):
            button.setAutoRaise(True)
            selection_layout.addWidget(button)
        self.select_all_button.clicked.connect(lambda: self.select_page())
        self.clear_button.clicked.connect(self.clear_selection)
        self.preview = QLabel(self)
        self.preview.setObjectName('toneText')
        selection_layout.addWidget(self.preview, 1, Qt.AlignmentFlag.AlignRight)
        self.selection_bar.setVisible(self.selecting)
        layout.addWidget(self.selection_bar)
        self.selection_notice = QLabel(self)
        self.selection_notice.setWordWrap(True)
        self.selection_notice.hide()
        layout.addWidget(self.selection_notice)
        self.recovery_area = QScrollArea(self)
        self.recovery_area.setWidgetResizable(True)
        self.recovery_area.setMaximumHeight(190)
        self.recovery_area.hide()
        layout.addWidget(self.recovery_area)
        self.cancel_export_button = QPushButton("Cancel contact sheet export", self)
        self.cancel_export_button.clicked.connect(lambda: self.history.tab.contact_sheet.cancel())
        self.cancel_export_button.hide()
        layout.addWidget(self.cancel_export_button)
        self.list = QListWidget(self)
        self.list.setObjectName("contactSheetCaptures")
        self.list.setAccessibleName("Captures to include in contact sheet" if self.selecting else "Capture history")
        self.thumbnails = CaptureThumbnailAdmission(self, self.visible_thumbnails, self.list.viewport())
        self.list.verticalScrollBar().valueChanged.connect(self.thumbnails.schedule)
        layout.addWidget(self.list, 1)
        pager = QHBoxLayout()
        self.previous_button = QPushButton("Previous page", self)
        self.next_button = QPushButton("Next page", self)
        self.refresh_button = QPushButton("Refresh", self)
        self.page_label = QLabel(self)
        for widget in (self.previous_button, self.next_button, self.refresh_button, self.page_label):
            pager.addWidget(widget)
        layout.addLayout(pager)
        self.previous_button.clicked.connect(self.previous_page)
        self.next_button.clicked.connect(self.next_page)
        self.refresh_button.clicked.connect(self.refresh_page)
        self.detail = ElidedLabel("", parent=self)
        self.detail.setObjectName("tomogramNote")
        self.detail.setVisible(not self.selecting)
        layout.addWidget(self.detail)
        if not self.selecting:
            for key, callback in (("Return", self.restore_current), ("Delete", self.remove_current)):
                shortcut = QShortcut(QKeySequence(key), self.list)
                shortcut.setProperty("helpBindingId", f"capture-history.{key}")
                shortcut.setWhatsThis("Restore the selected capture" if key == "Return" else "Remove capture history")
                shortcut.setContext(Qt.ShortcutContext.WidgetShortcut)
                shortcut.activated.connect(callback)
        controls = (QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel) if self.selecting else QDialogButtonBox.StandardButton.Close
        self.buttons = QDialogButtonBox(controls, self)
        if self.selecting:
            self.buttons.button(QDialogButtonBox.StandardButton.Save).setText("Export PDF…")
            self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)
        self.list.currentItemChanged.connect(self.current_changed)
        if self.selecting:
            toggle = QShortcut(QKeySequence("Space"), self.list)
            toggle.setProperty("helpBindingId", "capture-history.select")
            toggle.setWhatsThis("Select the capture for export")
            toggle.setContext(Qt.ShortcutContext.WidgetShortcut)
            toggle.activated.connect(self.toggle_current)
            self.list.itemActivated.connect(lambda _item: self.toggle_current())
        self.loader = None
        if history is not None:
            self.loader = HistoryLoader(history.pool)
            self.loader.signals.finished.connect(self._history_finished)
            self.loader.signals.failed.connect(self._history_failed)
            self.refresh_page()
        else:
            for widget in (self.previous_button, self.next_button, self.refresh_button, self.page_label):
                widget.hide()
            self.fill(tuple((str(i), capture, f"{i+1}. {capture.name}") for i, capture in enumerate(candidates)))
        self.refresh()

    def refresh_export(self) -> None:
        if self.history is None or self.selecting:
            return
        captures, export = self.history.tab.captures, self.history.tab.contact_sheet
        self.cancel_export_button.setVisible(export.pending is not None)
        self.cancel_export_button.setEnabled(not export.cancelled.is_set())
        token = tuple(id(value) for value in captures.failures), len(captures.pending)
        if token == self.recovery_token:
            return
        self.recovery_token = token
        self.recovery_area.setVisible(bool(captures.failures))
        content = QWidget(self.recovery_area)
        column = QVBoxLayout(content)
        for failure in captures.failures:
            row = RecoveryRow(failure, content)
            row.set_retry_reason(captures.queue_reason(failure.job))
            row.retry_requested.connect(captures.retry)
            row.folder_requested.connect(lambda value: captures.retry(value, choose_folder=True))
            row.dismissed.connect(captures.dismiss)
            column.addWidget(row)
        old = self.recovery_area.takeWidget()
        if old is not None:
            old.deleteLater()
        self.recovery_area.setWidget(content)

    def prepare_selection(self, allowed_keys: frozenset[str]) -> None:
        self.allowed_keys = allowed_keys
        self.invalidated = False
        self.selected_requests.clear()
        self.selection_notice.hide()
        self.cursors = [None]
        self.selected_id = None
        self.refresh_page()

    def scope_changed(self) -> None:
        if self.loader is not None:
            self.loader.cancel()
        if self.selecting:
            self.invalidated = True
            self.preview.setText("The review scope changed. Close and reopen contact-sheet selection.")
            self.refresh()
        else:
            self.cursors = [None]
            self.clear_rows()
            self.entries.clear()
            if self.isVisible():
                self.refresh_page()

    def refresh_page(self) -> None:
        if self.history is None or self.loader is None or self.invalidated:
            return
        context = self.history.context(self.allowed_keys if self.selecting else None)
        repository, extra = self.history.tab.persistence.repository, self.history.extras()
        writer = self.history.tab.persistence.writer
        before = self.cursors[-1]
        self.queried_selection = retain = frozenset(self.selected_requests)
        job = self.loader.request(lambda cancelled: writer.read(lambda: capture_page(repository, context, before=before,
                                 limit=PAGE_SIZE, extra=extra, retain_ids=retain, cancelled=cancelled)), stamp=self.history.stamp())
        if not self.selecting:
            self.history.begin_counts(job)
        self.page_label.setText("Loading captures…")
        self.previous_button.setEnabled(False)
        self.next_button.setEnabled(False)
        self.refresh_button.setEnabled(False)
        self.refresh()

    def next_page(self) -> None:
        if self.next_cursor is not None:
            self.cursors.append(self.next_cursor)
            self.refresh_page()

    def previous_page(self) -> None:
        if len(self.cursors) > 1:
            self.cursors.pop()
            self.refresh_page()

    @Slot(object, object)
    def _history_finished(self, job: HistoryJob, page: CapturePage | None) -> None:
        if self.loader is None or not self.loader.retire(job) or page is None or self.invalidated:
            return
        if self.history is not None and job.stamp != self.history.stamp():
            self.refresh_page()
            return
        self.entries = {entry.id: entry for entry in page.entries}
        self.page_counts = dict(page.counts)
        self.next_cursor = page.next_cursor
        removed = self.queried_selection.intersection(self.selected_requests) - page.retained_ids
        for identity in removed:
            self.selected_requests.pop(identity, None)
        if removed:
            self.selection_notice.setText(f'The PDF selection no longer includes {count_phrase(len(removed), "removed or unavailable capture")}.')
            self.selection_notice.show()
        if not self.selecting and self.history is not None:
            self.history.complete_counts(job, page, recent="merge" if self.cursors[-1] is None else "keep")
        self.fill(tuple((entry.id, entry.record.request, entry.record.path.name) for entry in page.entries))
        self.page_label.setText(f"Page {len(self.cursors)} · {len(page.entries)} shown · {count_phrase(page.total, 'capture')}")
        self.page_label.setToolTip('')
        self.previous_button.setEnabled(len(self.cursors) > 1)
        self.next_button.setEnabled(page.next_cursor is not None)
        self.refresh_button.setEnabled(True)
        self.refresh_button.setText('Refresh')
        multiple = len(self.cursors) > 1 or page.next_cursor is not None
        for widget in (self.previous_button, self.next_button):
            widget.setVisible(multiple)
        self.page_label.setVisible(multiple or not self.selecting)
        self.refresh_button.setVisible(not self.selecting)
        self.detail.setVisible(not self.selecting)
        self.refresh()

    @Slot(object, str)
    def _history_failed(self, job: HistoryJob, message: str) -> None:
        if self.loader is not None and self.loader.retire(job):
            if self.history is not None:
                if job.stamp != self.history.stamp():
                    self.refresh_page()
                    return
                if not self.selecting:
                    self.history.fail_counts(job, message)
            self.page_label.setText("History could not be loaded. Use Retry.")
            self.page_label.setToolTip(message)
            self.page_label.show()
            self.refresh_button.setText('Retry')
            self.refresh_button.setEnabled(True)
            self.refresh_button.show()
            self.refresh()

    def clear_rows(self) -> None:
        self.thumbnails.set_clients(())
        self.row_widgets.clear()
        self.checkboxes.clear()
        self.group_checks.clear()
        self.list.clear()

    def visible_thumbnails(self):
        viewport = self.list.viewport().rect()
        return tuple(self.row_widgets[identity].thumbnail for index in range(self.list.count())
                     if (identity := self.list.item(index).data(Qt.ItemDataRole.UserRole)) in self.row_widgets
                     and viewport.intersects(self.list.visualItemRect(self.list.item(index))))

    def fill(self, values: tuple[tuple[str, SnapshotRequest, str], ...]) -> None:
        if self.history is None:
            self.page_counts = Counter(request.row_key for _, request, _ in values)
        with QSignalBlocker(self.list):
            self.clear_rows()
            self.requests = {identity: request for identity, request, _ in values}
            displayed = Counter(request.row_key for _, request, _ in values)
            group = None
            for identity, request, title in values:
                if group != request.row_key:
                    group = request.row_key
                    count = self.page_counts.get(group)
                    heading = QListWidgetItem(f"{request.name} · "
                                              f"{count_phrase(count, 'capture') if count is not None else 'capture total unavailable'}")
                    heading.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable if self.selecting else Qt.ItemFlag.NoItemFlags)
                    heading.setData(GROUP_ROLE, group)
                    self.list.addItem(heading)
                    if self.selecting:
                        header = QWidget(self.list)
                        header_layout = QHBoxLayout(header)
                        header_layout.setContentsMargins(8, 4, 8, 4)
                        checkbox = QCheckBox(f'{request.name} · {displayed[group]} on this page', header)
                        header_layout.addWidget(checkbox)
                        checkbox.setTristate(True)
                        checkbox.setAccessibleName(f'Select captures of {request.name} on this page')
                        checkbox.clicked.connect(lambda checked, group=group: self.select_page(group, checked))
                        self.group_checks.setdefault(group, []).append(checkbox)
                        heading.setData(Qt.ItemDataRole.AccessibleTextRole, checkbox.text())
                        heading.setText('')
                        header.ensurePolished()
                        heading.setSizeHint(header.sizeHint())
                        self.list.setItemWidget(heading, header)
                recipe = request.recipe
                item = QListWidgetItem(f"{title} · {request.volume.version} · {recipe.axis} plane {recipe.plane + 1}")
                item.setData(Qt.ItemDataRole.UserRole, identity)
                detail = (f"{request.volume.path}\nCrop: {recipe.region}\nRotation XYZ: {recipe.rotation} degrees\n"
                          f"Requested averaging: {recipe.slices} slices\nLevels: {recipe.black} to {recipe.white}\n"
                          f"Lowpass sigma: {recipe.lowpass} cycles/pixel\nHighpass radius: {recipe.highpass} Fourier pixels\n"
                          f"Brightness: {recipe.brightness}; contrast: {recipe.contrast}; inverted: {recipe.invert}")
                item.setToolTip(detail)
                item.setData(Qt.ItemDataRole.AccessibleDescriptionRole, detail)
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsUserCheckable)
                self.list.addItem(item)
                entry = self.entries.get(identity)
                if entry is not None:
                    source = self.history.tab.review_list.rows[request.row_key].tomogram
                    row = CaptureRow(entry, self.list, show_version=len(review_versions(source)) > 1)
                    if self.selecting:
                        row.show_button.hide()
                        row.remove_button.hide()
                        row.restore_button.setAccessibleName("Select capture for contact sheet")
                        checkbox = self.selection_checkbox(identity, title, row)
                        row.row.insertWidget(0, checkbox)
                        row.activated.connect(lambda _identity, checkbox=checkbox: checkbox.toggle())
                        self.checkboxes[identity] = checkbox
                    else:
                        row.set_restore_reason(self.history.tab.captures.restore_reason(entry))
                        row.activated.connect(self.restore_entry)
                        row.shown.connect(self.show_entry)
                        row.removed.connect(self.remove_entry)
                    self.row_widgets[identity] = row
                    row.ensurePolished()
                    item.setSizeHint(row.sizeHint())
                    self.list.setItemWidget(item, row)
                elif self.selecting:
                    row = QWidget(self.list)
                    layout = QHBoxLayout(row)
                    checkbox = self.selection_checkbox(identity, title, row)
                    layout.addWidget(checkbox)
                    label = QLabel(item.text(), row)
                    label.setWordWrap(True)
                    layout.addWidget(label, 1)
                    item.setSizeHint(row.sizeHint())
                    self.list.setItemWidget(item, row)
                if identity == self.selected_id:
                    self.list.setCurrentItem(item)
        self.current_changed(self.list.currentItem())
        self.thumbnails.set_clients(tuple(row.thumbnail for row in self.row_widgets.values()))

    def current_changed(self, item=None, _previous=None) -> None:
        if item is not None:
            self.selected_id = item.data(Qt.ItemDataRole.UserRole)
        entry = self.entries.get(self.selected_id) if item is not None else None
        if entry is not None and self.history is not None:
            reason = self.history.tab.captures.restore_reason(entry)
            self.detail.setText(reason or str(entry.record.path))
            self.detail.setToolTip(str(entry.record.path))
        else:
            self.detail.setText("")

    def selection_checkbox(self, identity: str, title: str, parent: QWidget) -> QCheckBox:
        checkbox = QCheckBox(parent)
        checkbox.setAccessibleName(f"Include {title} in the contact sheet")
        checkbox.setChecked(identity in self.selected_requests)
        checkbox.toggled.connect(lambda checked: self.set_selected(identity, checked))
        self.checkboxes[identity] = checkbox
        return checkbox

    def toggle_current(self) -> None:
        item = self.list.currentItem()
        if item is not None:
            identity = item.data(Qt.ItemDataRole.UserRole)
            if identity in self.requests:
                self.set_selected(identity, identity not in self.selected_requests)
            elif (group := item.data(GROUP_ROLE)) is not None:
                identities = [key for key, request in self.requests.items() if request.row_key == group]
                self.select_page(group, not all(key in self.selected_requests for key in identities))

    def set_selected(self, identity: str, selected: bool) -> None:
        if selected:
            if identity not in self.requests:
                return
            if identity not in self.selected_requests and len(self.selected_requests) >= MAX_CONTACT_CAPTURES:
                self.selection_notice.setText('This capture was not added. Clear a selection before adding another.')
                self.selection_notice.show()
                self.sync_checks()
                return
            self.selected_requests[identity] = self.requests[identity]
        else:
            self.selected_requests.pop(identity, None)
        self.refresh()

    def select_page(self, group: str | None = None, selected: bool = True) -> None:
        omitted = 0
        for identity, request in self.requests.items():
            if group is not None and request.row_key != group:
                continue
            if not selected:
                self.selected_requests.pop(identity, None)
            elif identity not in self.selected_requests:
                if len(self.selected_requests) == MAX_CONTACT_CAPTURES:
                    omitted += 1
                else:
                    self.selected_requests[identity] = request
        self.selection_notice.setText(f'{omitted} captures on this page were not added: the PDF selection is full.')
        self.selection_notice.setVisible(bool(omitted))
        self.refresh()

    def clear_selection(self) -> None:
        self.selected_requests.clear()
        self.selection_notice.hide()
        self.refresh()

    def sync_checks(self) -> None:
        for identity, checkbox in self.checkboxes.items():
            with QSignalBlocker(checkbox):
                checkbox.setChecked(identity in self.selected_requests)
        for group, checkboxes in self.group_checks.items():
            values = [identity in self.selected_requests for identity, request in self.requests.items() if request.row_key == group]
            state = Qt.CheckState.Checked if all(values) else Qt.CheckState.PartiallyChecked if any(values) else Qt.CheckState.Unchecked
            for checkbox in checkboxes:
                with QSignalBlocker(checkbox):
                    checkbox.setCheckState(state)

    def selected_scope(self):
        values = tuple(self.selected_requests.values())
        return normalise_contact_sheet(values, self.allowed_keys or frozenset(), frozenset(range(len(values))))

    def refresh(self) -> None:
        self.refresh_export()
        self.sync_checks()
        if not self.selecting:
            return
        count = len(self.selected_requests)
        pages = ceil(count / 4)
        text = f'{count} selected · {pages} {"page" if pages == 1 else "pages"}, 4 to a page'
        if count == MAX_CONTACT_CAPTURES:
            text = f'{count} selected · the most one sheet holds'
        tone = 'warn' if count == MAX_CONTACT_CAPTURES else ''
        if self.preview.property('tone') != tone:
            self.preview.setProperty('tone', tone)
            self.preview.style().unpolish(self.preview)
            self.preview.style().polish(self.preview)
        reason = ("The review scope changed. Close and reopen contact-sheet selection." if self.invalidated else
                  "Capture history is loading." if self.loader and self.loader.loading else
                  "No captures are available on this page." if not self.requests else "")
        self.select_all_button.setToolTip(reason or "Select all captures on this page")
        self.select_all_button.setEnabled(not reason)
        self.clear_button.setToolTip("Clear the selection across all pages" if count else "No captures are selected.")
        self.clear_button.setEnabled(bool(count))
        try:
            self.selected_scope()
            enabled = not self.invalidated and not (self.loader and self.loader.loading)
        except ValueError:
            enabled = False
        self.preview.setText("The review scope changed. Close and reopen contact-sheet selection." if self.invalidated else text)
        self.buttons.button(QDialogButtonBox.StandardButton.Save).setEnabled(enabled)

    def restore_current(self) -> None:
        entry = self.entries.get(self.selected_id)
        if entry is not None:
            if not self.history.tab.captures.restore(entry):
                reason = self.history.tab.status.message.text_full()
                self.detail.setText(reason)
                self.row_widgets[entry.id].set_restore_reason(reason)

    def restore_entry(self, identity: str) -> None:
        self.selected_id = identity
        self.restore_current()

    def show_entry(self, identity: str) -> None:
        self.selected_id = identity
        self.show_current()

    def remove_entry(self, identity: str) -> None:
        self.selected_id = identity
        self.remove_current()

    def show_current(self) -> None:
        entry = self.entries.get(self.selected_id)
        if entry is not None:
            self.history.tab.captures.show_in_folder(entry)

    def remove_current(self) -> None:
        entry = self.entries.get(self.selected_id)
        if entry is not None and not self.history.tab.captures.forget(entry):
            self.detail.setText(self.history.tab.status.message.text_full())

    def done(self, result: int) -> None:
        self.thumbnails.stop()
        if self.loader is not None:
            job = self.loader.latest
            self.loader.cancel()
            if self.history is not None and not self.selecting:
                self.history.cancel_counts(job, replace=True)
        super().done(result)
