"""Review exactly one current move proposal; matching remains service-owned."""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QComboBox, QLabel, QSizePolicy

from tomography_session_browser.domain.tomograms import ProcessingSource
from tomography_session_browser.domain.tomogram_tags import TagSource
from tomography_session_browser.presentation.messages import ActionPrompt, ActionRole, Cancelled, MessageAction, MessageContent
from tomography_session_browser.presentation.tomogram_tag_messages import MoveAccepted
from tomography_session_browser.services.tomogram_tags import binding
from tomography_session_browser.services.tomogram_tags.session import TagSession
from tomography_session_browser.ui.widgets.elided_label import ElidedLabel
from tomography_session_browser.ui.widgets.message_sheet import MessageSheet


class MoveSheet(MessageSheet[str]):
    def __init__(self, parent, session: TagSession, sources: tuple[TagSource, ...], folders: tuple[ProcessingSource, ...]):
        super().__init__(parent, ActionPrompt(MessageContent("Find moved folder",
            "Reconnect this project's saved observations to a loaded folder. Source files stay unchanged."),
            (MessageAction("cancel", "Cancel"), MessageAction("confirm", "Confirm moved folder", ActionRole.PRIMARY)), "cancel", "cancel"))
        self.session = session
        self.proposal: binding.MoveProposal | None = None
        self.source = QComboBox(self)
        self.folder = QComboBox(self)
        self.source_path = ElidedLabel(parent=self, mode=Qt.TextElideMode.ElideMiddle)
        self.folder_path = ElidedLabel(parent=self, mode=Qt.TextElideMode.ElideMiddle)
        for combo, title, values, path in ((self.source, "Project source", sources, self.source_path),
                                          (self.folder, "Loaded destination", folders, self.folder_path)):
            combo.setAccessibleName(title)
            combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
            combo.setMinimumContentsLength(18)
            combo.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
            for value in values:
                combo.addItem(value.name, value)
                combo.setItemData(combo.count() - 1, str(value.path), Qt.ItemDataRole.ToolTipRole)
            label = QLabel(title, self)
            label.setBuddy(combo)
            self.inputs.addWidget(label)
            self.inputs.addWidget(combo)
            self.inputs.addWidget(path)
        caution = QLabel("Equal geometry does not prove identical voxels.", self)
        caution.setWordWrap(True)
        self.inputs.addWidget(caution)
        self.matches = QLabel(self)
        self.matches.setTextFormat(Qt.TextFormat.PlainText)
        self.matches.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.matches.setWordWrap(True)
        self.inputs.addWidget(self.matches)
        self.source.currentIndexChanged.connect(self.rebuild)
        self.folder.currentIndexChanged.connect(self.rebuild)
        self.rebuild()
        self.resize(600, 520)

    def rebuild(self, _index: int = -1) -> None:
        # Invalidate before building, including a build that raises or re-enters Qt.
        self.proposal = None
        self.buttons["confirm"].setEnabled(False)
        source, folder = self.source.currentData(), self.folder.currentData()
        self.source_path.setText(str(source.path) if source else "")
        self.folder_path.setText(str(folder.path) if folder else "")
        if source is None or folder is None or self.session.project is None:
            reason = "Choose a project source and a loaded destination."
        else:
            try:
                proposal = binding.propose_move(self.session.project, source.id, folder)
                reason = "\n\n".join(f"{match.relative}\n{match.reason}" for match in proposal.matches)
                self.proposal = proposal
                self.buttons["confirm"].setEnabled(all(match.eligible for match in proposal.matches))
            except Exception as error:
                reason = str(error)
        self.matches.setText(reason)
        self.details.setPlainText("\n\n".join((str(self.session.store.path), self.source_path.text_full(),
            self.folder_path.text_full(), reason, "Equal geometry does not prove identical voxels.")))

    def choose(self, identity: str) -> None:
        if identity == "confirm" and (self.proposal is None or not self.buttons["confirm"].isEnabled()):
            return
        super().choose(identity)


def review_move(parent, session: TagSession, sources: tuple[TagSource, ...],
                folders: tuple[ProcessingSource, ...]) -> MoveAccepted | Cancelled:
    sheet = MoveSheet(parent, session, sources, folders)
    try:
        sheet.exec()
        return MoveAccepted(sheet.proposal) if sheet.selected_action == "confirm" and sheet.proposal is not None else Cancelled()
    finally:
        sheet.deleteLater()
