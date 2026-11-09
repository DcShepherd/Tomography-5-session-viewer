"""The movie owner: film button, command, frozen view, worker lanes, progress and completion (plan §1.1, §3.1, §4).

Workers receive frozen requests and callbacks only. Their results come back
through the tab's `_movie_event` receiver, which forwards them here.
"""
from __future__ import annotations

import atexit
from dataclasses import dataclass
import itertools
import logging
import os
from pathlib import Path
import tempfile
import threading
from typing import TYPE_CHECKING, Any, ClassVar

from PySide6.QtCore import QObject, QRunnable, QSize, QThreadPool, QUrl, Signal, Slot
from PySide6.QtGui import QAction, QDesktopServices
from PySide6.QtWidgets import QDialog, QFrame, QHBoxLayout, QPushButton

from tomography_session_browser.presentation.messages import ActionPrompt, ActionRole, MessageAction, MessageContent
from tomography_session_browser.presentation.tomogram_movie import (FrozenPane, FrozenView, LastSettings, MovieSettings,
                                                                    build_request, default_settings, from_last_settings,
                                                                    preview_request, remembered)
from tomography_session_browser.services.movie.encoder import EncoderCheck, check_encoder
from tomography_session_browser.services.movie.export import (clear_previews, preview_folder, run_export,
                                                              run_preview, source_unchanged)
from tomography_session_browser.services.movie.frames import FrameSource
from tomography_session_browser.services.movie.models import MovieFailure, MovieRequest
from tomography_session_browser.ui.tomograms.layout_controls import TOOL_CELL, canvas_icon
from tomography_session_browser.ui.widgets import message_sheet

if TYPE_CHECKING:
    from tomography_session_browser.ui.tomograms.movie.dialog import MovieDialog
    from tomography_session_browser.ui.tomograms.movie.progress import MovieProgressDialog
    from tomography_session_browser.ui.tomograms.tab import TomogramTab

FILM_TOOLTIP = "Save these planes as a movie (Ctrl+E). The movie starts with this rate and end behaviour."
LAST_SETTINGS_TOOLTIP = "Save these planes as a movie (Ctrl+E). The dialog starts with the last settings for this tomogram."
NO_TOMOGRAM = "Not available: select a tomogram first."
ONE_PLANE = "Not available: this tomogram has one plane. A movie needs two or more."
RUNNING = "Not available: a movie export is running. Wait for it to finish, or cancel it in the progress window."
STOPPING = "Not available: the cancelled export is still stopping."
WAITING = "The movie dialog opens when the requested plane is shown…"
LOGGER = logging.getLogger(__name__)


class MovieSignals(QObject):
    event = Signal(str, object, object)


class MovieTask(QRunnable):
    """Report a MovieFailure as itself; anything else is logged and classified by the service."""

    def __init__(self, work, kind: str, job: int, signals: MovieSignals) -> None:
        super().__init__()
        self.work, self.kind, self.job, self.signals = work, kind, job, signals

    @Slot()
    def run(self) -> None:
        try:
            result = self.work()
        except MovieFailure as failure:
            self.signals.event.emit(self.kind + "-failed", self.job, failure)
            return
        except Exception as exc:  # A bug, not a classified failure: log it with its traceback.
            LOGGER.exception("Movie %s failed", self.kind)
            self.signals.event.emit(self.kind + "-failed", self.job, MovieFailure("encoder", str(exc) or type(exc).__name__))
            return
        self.signals.event.emit(self.kind, self.job, result)


class LiveRoots:
    """At publication the worker asks the UI thread for the roots loaded now, then joins them with its frozen roots."""

    def __init__(self, signals: MovieSignals, job: int, cancelled: threading.Event) -> None:
        self.signals, self.job, self.cancelled = signals, job, cancelled
        self.ready = threading.Event()
        self.roots: tuple[Path, ...] = ()

    def __call__(self) -> tuple[Path, ...]:
        self.signals.event.emit("roots", self.job, self)
        while not self.ready.wait(.1):
            if self.cancelled.is_set():
                raise InterruptedError("Movie export cancelled")
        return self.roots

    def supply(self, roots: tuple[Path, ...]) -> None:
        self.roots = tuple(roots)
        self.ready.set()


@dataclass(frozen=True, slots=True)
class MovieJob:
    job: int
    request: MovieRequest
    view: FrozenView
    settings: MovieSettings
    cancelled: threading.Event


def preview_base() -> Path:
    return preview_folder(Path(tempfile.gettempdir()))


class MovieExport:
    #: One encoder check for the app session, shared by every window.
    encoder: ClassVar[EncoderCheck | None] = None
    checking: ClassVar[bool] = False
    started: ClassVar[bool] = False
    numbers = itertools.count(1)

    def __init__(self, tab: TomogramTab) -> None:
        self.tab = tab
        self.signals = MovieSignals()
        self.signals.event.connect(tab._movie_event)
        # The export lane, and a separate lane for the dialog's frame preview and clips.
        self.pool = QThreadPool(tab)
        self.pool.setMaxThreadCount(1)
        self.preview_pool = QThreadPool(tab)
        self.preview_pool.setMaxThreadCount(1)
        self.button = QPushButton(tab)
        self.button.setObjectName("tomogramFilm")
        self.button.setAccessibleName("Export movie")
        self.button.setIcon(canvas_icon("film"))
        self.button.setIconSize(QSize(16, 16))
        self.button.setFixedSize(TOOL_CELL + 2, TOOL_CELL)
        self.button.clicked.connect(self.open)
        # The film button is joined to the rate button: the movie starts with that rate.
        self.group = QFrame(tab)
        self.group.setObjectName("tomogramRateGroup")
        row = QHBoxLayout(self.group)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(0)
        row.addWidget(tab.playback.rate_button)
        self.hair = QFrame(self.group)
        self.hair.setObjectName("tomogramToolHair")
        self.hair.setFixedSize(1, 14)
        row.addWidget(self.hair)
        row.addWidget(self.button)
        menu = tab.playback.rate_button.menu()
        assert menu is not None
        self.separator = menu.addSeparator()
        self.menu_action = QAction("Export movie…\tCtrl+E", menu)
        self.menu_action.triggered.connect(self.open)
        menu.addAction(self.menu_action)
        self.place(False)
        self.running: MovieJob | None = None
        self.waiting: str | None = None
        #: By tomogram row, for all of its versions. Only memory: closing the app clears it.
        self.last_settings: dict[str, LastSettings] = {}
        self.dialog: MovieDialog | None = None
        self.progress: MovieProgressDialog | None = None
        self.preview_generation = 0
        #: (picture key, source, the generation it serves): a reused source cancels on its newest request.
        self.preview_source: tuple[tuple, FrameSource, list[int]] | None = None
        self.clips = itertools.count(1)
        if not MovieExport.started:
            MovieExport.started = True
            clear_previews(preview_base())
            atexit.register(lambda: clear_previews(preview_base(), everything_of_this_process=True))

    def place(self, in_band: bool) -> None:
        """A narrow band keeps the slider usable: the film button moves into the rate menu."""
        self.button.setVisible(in_band)
        self.hair.setVisible(in_band)
        self.separator.setVisible(not in_band)
        self.menu_action.setVisible(not in_band)

    def reason(self) -> str:
        tab = self.tab
        if self.running is not None:
            return STOPPING if self.running.cancelled.is_set() else RUNNING
        if tab.volume() is None or not tab.slider.isEnabled():
            return NO_TOMOGRAM
        if tab.slider.maximum() < 1:
            return ONE_PLANE
        return ""

    def refresh(self) -> None:
        reason = self.reason()
        tip = reason or (LAST_SETTINGS_TOOLTIP if self.tab.state.row_key in self.last_settings else FILM_TOOLTIP)
        self.button.setEnabled(not reason)
        self.button.setToolTip(tip)
        self.menu_action.setEnabled(not reason)
        self.menu_action.setToolTip(tip)

    # Opening: stop playback, wait for the refined frame, then freeze the view.
    def open(self) -> None:
        tab = self.tab
        if reason := self.reason():
            tab.status.message.setText(reason)
            return
        tab.playback.stop()
        if not tab.captures.ready():
            self.waiting = tab.state.row_key or ""
            tab.status.message.setText(WAITING)
            tab.status.message.owner = "movie"
            return
        self.waiting = None
        if tab.status.message.owner == "movie":  # Only its own message: a render note stays.
            tab.status.message.setText("")
        try:
            view = self.freeze()
        except (ValueError, KeyError) as exc:
            tab.status.message.setText(str(exc))
            return
        self.show_dialog(view)

    def frame_ready(self) -> None:
        if self.waiting is not None and self.tab.captures.ready():
            wanted, self.waiting = self.waiting, None
            if wanted == (self.tab.state.row_key or ""):
                self.open()

    def freeze(self) -> FrozenView:
        """Source, recipes with resolved levels, pane arrangement, intersection and the band, as they are now."""
        tab = self.tab
        accepted = tab.rendering.accepted
        assert accepted is not None
        job, result = accepted.job, accepted.result
        row = tab.review_list.rows.get(job.row_key)
        if row is None or job.volume.grid is None:
            raise ValueError(NO_TOMOGRAM)
        size = lambda axis: (max(1, tab.stage.panes[axis].width()), max(1, tab.stage.panes[axis].height()))  # noqa: E731
        panes = [FrozenPane(result.recipe, size(result.recipe.axis), job.position)]
        panes += [FrozenPane(frame.recipe, size(axis), position) for axis, frame, position in result.companions]
        cross = job.cross_xyz or tuple(n // 2 for n in job.volume.grid.shape_xyz)
        return FrozenView(job.row_key, row.tomogram.name, job.volume, tab.state.layout, result.recipe.axis,
                          tuple(panes), (cross[0], cross[1], cross[2]), tab.state.linked, tab.state.show_bar,
                          tab.playback.speed.value(), tab.playback.end.currentText())

    def movie_available(self) -> bool | None:
        return None if MovieExport.encoder is None else MovieExport.encoder.available

    def check_encoder(self) -> None:
        if MovieExport.encoder is not None or MovieExport.checking:
            return
        MovieExport.checking = True
        # The export lane is free while the dialog is open, so the preview never waits for the check.
        self.pool.start(MovieTask(check_encoder, "encoder", 0, self.signals))

    def starting_settings(self, view: FrozenView) -> tuple[MovieSettings, str]:
        """This tomogram's last settings if it has them; otherwise the viewer, the band and the remembered choices."""
        tab = self.tab
        available = self.movie_available() is not False
        last = self.last_settings.get(view.row_key)
        if last is not None:
            return from_last_settings(last, view, movie_available=available)
        return default_settings(view, tab.settings.tomogram_movie, folder=tab.settings.tomogram_snapshot_folder or "",
                                movie_available=available), ""

    def show_dialog(self, view: FrozenView, settings: MovieSettings | None = None) -> None:
        from tomography_session_browser.ui.tomograms.movie.dialog import MovieDialog
        self.check_encoder()
        note = ""
        if settings is None:
            settings, note = self.starting_settings(view)
        dialog = MovieDialog(self, view, settings, note)
        self.dialog = dialog
        try:
            accepted = dialog.exec() == QDialog.DialogCode.Accepted
            chosen, changed = dialog.settings, dialog.changed
        finally:
            self.dialog = None
            self.preview_generation += 1
            self.preview_source = None
            dialog.deleteLater()
        if accepted or changed:
            # The view is frozen, so changing the zoom means closing the dialog: Cancel must not lose the entries.
            # A look with no change keeps nothing, and leaves the settings it came from as they were.
            self.last_settings[view.row_key] = LastSettings(view, chosen)
            self.refresh()
        if accepted:
            self.start(view, chosen)

    # The dialog's preview lane: one frame at a time; a newer request supersedes an older one.
    def request_frame(self, view: FrozenView, settings: MovieSettings, index: int) -> None:
        self.preview_generation += 1
        generation = self.preview_generation
        try:
            request = preview_request(view, settings, generation)
        except ValueError as exc:
            if self.dialog is not None:
                self.dialog.frame_failed(generation, MovieFailure("planes", str(exc)))
            return
        current = lambda: generation == self.preview_generation  # noqa: E731

        # The frame source depends on the picture, not on the timing: scrubbing reuses its companions.
        key = (id(view), settings.view, settings.sweep, settings.framing, settings.size, settings.shape,
               settings.scale_bar, settings.names, settings.numbers, settings.lines, settings.title_text)

        def work():
            if not current():
                raise InterruptedError
            cached = self.preview_source
            if cached is None or cached[0] != key:
                serving = [generation]
                cached = (key, FrameSource(request, lambda: serving[0] != self.preview_generation), serving)
                self.preview_source = cached
            cached[2][0] = generation
            plane = request.timeline.frames[index].plane
            return index, cached[1].frame(plane)

        self.preview_pool.start(MovieTask(lambda: guarded(work), "frame", generation, self.signals))

    def preview_clip(self, view: FrozenView, settings: MovieSettings, index: int) -> None:
        encoder = MovieExport.encoder
        if encoder is None or not encoder.available:
            return
        number = next(self.clips)
        try:
            request = preview_request(view, settings, number)
        except ValueError as exc:
            self.tab.status.message.setText(str(exc))
            return
        path = preview_base() / f"preview-{os.getpid()}-{number}.mp4"
        self.preview_pool.start(MovieTask(lambda: run_preview(request, encoder, index, path, cancelled=lambda: False),
                                          "clip", number, self.signals))

    # The export lane.
    def start(self, view: FrozenView, settings: MovieSettings) -> None:
        from tomography_session_browser.ui.tomograms.movie.progress import MovieProgressDialog
        tab = self.tab
        number = next(self.numbers)
        try:
            request = build_request(view, settings, number, tab.protected_roots())
        except ValueError as exc:
            tab.status.message.setText(str(exc))
            return
        cancelled = threading.Event()
        job = MovieJob(number, request, view, settings, cancelled)
        self.running = job
        encoder = MovieExport.encoder or EncoderCheck(None, None, "The FFmpeg check has not finished. Try again.")
        roots = LiveRoots(self.signals, number, cancelled)
        if self.progress is not None:
            self.progress.close_quietly()
        self.progress = MovieProgressDialog(self, job)
        self.progress.place()
        self.progress.show()
        emit = self.signals.event.emit
        self.pool.start(MovieTask(lambda: run_export(request, encoder, cancelled=cancelled.is_set,
                                                     progress=lambda value: emit("progress", number, value),
                                                     live_roots=roots), "export", number, self.signals))
        tab.update_commands()

    def cancel(self) -> None:
        """Cancel export, or ×: the worker stops and deletes its staging; the window closes at once."""
        if self.running is not None:
            self.running.cancelled.set()
        progress, self.progress = self.progress, None
        if progress is not None:
            progress.close_quietly()
        self.tab.update_commands()

    def receive(self, kind: str, job: int, payload: Any) -> None:
        if kind == "roots":
            payload.supply(self.tab.protected_roots())
            return
        if kind in ("encoder", "encoder-failed"):
            MovieExport.checking = False
            MovieExport.encoder = payload if kind == "encoder" else EncoderCheck(None, None, payload.message)
            if self.dialog is not None:
                self.dialog.encoder_checked()
            return
        if kind in ("frame", "frame-failed"):
            if self.dialog is not None and job == self.preview_generation:
                if kind == "frame":
                    self.dialog.show_frame(job, *payload)
                elif payload.kind != "cancelled":
                    self.dialog.frame_failed(job, payload)
            return
        if kind == "clip":
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(payload.path)))
            return
        if kind == "clip-failed":
            self.tab.status.message.setText(f"Preview clip not saved: {payload.message}")
            return
        running = self.running
        if running is None or running.job != job:
            return
        if kind == "progress":
            if self.progress is not None and not running.cancelled.is_set():
                self.progress.update_progress(payload)
            return
        self.running = None
        if kind == "export":
            self.tab.settings.tomogram_movie = remembered(running.settings)
            self.tab.save_settings(self.tab.settings)
            self.tab.history.movie_saved(running.view, payload)
            if self.progress is not None:
                self.progress.saved(payload)
        elif payload.kind != "cancelled" and not running.cancelled.is_set():
            if self.progress is None:
                from tomography_session_browser.ui.tomograms.movie.progress import MovieProgressDialog
                self.progress = MovieProgressDialog(self, running)
                self.progress.place()
                self.progress.show()
            self.progress.not_saved(payload)
        self.tab.update_commands()

    def change_settings(self, job: MovieJob) -> None:
        """Open the dialog again with the failed export's settings and frozen view, or from the viewer if its source is gone."""
        if self.progress is not None:
            self.progress.close_quietly()
            self.progress = None
        tab = self.tab
        row = tab.review_list.rows.get(job.view.row_key)
        if row is not None and job.view.volume in row.tomogram.volumes and source_unchanged(job.view.volume):
            self.show_dialog(job.view, job.settings)
            return
        tab.status.message.setText("The exported source changed or is no longer loaded. The dialog opens from the viewer.")
        self.open()

    def confirm_close(self) -> bool:
        """Closing the app during an export stops it; ask first."""
        if self.running is None or self.running.cancelled.is_set():
            return True
        answer = message_sheet.choose_action(self.tab.window(), ActionPrompt(
            MessageContent("A movie export is running.", "If you close the app, the export stops and no movie is saved."),
            (MessageAction("keep", "Keep the app open"),
             MessageAction("cancel", "Cancel export and close", ActionRole.DESTRUCTIVE)), "keep", "keep"))
        if answer == "cancel":
            self.cancel()
            return True
        return False

    def shutdown(self) -> None:
        """Cancel movie work before its lanes are joined."""
        self.waiting = None
        self.preview_generation += 1
        if self.running is not None:
            self.running.cancelled.set()
        if self.progress is not None:
            self.progress.close_quietly()
            self.progress = None


def guarded(work):
    try:
        return work()
    except InterruptedError as exc:
        raise MovieFailure("cancelled", "The preview is out of date.") from exc
