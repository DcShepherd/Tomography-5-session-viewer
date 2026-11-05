"""Selected-row tags and note. All edits are delegated to the shared owner."""
from tomography_session_browser.presentation.help_content import LocalBinding, InteractionKind
from PySide6.QtCore import QEvent, Qt, Signal, QSignalBlocker, QTimer, QVariantAnimation
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import QApplication
from PySide6.QtWidgets import (QFrame, QHBoxLayout, QLabel, QMenu, QPlainTextEdit,
                               QPushButton, QToolButton, QScrollArea, QVBoxLayout, QWidget, QSizePolicy)

from tomography_session_browser.presentation.design_tokens import SPACE_S
from tomography_session_browser.presentation.tomogram_tag_entry import DraftValidity
from tomography_session_browser.services.tomogram_tags.session import SaveState, SaveStatus
from tomography_session_browser.services.tomogram_tags.binding import ProjectKey
from tomography_session_browser.presentation.messages import MessageContent
from tomography_session_browser.ui.tomograms.tags.entry import TagEntry
from tomography_session_browser.ui.widgets.switch import Switch
from tomography_session_browser.ui.icons import themed_icon
from tomography_session_browser.ui.widgets.elided_label import ElidedLabel
from tomography_session_browser.ui.motion.preferences import motion_enabled
from tomography_session_browser.ui.theme import current_palette
from tomography_session_browser.ui.widgets.elided_button import ElidedButton
from tomography_session_browser.ui.widgets.inline_notice import InlineNotice
from tomography_session_browser.ui.widgets.link_button import link_button
from tomography_session_browser.ui.widgets.literal_text import literal_mnemonic, literal_tooltip
from tomography_session_browser.ui.dashboard.layout import FlowLayout

NOTE_LIMIT = 4000


def utf16_length(text: str) -> int:
    """Length in the UTF-16 units that QTextCursor positions count."""
    return len(text.encode("utf-16-le")) // 2


class TagRow(QWidget):
    def __init__(self, parent):
        super().__init__(parent)
        self.remove_button = None
        #: Controls shown only while the row is hovered or holds focus.
        self.hover_buttons: list[QWidget] = []
        self.alpha = 0.
        self.animation = QVariantAnimation(self)
        self.animation.setDuration(600)
        self.animation.valueChanged.connect(self.highlight)

    def highlight(self, value):
        self.alpha = float(value)
        self.update()

    def flash(self):
        if motion_enabled(self):
            self.animation.setStartValue(.25)
            self.animation.setEndValue(0.)
            self.animation.start()
        else:
            self.highlight(.15)
            QTimer.singleShot(1600, self, lambda: self.highlight(0.))

    def refresh_remove(self):
        focus = QApplication.focusWidget()
        shown = self.underMouse() or (focus is not None and self.isAncestorOf(focus))
        for button in self.hover_buttons:
            button.setVisible(shown)

    def enterEvent(self, event):
        self.refresh_remove()
        super().enterEvent(event)

    def leaveEvent(self, event):
        self.refresh_remove()
        super().leaveEvent(event)

    def eventFilter(self, watched, event):
        if event.type() in (QEvent.Type.FocusIn, QEvent.Type.FocusOut):
            QTimer.singleShot(0, self, self.refresh_remove)
        return super().eventFilter(watched, event)

    def paintEvent(self, event):
        super().paintEvent(event)
        if self.alpha:
            colour = QColor(current_palette().accent)
            colour.setAlphaF(self.alpha)
            painter = QPainter(self)
            painter.fillRect(self.rect(), colour)


class TagsPanel(QFrame):
    complete_changed = Signal(bool)
    note_changed = Signal(str)
    note_cancelled = Signal()
    retry_requested = Signal()
    save_as_requested = Signal(object)

    def __init__(self, owner):
        super().__init__(owner.tab)
        self.owner = owner
        self.setObjectName("tomogramTagsPanel")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(SPACE_S, SPACE_S, SPACE_S, SPACE_S)
        outer.setSpacing(SPACE_S)
        header = QHBoxLayout()
        self.heading = QLabel("TAGS", self)
        self.heading.setObjectName("metaPlain")
        self.complete = Switch(self, name="Counting complete")
        self.complete.setToolTip("Record that you finished counting this tomogram")
        self.more = QToolButton(self)
        self.more.setText("…")
        self.more.setAutoRaise(True)
        self.more.setAccessibleName("Tag project actions")
        self.more.clicked.connect(owner.show_menu)
        header.addWidget(self.heading)
        header.addWidget(self.complete)
        header.addWidget(ElidedLabel("Counting complete", parent=self, prefer_full_width=True), 1)
        header.addWidget(self.more)
        outer.addLayout(header)
        self.content_scroll = QScrollArea(self)
        self.content_scroll.setAccessibleName("Tags, notes and recovery")
        self.content_scroll.setWidgetResizable(True)
        self.content_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.content_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        outer.addWidget(self.content_scroll)
        body = QWidget(self.content_scroll)
        self.layout_ = QVBoxLayout(body)
        self.layout_.setContentsMargins(0, 0, 0, 0)
        self.layout_.setSpacing(SPACE_S)
        self.content_scroll.setWidget(body)
        self.reason = QLabel(self)
        self.reason.setTextFormat(Qt.TextFormat.PlainText)
        self.reason.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.reason.setWordWrap(True)
        self.layout_.addWidget(self.reason)
        self.setup = QWidget(self)
        setup = QVBoxLayout(self.setup)
        setup.setContentsMargins(0, 0, 0, 0)
        setup.setSpacing(SPACE_S)
        buttons = FlowLayout(spacing=SPACE_S)
        self.create_project = QPushButton("Create project…", self.setup)
        self.open_project = QPushButton("Open project…", self.setup)
        self.create_project.clicked.connect(lambda: owner.project_dialog(create=True))
        self.open_project.clicked.connect(lambda: owner.project_dialog())
        buttons.addWidget(self.create_project)
        buttons.addWidget(self.open_project)
        setup.addLayout(buttons)
        self.setup_hint = QLabel("Type # in the tag field to start a new tag.", self.setup)
        self.setup_hint.setWordWrap(True)
        self.setup_hint.setObjectName("viewerStatus")
        setup.addWidget(self.setup_hint)
        self.setup.hide()
        self.layout_.addWidget(self.setup)
        self.scroll = QScrollArea(self)
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll.setMaximumHeight(5 * 34)
        self.scroll.setMinimumHeight(0)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.rows = QWidget(self.scroll)
        self.rows_layout = QVBoxLayout(self.rows)
        self.rows_layout.setContentsMargins(0, 0, 0, 0)
        self.rows_layout.setSpacing(0)
        self.scroll.setWidget(self.rows)
        self.layout_.addWidget(self.scroll, 1)
        self.note_preview = ElidedButton(self)
        self.note_preview.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self.note_preview.setAccessibleName("Edit tomogram note")
        self.note_preview.clicked.connect(self.open_note)
        self.note_preview.hide()
        self.layout_.addWidget(self.note_preview)
        self.note = QPlainTextEdit(self)
        self.note.setAccessibleName("Tomogram note")
        self.note.setMaximumHeight(100)
        self.note.setPlaceholderText("Note for this tomogram…")
        self.note.installEventFilter(self)
        self.note.textChanged.connect(self.note_edited)
        self.note.hide()
        self.layout_.addWidget(self.note)
        self.counter = QLabel(self)
        self.counter.hide()
        self.layout_.addWidget(self.counter)
        self.entry = TagEntry(self)
        self.entry.field.installEventFilter(self)
        self.layout_.addWidget(self.entry)
        self.saved = ElidedLabel(parent=self)
        self.saved.setTextFormat(Qt.TextFormat.PlainText)
        self.layout_.addWidget(self.saved)
        self.project_key: ProjectKey | None = None
        self.failure_notice = InlineNotice(self)
        retry = QPushButton("Retry all", self.failure_notice)
        retry.setToolTip("Retry saving all tag projects and failed folder bindings")
        retry.clicked.connect(self.retry_requested)
        self.save_as = QPushButton("Save As…", self.failure_notice)
        self.save_as.clicked.connect(self.request_save_as)
        self.failure_notice.actions.addWidget(retry)
        self.failure_notice.actions.addWidget(self.save_as)
        self.layout_.addWidget(self.failure_notice)
        self.export_notice = InlineNotice(self)
        dismiss = link_button("Dismiss", self.export_notice)
        dismiss.clicked.connect(owner.dismiss_export)
        self.export_notice.actions.addWidget(dismiss)
        self.layout_.addWidget(self.export_notice)
        self.save_state: SaveState | None = None
        self.entry.validity_changed.connect(self.render_save_state)
        self.entry.validity_changed.connect(lambda _validity: self.reveal_editor(self.entry))
        self.complete.toggled.connect(self.complete_changed)
        self.original_note = ""
        self.bound_note = ""
        self.note_target = None
        self.note_resume_focus = False
        self.rows_content = None
        self.pending_flash = None

    def render(self, target, project, row, reason: str, editable: bool, save_state: SaveState | None, can_create: bool = False,
               *, save_notice: MessageContent | None = None, project_key: ProjectKey | None = None) -> None:
        previous_key = self.project_key
        if self.note_target is not None and target != self.note_target:
            self.close_note()
        self.reason.setText(reason)
        self.reason.setVisible(bool(reason))
        self.save_state = save_state
        self.project_key = project_key
        self.save_as.setEnabled(project_key is not None)
        if save_notice is not None:
            changed = previous_key != project_key or self.failure_notice.content != save_notice or self.failure_notice.isHidden()
            self.failure_notice.set_content(save_notice)
            if changed and project_key is not None:
                self.reveal_notice(self.failure_notice, project_key)
        else:
            self.failure_notice.hide()
        if target != self.entry.target:
            self.content_scroll.verticalScrollBar().setValue(0)
        self.entry.bind(target, project.catalogue if project else (), row.tags if row else ())
        self.render_save_state()
        self.setup.setVisible(project is None and can_create)
        self.entry.setEnabled(editable or can_create)
        self.complete.setEnabled(editable)
        with QSignalBlocker(self.complete):
            self.complete.setChecked(row.counting_complete if row else False)
        self.heading.setText(f"TAGS · {len(row.tags) if row else 0}")
        content = (target, project.catalogue if project else (), row.tags if row else (), row.links if row else (), editable)
        if content != self.rows_content:
            self.rows_content = content
            self.render_rows(project, row, editable)
        self.bound_note = row.note if row else ""
        if self.note.isHidden():
            self.show_note_preview()
        self.note_preview.setEnabled(editable)

    def render_save_state(self, _validity: DraftValidity | None = None) -> None:
        state = self.save_state
        text = state.status.value if state else ""
        self.saved.setText(text)
        self.saved.setVisible(state is not None and self.failure_notice.isHidden() and not (
            state.status == SaveStatus.SAVED and self.entry.validity == DraftValidity.INVALID))

    def request_save_as(self) -> None:
        if self.project_key is not None:
            self.save_as_requested.emit(self.project_key)

    def reveal_notice(self, notice: InlineNotice, expected: ProjectKey) -> None:
        def reveal():
            if self.project_key == expected and not notice.isHidden():
                self.content_scroll.ensureWidgetVisible(notice, 0, SPACE_S)
        QTimer.singleShot(0, self, reveal)

    def reveal_editor(self, editor: QWidget) -> None:
        def reveal():
            focus = QApplication.focusWidget()
            if focus is not None and (focus is editor or editor.isAncestorOf(focus)):
                self.content_scroll.ensureWidgetVisible(editor, 0, SPACE_S)
        QTimer.singleShot(0, self, reveal)

    def render_rows(self, project, row, editable):
        while self.rows_layout.count():
            item = self.rows_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        catalogue = {t.id: t for t in project.catalogue} if project else {}
        for assignment in sorted(row.tags if row else (), key=lambda a: catalogue[a.tag_id].name.casefold()):
            widget = TagRow(self.rows)
            widget.setFixedHeight(34)
            line = QHBoxLayout(widget)
            line.setContentsMargins(0, 0, 0, 0)
            label = QLabel(catalogue[assignment.tag_id].name, widget)
            label.setTextFormat(Qt.TextFormat.PlainText)
            label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
            label.setToolTip(literal_tooltip(catalogue[assignment.tag_id].name))
            count = QPushButton(str(assignment.count) if assignment.count is not None else "–", widget)
            count.setFixedWidth(52)
            count.setObjectName("tomogramTagCount")
            count.setFlat(True)
            count.setEnabled(editable)
            count.setAccessibleName(f"Count for {catalogue[assignment.tag_id].name}")
            count.setToolTip(f"Recorded on {assignment.version_label} · {assignment.recorded_at}")
            count.clicked.connect(lambda _, i=assignment.tag_id: self.entry.edit_count(i))
            camera = QToolButton(widget)
            camera.setAutoRaise(True)
            camera.setFixedWidth(42)
            total = sum(l.tag_id == assignment.tag_id for l in row.links)
            # A camera with the number of linked captures when the tag has
            # links (TD3). Without links the same button stays out of sight
            # until the row is hovered or focused, like ×: a bare "…" there
            # read as cut-off text.
            camera.setIcon(themed_icon("camera"))
            camera.setText(str(total) if total else "")
            camera.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon if total
                                      else Qt.ToolButtonStyle.ToolButtonIconOnly)
            camera.setToolTip("Show the captures linked to this tag" if total else "Link captures to this tag")
            camera.setAccessibleName(f"Captures for {catalogue[assignment.tag_id].name}")
            camera.clicked.connect(lambda _, i=assignment.tag_id, b=camera, n=total: self.owner.camera_clicked(i, b, n))
            widget.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
            widget.customContextMenuRequested.connect(
                lambda point, i=assignment.tag_id, w=widget, n=total: self.owner.capture_menu(i, w.mapToGlobal(point), n))
            remove = QToolButton(widget)
            remove.setText("×")
            remove.setAutoRaise(True)
            remove.setFixedWidth(26)
            remove.setEnabled(editable)
            remove.setAccessibleName(f"Remove {catalogue[assignment.tag_id].name} from this tomogram")
            remove.clicked.connect(lambda _, i=assignment.tag_id: self.owner.remove_assignment(i))
            line.addWidget(label, 1)
            line.addWidget(camera)
            line.addWidget(count)
            line.addWidget(remove)
            widget.remove_button = remove
            widget.hover_buttons = [remove] if total else [camera, remove]
            for button in widget.hover_buttons:
                policy = button.sizePolicy()
                policy.setRetainSizeWhenHidden(True)
                button.setSizePolicy(policy)
                button.hide()
            for control in (count, camera, remove):
                control.installEventFilter(widget)
            self.rows_layout.addWidget(widget)
            if self.pending_flash == assignment.tag_id:
                self.pending_flash = None
                widget.flash()
                QTimer.singleShot(0, widget, lambda w=widget: self.scroll.ensureWidgetVisible(w))
        self.rows_layout.addStretch(1)

    def open_note(self) -> None:
        if not self.owner.editable():
            return
        self.note_target = self.owner.target()
        self.original_note = self.bound_note
        with QSignalBlocker(self.note):
            self.note.setPlainText(self.bound_note)
        self.note_preview.hide()
        self.note.show()
        self.note.setFocus()

    def note_edited(self) -> None:
        value = self.note.toPlainText()
        excess = len(value) - NOTE_LIMIT
        truncated = excess > 0
        if truncated:
            # Drop the overflow just typed or pasted, which ends at the
            # cursor; the text after the cursor and the cursor itself stay.
            cursor = self.note.textCursor()
            stop = len(value.encode("utf-16-le")[:2 * cursor.position()].decode("utf-16-le", "ignore"))
            start = max(0, stop - excess)
            with QSignalBlocker(self.note):
                cursor.setPosition(utf16_length(value[:start]))
                cursor.setPosition(utf16_length(value[:stop]), cursor.MoveMode.KeepAnchor)
                cursor.removeSelectedText()
                value = value[:start] + value[stop:]
                if len(value) > NOTE_LIMIT:
                    cursor.setPosition(utf16_length(value[:NOTE_LIMIT]))
                    cursor.movePosition(cursor.MoveOperation.End, cursor.MoveMode.KeepAnchor)
                    cursor.removeSelectedText()
                    value = value[:NOTE_LIMIT]
        self.counter.setText(f"{NOTE_LIMIT - len(value)} characters left" + (" · extra text not added" if truncated else ""))
        self.counter.setVisible(len(value) >= NOTE_LIMIT - 200 or truncated)
        self.note_changed.emit(value)

    def show_note_preview(self) -> None:
        self.note_preview.setText(literal_mnemonic(self.bound_note.replace("\n", " ")))
        self.note_preview.setToolTip(literal_tooltip(self.bound_note))
        self.note_preview.setVisible(bool(self.bound_note))

    def close_note(self) -> None:
        self.note.hide()
        self.counter.hide()
        self.note_target = None
        self.note_resume_focus = False
        self.show_note_preview()

    def note_focus_left(self) -> None:
        if not self.note.isHidden() and not self.note.hasFocus():
            self.close_note()

    def eventFilter(self, watched, event):
        if event.type() == QEvent.Type.FocusIn:
            self.reveal_editor(self.entry if watched is self.entry.field else self.note)
        elif watched is self.note and event.type() == QEvent.Type.KeyPress:
            if event.key() == Qt.Key.Key_Escape:
                self.note_cancelled.emit()
                self.close_note()
                return True
            if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and event.modifiers() & Qt.KeyboardModifier.ControlModifier:
                self.close_note()
                self.entry.field.setFocus()
                return True
        elif watched is self.note and event.type() == QEvent.Type.FocusOut:
            # A context menu or another application only borrows focus.
            if event.reason() in (Qt.FocusReason.PopupFocusReason, Qt.FocusReason.ActiveWindowFocusReason):
                pass
            elif self.note.isVisible():
                QTimer.singleShot(0, self, self.note_focus_left)
            else:
                # A hidden ancestor (compact move, layout change) took focus:
                # the editor stays open and takes focus back when shown.
                self.note_resume_focus = True
        elif watched is self.note and event.type() == QEvent.Type.Show and self.note_resume_focus:
            self.note_resume_focus = False
            self.note.setFocus(Qt.FocusReason.OtherFocusReason)
        return super().eventFilter(watched, event)


LOCAL_KEYS = (
    LocalBinding('tag-row.menu', 'Tag row', InteractionKind.MOUSE, ('Right-click',), 'Link captures to the tag, show its linked captures or remove it from this tomogram'),
    LocalBinding('note.cancel', 'Tomogram note', InteractionKind.KEYBOARD, ('Esc',), 'Restore the note from when editing began, then close the editor'),
    LocalBinding('note.finish', 'Tomogram note', InteractionKind.KEYBOARD, ('Ctrl+Enter', 'Ctrl+Return'), 'Close the note editor and return to tag entry'),
)
