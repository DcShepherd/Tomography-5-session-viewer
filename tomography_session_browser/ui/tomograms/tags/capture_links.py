"""Consume frozen snapshot targets once, independently of history retry."""
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtWidgets import QMenu
from tomography_session_browser.presentation.messages import ItemAccepted, ItemChoice, MessageContent
from tomography_session_browser.ui.widgets import message_sheet

from tomography_session_browser.domain.tomogram_tags import TomogramRef
from tomography_session_browser.services.tomogram_tags.binding import bind_row
from tomography_session_browser.services.tomogram_tags.project import find_row, freeze_capture, link_capture
from tomography_session_browser.services.tomogram_tags.session import TagSession


@dataclass(frozen=True, slots=True)
class LinkTarget:
    session: TagSession
    folder: Path
    ref: TomogramRef
    row_key: str
    tag_id: str


class CaptureLinks:
    def __init__(self, owner):
        self.owner = owner
        self.requests = {}
        self.jobs = {}
        captures = owner.tab.captures
        captures.admitted, captures.cancelled = self.admitted, self.cancelled
        captures.published, captures.dismissed = self.published, self.dismissed
        owner.projects.producer_drains.append(self.finalise)

    def busy(self, session):
        return any(t.session is session for _, t in self.requests.values()) or any(t.session is session for t in self.jobs.values())

    def finalise(self):
        """Called before project lanes retire, without touching widget state."""
        captures = self.owner.tab.captures
        captures.pool.waitForDone()
        from tomography_session_browser.presentation.tomogram_capture_history import CaptureEntry
        for job, outcome in captures.completions.take():
            target = self.jobs.pop(job.created_ns, None)
            if target is not None and job.saved_record is None and not isinstance(outcome, str):
                self.apply(target, CaptureEntry(outcome.record, job.state, created_ns=job.created_ns))

    def target(self, tag_id):
        target = self.owner.target()
        if not target or not target.session or not target.session.project:
            raise ValueError("Open a tag project first.")
        binding = bind_row(target.session.project, target.folder, target.row)
        if binding.ref is None or not any(a.tag_id == tag_id for a in find_row(target.session.project, binding.ref).tags):
            raise ValueError("The tag is no longer on this tomogram.")
        return LinkTarget(target.session, target.folder.path, binding.ref, target.row.id, tag_id)

    def snapshot(self, tag_id):
        try:
            target = self.target(tag_id)
            if not target.session.editable or self.owner.projects.stopping:
                raise ValueError("This tag project is not editable.")
            intent = self.owner.tab.captures.intent()
            self.requests[id(intent)] = intent, target
            self.owner.tab.captures.snapshot(intent)
        except ValueError as exc:
            self.owner.tab.status.message.setText(str(exc))

    def admitted(self, intent, job):
        value = self.requests.pop(id(intent), None)
        if value and value[0] is intent:
            self.jobs[job.created_ns] = value[1]

    def cancelled(self, intent):
        value = self.requests.get(id(intent))
        if value and value[0] is intent:
            self.requests.pop(id(intent))

    def dismissed(self, job):
        self.jobs.pop(job.created_ns, None)

    def apply(self, target, entry):
        session = target.session
        if self.owner.projects.session_for(target.folder) is not session or not session.editable or session.project is None:
            return "Snapshot saved; not linked: the project was closed."
        try:
            project = link_capture(session.project, target.ref, target.tag_id, freeze_capture(entry.metadata()))
            session.edit(project)
        except ValueError:
            return "Snapshot saved; not linked: the tag was removed."
        return ""

    def published(self, job, entry):
        target = self.jobs.pop(job.created_ns, None)
        if target is None:
            return
        reason = self.apply(target, entry)
        self.owner.changed()
        if reason:
            self.owner.tab.status.message.setText(reason)

    def show_menu(self, tag_id):
        menu = QMenu(self.owner.panel)
        menu.addAction("Linked captures…", lambda: self.picker(tag_id, linked_only=True))
        menu.addAction("Link captures…", lambda: self.picker(tag_id)).setEnabled(self.owner.editable())
        menu.addAction("Snapshot and link", lambda: self.snapshot(tag_id)).setEnabled(self.owner.editable())
        menu.exec(self.owner.panel.mapToGlobal(self.owner.panel.rect().center()))

    def picker(self, tag_id, *, linked_only=False):
        from tomography_session_browser.ui.tomograms.tags.capture_picker import CapturePicker
        try:
            target = self.target(tag_id)
            CapturePicker(self, target, linked_only=linked_only).exec()
        except ValueError as exc:
            self.owner.tab.status.message.setText(str(exc))

    def link_existing(self, entry):
        target = self.owner.target()
        if not target or target.row.id != entry.record.request.row_key or not target.session or not target.session.project:
            self.owner.tab.status.message.setText("Select this capture's tomogram and open its tag project first.")
            return
        binding = bind_row(target.session.project, target.folder, target.row)
        if binding.ref is None:
            return
        row = find_row(target.session.project, binding.ref)
        names = {t.id: t.name for t in target.session.project.catalogue}
        labels = [names[t.tag_id] for t in row.tags]
        if not labels:
            self.owner.tab.status.message.setText("Add a tag to this tomogram before linking its capture.")
            return
        answer = message_sheet.choose_item(self.owner.tab,
            MessageContent("Link capture to tag", "The capture is linked to the chosen observation. Its image stays unchanged."),
            tuple(ItemChoice(t.tag_id, names[t.tag_id]) for t in row.tags), label="Tag", accept_label="Link capture")
        if isinstance(answer, ItemAccepted):
            reason = self.apply(self.target(answer.item_id), entry)
            self.owner.changed()
            if reason:
                self.owner.tab.status.message.setText(reason)
