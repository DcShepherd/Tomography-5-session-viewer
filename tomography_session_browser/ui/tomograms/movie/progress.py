"""The modeless progress window (plan §4.1–4.2, board 4): beside the stage, over the list column.

It shows one export: the frames done and the time left, then the result once.
Cancel export, or ×, stops the export and closes the window at once.
"""
from __future__ import annotations

import math
from time import monotonic
from typing import TYPE_CHECKING

from PySide6.QtCore import QPoint, Qt, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QDialog, QHBoxLayout, QLabel, QProgressBar, QPushButton, QSizePolicy, QVBoxLayout

from tomography_session_browser.presentation.design_tokens import SPACE_L, SPACE_M, SPACE_S, SPACE_XS
from tomography_session_browser.presentation.tomogram_movie import duration_text, file_size_text
from tomography_session_browser.services.movie.export import FRAMES
from tomography_session_browser.services.movie.models import FRAMES_ONLY, MovieFailure, MovieProgress, MovieResult
from tomography_session_browser.ui.file_actions import show_in_folder
from tomography_session_browser.ui.widgets.elided_label import ElidedLabel

if TYPE_CHECKING:
    from tomography_session_browser.ui.tomograms.movie.owner import MovieExport, MovieJob

WIDTH = 340
#: The time left appears once this share of the frames is done, and not before this many seconds.
ESTIMATE_SHARE, ESTIMATE_SECONDS = .05, 1.


def time_left(seconds: float) -> str:
    return f"{max(1, math.ceil(seconds))} s left" if seconds < 60 else f"{max(1, round(seconds / 60))} min left"


class MovieProgressDialog(QDialog):
    def __init__(self, owner: MovieExport, job: MovieJob) -> None:
        super().__init__(owner.tab.window())
        self.owner, self.job = owner, job
        self.state = "exporting"
        self.result: MovieResult | None = None
        self.started = monotonic()
        self.setObjectName("movieProgress")
        self.setWindowModality(Qt.WindowModality.NonModal)
        self.setWindowFlag(Qt.WindowType.WindowContextHelpButtonHint, False)
        frames_only = job.settings.save == FRAMES_ONLY
        self.noun = "Frames" if frames_only else "Movie"
        layout = QVBoxLayout(self)
        layout.setContentsMargins(SPACE_L, SPACE_M, SPACE_L, SPACE_M)
        layout.setSpacing(SPACE_S)
        self.title = QLabel(self)
        self.title.setObjectName("messageSheetTitle")
        layout.addWidget(self.title)
        # The source frozen at the start: it keeps this name when the user changes tomograms.
        self.source = ElidedLabel(f"{job.view.tomogram} · {job.view.volume.version}", mode=Qt.TextElideMode.ElideMiddle,
                                  parent=self)
        self.source.setObjectName("mutedLabel")
        self.source.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        layout.addWidget(self.source)
        self.bar = QProgressBar(self)
        self.bar.setTextVisible(False)
        self.bar.setRange(0, job.request.timeline.count)
        self.bar.setAccessibleName("Export progress")
        layout.addWidget(self.bar)
        status = QHBoxLayout()
        status.setSpacing(SPACE_S)
        self.count = QLabel(self)
        self.left = QLabel(self)
        self.left.setObjectName("mutedLabel")
        status.addWidget(self.count)
        status.addStretch()
        status.addWidget(self.left)
        layout.addLayout(status)
        # The result: one line that names it, one line of detail.
        self.line = ElidedLabel("", mode=Qt.TextElideMode.ElideMiddle, parent=self)
        self.line.setObjectName("movieResultName")
        self.line.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.reason = QLabel(self)
        self.reason.setWordWrap(True)
        self.detail = QLabel(self)
        self.detail.setObjectName("mutedLabel")
        self.detail.setWordWrap(True)
        for label in (self.line, self.reason, self.detail):
            label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            layout.addWidget(label)
        buttons = QHBoxLayout()
        buttons.setSpacing(SPACE_S)
        buttons.addStretch()
        self.cancel = QPushButton("Cancel export", self)
        self.cancel.clicked.connect(self.owner.cancel)
        self.folder = QPushButton("Show in folder", self)
        self.folder.clicked.connect(self.show_folder)
        self.open_movie = QPushButton("Open movie", self)
        self.open_movie.setObjectName("primaryButton")
        self.open_movie.clicked.connect(self.open_result)
        self.change = QPushButton("Change settings…", self)
        self.change.setObjectName("primaryButton")
        self.change.clicked.connect(lambda: self.owner.change_settings(self.job))
        for button in (self.cancel, self.folder, self.open_movie, self.change):
            button.setAutoDefault(False)
            buttons.addWidget(button)
        layout.addSpacing(SPACE_XS)
        layout.addLayout(buttons)
        self.setFixedWidth(WIDTH)
        self.show_state("exporting")
        self.update_progress(MovieProgress(job.job, FRAMES, 0, job.request.timeline.count))

    def show_state(self, state: str) -> None:
        self.state = state
        title = {"exporting": "Exporting movie" if self.noun == "Movie" else "Saving frames",
                 "saved": f"{self.noun} saved", "failed": f"{self.noun} not saved"}[state]
        self.title.setText(title)
        self.setWindowTitle(title)
        self.setAccessibleName(title)
        exporting = state == "exporting"
        for widget in (self.bar, self.count, self.left, self.cancel):
            widget.setVisible(exporting)
        self.line.setVisible(state == "saved")
        self.reason.setVisible(state == "failed")
        self.detail.setVisible(not exporting)
        self.folder.setVisible(state == "saved")
        self.open_movie.setVisible(state == "saved" and self.result is not None and self.result.movie is not None)
        self.change.setVisible(state == "failed")
        self.adjustSize()

    def place(self) -> None:
        """Over the list column, so the tomogram stays in view; at the window's right edge when it is hidden."""
        tab = self.owner.tab
        column = tab.list_column()
        self.adjustSize()
        if column.isVisible() and column.width() >= WIDTH // 2:
            origin = column.mapToGlobal(QPoint(0, 0))
            x = origin.x() + max(0, (column.width() - self.width()) // 2)
            y = origin.y() + SPACE_L
        else:
            window = tab.window()
            origin = window.mapToGlobal(QPoint(0, 0))
            x = origin.x() + window.width() - self.width() - SPACE_L
            y = tab.mapToGlobal(QPoint(0, 0)).y() + SPACE_L
        self.move(x, y)

    def update_progress(self, progress: MovieProgress) -> None:
        if progress.phase != FRAMES:
            self.bar.setValue(self.bar.maximum())
            self.count.setText(progress.phase)
            self.left.setText("")
            return
        self.bar.setValue(progress.done)
        self.count.setText(f"{progress.done} of {progress.total} frames")
        elapsed = monotonic() - self.started
        if progress.done >= max(1, progress.total * ESTIMATE_SHARE) and elapsed >= ESTIMATE_SECONDS:
            self.left.setText(time_left(elapsed * (progress.total - progress.done) / progress.done))
        else:
            self.left.setText("")

    def saved(self, result: MovieResult) -> None:
        self.result = result
        name = (result.movie or result.path).name
        self.line.setText(f"✓ {name}")
        self.line.setToolTip(str(result.movie or result.path))
        parts = [file_size_text(result.size_bytes), duration_text(result.duration)]
        if result.png_frames:
            parts.append(f"{result.png_frames} PNG frames")
        self.detail.setText(" · ".join(parts))
        self.show_state("saved")
        (self.open_movie if result.movie is not None else self.folder).setFocus()

    def not_saved(self, failure: MovieFailure) -> None:
        self.reason.setText(failure.message)
        self.detail.setText(failure.help)
        self.detail.setToolTip(failure.detail)
        self.show_state("failed")
        self.change.setFocus()

    def show_folder(self) -> None:
        if self.result is not None:
            show_in_folder(self.result.path)

    def open_result(self) -> None:
        if self.result is not None and self.result.movie is not None:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.result.movie)))

    def close_quietly(self) -> None:
        """The owner has let go of this window: close it without cancelling anything."""
        if self.state != "closed":
            self.state = "closed"
            self.close()
        self.deleteLater()

    def reject(self) -> None:
        self.close()

    def closeEvent(self, event) -> None:  # noqa: N802
        # × during an export is Cancel export: there is no separate cancelled message.
        exporting, self.state = self.state == "exporting", "closed"
        if exporting:
            self.owner.cancel()
        elif self.owner.progress is self:
            self.owner.progress = None
            self.deleteLater()
        super().closeEvent(event)
