"""Tag selection has its own delta; history paging never drops untouched links."""
from dataclasses import replace

from PySide6.QtCore import Qt, QSize, QTimer
from PySide6.QtWidgets import (QCheckBox, QDialog, QDialogButtonBox, QHBoxLayout, QLabel,
                               QListWidget, QListWidgetItem, QPushButton, QVBoxLayout, QWidget)

from tomography_session_browser.presentation.tomogram_capture_history import HistoryContext, decode_capture
from tomography_session_browser.services.tomogram_tags.codec import thaw
from tomography_session_browser.services.tomogram_tags.project import find_row, freeze_capture, link_capture, unlink_capture
from tomography_session_browser.services.volume.review_repository import ReviewRecord
from tomography_session_browser.domain.tomogram_tags import CaptureKey
from tomography_session_browser.ui.tomograms.capture_rows import CaptureRow
from tomography_session_browser.ui.tomograms.thumbnail_admission import CaptureThumbnailAdmission
from tomography_session_browser.ui.widgets.atlas_thumbnail import AtlasThumbnail


class CapturePicker(QDialog):
    def __init__(self, links, target, *, linked_only=False):
        super().__init__(links.owner.tab)
        self.links, self.target, self.tab = links, target, links.owner.tab
        self.linked_only = linked_only
        self.closed = False
        self.busy = False
        self.touched = {}
        self.candidates = {}
        self.cursor = None
        self.cursors = [None]
        self.next_cursor = None
        self.entries = []
        self.pictures = []
        self.setWindowTitle("Linked captures" if linked_only else "Link captures to tag")
        self.resize(660, 600)
        layout = QVBoxLayout(self)
        self.notice = QLabel("Already linked views are kept even when their history or exported image is missing.", self)
        self.notice.setWordWrap(True)
        layout.addWidget(self.notice)
        self.list = QListWidget(self)
        layout.addWidget(self.list, 1)
        self.thumbnails = CaptureThumbnailAdmission(self, self.visible_thumbnails, self.list.viewport())
        self.list.verticalScrollBar().valueChanged.connect(self.thumbnails.schedule)
        pager = QHBoxLayout()
        self.previous = QPushButton("Previous", self)
        self.next = QPushButton("Next", self)
        self.previous.clicked.connect(self.back)
        self.next.clicked.connect(self.forward)
        pager.addWidget(self.previous)
        pager.addStretch(1)
        pager.addWidget(self.next)
        layout.addLayout(pager)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel, self)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Done")
        buttons.accepted.connect(self.commit)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.buttons = buttons
        self.finished.connect(self.retire)
        self.refresh()

    def visible_thumbnails(self):
        viewport = self.list.viewport().rect()
        return tuple(picture for item, picture in self.pictures if viewport.intersects(self.list.visualItemRect(item)))

    def retire(self, *_args):
        self.closed = True
        self.thumbnails.stop()

    def refresh(self):
        if self.busy or self.closed or self.target.session.project is None:
            return
        row = find_row(self.target.session.project, self.target.ref)
        retained_keys = {l.capture_key for l in row.links if l.tag_id == self.target.tag_id}
        retained = tuple(c for c in row.captures if c.key in retained_keys)
        context = HistoryContext(tuple(r for r in self.tab.payload.rows if r.tomogram.id == self.target.row_key), self.tab.protected_roots())
        extras = tuple(e.metadata() for e in self.tab.history.unrecorded.values())
        repository, writer, cursor = self.tab.persistence.repository, self.tab.persistence.writer, self.cursor
        self.busy = True
        self.previous.setEnabled(False)
        self.next.setEnabled(False)
        def work():
            present = set()
            def accept(record):
                key = CaptureKey(*record.order)
                if key in retained_keys:
                    present.add(key)
                return True
            page = writer.read(lambda: repository.page("capture", context.identities, before=cursor, accept=accept, extra=extras))
            return page, frozenset(present)
        def finished(future):
            self.busy = False
            if self.closed:
                return
            try:
                page, present = future.result()
                self.fill(retained, page, present, context)
            except Exception as exc:
                self.notice.setText("Capture history unavailable: " + str(exc))
        self.links.owner.projects.submit(work, finished)

    def fill(self, retained, page, present, context):
        self.thumbnails.set_clients(())
        self.list.clear()
        self.entries, self.pictures = [], []
        values = {c.key: c for c in retained} if self.cursor is None else {}
        if not self.linked_only:
            for record in page.records:
                try:
                    saved = freeze_capture(record)
                    values.setdefault(saved.key, saved)
                except ValueError:
                    continue
        initial = {l.capture_key for l in find_row(self.target.session.project, self.target.ref).links if l.tag_id == self.target.tag_id}
        live_page = {CaptureKey(*r.order) for r in page.records}
        for key, saved in values.items():
            self.candidates[key] = saved
            entry = decode_capture(ReviewRecord(key.history_id, (key.created_ns, key.history_id), thaw(saved.record)), context)
            widget = QWidget(self.list)
            line = QHBoxLayout(widget)
            line.setContentsMargins(0, 0, 0, 0)
            check = QCheckBox(widget)
            check.setProperty("captureKey", key)
            check.setAccessibleName("Link this captured view")
            check.setChecked(self.touched.get(key, key in initial))
            check.setEnabled(self.target.session.editable)
            check.toggled.connect(lambda checked, k=key: self.touched.update({k: checked}))
            line.addWidget(check)
            if entry:
                panel = CaptureRow(entry, widget)
                panel.remove_button.setToolTip("Unlink this retained view; keep image and history")
                panel.remove_button.clicked.disconnect()
                panel.remove_button.clicked.connect(lambda _, c=check: c.setChecked(False))
                panel.remove_button.setEnabled(self.target.session.editable)
                panel.set_restore_reason(self.tab.captures.restore_reason(entry))
                panel.activated.connect(lambda _, e=entry: self.tab.captures.restore(e))
                panel.shown.connect(lambda _, e=entry: self.tab.captures.show_in_folder(e))
                verified = key in present or key in live_page
                if not verified:
                    replacement = AtlasThumbnail(None, QSize(40, 40), placeholder="—", auto_request=False, parent=panel)
                    panel.row.replaceWidget(panel.thumbnail, replacement)
                    panel.thumbnail.deleteLater()
                    panel.thumbnail = replacement
                    panel.detail.setText("Not in history · image identity cannot be verified")
                else:
                    self.pictures.append((None, panel.thumbnail))
                line.addWidget(panel, 1)
            else:
                label = QLabel("Saved view unavailable in the loaded sources · untick to unlink", widget)
                label.setWordWrap(True)
                line.addWidget(label, 1)
            item = QListWidgetItem(self.list)
            item.setSizeHint(widget.sizeHint())
            self.list.setItemWidget(item, widget)
            if entry and self.pictures and self.pictures[-1][0] is None:
                self.pictures[-1] = item, self.pictures[-1][1]
        self.thumbnails.set_clients(tuple(p for _, p in self.pictures))
        self.next_cursor = page.next_cursor
        self.previous.setEnabled(len(self.cursors) > 1)
        self.next.setEnabled(page.next_cursor is not None and not self.linked_only)

    def back(self):
        if len(self.cursors) > 1:
            self.cursors.pop()
            self.cursor = self.cursors[-1]
            self.refresh()

    def forward(self):
        if self.next_cursor is not None:
            self.cursor = self.next_cursor
            self.cursors.append(self.cursor)
            self.refresh()

    def commit(self):
        if not self.touched:
            self.accept()
            return
        target, session = self.target, self.target.session
        if self.links.owner.projects.session_for(target.folder) is not session or not session.editable or session.project is None:
            self.notice.setText("The project changed or was closed. Reopen the picker.")
            return
        project = session.project
        try:
            if not any(a.tag_id == target.tag_id for a in find_row(project, target.ref).tags):
                raise ValueError("The tag was removed. Reopen the picker.")
            for key, selected in self.touched.items():
                project = link_capture(project, target.ref, target.tag_id, self.candidates[key]) if selected else unlink_capture(project, target.ref, target.tag_id, key)
            session.edit(project)
        except ValueError as exc:
            self.notice.setText(str(exc))
            return
        self.links.owner.changed()
        self.accept()
