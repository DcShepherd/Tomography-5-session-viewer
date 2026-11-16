"""The Movies menu of one review row: saved movies, newest first, 12 to a page.

Each row says whether its file or frames folder is still there (a read-only
check in its own lane) and offers Open and Show in folder. Movies have no
Delete. The menu keeps only the page it shows and the way back to newer pages.
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

from PySide6.QtCore import Qt, Signal, Slot
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QAbstractButton, QFrame, QHBoxLayout, QLabel, QToolButton, QVBoxLayout, QWidget

from tomography_session_browser.domain.display_names import count_phrase
from tomography_session_browser.presentation.help_content import InteractionKind, LocalBinding
from tomography_session_browser.presentation.tomogram_movie import file_size_text
from tomography_session_browser.presentation.tomogram_movie_history import MovieEntry, MoviePage, movie_page
from tomography_session_browser.presentation.tomogram_versions import review_versions
from tomography_session_browser.services.movie.output_check import OutputCheck, check_outputs
from tomography_session_browser.ui.file_actions import open_file, show_in_folder
from tomography_session_browser.ui.icons import themed_icon
from tomography_session_browser.ui.theme import current_palette
from tomography_session_browser.ui.tomograms.history_loading import HistoryJob, HistoryLoader
from tomography_session_browser.ui.widgets.row_menu import FooterKey, MenuRow, RowMenu
from tomography_session_browser.ui.widgets.elided_label import ElidedLabel
from tomography_session_browser.ui.widgets.link_button import link_button

MISSING = "Not found: moved or deleted"
CHECKING = "Checking that the file is still there…"


def saved_text(created_ns: int) -> str:
    try:
        return datetime.fromtimestamp(created_ns / 1e9).strftime("%d %b %Y %H:%M") if created_ns else "Save time not recorded"
    except (OverflowError, ValueError, OSError):
        return "Save time unavailable"


class MovieRow(QFrame):
    opened = Signal(str)
    shown = Signal(str)

    def __init__(self, entry: MovieEntry, version: str, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("captureRow")  # The capture rows' look.
        self.entry = entry
        self.version = version
        self.check: OutputCheck | None = None
        row = QHBoxLayout(self)
        row.setContentsMargins(8, 4, 8, 4)
        self.icon = QLabel(self)
        self.icon.setFixedSize(16, 16)
        row.addWidget(self.icon)
        text = QVBoxLayout()
        text.setSpacing(0)
        self.name = ElidedLabel(entry.output.name, parent=self)
        self.name.setObjectName("movieRowName")
        text.addWidget(self.name)
        self.detail = ElidedLabel("", parent=self)
        text.addWidget(self.detail)
        row.addLayout(text, 1)
        self.open_button = link_button("Open", self)
        self.open_button.setAccessibleName("Open the movie")
        self.open_button.clicked.connect(lambda: self.opened.emit(entry.identity))
        row.addWidget(self.open_button)
        self.show_button = QToolButton(self)
        self.show_button.setAccessibleName("Show in folder")
        self.show_button.clicked.connect(lambda: self.shown.emit(entry.identity))
        row.addWidget(self.show_button)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.refresh_theme()
        self.set_check(None)

    def set_check(self, check: OutputCheck | None) -> None:
        self.check = check
        parts = [saved_text(self.entry.created_ns), *((self.version,) if self.version else ())]
        kind = check.kind if check is not None else None
        if kind == "file":
            parts.append(file_size_text(check.size or 0))
        elif kind == "folder":
            parts.append("Frames folder")
        elif kind == "missing":
            parts.insert(0, MISSING)  # First, so that the reason is never cut off.
        self.detail.setText(" · ".join(parts))
        reason = CHECKING if check is None else MISSING if kind == "missing" else ""
        self.open_button.setVisible(kind != "folder")
        self.open_button.setEnabled(kind == "file")
        self.open_button.setToolTip(reason or "Open the movie in the app that plays it")
        self.show_button.setEnabled(kind in ("file", "folder"))
        self.show_button.setToolTip(reason or ("Open the folder that holds this movie" if kind == "file"
                                               else "Open the folder of these frames"))
        detail = f"{self.entry.output}\nSaved {saved_text(self.entry.created_ns)}" + (f"\n{reason}" if reason else "")
        self.setToolTip(detail)
        self.setAccessibleDescription(detail)

    @property
    def controls(self) -> tuple[QAbstractButton, ...]:
        return tuple(button for button in (self.open_button, self.show_button) if not button.isHidden())

    def activate(self) -> None:
        """Enter on the row: open a movie file, show a frames folder, nothing for a missing one."""
        kind = self.check.kind if self.check is not None else None
        if kind == "file":
            self.opened.emit(self.entry.identity)
        elif kind == "folder":
            self.shown.emit(self.entry.identity)

    def refresh_theme(self) -> None:
        self.icon.setPixmap(themed_icon("film", size=16).pixmap(16, 16))
        self.show_button.setIcon(folder_icon())


def folder_icon() -> QIcon:
    """The Show in folder icon, muted when disabled like the Open text beside it.

    Qt's own disabled pixmap is nearly the enabled colour on the dark palette.
    """
    icon = themed_icon("folder-open")
    muted = themed_icon("folder-open", color=current_palette().text_muted)
    for size in muted.availableSizes():
        for dpr in (1., 1.25, 1.5, 2., 3.):
            icon.addPixmap(muted.pixmap(size, dpr), QIcon.Mode.Disabled, QIcon.State.Off)
    return icon


class Pager(QWidget):
    """Newer, Older and Refresh as buttons in one row, so that paging keeps the menu open."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        row = QHBoxLayout(self)
        row.setContentsMargins(8, 2, 8, 2)
        self.newer = link_button("Newer movies", self)
        self.older = link_button("Older movies", self)
        self.refresh = link_button("Refresh", self)
        for button in (self.newer, self.older):
            row.addWidget(button)
        row.addStretch(1)
        row.addWidget(self.refresh)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

    @property
    def controls(self) -> tuple[QAbstractButton, ...]:
        return tuple(button for button in (self.newer, self.older, self.refresh) if not button.isHidden())


class MoviePopup(RowMenu):
    def __init__(self, movies, row_key: str) -> None:
        super().__init__(movies.tab)
        self.movies, self.tab, self.row_key = movies, movies.tab, row_key
        self.rows: list[MovieRow] = []
        #: The ``before`` cursor of each page from the newest to the one shown.
        self.cursors: list[tuple[int, str] | None] = [None]
        self.wanted: list[tuple[int, str] | None] = [None]
        self.page: MoviePage | None = None
        self.checks: dict[Path, OutputCheck] = {}
        self.error = ""
        self.notice = ""
        self.loader = HistoryLoader(self.tab.history.pool)
        self.loader.signals.finished.connect(self._loaded)
        self.loader.signals.failed.connect(self._failed)
        self.check_loader = HistoryLoader(movies.check_pool)
        self.check_loader.signals.finished.connect(self._checked)
        self.setMaximumWidth(540)
        self.aboutToHide.connect(self.cancel)
        self.request([None])

    def cancel(self) -> None:
        self.loader.cancel()
        self.check_loader.cancel()

    # Pages ------------------------------------------------------------------------
    def request(self, cursors: list[tuple[int, str] | None]) -> None:
        """Ask for a page; the shown page and its place change only when it arrives."""
        self.wanted = cursors
        repository, writer = self.tab.persistence.repository, self.tab.persistence.writer
        identities = self.tab.history.context(frozenset({self.row_key})).identities
        extra, before = self.movies.extras(), cursors[-1]
        if not self.actions():
            self.build()
        self.loader.request(lambda cancelled: writer.read(lambda: movie_page(
            repository, identities, before=before, extra=extra, cancelled=cancelled)), stamp=self.tab.history.stamp())

    def refresh_page(self) -> None:
        self.request(self.wanted)

    def show_newest(self) -> None:
        """A new export or Refresh: back to the newest page, saying so if an older page was shown."""
        if len(self.cursors) > 1:
            self.notice = "Showing newest movies"
        self.request([None])

    def newer(self) -> None:
        if len(self.cursors) > 1:
            self.notice = ""
            self.request(self.cursors[:-1])

    def older(self) -> None:
        if self.page is not None and self.page.next_cursor is not None:
            self.notice = ""
            self.request([*self.cursors, self.page.next_cursor])

    def refresh(self) -> None:
        self.notice = ""
        self.show_newest()

    def retry(self) -> None:
        self.request(self.wanted)

    @Slot(object, object)
    def _loaded(self, job: HistoryJob, page: MoviePage | None) -> None:
        if not self.loader.retire(job) or page is None or self.tab.retired:
            return
        if job.stamp != self.tab.history.stamp():
            self.request(self.wanted)
            return
        if not page.entries and len(self.wanted) > 1:
            self.notice = "The older movies changed. Showing newest movies."
            self.request([None])
            return
        self.cursors, self.page, self.error, self.checks = self.wanted, page, "", {}
        self.build()
        paths = tuple(entry.output for entry in page.entries)
        self.check_loader.request(lambda cancelled: check_outputs(paths, cancelled=cancelled),
                                  stamp=self.tab.history.stamp())

    @Slot(object, str)
    def _failed(self, job: HistoryJob, message: str) -> None:
        if self.loader.retire(job) and not self.tab.retired:
            self.error = message or "Movie history is unavailable"
            self.build()

    @Slot(object, object)
    def _checked(self, job: HistoryJob, found) -> None:
        if not self.check_loader.retire(job) or found is None or self.tab.retired or job.stamp != self.tab.history.stamp():
            return
        self.checks = dict(found)
        anchor = self.anchor()
        for row in self.rows:
            row.set_check(self.checks.get(row.entry.output))
        self.restore_anchor(anchor)

    # Building ---------------------------------------------------------------------
    def build(self) -> None:
        anchor = self.reset()
        self.rows.clear()
        row = self.tab.review_list.rows.get(self.row_key)
        name = row.tomogram.name if row else "Tomogram outside this scope"
        total = self.page.total if self.page is not None else self.movies.count(self.row_key)
        self.note(f"{name} · {count_phrase(total, 'movie') if total is not None else '… movies'}")
        if self.notice:
            self.note(self.notice)
        versions = {volume.id: volume.version for volume in review_versions(row.tomogram)} if row else {}
        for entry in self.page.entries if self.page is not None else ():
            panel = MovieRow(entry, versions.get(entry.volume_id, "") if len(versions) > 1 else "", self)
            panel.set_check(self.checks.get(entry.output))
            panel.opened.connect(self.open_movie)
            panel.shown.connect(self.show_folder)
            self.rows.append(panel)
            self.add_row(MenuRow(entry.identity, panel, lambda panel=panel: panel.controls, panel.activate))
        if self.error:
            self.note("Movie history is unavailable. Use Retry.").setToolTip(self.error)
        elif self.page is None:
            self.note("Loading movies…")
        elif not self.page.entries:
            self.note("No movies of this tomogram")
        self.addSeparator()
        pager = Pager(self)
        paged = self.page is not None and (len(self.cursors) > 1 or self.page.next_cursor is not None)
        pager.newer.setVisible(paged)
        pager.older.setVisible(paged)
        pager.newer.setEnabled(len(self.cursors) > 1)
        pager.older.setEnabled(self.page is not None and self.page.next_cursor is not None)
        pager.newer.setToolTip("Show the newer movies" if len(self.cursors) > 1 else "These are the newest movies")
        pager.older.setToolTip("Show the older movies" if pager.older.isEnabled() else "There are no older movies")
        pager.refresh.setText("Retry" if self.error else "Refresh")
        pager.refresh.setToolTip(self.error or "Read the movie history again and show the newest movies")
        pager.newer.clicked.connect(self.newer)
        pager.older.clicked.connect(self.older)
        pager.refresh.clicked.connect(self.retry if self.error else self.refresh)
        self.add_row(MenuRow(FooterKey("pages"), pager, lambda: pager.controls))
        self.restore_anchor(anchor)

    # Row actions --------------------------------------------------------------------
    def entry(self, identity: str) -> MovieEntry | None:
        return next((row.entry for row in self.rows if row.entry.identity == identity), None)

    def open_movie(self, identity: str) -> None:
        entry = self.entry(identity)
        self.close()
        if entry is not None and not open_file(entry.output):
            self.tab.status.message.setText("The movie could not be opened; its history is kept.")

    def show_folder(self, identity: str) -> None:
        entry = self.entry(identity)
        self.close()
        if entry is not None and not show_in_folder(entry.output):
            self.tab.status.message.setText("The movie folder could not be opened; its history is kept.")


# Contextual keys of this menu; the traversal keys are shared (``row_menu.LOCAL_KEYS``).
LOCAL_KEYS = (
    LocalBinding('tomograms.movie_popup.movies-menu-enter', 'Movies menu', InteractionKind.KEYBOARD, ('Enter', 'Return', 'Space'),
                 'Open the movie, or show the folder of its frames'),
)
