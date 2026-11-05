"""Selected-row edits compose the shared project service and the panel."""
from dataclasses import dataclass, replace
from pathlib import Path
import uuid

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QFileDialog, QMenu, QWidget

from tomography_session_browser.domain.identity import path_identity_key
from tomography_session_browser.domain.display_names import count_phrase
from tomography_session_browser.presentation.messages import ActionPrompt, ActionRole, ItemAccepted, ItemChoice, MessageAction, MessageContent
from tomography_session_browser.presentation.tomogram_tag_messages import ProjectRoute
from tomography_session_browser.ui.widgets import message_sheet
from tomography_session_browser.ui.widgets.literal_text import literal_mnemonic
from tomography_session_browser.domain.tomogram_tags import TagAssignment, TagSource
from tomography_session_browser.domain.tomograms import ProcessingSource, Tomogram, Volume
from tomography_session_browser.services.tomogram_tags.binding import ProjectKey, bind_row, reference, suggested_path
from tomography_session_browser.services.tomogram_tags.project import (
    assign, create_tag, find_row, new_project, set_complete, set_note, unassign, utc_now,
)
from tomography_session_browser.services.tomogram_tags.session import SaveStatus, TagSession
from tomography_session_browser.services.tomogram_tags.store import ProjectStatus
from tomography_session_browser.ui.window_parts.tag_projects import TagProjects
from tomography_session_browser.ui.tomograms.tags.panel import TagsPanel
from tomography_session_browser.ui.tomograms.tags.column import TagsColumn


@dataclass(frozen=True, slots=True)
class TagEditTarget:
    folder: ProcessingSource
    row: Tomogram
    volume: Volume
    session: TagSession | None


class TagOwner(QWidget):
    #: A CSV export's result, for a dialog that covers the panel's own notice.
    export_reported = Signal()

    def __init__(self, tab, projects: TagProjects | None = None):
        super().__init__(tab)
        self.hide()
        self.tab = tab
        self.owns_projects = projects is None
        self.projects = projects or TagProjects(self, tab.protected_roots)
        if self.owns_projects:
            tab.destroyed.connect(self.projects.dispose)
            self.projects.loaded_sources = lambda: tab.payload.sources
        self.export_feedback: dict[ProjectKey, MessageContent] = {}
        self.panel = TagsPanel(self)
        self.column = TagsColumn(self)
        self.panel.entry.commit = self.commit_entry
        self.panel.entry.dismissed.connect(self.column.dismiss)
        self.panel.complete_changed.connect(self.complete_changed)
        self.panel.note_changed.connect(self.note_changed)
        self.panel.note_cancelled.connect(lambda: self.note_changed(self.panel.original_note))
        self.panel.retry_requested.connect(self.retry)
        self.panel.save_as_requested.connect(self.save_project_as)
        self.projects.changed.connect(self.changed)
        tab.item_selected.connect(self.selection_changed)
        tab.metadata_changed.connect(self.selection_changed)
        tab.scope_changed.connect(self.scope_changed)
        from tomography_session_browser.ui.tomograms.tags.filter_field import TagFilterField
        self.filters = TagFilterField(self)
        from tomography_session_browser.ui.tomograms.tags.capture_links import CaptureLinks
        self.capture_links = CaptureLinks(self)
        from tomography_session_browser.ui.tomograms.tags.lifecycle import TagClosePreflight
        self.lifecycle = TagClosePreflight(self)
        self.last_target = None

    def target(self) -> TagEditTarget | None:
        row, volume = self.tab.current_object(), self.tab.volume()
        folder = next((s for s in self.tab.payload.sources if row and s.id == row.source_id), None)
        return TagEditTarget(folder, row, volume, self.projects.session_for(folder.path)) if folder and row and volume else None

    def editable(self) -> bool:
        target = self.target()
        return bool(target and target.session and target.session.editable and not self.projects.stopping)

    def scope_changed(self) -> None:
        self.projects.ensure(tuple(s.path for s in self.tab.payload.sources))
        self.selection_changed()

    def selection_changed(self, *_args) -> None:
        target = self.target()
        if target != self.last_target:
            self.panel.entry.cancel()
            self.panel.close_note()
            self.column.popup.hide()
        self.last_target = target
        self.render()

    def changed(self) -> None:
        self.filters.refresh()
        self.render()
        self.tab.review_metadata_changed.emit()

    def render(self) -> None:
        target = self.target()
        session = target.session if target else None
        project = session.project if session else None
        row, warning = None, ""
        if target and project:
            binding = bind_row(project, target.folder, target.row)
            row, warning = binding.saved, binding.reason
        if target:
            folder_key = path_identity_key(target.folder.path)
            if folder_key in self.projects.loading:
                warning = "Loading tag project…"
            elif session is None:
                warning = self.projects.issues.get(folder_key, "Create or open a project file to save tags outside your source folders.")
            elif session.status == ProjectStatus.CONFLICT and session.save_state.status == SaveStatus.FAILED:
                warning = "Conflict · This tag project changed outside the app. Reload it or save a separate copy."
            elif session.status not in (ProjectStatus.READY, ProjectStatus.RECOVERING):
                warning = session.reason or ("The project file is missing · Restore, Locate or Unbind from More." if session.status == ProjectStatus.MISSING else session.status.value)
            elif session.reason:
                warning = session.reason
            if session and session.suspended:
                warning = "Save As is pending. Use Cancel Save As in More to resume edits here."
            issue = self.projects.issues.get(folder_key, "")
            if issue and folder_key not in self.projects.failed_bindings and issue not in warning:
                warning = "\n".join(filter(None, (warning, issue)))
        else:
            warning = "Select a tomogram to edit its tags."
        can_create = bool(target and session is None and path_identity_key(target.folder.path) not in self.projects.loading and not self.projects.stopping)
        failed_folders = tuple(folder for folder, saved in self.projects.failed_bindings.values() if saved is session)
        notice = None
        if session and (session.save_state.status == SaveStatus.FAILED or failed_folders):
            details = "\n".join(filter(None, (session.save_state.reason, *(self.projects.issues.get(path_identity_key(f), str(f)) for f in failed_folders))))
            notice = MessageContent("Tag changes are not saved" if session.dirty else "Folder binding not saved",
                f"{session.store.path.name}: " + ("Your edits remain in this window." if session.dirty else "The project is saved; its folder binding still needs attention.") + " Retry all retries every affected project and binding.",
                (str(session.store.path), *(str(f) for f in failed_folders)), details)
        key = ProjectKey.at(session.store.path, project) if session and project else None
        previous_key = self.panel.project_key
        self.panel.render(target, project, row, warning, self.editable(), session.save_state if session else None, can_create,
            save_notice=notice, project_key=key)
        # One retained export result per open project; a retired project cannot publish feedback.
        self.export_feedback = {identity: content for identity, content in self.export_feedback.items()
            if (saved := self.projects.sessions.get(identity.path)) is not None
            and saved.project is not None and saved.project.project_id == identity.project_id}
        feedback = self.export_feedback.get(key) if key is not None else None
        if feedback is not None and key is not None:
            self.panel.export_notice.set_content(feedback)
            if previous_key != key:
                self.panel.reveal_notice(self.panel.export_notice, key)
        else:
            self.panel.export_notice.hide()
        self.column.button.setText(f"Tags · {len(row.tags) if row else 0}")
        total = len(self.projects.attention_sessions())
        verb = "needs" if total == 1 else "need"
        self.tab.status.tag_save.setText(f"{count_phrase(total, 'tag project')} {verb} attention · Review")
        self.tab.status.tag_save.setVisible(bool(total))

    def begin(self) -> None:
        if self.column.compact:
            self.column.open_popup()
        self.panel.entry.begin()

    def shutdown(self) -> None:
        self.capture_links.finalise()
        if self.owns_projects:
            self.projects.dispose()

    def commit_entry(self, draft, target: TagEditTarget) -> bool:
        if target is None or target != self.target() or draft.selected is None or self.projects.stopping:
            return False
        try:
            if target.session is None:
                return self.create_for_entry(draft, target)
            session = target.session
            if not session.editable or session.project is None:
                raise ValueError(session.reason or "This project is not editable.")
            project = session.project
            if draft.selected.tag_id is not None:
                tag = next((t for t in project.catalogue if t.id == draft.selected.tag_id), None)
                if tag is None:
                    raise ValueError("The selected tag was removed. Cancel this draft and choose a tag again.")
            else:
                project, tag = create_tag(project, draft.selected.name)
            binding = bind_row(project, target.folder, target.row)
            if binding.ref is None:
                raise ValueError(binding.reason)
            relative = target.volume.path.relative_to(target.folder.path).as_posix()
            assignment = TagAssignment(tag.id, draft.value(), relative, target.volume.version, utc_now())
            session.edit(assign(project, binding.ref, assignment))
            self.panel.pending_flash = tag.id
            self.changed()
            return True
        except (OSError, ValueError) as exc:
            self.tab.status.message.setText(str(exc))
            return False

    def create_for_entry(self, draft, target: TagEditTarget) -> bool:
        sessions = {self.projects.session_for(s.path) for s in self.tab.payload.sources}
        sessions = [s for s in sessions if s is not None and s.editable and s.project is not None]
        if sessions:
            choices = tuple(ItemChoice(ProjectKey.at(s.store.path, s.project), str(s.store.path)) for s in sessions)
            answer = message_sheet.choose_item(self.tab, MessageContent("Tag project for this folder",
                "Choose where this folder's tags will be saved. Source files stay unchanged."),
                (*choices, ItemChoice(ProjectRoute.CREATE, "Create a new project…")), label="Project file")
            if not isinstance(answer, ItemAccepted) or target != self.target():
                return False
            chosen = next((s for s in sessions if s.project and ProjectKey.at(s.store.path, s.project) == answer.item_id), None)
            if chosen is None and answer.item_id != ProjectRoute.CREATE:
                self.tab.status.message.setText("The chosen project is unavailable. Choose a project again.")
                return False
            if isinstance(answer.item_id, ProjectKey) and (chosen is None or not chosen.editable or self.projects.sessions.get(answer.item_id.path) is not chosen):
                self.tab.status.message.setText("The chosen project changed. Choose a project again.")
                return False
            if chosen is not None:
                from tomography_session_browser.services.tomogram_tags.binding import tag_source
                project = chosen.project
                source = tag_source(project, target.folder.path) or TagSource(uuid.uuid4().hex, target.folder.path, target.folder.name)
                if source not in project.sources:
                    project = replace(project, sources=(*project.sources, source))
                project, tag = create_tag(project, draft.selected.name)
                ref = reference(source, target.folder, target.row)
                chosen.edit(assign(project, ref, TagAssignment(tag.id, draft.value(), target.volume.path.relative_to(target.folder.path).as_posix(), target.volume.version, utc_now())))
                self.panel.pending_flash = tag.id
                self.projects.bind_after_save(target.folder.path, chosen)
                self.changed()
                return True
        project = new_project()
        source = TagSource(uuid.uuid4().hex, target.folder.path, target.folder.name)
        project = replace(project, sources=(source,))
        project, tag = create_tag(project, draft.selected.name)
        ref = reference(source, target.folder, target.row)
        project = assign(project, ref, TagAssignment(tag.id, draft.value(), target.volume.path.relative_to(target.folder.path).as_posix(), target.volume.version, utc_now()))
        folder = self.tab.settings.tomogram_snapshot_folder
        suggested = suggested_path(target.folder.path, Path(folder)) if folder else Path.home() / (target.folder.name + ".tags.json")
        if folder and not suggested.exists():
            path = str(suggested)
        else:
            path, _ = QFileDialog.getSaveFileName(self.tab, "Create tag project outside source folders", str(suggested), "Tag projects (*.tags.json)")
        if not path or target != self.target():
            return False
        self.panel.pending_flash = tag.id
        self.projects.open(Path(path), target.folder.path, create=True, project=project)
        return True

    def mutate_row(self, operation, target=None) -> None:
        target = target or self.target()
        if not target or not target.session or not target.session.editable or target.session.project is None or self.projects.stopping:
            return
        try:
            binding = bind_row(target.session.project, target.folder, target.row)
            if binding.ref is None:
                raise ValueError(binding.reason)
            target.session.edit(operation(target.session.project, binding.ref))
            self.changed()
        except (ValueError, OSError) as exc:
            self.tab.status.message.setText(str(exc))

    def complete_changed(self, value: bool) -> None:
        self.mutate_row(lambda p, ref: set_complete(p, ref, value))

    def note_changed(self, value: str) -> None:
        self.mutate_row(lambda p, ref: set_note(p, ref, value), self.panel.note_target)

    def remove_assignment(self, tag_id: str) -> None:
        self.mutate_row(lambda p, ref: unassign(p, ref, tag_id))

    def show_menu(self) -> None:
        target = self.target()
        loading = bool(target and path_identity_key(target.folder.path) in self.projects.loading)
        menu = QMenu(self.panel)
        if target and target.session:
            label = menu.addAction(literal_mnemonic(target.session.store.path.name))
            label.setToolTip(str(target.session.store.path))
            label.setEnabled(False)
            menu.addSeparator()
        menu.addAction("Add a note…", self.panel.open_note).setEnabled(self.editable())
        menu.addSeparator()
        menu.addAction("Create project…", lambda: self.project_dialog(create=True)).setEnabled(target is not None and not loading)
        menu.addAction("Open project…", self.project_dialog).setEnabled(target is not None and not loading)
        menu.addAction("Save As…", self.save_as).setEnabled(bool(target and target.session and target.session.project))
        menu.addAction("Cancel Save As", self.cancel_save_as).setEnabled(bool(target and target.session and target.session.suspended))
        menu.addAction("Reload project…", self.reload)
        menu.addAction("Restore from backup", self.restore_backup).setEnabled(bool(target and target.session and target.session.status == ProjectStatus.MISSING and target.session.has_backup))
        menu.addAction("Unbind this folder", self.unbind).setEnabled(target is not None and not loading)
        menu.addSeparator()
        menu.addAction("Manage tags…", self.manage).setEnabled(self.editable())
        menu.addAction("Export tags as CSV…", self.export).setEnabled(bool(target and target.session and target.session.project))
        menu.addAction("Retry saving", self.retry)
        menu.exec(self.panel.more.mapToGlobal(self.panel.more.rect().bottomLeft()))

    def project_dialog(self, *, create: bool = False) -> None:
        target = self.target()
        if target is None:
            return
        choose = QFileDialog.getSaveFileName if create else QFileDialog.getOpenFileName
        path, _ = choose(self.tab, "Create tag project" if create else "Open tag project", "", "Tag projects (*.json)")
        if path and self.resolve_old(target.session):
            if target.session and target.session.dirty:
                self.projects.reload_session(target.session)
                if path_identity_key(Path(path).resolve()) == path_identity_key(target.session.store.path.resolve()):
                    return
            self.projects.open(Path(path), target.folder.path, create=create)

    def resolve_old(self, session) -> bool:
        if session is None:
            return True
        if self.capture_links.busy(session) or session in self.projects.binding_tasks:
            self.tab.status.message.setText("Resolve this project's pending captures and folder binding first.")
            return False
        if session.writer and not session.writer.flush(0):
            self.tab.status.message.setText("The project is still saving. Try again when it finishes.")
            return False
        session.poll()
        if session.dirty:
            return message_sheet.choose_action(self.tab, ActionPrompt(
                MessageContent("Discard unsaved tag changes?", "The saved project and source files stay unchanged. Unsaved edits in this project will be lost.", (str(session.store.path),)),
                (MessageAction("cancel", "Keep working"), MessageAction("discard", "Discard edits and continue", ActionRole.DESTRUCTIVE)), "cancel", "cancel")) == "discard"
        return True

    def reload(self) -> None:
        target = self.target()
        if target and target.session and self.resolve_old(target.session):
            session = target.session
            session.close(None, discard=True)
            self.projects.sessions.pop(path_identity_key(session.store.path.resolve()), None)
            self.projects.open(session.store.path, target.folder.path)

    def unbind(self) -> None:
        target = self.target()
        if target and self.resolve_old(target.session):
            if target.session and target.session.dirty:
                self.projects.reload_session(target.session)
            self.projects.unbind(target.folder.path)

    def restore_backup(self) -> None:
        target = self.target()
        if target and target.session:
            session = target.session
            if self.resolve_old(session):
                session.close(None, discard=True)
                self.projects.sessions.pop(path_identity_key(session.store.path.resolve()), None)
                self.projects.open(session.store.path, target.folder.path, restore=True)

    def save_as(self, session=None) -> None:
        target = self.target()
        session = session if isinstance(session, TagSession) else target.session if target else None
        if session is None or session.project is None:
            return
        if self.capture_links.busy(session) or session in self.projects.binding_tasks or (not session.dirty and any(s is session for _, s in self.projects.pending_bindings.values())):
            self.tab.status.message.setText("Resolve this project's pending captures and folder binding before Save As.")
            return
        if session.writer and not session.writer.flush(0):
            self.tab.status.message.setText("The project is still saving. Try Save As when it finishes.")
            return
        path, _ = QFileDialog.getSaveFileName(self.tab, "Save tag project as", str(session.store.path), "Tag projects (*.tags.json)")
        if path:
            folder = next((s.path for s in session.project.sources if self.projects.session_for(s.path) is session), None)
            if folder is None and session.project.sources:
                folder = session.project.sources[0].path
            if folder is not None:
                self.projects.open(Path(path), folder, create=True, project=session.project, relocating=session)

    def save_project_as(self, key: ProjectKey) -> None:
        session = self.projects.sessions.get(key.path)
        if session is None or session.project is None or ProjectKey.at(session.store.path, session.project) != key:
            self.tab.status.message.setText("The project changed. Review its recovery actions again.")
            return
        self.save_as(session)

    def cancel_save_as(self) -> None:
        target = self.target()
        if target and target.session:
            self.projects.cancel_relocation(target.session)
            self.changed()

    def retry(self) -> None:
        self.projects.pending_bindings.update(self.projects.failed_bindings)
        self.projects.failed_bindings.clear()
        for session in self.projects.sessions.values():
            if session.editable and session.dirty:
                session.save()
        self.changed()

    def show_recovery(self) -> None:
        menu = QMenu(self.tab.status.tag_save)
        for session in self.projects.sessions.values():
            if not session.dirty and not any(s is session for _, s in self.projects.failed_bindings.values()):
                continue
            item = menu.addMenu(str(session.store.path))
            item.addAction("Retry saving", lambda _, s=session: (s.save(), self.changed())).setEnabled(session.editable)
            item.addAction("Save As…", lambda _, s=session: self.save_as(s)).setEnabled(session.project is not None)
            item.addAction("Retry folder bindings", self.retry)
            item.addAction("Reload and discard edits…", lambda _, s=session: self.recovery_reload(s))
        menu.exec(self.tab.status.tag_save.mapToGlobal(self.tab.status.tag_save.rect().bottomLeft()))

    def recovery_reload(self, session) -> None:
        if self.resolve_old(session):
            self.projects.reload_session(session)

    def manage(self) -> None:
        from tomography_session_browser.ui.tomograms.tags.manager import TagManager
        if self.editable():
            dialog = TagManager(self)
            try:
                dialog.exec()
            finally:
                dialog.deleteLater()

    def export(self, *, parent: QWidget | None = None) -> None:
        from tomography_session_browser.services.tomogram_tags.csv_export import export_csv
        target = self.target()
        if not target or not target.session or target.session.project is None:
            return
        project = target.session.project
        project_path = target.session.store.path
        project_key = ProjectKey.at(project_path, project)
        unsaved = target.session.dirty
        path, _ = QFileDialog.getSaveFileName(parent or self.tab, "Export tags as CSV", "tags.csv", "CSV files (*.csv)")
        if not path:
            return
        states = {}
        for folder in self.tab.payload.sources:
            for row in folder.tomograms:
                binding = bind_row(project, folder, row)
                if binding.ref:
                    states[(binding.ref.source, binding.ref.relative)] = ("Bound", binding.changed)
        roots = self.tab.protected_roots()
        def finished(future):
            if future.exception():
                content = MessageContent("The CSV was not saved", "Your tag project and edits are still available. Use Export tags as CSV to try another destination.",
                    (str(path), str(project_path)), str(future.exception()))
            else:
                content = MessageContent("CSV saved", future.result(), (str(path), str(project_path)))
            self.export_feedback[project_key] = content
            self.render()
            if project_key not in self.export_feedback:
                return
            self.panel.reveal_notice(self.panel.export_notice, project_key)
            self.export_reported.emit()
        self.projects.submit(lambda: export_csv(project, Path(path), roots, binding_status=states, unsaved=unsaved), finished)

    def dismiss_export(self) -> None:
        target = self.target()
        if target is not None and target.session is not None and target.session.project is not None:
            self.export_feedback.pop(ProjectKey.at(target.session.store.path, target.session.project), None)
        self.panel.export_notice.hide()

    def capture_menu(self, tag_id: str, position, linked: int) -> None:
        # Installed by T7 on the same owner; no second panel or capture pipeline.
        self.capture_links.show_menu(tag_id, position, linked)

    def camera_clicked(self, tag_id: str, button: QWidget, linked: int) -> None:
        self.capture_links.camera_clicked(tag_id, button, linked)
