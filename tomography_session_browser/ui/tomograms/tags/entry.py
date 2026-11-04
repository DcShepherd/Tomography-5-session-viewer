"""Keyboard-first tag draft, with an upward screen-clamped suggestion popup."""
from tomography_session_browser.presentation.help_content import LocalBinding, InteractionKind
from dataclasses import replace

from PySide6.QtCore import QEvent, Qt, Signal, QPoint, QTimer
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem, QSizePolicy, QVBoxLayout
from tomography_session_browser.ui.widgets.elided_label import ElidedLabel
from tomography_session_browser.ui.widgets.literal_text import literal_tooltip

from tomography_session_browser.presentation.tomogram_tag_entry import TagDraft, TagSuggestion, suggestions, EntryStage, DraftValidity, count_error


class TagEntry(QFrame):
    committed = Signal(object, object)
    dismissed = Signal()
    validity_changed = Signal(object)

    def __init__(self, parent):
        super().__init__(parent)
        self.draft = TagDraft()
        self.catalogue = ()
        self.assignments = ()
        self.target = None
        self.commit = None
        self.validity = DraftValidity.VALID
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        layout.addLayout(row)
        self.field = QLineEdit(self)
        self.field.setPlaceholderText("# Add a tag…")
        self.field.setAccessibleName("Tag name or count")
        self.field.setMaxLength(130)
        self.chip = ElidedLabel(parent=self, tooltip=False)
        self.chip.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.chip.setTextFormat(Qt.TextFormat.PlainText)
        self.chip.hide()
        row.addWidget(self.chip, 1)
        row.addWidget(self.field, 1)
        self.error = QLabel(self)
        self.error.setObjectName("tomogramTagEntryError")
        self.error.setTextFormat(Qt.TextFormat.PlainText)
        self.error.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.error.setWordWrap(True)
        self.error.hide()
        layout.addWidget(self.error)
        self.popup = QListWidget(self)
        self.popup.setWindowFlags(Qt.WindowType.ToolTip)
        self.popup.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.popup.itemClicked.connect(self.accept_item)
        # The popup is a separate window: it follows the field's window.
        self.watched_window = None
        self.field.installEventFilter(self)
        self.field.textEdited.connect(self.typed)
        self.field.textChanged.connect(lambda _text: self.set_error(""))

    def set_error(self, message: str) -> None:
        self.error.setText(message)
        self.error.setVisible(bool(message))
        self.field.setAccessibleDescription(message)
        self.field.setToolTip(message)
        validity = DraftValidity.INVALID if message else DraftValidity.VALID
        if validity != self.validity:
            self.validity = validity
            self.validity_changed.emit(validity)

    def bind(self, target, catalogue, assignments) -> None:
        if target != self.target:
            self.cancel()
        self.target, self.catalogue, self.assignments = target, catalogue, assignments

    def begin(self) -> None:
        if not self.isEnabled():
            return
        self.field.setFocus()
        if self.draft.stage != EntryStage.COUNT:
            self.field.setText("#")
            self.typed("#")

    def typed(self, text: str) -> None:
        if self.draft.stage == EntryStage.COUNT:
            self.draft = replace(self.draft, count_text=text)
            return
        self.draft = TagDraft(text)
        self.popup.clear()
        assigned = frozenset(t.tag_id for t in self.assignments)
        for suggestion in suggestions(text, self.catalogue, assigned):
            label = (f"Create {suggestion.name}" if suggestion.tag_id is None else suggestion.name)
            if suggestion.assigned:
                label += " · edit existing count"
            item = QListWidgetItem(label, self.popup)
            item.setData(Qt.ItemDataRole.UserRole, suggestion)
        if not self.popup.count() or not text:
            self.popup.hide()
            return
        self.popup.setCurrentRow(0)
        height = min(8, self.popup.count()) * max(24, self.popup.sizeHintForRow(0)) + 4
        self.popup.resize(max(260, self.width()), height)
        screen = self.screen().availableGeometry()
        point = self.mapToGlobal(QPoint(0, -height))
        if point.y() < screen.top():
            point = self.mapToGlobal(QPoint(0, self.height()))
        point.setX(max(screen.left(), min(point.x(), screen.right() - self.popup.width() + 1)))
        point.setY(max(screen.top(), min(point.y(), screen.bottom() - height + 1)))
        self.popup.move(point)
        self.popup.show()

    def accept_item(self, item=None) -> None:
        item = item or self.popup.currentItem()
        if item is None:
            return
        suggestion = item.data(Qt.ItemDataRole.UserRole)
        self.accept_suggestion(suggestion)

    def accept_suggestion(self, suggestion: TagSuggestion) -> None:
        self.set_error("")
        current = next((a.count for a in self.assignments if a.tag_id == suggestion.tag_id), None)
        self.draft = self.draft.accept(suggestion, current)
        self.chip.setText(suggestion.name)
        self.chip.setToolTip(literal_tooltip(suggestion.name))
        self.chip.show()
        self.field.setText(self.draft.count_text)
        self.field.setPlaceholderText("Count (optional)")
        self.field.selectAll()
        self.popup.hide()
        self.field.setFocus()

    def edit_count(self, tag_id: str) -> None:
        tag = next((t for t in self.catalogue if t.id == tag_id), None)
        if tag is not None:
            self.accept_suggestion(TagSuggestion(tag.name, tag.id, True))

    def cancel(self) -> None:
        self.set_error("")
        self.draft = TagDraft()
        self.field.clear()
        self.field.setPlaceholderText("# Add a tag…")
        self.chip.hide()
        self.popup.hide()
        # A hidden list must never answer a later Enter or Tab.
        self.popup.clear()

    def showEvent(self, event):
        super().showEvent(event)
        window = self.window()
        if window is not self.watched_window:
            if self.watched_window is not None:
                self.watched_window.removeEventFilter(self)
            window.installEventFilter(self)
            self.watched_window = window

    def hideEvent(self, event):
        self.popup.hide()
        super().hideEvent(event)

    def focus_left(self) -> None:
        if not self.field.hasFocus() and not self.popup.underMouse():
            self.popup.hide()

    def eventFilter(self, watched, event):
        if watched is self.watched_window:
            if event.type() in (QEvent.Type.Move, QEvent.Type.Resize, QEvent.Type.WindowDeactivate):
                self.popup.hide()
            return super().eventFilter(watched, event)
        if event.type() == QEvent.Type.FocusOut:
            # Deferred: a click on a suggestion must still reach the list.
            QTimer.singleShot(0, self, self.focus_left)
        elif event.type() == QEvent.Type.KeyPress:
            key = event.key()
            if key in (Qt.Key.Key_Up, Qt.Key.Key_Down) and self.popup.isVisible():
                step = -1 if key == Qt.Key.Key_Up else 1
                self.popup.setCurrentRow((self.popup.currentRow() + step) % self.popup.count())
                return True
            if key in (Qt.Key.Key_Tab, Qt.Key.Key_Return, Qt.Key.Key_Enter):
                if self.draft.stage == EntryStage.COUNT:
                    try:
                        draft = replace(self.draft, count_text=self.field.text()).save()
                        if self.commit is not None and not self.commit(draft, self.target):
                            return True
                        self.committed.emit(draft, self.target)
                    except ValueError:
                        selected = self.draft.selected
                        current = next((a.count for a in self.assignments if selected and a.tag_id == selected.tag_id), None)
                        self.set_error(count_error(current))
                        return True
                    self.cancel()
                elif self.popup.isVisible() and self.popup.currentItem() is not None:
                    self.accept_item()
                elif self.field.text().strip("# "):
                    # Show the matches again; never accept one the user cannot see.
                    self.typed(self.field.text())
                elif key == Qt.Key.Key_Tab:
                    return False
                return True
            if key == Qt.Key.Key_Escape:
                active = bool(self.field.text()) or self.draft.stage == EntryStage.COUNT
                self.cancel()
                if not active:
                    self.dismissed.emit()
                return True
        return super().eventFilter(watched, event)


LOCAL_KEYS = (
    LocalBinding('tags.choose', 'Tag suggestions', InteractionKind.KEYBOARD, ('Up', 'Down'), 'Move between matching tags'),
    LocalBinding('tags.accept', 'Tag entry', InteractionKind.KEYBOARD, ('Enter', 'Return', 'Tab'), 'Choose the highlighted tag, then commit its optional count. Invalid counts stay in the field for correction. From an empty field, Tab moves to the next control.'),
    LocalBinding('tags.cancel', 'Tag entry', InteractionKind.KEYBOARD, ('Esc',), 'Cancel the draft; press again from an empty field to close Tags'),
)
