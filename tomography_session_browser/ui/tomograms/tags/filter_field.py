"""Tag chips reuse the existing query, working set and route restriction."""
from tomography_session_browser.presentation.help_content import LocalBinding, InteractionKind
from dataclasses import replace

from PySide6.QtCore import QEvent, QPoint, Qt, QSignalBlocker
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (QFrame, QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem, QMenu, QPushButton,
                               QScrollArea, QStyledItemDelegate, QVBoxLayout, QWidget, QSizePolicy)
from tomography_session_browser.domain.display_names import count_phrase
from tomography_session_browser.domain.identity import path_identity_key

from tomography_session_browser.presentation.design_tokens import SPACE_M, SPACE_S, SPACE_XS
from tomography_session_browser.presentation.tomogram_tag_filters import TagFilter, CompletionFilter
from tomography_session_browser.presentation.tomogram_tag_entry import matching_tags
from tomography_session_browser.services.tomogram_tags.binding import bind_row, ProjectKey
from tomography_session_browser.ui.theme import current_palette
from tomography_session_browser.ui.widgets.literal_text import literal_mnemonic

CHOICE_ROWS = 8
CHOICE_HINT = "Enter adds the highlighted filter · Esc closes"
COUNT_ROLE = Qt.ItemDataRole.UserRole + 1


class ChoiceCountDelegate(QStyledItemDelegate):
    """A filter's tomogram count, right-aligned. A long name elides before it."""

    def initStyleOption(self, option, index) -> None:  # noqa: N802 - Qt API
        super().initStyleOption(option, index)
        count = index.data(COUNT_ROLE)
        if count:
            room = option.rect.width() - option.fontMetrics.horizontalAdvance(count) - SPACE_M - SPACE_S
            option.text = option.fontMetrics.elidedText(option.text, Qt.TextElideMode.ElideRight, room)

    def paint(self, painter, option, index) -> None:
        super().paint(painter, option, index)
        count = index.data(COUNT_ROLE)
        if count:
            painter.save()
            painter.setPen(QColor(current_palette().text_muted))
            painter.drawText(option.rect.adjusted(0, 0, -SPACE_S, 0), Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, count)
            painter.restore()


class TagFilterChoices(QFrame):
    """Type after # to narrow the choices; every matching filter is reachable."""

    def __init__(self, field):
        super().__init__(field, Qt.WindowType.Popup)
        self.field = field
        self.setObjectName("tomogramTagFilterChoices")
        self.setAccessibleName("Tag filter choices")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(SPACE_XS, SPACE_XS, SPACE_XS, SPACE_XS)
        layout.setSpacing(SPACE_XS)
        self.search = QLineEdit(self)
        self.search.setAccessibleName("Tag filter text")
        self.search.setMaxLength(130)
        self.choices = QListWidget(self)
        self.choices.setAccessibleName("Matching tag filters")
        self.choices.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.choices.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.choices.setItemDelegate(ChoiceCountDelegate(self.choices))
        self.hint = QLabel(CHOICE_HINT, self)
        self.hint.setObjectName("viewerStatus")
        self.hint.setWordWrap(True)
        layout.addWidget(self.search)
        layout.addWidget(self.choices)
        layout.addWidget(self.hint)
        self.search.textEdited.connect(self.fill)
        self.search.installEventFilter(self)
        self.choices.itemClicked.connect(self.accept)

    def open(self, text: str) -> None:
        self.search.setText(text)
        self.fill(text)
        anchor = self.field.listing.query
        screen = anchor.screen().availableGeometry()
        self.resize(min(max(340, anchor.width()), screen.width()), self.height())
        self.fit_height()
        point = anchor.mapToGlobal(QPoint(0, anchor.height()))
        if point.y() + self.height() > screen.bottom() + 1:
            point = anchor.mapToGlobal(QPoint(0, -self.height()))
        point.setX(max(screen.left(), min(point.x(), screen.right() - self.width() + 1)))
        point.setY(max(screen.top(), min(point.y(), screen.bottom() - self.height() + 1)))
        self.move(point)
        self.show()
        self.search.setFocus(Qt.FocusReason.PopupFocusReason)

    def fill(self, text: str) -> None:
        if not text:
            self.close_choices()
            return
        self.choices.clear()
        options = self.field.options(text)
        tag_rows = tuple(self.field.tag_rows().values()) if options else ()
        for index, (label, value) in enumerate(options):
            item = QListWidgetItem(label, self.choices)
            item.setData(Qt.ItemDataRole.UserRole, value)
            # What adding this filter would leave, before text or segment filters.
            matching = sum(value.accepts(key, row) for key, row in tag_rows)
            phrase = count_phrase(matching, "tomogram")
            item.setData(COUNT_ROLE, phrase if index == 0 else str(matching))
            item.setData(Qt.ItemDataRole.AccessibleTextRole, f"{label}, {phrase}")
        count = self.choices.count()
        if count:
            self.choices.setCurrentRow(0)
        self.choices.setVisible(bool(count))
        self.choices.setFixedHeight(min(CHOICE_ROWS, max(1, count)) * max(24, self.choices.sizeHintForRow(0)) + 2 * self.choices.frameWidth())
        self.hint.setText(CHOICE_HINT if count else "No tag filter starts with this text")
        if self.isVisible():
            self.fit_height()

    def fit_height(self) -> None:
        layout = self.layout()
        layout.invalidate()
        layout.activate()
        self.resize(self.width(), layout.heightForWidth(self.width()) if layout.hasHeightForWidth() else layout.sizeHint().height())

    def accept(self, item=None) -> None:
        item = item or self.choices.currentItem()
        value = item.data(Qt.ItemDataRole.UserRole) if item is not None else None
        self.close_choices()
        if value is not None:
            self.field.set_filter(value)

    def close_choices(self) -> None:
        self.hide()
        self.field.listing.query.setFocus(Qt.FocusReason.PopupFocusReason)

    def eventFilter(self, watched, event):
        if watched is self.search and event.type() == QEvent.Type.KeyPress:
            key = event.key()
            if key in (Qt.Key.Key_Up, Qt.Key.Key_Down):
                if self.choices.count():
                    step = -1 if key == Qt.Key.Key_Up else 1
                    self.choices.setCurrentRow((self.choices.currentRow() + step) % self.choices.count())
                return True
            if key in (Qt.Key.Key_Tab, Qt.Key.Key_Return, Qt.Key.Key_Enter):
                self.accept()
                return True
            if key == Qt.Key.Key_Escape:
                self.close_choices()
                return True
        return super().eventFilter(watched, event)


class TagFilterField(QScrollArea):
    def __init__(self, owner):
        super().__init__(owner.tab.review_list)
        self.owner, self.listing = owner, owner.tab.review_list
        self.bar = QWidget(self)
        self.row = QHBoxLayout(self.bar)
        self.row.setContentsMargins(0, 0, 0, 0)
        self.setWidget(self.bar)
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self.setFixedHeight(52)
        self.listing.layout().insertWidget(2, self)
        self.hide()
        self.listing.query.installEventFilter(self)
        self.listing.query.textEdited.connect(self.typed)
        self.listing.filter_restored.connect(self.draw)
        self.listing.filters_cleared.connect(self.draw)
        self.listing.tag_rows = self.tag_rows
        self.owner.projects.changed.connect(self.refresh)
        self.owner.projects.project_moved.connect(self.project_moved)
        self.availability = ()
        self.drawn = None
        self.label_project = None
        self.known_labels = {}
        self.choices = TagFilterChoices(self)

    def tag_rows(self):
        result = {}
        for source in self.owner.tab.payload.sources:
            session = self.owner.projects.session_for(source.path)
            if not session or not session.project:
                continue
            key = ProjectKey.at(session.store.path, session.project)
            for row in source.tomograms:
                binding = bind_row(session.project, source, row)
                if binding.ref:
                    result[row.id] = key, binding.saved
        return result

    def project(self):
        target = self.owner.target()
        if target:
            return target.session
        sessions = {self.owner.projects.session_for(s.path) for s in self.owner.tab.payload.sources}
        sessions.discard(None)
        return next(iter(sessions)) if len(sessions) == 1 else None

    def filter_session(self, value):
        scope = {self.owner.projects.key_for(s.path) for s in self.owner.tab.payload.sources}
        if value.project not in scope:
            return None
        return next((s for s in self.owner.projects.sessions.values() if s.project and not s.retired and
                     ProjectKey.at(s.store.path, s.project) == value.project), None)

    def session_state(self, value):
        session = self.filter_session(value)
        return (id(session), session.status) if session else ()

    def project_moved(self, old, new):
        if self.listing.tag_filter.project == old:
            self.listing.tag_filter = replace(self.listing.tag_filter, project=new)
            self.availability = self.session_state(self.listing.tag_filter)
            self.draw()

    def typed(self, value):
        if "#" not in value:
            return
        query, prefix = value.split("#", 1)
        with QSignalBlocker(self.listing.query):
            self.listing.query.setText(query.rstrip())
        self.show_suggestions(prefix)

    def choice_session(self):
        current = self.listing.tag_filter
        return self.filter_session(current) if current.active else self.project()

    def options(self, text: str) -> tuple[tuple[str, TagFilter], ...]:
        """Every filter that starts with ``text`` and is not already on."""
        current = self.listing.tag_filter
        session = self.choice_session()
        if session is None or session.project is None:
            return ()
        key = ProjectKey.at(session.store.path, session.project)
        folded = text.strip().lstrip("#").strip().casefold()
        completions = tuple((c.value, replace(current, project=key, completion=c)) for c in (CompletionFilter.INCOMPLETE, CompletionFilter.COMPLETE)
                            if c != current.completion and c.value.casefold().startswith(folded))
        return completions + tuple(("#" + t.name, replace(current, project=key, tag_ids=current.tag_ids | {t.id}))
                                   for t in matching_tags(text, session.project.catalogue) if t.id not in current.tag_ids)

    def show_suggestions(self, prefix: str = "") -> None:
        session = self.choice_session()
        if session is None or session.project is None:
            menu = QMenu(self)
            menu.addAction("Select a tomogram to choose its project's tags" if self.owner.target() is None else "This tomogram has no available tag project").setEnabled(False)
            menu.exec(self.listing.query.mapToGlobal(self.listing.query.rect().bottomLeft()))
            return
        self.choices.open("#" + prefix)

    def set_filter(self, value: TagFilter) -> None:
        self.listing.tag_filter = value if value.active else TagFilter()
        self.availability = self.session_state(self.listing.tag_filter)
        self.listing.filters_changed()
        self.draw()

    def clear(self, *, notify=True) -> None:
        self.listing.tag_filter = TagFilter()
        if notify:
            self.listing.filters_changed()
        self.draw()

    def description(self, value=None) -> str:
        value = value or self.listing.tag_filter
        session = self.filter_session(value)
        names = {t.id: t.name for t in session.project.catalogue} if session and session.project else self.known_labels if value.project == self.label_project else {}
        labels = ["#" + names.get(i, "unavailable tag") for i in sorted(value.tag_ids)]
        if value.completion != CompletionFilter.ANY:
            labels.append(value.completion.value)
        return " · ".join(labels)

    def refresh(self, remapped=None) -> None:
        value = self.listing.tag_filter
        if not value.active:
            self.draw()
            return
        session = self.filter_session(value)
        loading = any(path_identity_key(s.path) in self.owner.projects.loading and
                      self.owner.projects.bindings.get(path_identity_key(s.path)) == value.project.path
                      for s in self.owner.tab.payload.sources) if value.project else False
        if session is None and loading:
            self.draw()
            return
        available = frozenset(t.id for t in session.project.catalogue) if session and session.project else frozenset()
        revised = value.catalogue_changed(available, remapped or {}) if session else TagFilter()
        if revised != value:
            label = self.description(value)
            if not revised.active:
                self.listing.restrict(frozenset(self.listing.ordered_keys), "preserved selection · " + label)
            self.listing.tag_filter = revised if revised.active else TagFilter()
            self.owner.tab.status.message.setText("Some tag filters were removed; the current selection was kept. Clear filters to recompute it.")
        state = self.session_state(self.listing.tag_filter)
        if state != self.availability and self.listing.route_keys is None:
            self.listing.membership = None
            self.listing.rebuild()
        self.availability = state
        self.draw()

    def restore(self, value: TagFilter) -> None:
        self.listing.tag_filter = value
        self.refresh()
        self.draw()

    def draw(self) -> None:
        value = self.listing.tag_filter
        session = self.filter_session(value)
        labels = {t.id: t.name for t in session.project.catalogue} if session and session.project else {}
        if self.label_project != value.project:
            self.label_project, self.known_labels = value.project, {}
        self.known_labels.update(labels)
        labels = self.known_labels
        multiple = len({self.owner.projects.key_for(s.path) for s in self.owner.tab.payload.sources} - {None}) > 1
        suffix = f" · {session.store.path.stem}" if multiple and session else ""
        content = value, tuple((key, labels.get(key, "unavailable tag")) for key in sorted(value.tag_ids)), suffix
        if content == self.drawn:
            self.listing.refresh_counts()
            return
        self.drawn = content
        while self.row.count():
            item = self.row.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        for identity in sorted(value.tag_ids):
            button = QPushButton(literal_mnemonic("#" + labels.get(identity, "unavailable tag") + suffix) + " ×", self.bar)
            button.clicked.connect(lambda _, i=identity: self.set_filter(replace(value, tag_ids=value.tag_ids - {i})))
            self.row.addWidget(button)
        if value.completion != CompletionFilter.ANY:
            button = QPushButton(literal_mnemonic(value.completion.value + suffix) + " ×", self.bar)
            button.clicked.connect(lambda: self.set_filter(replace(value, completion=CompletionFilter.ANY)))
            self.row.addWidget(button)
        self.row.addStretch(1)
        self.setVisible(value.active)
        self.listing.refresh_counts()

    def eventFilter(self, watched, event):
        if event.type() == QEvent.Type.KeyPress:
            if event.text() == "#":
                self.show_suggestions()
                return True
            if event.key() == Qt.Key.Key_Backspace and not self.listing.query.text() and self.listing.tag_filter.active:
                value = self.listing.tag_filter
                if value.completion != CompletionFilter.ANY:
                    self.set_filter(replace(value, completion=CompletionFilter.ANY))
                else:
                    self.set_filter(replace(value, tag_ids=value.tag_ids - {sorted(value.tag_ids)[-1]}))
                return True
        return super().eventFilter(watched, event)


LOCAL_KEYS = (
    LocalBinding('tag-filter.open', 'Tomograms filter', InteractionKind.KEYBOARD, ('#',), 'Open the tag filter choices. Type after # to show only the matching tags'),
    LocalBinding('tag-filter.remove', 'Tomograms filter', InteractionKind.KEYBOARD, ('Backspace',), 'From an empty query, remove the completion filter or the last tag filter'),
    LocalBinding('tag-filter.choose', 'Tag filter choices', InteractionKind.KEYBOARD, ('Up', 'Down'), 'Move between matching filters'),
    LocalBinding('tag-filter.accept', 'Tag filter choices', InteractionKind.KEYBOARD, ('Enter', 'Return', 'Tab'), 'Add the highlighted filter as a chip'),
    LocalBinding('tag-filter.cancel', 'Tag filter choices', InteractionKind.KEYBOARD, ('Esc',), 'Close the choices and return to the list filter'),
)
