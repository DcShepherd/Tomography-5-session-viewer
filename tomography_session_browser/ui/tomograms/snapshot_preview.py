"""Debounced advisory filename; one running filesystem query and one latest choice."""
from __future__ import annotations

from dataclasses import dataclass, field
import logging
from pathlib import Path
from threading import Event
from typing import TYPE_CHECKING

from PySide6.QtCore import QThreadPool, QTimer, Slot, Qt
from PySide6.QtWidgets import QSizePolicy

from tomography_session_browser.services.output_boundary import require_output_directory
from tomography_session_browser.services.volume.snapshot_names import next_snapshot_path
from tomography_session_browser.ui.loading.tasks import BackgroundTask, TaskSignals
from tomography_session_browser.ui.widgets.elided_label import ElidedLabel

if TYPE_CHECKING:
    from tomography_session_browser.ui.tomograms.tab import TomogramTab


@dataclass(frozen=True, slots=True)
class NameQuery:
    key: tuple
    generation: int
    cancelled: Event = field(default_factory=Event, compare=False)


class NextFilePreview(ElidedLabel):
    def __init__(self, tab: TomogramTab, parent) -> None:
        super().__init__("Select a tomogram", mode=Qt.TextElideMode.ElideMiddle, parent=parent)
        self.setObjectName("tomogramNote")
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.setAccessibleName("Next snapshot filename")
        self.tab = tab
        self.key: tuple | None = None
        self.generation = 0
        self.closed = False
        self.running: NameQuery | None = None
        self.pending: NameQuery | None = None
        self.pool = QThreadPool(self)
        self.pool.setMaxThreadCount(1)
        self.signals = TaskSignals(self)
        self.signals.finished.connect(self.finished)
        self.signals.failed.connect(self.failed)
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.setInterval(150)
        self.timer.timeout.connect(self.start)

    @property
    def busy(self) -> bool:
        return self.running is not None or self.pending is not None or self.timer.isActive()

    def refresh(self, *, force: bool = False) -> None:
        if self.closed:
            return
        row, volume = self.tab.current_object(), self.tab.volume()
        directory = self.tab.settings.tomogram_snapshot_folder
        key = (row.id, volume.id, row.name, volume.version,
               self.tab.image_settings.export_format.currentText(), directory,
               (*self.tab.protected_roots(), volume.path.parent),
               self.tab.persistence.repository.directory, self.tab.captures.held_names()) if row and volume else None
        if key == self.key and not force:
            return
        self.key = key
        self.generation += 1
        self.timer.stop()
        self.pending = None
        if self.running:
            self.running.cancelled.set()
        if key is None or not directory:
            self.setText("Select a tomogram" if key is None else "Choose an output folder")
            self.setToolTip("The advisory name is available after selecting a tomogram and an output folder. No file is reserved.")
            return
        self.setText("Checking filename…")
        self.setToolTip("Advisory only. No file is reserved; another writer may take this name before saving.")
        self.pending = NameQuery(key, self.generation)
        self.timer.start()

    def start(self) -> None:
        if self.running is not None or self.pending is None or self.closed or self.timer.isActive():
            return
        query, self.pending = self.pending, None
        self.running = query

        def work():
            _, _, name, version, format, directory, roots, history, held = query.key
            try:
                if query.cancelled.is_set():
                    return None, ""
                folder = require_output_directory(Path(directory), roots)
                return next_snapshot_path(folder, name, version, format, history=history, held=held,
                                          cancelled=query.cancelled.is_set), ""
            except InterruptedError:
                return None, ""
            except (OSError, ValueError) as exc:
                return None, str(exc)

        self.pool.start(BackgroundTask(work, context=query, signals=self.signals,
            logger=logging.getLogger(__name__), failure_log="Snapshot filename preview failed"))

    @Slot(object, object)
    def finished(self, query: NameQuery, result) -> None:
        if self.running is not query:
            return
        self.running = None
        if not self.closed and query.generation == self.generation and not query.cancelled.is_set():
            path, error = result
            self.setText(path.name if path else "Filename unavailable")
            self.setToolTip((str(path) + "\n" if path else error + "\n")
                + "Advisory only. No file is reserved; the writer chooses the final name when saving.")
        self.start()

    @Slot(object, str)
    def failed(self, query: NameQuery, message: str) -> None:
        self.finished(query, (None, message))

    def shutdown(self) -> None:
        self.closed = True
        self.timer.stop()
        self.pending = None
        if self.running:
            self.running.cancelled.set()
        self.pool.waitForDone()
