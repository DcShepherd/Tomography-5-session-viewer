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

from PySide6.QtCore import QRectF, Qt, QTimer, Signal
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
    DroppedFolder,
    FolderCheck,
    SessionIntake,
    assess_paths,
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
    """One queued folder: what it holds, or why it will not load."""

    remove_requested = Signal(str)
    use_parent_requested = Signal(str)

    def __init__(self, folder: DroppedFolder, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.folder = folder
        self.setObjectName("queuedFolderRow")
        self.setProperty("tone", "bad" if folder.problem else "")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(SPACE_M, SPACE_S, SPACE_S, SPACE_S)
        layout.setSpacing(SPACE_M)

        self.badge = QLabel(folder.badge or "–", self)
        self.badge.setObjectName("queuedFolderBadge")
        self.badge.setProperty("empty", not folder.badge)
        self.badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.badge.setFixedSize(30, 20)
        layout.addWidget(self.badge)

        text = QVBoxLayout()
        text.setSpacing(0)
        self.name = ElidedLabel(folder.name, mode=Qt.TextElideMode.ElideMiddle, parent=self)
        self.name.setObjectName("queuedFolderName")
        text.addWidget(self.name)
        if folder.problem:
            self.detail = ElidedLabel(folder.reason, parent=self)
            self.detail.setObjectName("queuedFolderReason")
        else:
            self.detail = ElidedLabel(folder.path, mode=Qt.TextElideMode.ElideMiddle, parent=self)
            self.detail.setObjectName("queuedFolderPath")
        text.addWidget(self.detail)
        layout.addLayout(text, 1)

        self.kind = QLabel(folder.kind_label, self)
        self.kind.setObjectName("queuedFolderKind")
        self.kind.setHidden(not folder.kind_label)
        layout.addWidget(self.kind)

        self.parent_button: QPushButton | None = None
        if folder.status == FolderCheck.SESSION_FILE and folder.parent_folder:
            self.parent_button = QPushButton("Use parent folder", self)
            self.parent_button.setToolTip(folder.parent_folder)
            self.parent_button.clicked.connect(lambda: self.use_parent_requested.emit(folder.path))
            layout.addWidget(self.parent_button)
        else:
            glyph, tone, status = (
                ("check-circle", "good", "Ready")
                if folder.ready
                else ("x-circle", "bad", "Won’t load")
            )
            self.status = Glyph(glyph, tone, 18, self)
            self.status.setAccessibleName(status)
            layout.addWidget(self.status)

        self.remove_button = GlyphButton("x", f"Remove {folder.name}", self, size=28)
        self.remove_button.clicked.connect(lambda: self.remove_requested.emit(folder.path))
        layout.addWidget(self.remove_button)
        parts = (folder.name, folder.kind_label, folder.reason or "ready")
        self.setAccessibleName(", ".join(part for part in parts if part))


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
        self._paths: list[str] = []
        self._intake = SessionIntake(())
        self.rows: list[QueuedFolderRow] = []
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
        self.footer_icon = Glyph("info", "muted", 14, footer)
        footer_layout.addWidget(self.footer_icon)
        self.footer_note = QLabel("", footer)
        self.footer_note.setObjectName("loadSessionsFooterNote")
        footer_layout.addWidget(self.footer_note)
        footer_layout.addStretch(1)
        self.cancel_button = QPushButton("Cancel", footer)
        self.cancel_button.clicked.connect(self.reject)
        footer_layout.addWidget(self.cancel_button)
        self.load_button = QPushButton(load_button_text(0), footer)
        self.load_button.setObjectName("primaryButton")
        self._load_arrow = themed_icon("arrow-right", color=current_palette().primary_text)
        self.load_button.setLayoutDirection(Qt.LayoutDirection.RightToLeft)
        self.load_button.setDefault(True)
        self.load_button.setAutoDefault(True)
        self.load_button.clicked.connect(self.accept)
        footer_layout.addWidget(self.load_button)
        outer.addWidget(footer)

        self._refresh()
        if self.recent_row is not None:
            self._hold_the_queue_area()
            self._order_the_keyboard()

    def _order_the_keyboard(self) -> None:
        """Tab goes Browse, the chips, then on as before; queue rows come last.

        Qt skips hidden and disabled chips itself, so fitting changes no order.
        """

        chain = (self.browse_button, *self.recent_row.chips, self.clear_button, self.scroll, self.cancel_button, self.load_button)
        for first, second in zip(chain, chain[1:]):
            QWidget.setTabOrder(first, second)
        # Focus moves on to Browse or Load after a choice, or when a chip goes
        # while its key is held; the key's repeats must not then act there.
        self._held_enter = HeldEnterFilter(self, (self.browse_button, self.load_button, self))

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
        layout.invalidate()
        self.setMinimumHeight(
            max(BASE_MINIMUM_HEIGHT, layout.minimumSize().height(), layout.minimumHeightForWidth(MINIMUM_WIDTH))
        )

    def event(self, event) -> bool:  # noqa: D102 - Qt override
        if self._remeasure is not None and event.type() in METRIC_CHANGE_EVENTS:
            self._remeasure.start()
        return super().event(event)

    # -- the queue ------------------------------------------------------------

    @property
    def intake(self) -> SessionIntake:
        return self._intake

    def selected_paths(self) -> list[str]:
        """The folders that will load, in the order they were queued."""

        return list(self._intake.ready_paths)

    def add_paths(self, paths: Iterable[str | Path]) -> None:
        known = {path_identity_key(path) for path in self._paths}
        for path in paths:
            text = str(Path(path))
            if path_identity_key(text) not in known:
                known.add(path_identity_key(text))
                self._paths.append(text)
        self._refresh()

    def _add_recent(self, path: str, activation: Activation) -> None:
        """Queue a recent folder. After the keyboard, Enter then loads it, or
        Browse is offered if it will not load, even when other folders will."""

        self.add_paths([path])
        if activation is Activation.KEYBOARD:
            key = path_identity_key(path)
            folder = next((value for value in self._intake.folders if path_identity_key(value.path) == key), None)
            target = self.load_button if folder is not None and folder.ready else self.browse_button
            target.setFocus(Qt.FocusReason.OtherFocusReason)

    def remove_path(self, path: str) -> None:
        key = path_identity_key(path)
        self._paths = [value for value in self._paths if path_identity_key(value) != key]
        self._refresh()

    def use_parent_folder(self, path: str) -> None:
        """Swap a session file for the folder that holds it."""

        folder = next((value for value in self._intake.folders if value.path == path), None)
        if folder is None or not folder.parent_folder:
            return
        key = path_identity_key(path)
        replaced: list[str] = []
        seen: set[str] = set()
        for value in self._paths:
            target = folder.parent_folder if path_identity_key(value) == key else value
            target_key = path_identity_key(target)
            if target_key not in seen:
                seen.add(target_key)
                replaced.append(target)
        self._paths = replaced
        self._refresh()

    def clear(self) -> None:
        self._paths = []
        self._refresh()

    def _refresh(self) -> None:
        # Before any chip changes: Qt moves focus on from a chip it turns off.
        focus = self.focusWidget()
        self._intake = assess_paths(self._paths, self._loader.classify, loaded_keys=self._loaded_keys)
        for row in self.rows:
            row.deleteLater()
        self.rows = []
        for index, folder in enumerate(self._intake.folders):
            row = QueuedFolderRow(folder, self.list)
            row.setProperty("last", index == len(self._intake.folders) - 1)
            row.remove_requested.connect(self.remove_path)
            row.use_parent_requested.connect(self.use_parent_folder)
            self._list_layout.insertWidget(index, row)
            self.rows.append(row)

        queued = len(self._intake.folders)
        ready = len(self._intake.ready)
        problems = len(self._intake.problems)
        self.queue.setVisible(bool(queued))
        # With nothing queued the drop well takes the room the list would, unless
        # it holds chips, which must stay where they are.
        self.drop_well.setSizePolicy(
            QSizePolicy.Policy.Preferred,
            QSizePolicy.Policy.Fixed if queued or self.recent_row is not None else QSizePolicy.Policy.Expanding,
        )
        if self.recent_row is not None:
            self.recent_row.set_queued({path_identity_key(folder.path) for folder in self._intake.folders})
            self._keep_focus(focus)
        self.ready_count.setText(f"{ready} ready")
        self.ready_count.setVisible(bool(ready))
        self.problem_count.setText(f"{problems} won’t load")
        self.problem_count.setVisible(bool(problems))
        self.load_button.setText(load_button_text(ready))
        self.load_button.setEnabled(bool(ready))
        # The arrow is drawn in the enabled label colour; a muted button drops it.
        self.load_button.setIcon(self._load_arrow if ready else QIcon())
        self.footer_note.setText(footer_note(queued, len(self._loaded_keys)))

    def _keep_focus(self, focus: QWidget | None) -> None:
        """Leave focus where it was; a chip that turned off hands it to Browse.

        Qt would pass it to the next control, which can be Clear list.
        """

        if focus is None or self.focusWidget() is focus:
            return
        if focus.isVisible() and focus.isEnabled():
            focus.setFocus(Qt.FocusReason.OtherFocusReason)
        elif focus in self.recent_row.chips:
            self.browse_button.setFocus(Qt.FocusReason.OtherFocusReason)

    # -- browsing and dropping ------------------------------------------------------

    def _browse(self) -> None:
        start = str(Path(self._paths[-1]).parent) if self._paths else self._start_path
        path = select_session_folder(self, "Select session folder", start, "Add folder")
        if path:
            self.add_paths([path])

    def dragEnterEvent(self, event) -> None:  # noqa: N802 - Qt API
        paths = dropped_paths(event.mimeData())
        if not paths:
            event.ignore()
            return
        preview = assess_paths(paths, self._loader.classify, loaded_keys=self._loaded_keys)
        self.drop_well.set_tone(preview.tone)
        event.setDropAction(Qt.DropAction.CopyAction)
        event.accept()

    def dragMoveEvent(self, event) -> None:  # noqa: N802 - Qt API
        event.setDropAction(Qt.DropAction.CopyAction)
        event.accept()

    def dragLeaveEvent(self, event) -> None:  # noqa: N802 - Qt API
        self.drop_well.set_tone("")
        super().dragLeaveEvent(event)

    def dropEvent(self, event) -> None:  # noqa: N802 - Qt API
        self.drop_well.set_tone("")
        paths = dropped_paths(event.mimeData())
        if not paths:
            event.ignore()
            return
        event.setDropAction(Qt.DropAction.CopyAction)
        event.accept()
        # Even folders that will not load are queued, so the list can say why.
        self.add_paths(paths)

    def accept(self) -> None:  # noqa: D102 - Qt override
        if not self._intake.ready:
            return
        super().accept()
