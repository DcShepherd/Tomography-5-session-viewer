"""Review the loaded evidence for one fixed tomogram before accepting it."""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLabel, QVBoxLayout

from tomography_session_browser.presentation.messages import ActionPrompt, ActionRole, MessageAction, MessageContent
from tomography_session_browser.presentation.tomogram_tag_changes import changed_fields, issue_text, review_versions, volume_detail
from tomography_session_browser.ui.widgets.message_sheet import MessageSheet


class ChangeSheet(MessageSheet[str]):
    def __init__(self, parent, owner, session, key):
        super().__init__(parent, ActionPrompt(
            MessageContent("Accept the current files?", ""),
            (MessageAction("cancel", "Cancel"), MessageAction("accept", "Accept current files", ActionRole.PRIMARY)),
            "cancel", "cancel"))
        self.owner, self.session = owner, session
        self.keys = frozenset({key})
        self.evidence = QVBoxLayout()
        self.inputs.addLayout(self.evidence)
        self.reason = self.add_line(self.inputs, "")
        self.rebuild()

    def add_line(self, layout: QVBoxLayout, text: str) -> QLabel:
        label = QLabel(text, self)
        label.setTextFormat(Qt.TextFormat.PlainText)
        label.setWordWrap(True)
        layout.addWidget(label)
        return label

    def rebuild(self, refusal: str = "") -> None:
        self.proposal = self.owner.change_proposal(self.session, self.keys)
        row = self.proposal.rows[0] if self.proposal and self.proposal.rows else None
        self.title.setText(f"Accept the current files for {row.row_name}?" if row else "Accept the current files?")
        self.consequence.setText(
            "Tags, counts, the note and linked captures stay as they are. Source files stay unchanged. "
            "The warning returns if a file changes again." if row else "")
        while self.evidence.count():
            widget = self.evidence.takeAt(0).widget()
            widget.hide()
            widget.deleteLater()
        if row:
            for change, provenance in review_versions(row.saved, row.changes):
                heading = self.add_line(self.evidence, change.saved.version)
                heading.setObjectName("inlineNoticeTitle")
                fields = changed_fields(change)
                if not fields:
                    self.add_line(self.evidence, volume_detail(change))
                for field in fields:
                    self.add_line(self.evidence, f"{field.label} · Recorded {field.recorded}")
                    now = self.add_line(self.evidence, f"Now {field.current}")
                    if field.warn:
                        now.setObjectName("toneText")
                        now.setProperty("tone", "warn")
                self.add_line(self.evidence, provenance)
            self.add_line(self.evidence, "Review the counts for the affected versions. "
                          "Equal geometry does not prove identical voxels.")
        reason = issue_text(row) if row else ""
        if not self.session.editable:
            reason = self.session.reason or "This project is not editable."
        self.reason.setText("\n".join(filter(None, (refusal, reason))))
        paths = sorted({row.folder_path / volume.relative for volume in (*row.saved.ref.volumes, *row.live.volumes)}) if row else ()
        self.details.setPlainText("\n".join(map(str, (self.session.store.path, *paths))))
        self.buttons["accept"].setEnabled(row is not None and row.issue is None and self.session.editable)

    def choose(self, identity: str) -> None:
        if identity == "accept":
            if self.proposal is None or not self.buttons["accept"].isEnabled():
                return
            refusal = self.owner.accept_changes(self.session, self.proposal, self.keys, self.keys)
            if refusal:
                self.rebuild(refusal)
                return
        super().choose(identity)
