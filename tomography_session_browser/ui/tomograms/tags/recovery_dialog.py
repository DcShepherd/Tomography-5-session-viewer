"""Derive recovery from authoritative projects; decisions name their exact target."""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QVBoxLayout, QWidget

from tomography_session_browser.domain.display_names import count_phrase
from tomography_session_browser.domain.identity import path_identity_key
from tomography_session_browser.presentation.messages import ActionPrompt, ActionRole, MessageAction
from tomography_session_browser.presentation.tomogram_tag_messages import (
    RecoveryAction, RecoveryProject, SaveProjectAs, recovery_close_label, recovery_content,
)
from tomography_session_browser.services.tomogram_tags.binding import ProjectKey
from tomography_session_browser.ui.widgets.elided_label import ElidedLabel
from tomography_session_browser.ui.widgets.link_button import link_button
from tomography_session_browser.ui.widgets.message_sheet import MessageSheet
from tomography_session_browser.ui.widgets.section_header import SectionHeader


def recovery_snapshot(projects, unresolved) -> tuple[RecoveryProject, ...]:
    sessions = {path_identity_key(s.store.path.resolve()): s for s in unresolved}
    bindings = {}
    for folder, session in projects.failed_bindings.values():
        key = path_identity_key(session.store.path.resolve())
        sessions[key] = session
        bindings.setdefault(key, []).append(folder)
    return tuple(RecoveryProject(ProjectKey.at(s.store.path, s.project) if s.project else ProjectKey(key, ""),
        s.store.path, s.dirty, tuple(bindings.get(key, ())), s.save_state.reason, s.project is not None)
        for key, s in sessions.items())


class RecoverySheet(MessageSheet[RecoveryAction]):
    def __init__(self, parent, projects: tuple[RecoveryProject, ...]):
        super().__init__(parent, ActionPrompt(recovery_content(projects), (
            MessageAction(RecoveryAction.KEEP_WORKING, "Keep working"), MessageAction(RecoveryAction.RETRY_ALL, "Retry all"),
            MessageAction(RecoveryAction.CLOSE_UNRESOLVED, recovery_close_label(projects), ActionRole.DESTRUCTIVE)),
            RecoveryAction.KEEP_WORKING, RecoveryAction.KEEP_WORKING))
        self.decision: RecoveryAction | SaveProjectAs = RecoveryAction.KEEP_WORKING
        self.project_buttons = {}
        extra = QWidget(self)
        extra_layout = QVBoxLayout(extra)
        extra_layout.setContentsMargins(0, 0, 0, 0)
        for index, project in enumerate(projects):
            row = QFrame(self)
            row.setObjectName("messageProjectRow")
            layout = QVBoxLayout(row)
            heading = QHBoxLayout()
            name = ElidedLabel(project.path.name, parent=row)
            name.setTextFormat(Qt.TextFormat.PlainText)
            heading.addWidget(name, 1)
            save = link_button("Save As…", row)
            save.setAccessibleName(f"Save {project.path} as")
            save.setEnabled(project.can_save_as)
            save.clicked.connect(lambda _checked=False, key=project.key: self.save_project(key))
            self.project_buttons[project.key] = save
            heading.addWidget(save)
            layout.addLayout(heading)
            path = ElidedLabel(str(project.path.parent), mode=Qt.TextElideMode.ElideMiddle, parent=row)
            path.setTextFormat(Qt.TextFormat.PlainText)
            layout.addWidget(path)
            issue = "Unsaved edits" if project.unsaved else "Project saved"
            if project.bindings:
                issue += " · Folder bindings not recorded: " + ", ".join(p.name for p in project.bindings)
            label = QLabel(issue, row)
            label.setTextFormat(Qt.TextFormat.PlainText)
            label.setWordWrap(True)
            layout.addWidget(label)
            (self.inputs if index < 5 else extra_layout).addWidget(row)
        if len(projects) > 5:
            more = SectionHeader("and " + count_phrase(len(projects) - 5, "more", "more"), self)
            extra.hide()
            more.toggled.connect(extra.setVisible)
            self.inputs.addWidget(more)
            self.inputs.addWidget(extra)
        else:
            extra.deleteLater()
        consequences = []
        dirty = [p.path.name for p in projects if p.unsaved]
        if dirty:
            consequences.append("Closing discards unsaved edits in " + ", ".join(dirty) + ".")
        if any(p.bindings for p in projects):
            consequences.append("Failed folder bindings will not be recorded. Saved project files remain saved.")
        notice = QLabel(" ".join(consequences), self)
        notice.setTextFormat(Qt.TextFormat.PlainText)
        notice.setWordWrap(True)
        self.inputs.addWidget(notice)
        self.resize(640, 580)

    def choose(self, identity: RecoveryAction) -> None:
        self.decision = identity
        super().choose(identity)

    def save_project(self, key: ProjectKey) -> None:
        self.decision = SaveProjectAs(key)
        self.accept()

    def reject(self) -> None:
        self.decision = RecoveryAction.KEEP_WORKING
        super().reject()


def choose_recovery(parent, projects: tuple[RecoveryProject, ...]) -> RecoveryAction | SaveProjectAs:
    sheet = RecoverySheet(parent, projects)
    try:
        sheet.exec()
        return sheet.decision
    finally:
        sheet.deleteLater()
