"""The Load sessions window: queue session folders, see which will load, load.

It replaces the two-field "Select session folder" dialog. Any number of atlas
screening and data collection folders go into one list, by browsing or by
dropping them on the window; each is checked as it arrives
(``services/session_intake.py``, the same check the project panel's drop uses)
and a folder that will not load says why, with "Use parent folder" when a
session file was chosen instead of its folder. Recent folders are one more way
to queue: a chip calls ``add_paths``, as browsing and dropping do, so it inherits
the same check, and the window never touches a recent folder until one is chosen.
With recents, the queue area is reserved from first show, so queuing never moves
a chip, and a keyboard choice hands focus to Load, or to Browse if the folder
will not load (``ui/widgets/recent_session_row.py`` owns the chips' keys).

Loading always adds to the current project, as Open and Import both did. The
window only chooses folders: it reads nothing but what classification needs,
writes no settings or history, and the source folders stay read-only.
"""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

from PySide6.QtCore import QEvent, QObject, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QIcon, QPainter, QPen
from PySide6.QtWidgets import (
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from tomography_session_browser.domain.display_names import count_phrase
from tomography_session_browser.domain.enums import SessionKind
from tomography_session_browser.domain.identity import path_identity_key
from tomography_session_browser.services.session_intake import (
    DRAG_CHECK_BUDGET_S,
    Admission,
    DroppedFolder,
    FolderCheck,
    QueueEntry,
    SessionIntake,
    admit,
    folder_typing,
    replace_entry,
)
from tomography_session_browser.services.settings_service import recent_session_paths
from tomography_session_browser.ui.folder_picker import select_session_folder
from tomography_session_browser.ui.icons import themed_icon
from tomography_session_browser.presentation.design_tokens import SPACE_L, SPACE_M, SPACE_S, SPACE_XL, SPACE_XS, SPACE_XXL
from tomography_session_browser.ui.theme import current_palette
from tomography_session_browser.ui.widgets.elided_label import ElidedLabel
from tomography_session_browser.ui.widgets.recent_session_row import (
    METRIC_CHANGE_EVENTS,
    Activation,
    HeldEnterFilter,
    RecentSessionRow,
)
from tomography_session_browser.ui.widgets.session_drop import (
    Glyph,
    GlyphButton,
    dropped_paths,
    paint_brackets,
    tone_colour,
)

LOAD_SESSIONS_TITLE = "Load sessions"
LOAD_SESSIONS_SUBTITLE = (
    "Add acquisition, Warp, IMOD, AreTomo3, Scipion or reconstruction folders. "
    "Sessions that belong together are linked when they load."
)
DROP_WELL_TITLE = "Drop session folders here"
BROWSE_HINT = "Browse adds one folder at a time; drop several at once"
NOTHING_QUEUED_NOTE = "Nothing queued yet"
#: The footer's second line while a queued folder can change on disk (D14, D17;
#: board Q3). Browse and a drop are the routes: a queued folder's chip is off.
RETRY_NOTE = "To check a folder again, add it again."
#: How many recent folders the window offers.
RECENT_CHIPS = 3
MINIMUM_WIDTH = 560
BASE_MINIMUM_HEIGHT = 440


def load_button_text(ready_count: int) -> str:
    return f"Load {count_phrase(ready_count, 'session')}" if ready_count else "Load sessions"


def footer_note(queued: int, loaded_count: int) -> str:
    if not queued:
        return NOTHING_QUEUED_NOTE
    if loaded_count:
        noun = "session" if loaded_count == 1 else "sessions"
        return f"Adds to the {loaded_count} {noun} already loaded"
    return "Starts a new project"


class _DropWell(QFrame):
    """Where folders are dropped: corner brackets, and a tone while dragging."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("loadSessionsDropWell")
        self._tone = ""

    @property
    def tone(self) -> str:
        return self._tone

    def set_tone(self, tone: str) -> None:
        self._tone = tone
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt API
        super().paintEvent(event)
        palette = current_palette()
        colour = tone_colour(palette, self._tone or "accent")
        colour.setAlphaF(0.95 if self._tone else 0.55)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(QPen(colour, 1.5))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        inset = SPACE_S + 2
        paint_brackets(painter, QRectF(self.rect()).adjusted(inset, inset, -inset, -inset), 16.0)


class QueuedFolderRow(QFrame):
    """One queued folder: what it holds, or why it will not load.

    A row lives as long as its queue entry. When the entry's check changes (a
    retry, or "Use parent folder"), ``show_folder`` shows it in the same row.
    """

    remove_requested = Signal(str)
    use_parent_requested = Signal(str)

    def __init__(self, folder: DroppedFolder, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("queuedFolderRow")
        self._layout = layout = QHBoxLayout(self)
        layout.setContentsMargins(SPACE_M, SPACE_S, SPACE_S, SPACE_S)
        layout.setSpacing(SPACE_M)

        self.badge = QLabel("", self)
        self.badge.setObjectName("queuedFolderBadge")
        self.badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.badge.setFixedSize(30, 20)
        layout.addWidget(self.badge)

        self._text = QVBoxLayout()
        self._text.setSpacing(0)
        self.name = ElidedLabel("", mode=Qt.TextElideMode.ElideMiddle, parent=self)
        self.name.setObjectName("queuedFolderName")
        self._text.addWidget(self.name)
        self.detail: ElidedLabel | None = None
        layout.addLayout(self._text, 1)

        self.kind = QLabel("", self)
        self.kind.setObjectName("queuedFolderKind")
        layout.addWidget(self.kind)

        self.parent_button: QPushButton | None = None
        self.status: Glyph | None = None
        self.remove_button = GlyphButton("x", "", self, size=28)
        self.remove_button.clicked.connect(lambda: self.remove_requested.emit(self.folder.path))
        layout.addWidget(self.remove_button)
        self.show_folder(folder)

    def show_folder(self, folder: DroppedFolder) -> None:
        """Show ``folder``'s check. Controls that no longer apply are retired."""

        self.folder = folder
        # A folder the load will ask about takes the warning tone, not the refusal red (owner D14 a).
        tone = "bad" if folder.problem else "warn" if folder.asks else ""
        self.setProperty("tone", tone)
        self.badge.setText(folder.badge or "–")
        self.badge.setProperty("empty", not folder.badge)
        self.badge.setProperty("tone", tone)
        self.name.setText(folder.name)
        if self.detail is not None:
            self._text.removeWidget(self.detail)
            self.detail.deleteLater()
        if folder.problem:
            self.detail = ElidedLabel(folder.reason, parent=self)
            self.detail.setObjectName("queuedFolderReason")
        elif folder.type_note:
            # "The app cannot find the type of 3 files." or "Read as: tomograms · your selection".
            self.detail = ElidedLabel(folder.type_note, parent=self)
            self.detail.setObjectName("queuedFolderNote" if folder.asks else "queuedFolderPath")
            self.detail.setToolTip(folder.path)
        else:
            self.detail = ElidedLabel(folder.path, mode=Qt.TextElideMode.ElideMiddle, parent=self)
            self.detail.setObjectName("queuedFolderPath")
        self._text.addWidget(self.detail)
        self.kind.setText(folder.kind_label)
        self.kind.setHidden(not folder.kind_label)
        self.kind.setProperty("tone", tone)

        for old in (self.parent_button, self.status):
            if old is not None:
                self._layout.removeWidget(old)
                old.hide()
                old.deleteLater()
        self.parent_button = self.status = None
        slot = self._layout.indexOf(self.remove_button)
        if folder.status == FolderCheck.SESSION_FILE and folder.parent_folder:
            self.parent_button = QPushButton("Use parent folder", self)
            self.parent_button.setToolTip(folder.parent_folder)
            self.parent_button.clicked.connect(lambda: self.use_parent_requested.emit(self.folder.path))
            self._layout.insertWidget(slot, self.parent_button)
        else:
            glyph, glyph_tone, status = (
                ("help-circle", "warn", "Select at load")
                if folder.asks
                else ("check-circle", "good", "Ready")
                if folder.ready
                else ("x-circle", "bad", "Won’t load")
            )
            self.status = Glyph(glyph, glyph_tone, 18, self)
            self.status.setAccessibleName(status)
            self._layout.insertWidget(slot, self.status)

        self.remove_button.setAccessibleName(f"Remove {folder.name}")
        parts = (folder.name, folder.kind_label, folder.reason or folder.type_note or "ready")
        self.setAccessibleName(", ".join(part for part in parts if part))
        for widget in (self, self.badge, self.kind):
            widget.style().unpolish(widget)
            widget.style().polish(widget)

    @property
    def controls(self) -> tuple[QWidget, ...]:
        """The row's keyboard stops, in Tab order."""

        return (self.parent_button, self.remove_button) if self.parent_button is not None else (self.remove_button,)


class _RevealOnFocus(QObject):
    """Scrolls a row control into view when it takes focus.

    The queue's scroll area is not a Tab stop (board Q1), and Qt scrolls only
    when focus moves between its own children, not when it arrives from Clear
    list or Cancel.
    """

    def __init__(self, scroll: QScrollArea) -> None:
        super().__init__(scroll)
        self._scroll = scroll

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802 - Qt API
        if event.type() == QEvent.Type.FocusIn and isinstance(watched, QWidget):
            self._scroll.ensureWidgetVisible(watched, 0, 0)
        return False


class LoadSessionsDialog(QDialog):
    """Queue any number of session folders and load them together."""

    #: Set only with a Recent row. Qt sends events while the window is being built.
    _remeasure: QTimer | None = None

    def __init__(
        self,
        loader,
        start_path: str,
        parent: QWidget | None = None,
        *,
        loaded_paths: Iterable[str | Path] = (),
        recent_paths: Iterable[str] = (),
    ) -> None:
        super().__init__(parent)
        self._loader = loader
        self._start_path = start_path
        self._loaded_keys = frozenset(path_identity_key(path) for path in loaded_paths)
        # The queue: entries in order, each row keyed by its entry's token.
        self._entries: tuple[QueueEntry[object], ...] = ()
        self._row_for: dict[object, QueuedFolderRow] = {}
        self.rows: list[QueuedFolderRow] = []
        # What a drag would add, until it drops or leaves; the queue waits for the drop.
        self._preview: Admission[object] | None = None
        self.setObjectName("loadSessionsDialog")
        self.setWindowTitle(LOAD_SESSIONS_TITLE)
        self.setAcceptDrops(True)
        self.resize(760, 580)
        self.setMinimumSize(MINIMUM_WIDTH, BASE_MINIMUM_HEIGHT)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        body = QWidget(self)
        body.setObjectName("loadSessionsBody")
        layout = QVBoxLayout(body)
        layout.setContentsMargins(SPACE_XXL, SPACE_XL, SPACE_XXL, SPACE_XL)
        layout.setSpacing(0)
        outer.addWidget(body, 1)

        header = QHBoxLayout()
        header.setSpacing(SPACE_M)
        emblem = QLabel(body)
        emblem.setObjectName("loadSessionsEmblem")
        emblem.setFixedSize(40, 40)
        emblem.setAlignment(Qt.AlignmentFlag.AlignCenter)
        emblem.setPixmap(themed_icon("folder-plus", color=current_palette().accent, size=20).pixmap(20, 20))
        header.addWidget(emblem, 0, Qt.AlignmentFlag.AlignTop)
        titles = QVBoxLayout()
        titles.setSpacing(SPACE_XS)
        self.title = QLabel(LOAD_SESSIONS_TITLE, body)
        self.title.setObjectName("loadSessionsTitle")
        titles.addWidget(self.title)
        self.subtitle = QLabel(LOAD_SESSIONS_SUBTITLE, body)
        self.subtitle.setObjectName("loadSessionsSubtitle")
        self.subtitle.setWordWrap(True)
        titles.addWidget(self.subtitle)
        header.addLayout(titles, 1)
        layout.addLayout(header)
        layout.addSpacing(SPACE_XL)

        self.drop_well = _DropWell(body)
        well = QHBoxLayout(self.drop_well)
        well.setContentsMargins(SPACE_XL, SPACE_XL, SPACE_XL, SPACE_XL)
        well.setSpacing(SPACE_L)
        well.addStretch(1)
        self.drop_emblem = Glyph("folder-down", "accent", 48, self.drop_well, ring=True)
        well.addWidget(self.drop_emblem)
        well_text = QVBoxLayout()
        well_text.setSpacing(SPACE_S)
        # Kept together, centred, when the empty window gives the well its room.
        well_text.addStretch(1)
        self.drop_title = QLabel(DROP_WELL_TITLE, self.drop_well)
        self.drop_title.setObjectName("dropWellTitle")
        well_text.addWidget(self.drop_title)
        browse_row = QHBoxLayout()
        browse_row.setSpacing(SPACE_M)
        self.browse_button = QPushButton("Browse folders…", self.drop_well)
        self.browse_button.setIcon(themed_icon("folder-open"))
        self.browse_button.clicked.connect(self._browse)
        browse_row.addWidget(self.browse_button)
        self.browse_hint = ElidedLabel(BROWSE_HINT, parent=self.drop_well, prefer_full_width=True)
        self.browse_hint.setObjectName("dropWellHint")
        self.browse_hint.setAccessibleName(BROWSE_HINT)
        browse_row.addWidget(self.browse_hint)
        browse_row.addStretch(1)
        well_text.addLayout(browse_row)
        recent = recent_session_paths(recent_paths, excluded_keys=self._loaded_keys)[:RECENT_CHIPS]
        self.recent_row = RecentSessionRow(recent, self.drop_well) if recent else None
        if self.recent_row is not None:
            self.recent_row.path_chosen.connect(self._add_recent)
            self.recent_row.focus_released.connect(self.browse_button.setFocus)
            self.drop_well.setProperty("hasRecent", True)
            well_text.addWidget(self.recent_row)
        well_text.addStretch(1)
        well.addLayout(well_text)
        well.addStretch(1)
        layout.addWidget(self.drop_well)

        self.queue = QWidget(body)
        queue_layout = QVBoxLayout(self.queue)
        queue_layout.setContentsMargins(0, SPACE_XL, 0, 0)
        queue_layout.setSpacing(SPACE_S)
        queue_header = QHBoxLayout()
        queue_header.setSpacing(SPACE_M)
        heading = QLabel("QUEUED", self.queue)
        heading.setObjectName("loadSessionsQueueHeading")
        queue_header.addWidget(heading)
        self.ready_count = QLabel("", self.queue)
        self.ready_count.setObjectName("queueCount")
        self.ready_count.setProperty("tone", "good")
        queue_header.addWidget(self.ready_count)
        self.select_count = QLabel("", self.queue)
        self.select_count.setObjectName("queueCount")
        self.select_count.setProperty("tone", "warn")
        queue_header.addWidget(self.select_count)
        self.problem_count = QLabel("", self.queue)
        self.problem_count.setObjectName("queueCount")
        self.problem_count.setProperty("tone", "bad")
        queue_header.addWidget(self.problem_count)
        queue_header.addStretch(1)
        self.clear_button = QPushButton("Clear list", self.queue)
        self.clear_button.setObjectName("secondaryAction")
        self.clear_button.clicked.connect(self.clear)
        queue_header.addWidget(self.clear_button)
        queue_layout.addLayout(queue_header)
        self.scroll = QScrollArea(self.queue)
        self.scroll.setObjectName("queuedFolderScroll")
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        # Not a Tab stop: Tab goes from Clear list to the rows (board Q1).
        self.scroll.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._reveal = _RevealOnFocus(self.scroll)
        self.list = QFrame()
        self.list.setObjectName("queuedFolderList")
        self._list_layout = QVBoxLayout(self.list)
        self._list_layout.setContentsMargins(0, 0, 0, 0)
        self._list_layout.setSpacing(0)
        self._list_layout.addStretch(1)
        self.scroll.setWidget(self.list)
        queue_layout.addWidget(self.scroll, 1)
        layout.addWidget(self.queue, 1)

        footer = QFrame(self)
        footer.setObjectName("loadSessionsFooter")
        footer_layout = QHBoxLayout(footer)
        footer_layout.setContentsMargins(SPACE_XXL, SPACE_L, SPACE_XL, SPACE_L)
        footer_layout.setSpacing(SPACE_S)
        # The note, and under it the retry line (board Q2, Q3), shown only while a
        # queued folder can change on disk. The icon stays with the first line.
        notes = QVBoxLayout()
        notes.setSpacing(SPACE_XS)
        first = QHBoxLayout()
        first.setSpacing(SPACE_S)
        self.footer_icon = Glyph("info", "muted", 14, footer)
        first.addWidget(self.footer_icon)
        self.footer_note = QLabel("", footer)
        self.footer_note.setObjectName("loadSessionsFooterNote")
        first.addWidget(self.footer_note)
        notes.addLayout(first)
        second = QHBoxLayout()
        # Qt puts no spacing beside a spacer, so the indent carries the icon's gap too.
        second.addSpacing(self.footer_icon.width() + SPACE_S)
        self.retry_note = QLabel(RETRY_NOTE, footer)
        self.retry_note.setObjectName("loadSessionsFooterNote")
        self.retry_note.setHidden(True)
        second.addWidget(self.retry_note)
        notes.addLayout(second)
        footer_layout.addLayout(notes)
        footer_layout.addStretch(1)
        self.cancel_button = QPushButton("Cancel", footer)
        self.cancel_button.clicked.connect(self.reject)
        # At the bottom, so a taller footer (the retry line) moves neither button.
        footer_layout.addWidget(self.cancel_button, 0, Qt.AlignmentFlag.AlignBottom)
        self.load_button = QPushButton(load_button_text(0), footer)
        self.load_button.setObjectName("primaryButton")
        self._load_arrow = themed_icon("arrow-right", color=current_palette().primary_text)
        self.load_button.setLayoutDirection(Qt.LayoutDirection.RightToLeft)
        self.load_button.setDefault(True)
        self.load_button.setAutoDefault(True)
        self.load_button.clicked.connect(self.accept)
        footer_layout.addWidget(self.load_button, 0, Qt.AlignmentFlag.AlignBottom)
        outer.addWidget(footer)

        # Focus moves on after a choice, a removal or a chip that goes while its
        # key is held; the key's repeats must not then act where it lands. Each
        # row's controls join when the row is made.
        self._held_enter = HeldEnterFilter(self, (self.browse_button, self.load_button, self))
        self._publish(())
        if self.recent_row is not None:
            self._hold_the_queue_area()

    def _order_the_keyboard(self) -> None:
        """Tab goes Browse, the chips, Clear list, the rows' controls, Cancel, Load (D13).

        Qt skips hidden and disabled controls itself, so fitting changes no order.
        Set again whenever the rows change.
        """

        chips = self.recent_row.chips if self.recent_row is not None else ()
        controls = [control for row in self.rows for control in row.controls]
        chain = (self.browse_button, *chips, self.clear_button, *controls, self.cancel_button, self.load_button)
        for first, second in zip(chain, chain[1:]):
            QWidget.setTabOrder(first, second)

    def _hold_the_queue_area(self) -> None:
        """Give the queue its room from the start, so that no chip ever moves.

        If the well shrank when the first folder arrived, the chips would jump
        and the second click of a double-click could land on Clear list.
        """

        policy = self.queue.sizePolicy()
        policy.setRetainSizeWhenHidden(True)
        self.queue.setSizePolicy(policy)
        # Theme changes can arrive in several steps: measure once, after them all.
        self._remeasure = QTimer(self)
        self._remeasure.setSingleShot(True)
        self._remeasure.setInterval(0)
        self._remeasure.timeout.connect(self._fit_minimum_to_contents)
        # The chips fit themselves after a style change, perhaps after this window measured.
        self.recent_row.refitted.connect(self._remeasure.start)
        self._fit_minimum_to_contents()

    def _queue_row_height(self) -> int:
        """The tallest queue row, from the real row, with no folder looked at."""

        heights = []
        for folder in (
            DroppedFolder("representative/collection", FolderCheck.READY, kind=SessionKind.COLLECTION),
            DroppedFolder("representative/Session.dm", FolderCheck.SESSION_FILE, parent_folder="representative"),
        ):
            row = QueuedFolderRow(folder, self.list)
            for widget in (row, *row.findChildren(QWidget)):
                widget.ensurePolished()
            heights.append(row.sizeHint().height())
            row.setParent(None)
            row.deleteLater()
        return max(heights)

    def _fit_minimum_to_contents(self) -> None:
        """The well, the chips and one whole queue row must fit at the smallest size.

        Measured, because text size decides it. Set again after a style change,
        which also resets minimums on the styled widgets themselves.
        """

        for widget in (self, *self.findChildren(QWidget)):
            widget.ensurePolished()
        one_row = self._queue_row_height() + 2 * self.list.frameWidth()
        self.scroll.setMinimumHeight(max(self.scroll.minimumSizeHint().height(), one_row))
        # A hidden queue asks for no new layout by itself, so its old size stays cached.
        self.queue.updateGeometry()
        self.queue.parentWidget().updateGeometry()
        layout = self.layout()
        # Measured with the retry line shown, so the line never makes the window
        # grow and one whole queue row still fits while it shows.
        hidden = not self.retry_note.isVisibleTo(self)
        footer = self.retry_note.parentWidget()
        if hidden:
            self.retry_note.setVisible(True)
            footer.layout().invalidate()
            footer.updateGeometry()
        layout.invalidate()
        needed = max(layout.minimumSize().height(), layout.minimumHeightForWidth(MINIMUM_WIDTH))
        if hidden:
            self.retry_note.setVisible(False)
            footer.layout().invalidate()
            footer.updateGeometry()
            layout.invalidate()
        self.setMinimumHeight(max(BASE_MINIMUM_HEIGHT, needed))

    def event(self, event) -> bool:  # noqa: D102 - Qt override
        if self._remeasure is not None and event.type() in METRIC_CHANGE_EVENTS:
            self._remeasure.start()
        return super().event(event)

    # -- the queue ------------------------------------------------------------
    # Admission (``services/session_intake.admit``) owns every file-system look.
    # Everything below works from the stored entries: rows, counts, the footer
    # and Load are derived, and Remove and Clear look at nothing.

    @property
    def intake(self) -> SessionIntake:
        return SessionIntake(tuple(entry.folder for entry in self._entries))

    def selected_paths(self) -> list[str]:
        """The folders that will load, in the order they were queued."""

        return list(self.intake.ready_paths)

    def add_paths(self, paths: Iterable[str | Path]) -> Admission[object]:
        """Queue ``paths``. Only new paths, and paths of rows that can change on disk, are looked at."""

        admission = admit(self._entries, paths, self._loader.classify, loaded_keys=self._loaded_keys,
                          read_as=folder_typing(self._loader))
        self._publish(admission.entries)
        return admission

    def _add_recent(self, path: str, activation: Activation) -> None:
        """Queue a recent folder. After the keyboard, Enter then loads it, or
        Browse is offered if it will not load, even when other folders will."""

        admission = self.add_paths([path])
        if activation is Activation.KEYBOARD:
            ready = bool(admission.records) and admission.records[0].folder.ready
            target = self.load_button if ready else self.browse_button
            target.setFocus(Qt.FocusReason.OtherFocusReason)

    def remove_path(self, path: str) -> None:
        key = path_identity_key(path)
        self._publish(tuple(entry for entry in self._entries if path_identity_key(entry.spelling) != key))

    def use_parent_folder(self, path: str) -> None:
        """Swap a session file for the folder that holds it, in the same place. Only that folder is looked at."""

        index = next((index for index, entry in enumerate(self._entries) if entry.folder.path == path), None)
        if index is None or not self._entries[index].folder.parent_folder:
            return
        parent = self._entries[index].folder.parent_folder
        self._publish(replace_entry(self._entries, index, parent, self._loader.classify, loaded_keys=self._loaded_keys,
                                    read_as=folder_typing(self._loader)))

    def clear(self) -> None:
        self._publish(())

    def _publish(self, entries: Iterable[QueueEntry[object]]) -> None:
        """Show ``entries``: rows are kept, updated, made or retired by token."""

        # Before any chip or row changes: Qt moves focus on from a control that goes.
        focus = self.focusWidget()
        old_rows = list(self.rows)
        focus_row = next((row for row in old_rows if focus is not None and row.isAncestorOf(focus)), None)
        focus_slot = focus_row.controls.index(focus) if focus_row is not None and focus in focus_row.controls else 0

        published: list[QueueEntry[object]] = []
        rows: list[QueuedFolderRow] = []
        for entry in entries:
            if entry.token is None:
                entry = QueueEntry(entry.spelling, entry.key, entry.folder, object())
            row = self._row_for.get(entry.token)
            if row is None:
                row = self._make_row(entry.folder)
            elif row.folder != entry.folder:
                row.show_folder(entry.folder)
                self._watch(row)
            published.append(entry)
            rows.append(row)
        kept = set(map(id, rows))
        retired = [row for row in old_rows if id(row) not in kept]
        self._entries = tuple(published)
        self._row_for = {entry.token: row for entry, row in zip(published, rows)}
        self.rows = rows

        for row in old_rows:
            self._list_layout.removeWidget(row)
        for index, row in enumerate(rows):
            self._list_layout.insertWidget(index, row)
            row.setProperty("last", index == len(rows) - 1)
            row.style().unpolish(row)
            row.style().polish(row)
        target = self._hand_off(focus_row, focus_slot, old_rows, rows) if focus_row in retired else None
        if focus_row is not None and focus_row in rows and focus not in focus_row.controls:
            # The row stays, but the control that had focus went ("Use parent folder").
            target = focus_row.controls[min(focus_slot, len(focus_row.controls) - 1)]
        for row in retired:
            row.hide()
            row.deleteLater()

        intake = self.intake
        queued, ready, problems = len(intake.folders), len(intake.ready), len(intake.problems)
        # A folder that will ask counts in "Load N sessions", not in "N ready" (owner D14 a).
        to_select = sum(1 for folder in intake.ready if folder.asks)
        self.queue.setVisible(bool(queued))
        # With nothing queued the drop well takes the room the list would, unless
        # it holds chips, which must stay where they are.
        self.drop_well.setSizePolicy(
            QSizePolicy.Policy.Preferred,
            QSizePolicy.Policy.Fixed if queued or self.recent_row is not None else QSizePolicy.Policy.Expanding,
        )
        if self.recent_row is not None:
            self.recent_row.set_queued({path_identity_key(entry.spelling) for entry in self._entries})
        self.ready_count.setText(f"{ready - to_select} ready")
        self.ready_count.setVisible(bool(ready - to_select))
        self.select_count.setText(f"{to_select} to select")
        self.select_count.setVisible(bool(to_select))
        self.problem_count.setText(f"{problems} won’t load")
        self.problem_count.setVisible(bool(problems))
        self.load_button.setText(load_button_text(ready))
        self.load_button.setEnabled(bool(ready))
        # The arrow is drawn in the enabled label colour; a muted button drops it.
        self.load_button.setIcon(self._load_arrow if ready else QIcon())
        self.footer_note.setText(footer_note(queued, len(self._loaded_keys)))
        # The window minimum already holds the line (``_fit_minimum_to_contents``).
        self.retry_note.setVisible(any(entry.folder.retryable for entry in self._entries))
        self._order_the_keyboard()
        self._keep_focus(focus, target)

    def _make_row(self, folder: DroppedFolder) -> QueuedFolderRow:
        row = QueuedFolderRow(folder, self.list)
        row.remove_requested.connect(self.remove_path)
        row.use_parent_requested.connect(self.use_parent_folder)
        self._watch(row)
        return row

    def _watch(self, row: QueuedFolderRow) -> None:
        """A row's controls drop held-key repeats and scroll into view on focus."""

        for control in row.controls:
            control.installEventFilter(self._held_enter)
            control.installEventFilter(self._reveal)

    def _hand_off(self, row: QueuedFolderRow, slot: int, old_rows: list[QueuedFolderRow],
                  rows: list[QueuedFolderRow]) -> QWidget:
        """Where focus goes when its row is retired: the same control in the next
        row, else in the previous row, else Browse."""

        position = old_rows.index(row)
        after = [candidate for candidate in old_rows[position + 1:] if candidate in rows]
        before = [candidate for candidate in old_rows[:position] if candidate in rows]
        destination = after[0] if after else (before[-1] if before else None)
        if destination is None:
            return self.browse_button
        controls = destination.controls
        if len(controls) == len(row.controls):
            return controls[slot]
        return destination.remove_button

    def _keep_focus(self, focus: QWidget | None, target: QWidget | None = None) -> None:
        """Leave focus where it was, or hand it on when its control went.

        Qt would pass it to the next control, which can be Clear list, or drop it
        when a row is deleted. A chip that turned off, and Clear list when the
        queue empties, hand it to Browse.
        """

        if target is not None:
            target.setFocus(Qt.FocusReason.OtherFocusReason)
            return
        if focus is None or self.focusWidget() is focus:
            return
        if focus.isVisibleTo(self) and focus.isEnabled():
            focus.setFocus(Qt.FocusReason.OtherFocusReason)
        elif focus is self.clear_button or (self.recent_row is not None and focus in self.recent_row.chips):
            self.browse_button.setFocus(Qt.FocusReason.OtherFocusReason)

    # -- browsing and dropping ------------------------------------------------------

    def _browse(self) -> None:
        start = str(Path(self._entries[-1].spelling).parent) if self._entries else self._start_path
        path = select_session_folder(self, "Select session folder", start, "Add folder")
        if path:
            self.add_paths([path])

    def dragEnterEvent(self, event) -> None:  # noqa: N802 - Qt API
        paths = dropped_paths(event.mimeData())
        if not paths:
            event.ignore()
            return
        # A preview within the drag budget; the queue does not change until the drop.
        self._preview = admit(self._entries, paths, self._loader.classify,
                              loaded_keys=self._loaded_keys, budget_s=DRAG_CHECK_BUDGET_S,
                              read_as=folder_typing(self._loader))
        self.drop_well.set_tone(self._preview.supplied.tone)
        event.setDropAction(Qt.DropAction.CopyAction)
        event.accept()

    def dragMoveEvent(self, event) -> None:  # noqa: N802 - Qt API
        event.setDropAction(Qt.DropAction.CopyAction)
        event.accept()

    def dragLeaveEvent(self, event) -> None:  # noqa: N802 - Qt API
        self._preview = None
        self.drop_well.set_tone("")
        super().dragLeaveEvent(event)

    def dropEvent(self, event) -> None:  # noqa: N802 - Qt API
        self.drop_well.set_tone("")
        preview, self._preview = self._preview, None
        paths = dropped_paths(event.mimeData())
        if not paths:
            event.ignore()
            return
        event.setDropAction(Qt.DropAction.CopyAction)
        event.accept()
        # Even folders that will not load are queued, so the list can say why. The
        # drag's completed checks are reused; only what it had no time for is looked at.
        known = preview.records if preview is not None else ()
        admission = admit(self._entries, paths, self._loader.classify, loaded_keys=self._loaded_keys, known=known,
                          read_as=folder_typing(self._loader))
        self._publish(admission.entries)

    def done(self, result: int) -> None:  # noqa: D102 - Qt override
        self._preview = None
        super().done(result)

    def accept(self) -> None:  # noqa: D102 - Qt override
        if not self.intake.ready:
            return
        super().accept()
