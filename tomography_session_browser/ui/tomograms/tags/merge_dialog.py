"""One reviewed merge; swaps preserve observations, stale projects require review."""
from PySide6.QtCore import QSignalBlocker, Qt
from PySide6.QtWidgets import QButtonGroup, QComboBox, QFrame, QHBoxLayout, QLabel, QPushButton, QRadioButton, QSizePolicy, QVBoxLayout

from tomography_session_browser.domain.display_names import count_phrase
from tomography_session_browser.domain.tomogram_tags import Tag, TagAssignment
from tomography_session_browser.presentation.design_tokens import SPACE_S
from tomography_session_browser.presentation.messages import ActionPrompt, ActionRole, Cancelled, MessageAction, MessageContent
from tomography_session_browser.presentation.tomogram_tag_merge import MergeCommitted, merge_summary, observation_text
from tomography_session_browser.services.tomogram_tags import project as tag_project
from tomography_session_browser.services.tomogram_tags.session import TagSession
from tomography_session_browser.ui.widgets.elided_label import ElidedLabel
from tomography_session_browser.ui.widgets.message_sheet import MessageSheet

STALE_NOTICE = ("The project changed. Nothing was merged. The preview has been rebuilt with the current observations; "
                "choose the counts again before merging.")


class ObservationCard(QFrame):
    def __init__(self, assignment: TagAssignment, name: str, parent):
        super().__init__(parent)
        self.setObjectName("mergeObservation")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(SPACE_S, SPACE_S, SPACE_S, SPACE_S)
        layout.setSpacing(SPACE_S)
        count = f"{assignment.count:,}" if assignment.count is not None else "No count"
        self.radio = QRadioButton(f"Keep {count}", self)
        self.radio.setObjectName("mergeCount")
        self.radio.setAccessibleName(f"Keep {count} from {name}")
        self.radio.setAccessibleDescription(observation_text(assignment))
        layout.addWidget(self.radio)
        for text in (name, observation_text(assignment)):
            label = QLabel(text, self)
            label.setTextFormat(Qt.TextFormat.PlainText)
            label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
            label.setWordWrap(True)
            layout.addWidget(label)
        self.radio.toggled.connect(self.selection_changed)
        self.selection_changed(False)

    def selection_changed(self, selected: bool) -> None:
        self.setProperty("selected", selected)
        style = self.style()
        style.unpolish(self)
        style.polish(self)
        self.update()


class MergeSheet(MessageSheet[str]):
    def __init__(self, parent, session: TagSession, removed_id: str, surviving_id: str | None = None):
        super().__init__(parent, ActionPrompt(MessageContent("Merge tags",
            "Choose the name to keep and review the recorded counts. Images and capture history are kept."),
            (MessageAction("cancel", "Cancel"), MessageAction("merge", "Merge tags", ActionRole.PRIMARY)), "cancel", "cancel"))
        self.session = session
        self.proposal: tag_project.MergeProposal | None = None
        self.committed: MergeCommitted | None = None
        self.catalogue: tuple[Tag, ...] = ()
        self.choices: dict[tuple[str, str], dict[str, QRadioButton]] = {}
        selectors = QHBoxLayout()
        self.removed, self.surviving = QComboBox(self), QComboBox(self)
        self.swap_button = QPushButton("Swap", self)
        for caption, combo in (("Merge", self.removed), ("into", self.surviving)):
            label = QLabel(caption, self)
            label.setBuddy(combo)
            selectors.addWidget(label)
            combo.setAccessibleName("Tag to remove" if combo is self.removed else "Tag to keep")
            combo.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
            combo.setMinimumContentsLength(6)
            selectors.addWidget(combo, 1)
        selectors.addWidget(self.swap_button)
        self.inputs.addLayout(selectors)
        self.summary = self.label()
        self.notice = self.label()
        self.notice.setObjectName("mergeNotice")
        self.inputs.addWidget(self.summary)
        self.inputs.addWidget(self.notice)
        self.rows = QVBoxLayout()
        self.inputs.addLayout(self.rows)
        self.remaining = self.label()
        self.inputs.addWidget(self.remaining)
        self.sync_catalogue()
        self.removed.setCurrentIndex(self.removed.findData(removed_id))
        self.surviving.setCurrentIndex(self.surviving.findData(surviving_id) if surviving_id is not None else -1)
        self.removed.currentIndexChanged.connect(self.rebuild)
        self.surviving.currentIndexChanged.connect(self.rebuild)
        self.swap_button.clicked.connect(self.swap)
        self.rebuild()
        self.setMinimumWidth(480)
        self.resize(720, 620)

    def label(self, text: str = "") -> QLabel:
        label = QLabel(text, self)
        label.setTextFormat(Qt.TextFormat.PlainText)
        label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        label.setWordWrap(True)
        return label

    def sync_catalogue(self) -> None:
        catalogue = self.session.project.catalogue if self.session.project is not None else ()
        if catalogue == self.catalogue:
            return
        for combo in (self.removed, self.surviving):
            identity = combo.currentData()
            with QSignalBlocker(combo):
                combo.clear()
                for tag in catalogue:
                    combo.addItem(tag.name, tag.id)
                combo.setCurrentIndex(combo.findData(identity) if identity is not None else -1)
        self.catalogue = catalogue

    def selected_counts(self) -> dict[tuple[str, str], str]:
        return {key: identity for key, buttons in self.choices.items()
                for identity, button in buttons.items() if button.isChecked()}

    def invalidate(self) -> None:
        self.proposal = None
        self.buttons["merge"].setEnabled(False)
        self.swap_button.setEnabled(False)

    def rebuild(self, _index: int = -1, *, previous: tag_project.MergeProposal | None = None,
                preserved: dict[tuple[str, str], str] | None = None, notice: str = "") -> None:
        basis = previous if previous is not None else self.proposal
        self.invalidate()
        self.sync_catalogue()
        self.choices = {}
        while self.rows.count():
            widget = self.rows.takeAt(0).widget()
            if widget is not None:
                widget.hide()
                widget.deleteLater()
        self.summary.setText("Choose two different tags to review the merge.")
        self.details.clear()
        current = self.session.project
        if basis is not None and current != basis.original:
            preserved, notice = None, STALE_NOTICE
        if current is not None and (self.removed.currentData() is None or self.surviving.currentData() is None):
            self.notice.setText(notice)
            self.notice.setVisible(bool(notice))
            self.refresh()
            return
        try:
            if current is None:
                raise ValueError("The project is no longer available. Nothing was merged.")
            proposal = tag_project.propose_merge(current, self.surviving.currentData(), self.removed.currentData())
            summary = merge_summary(proposal)
            self.summary.setText(summary.title + "\n\n" + summary.detail)
            keys = {conflict.row for conflict in proposal.conflicts}
            if preserved is not None and previous is not None and keys != {conflict.row for conflict in previous.conflicts}:
                preserved = None
                notice = "The conflict list changed. Choose every count again before merging."
            names = {tag.id: tag.name for tag in current.catalogue}
            sources = {source.id: source for source in current.sources}
            details = [str(self.session.store.path), summary.title, summary.detail]
            for conflict in proposal.conflicts:
                source = sources[conflict.row[0]]
                path = source.path / conflict.row[1]
                frame = QFrame(self)
                frame.setObjectName("mergeConflict")
                row = QVBoxLayout(frame)
                heading = ElidedLabel(f"{source.name} / {conflict.row[1]}", parent=frame,
                                      mode=Qt.TextElideMode.ElideMiddle)
                heading.setTextFormat(Qt.TextFormat.PlainText)
                heading.setToolTip(str(path))
                heading.setAccessibleName(str(path))
                row.addWidget(heading)
                options = QHBoxLayout()
                group = QButtonGroup(frame)
                buttons = self.choices[conflict.row] = {}
                details.append(str(path))
                for assignment in (conflict.surviving, conflict.removed):
                    option = ObservationCard(assignment, names[assignment.tag_id], frame)
                    radio = option.radio
                    group.addButton(radio)
                    buttons[assignment.tag_id] = radio
                    radio.setChecked(preserved is not None and preserved.get(conflict.row) == assignment.tag_id)
                    radio.toggled.connect(self.refresh)
                    options.addWidget(option, 1)
                    details.append(f"{names[assignment.tag_id]}\nCount: {assignment.count}\nVersion: {assignment.version_label}\n"
                                   f"Version path: {assignment.version_relative}\nRecorded: {assignment.recorded_at}")
                row.addLayout(options)
                self.rows.addWidget(frame)
            self.proposal = proposal
            self.details.setPlainText("\n\n".join(details))
            self.swap_button.setEnabled(True)
        except (ValueError, KeyError) as error:
            notice = notice or str(error)
        self.notice.setText(notice)
        self.notice.setVisible(bool(notice))
        self.refresh()

    def refresh(self, _checked: bool = False) -> None:
        proposal = self.proposal
        missing = len(proposal.conflicts) - len(self.selected_counts()) if proposal is not None else 0
        self.remaining.setText(f"{count_phrase(missing, 'count')} still to choose" if missing else "")
        self.remaining.setVisible(bool(missing))
        self.buttons["merge"].setEnabled(proposal is not None and missing == 0)

    def swap(self) -> None:
        previous, preserved = self.proposal, self.selected_counts()
        if previous is None:
            return
        self.invalidate()
        with QSignalBlocker(self.removed), QSignalBlocker(self.surviving):
            self.removed.setCurrentIndex(self.removed.findData(previous.surviving_id))
            self.surviving.setCurrentIndex(self.surviving.findData(previous.removed_id))
        self.rebuild(previous=previous, preserved=preserved)

    def choose(self, identity: str) -> None:
        if identity == "merge":
            proposal = self.proposal
            if proposal is None or not self.buttons["merge"].isEnabled():
                return
            current = self.session.project
            if current is None or current != proposal.original:
                self.rebuild(notice=STALE_NOTICE)
                return
            try:
                merged = tag_project.commit_merge(current, proposal, self.selected_counts())
                self.session.edit(merged, expected=proposal.original)
            except ValueError as error:
                self.rebuild(notice=str(error))
                return
            self.committed = MergeCommitted(proposal.removed_id, proposal.surviving_id)
        super().choose(identity)


def review_merge(parent, session: TagSession, removed_id: str, surviving_id: str | None = None) -> MergeCommitted | Cancelled:
    sheet = MergeSheet(parent, session, removed_id, surviving_id)
    try:
        sheet.exec()
        return sheet.committed if sheet.committed is not None else Cancelled()
    finally:
        sheet.deleteLater()
