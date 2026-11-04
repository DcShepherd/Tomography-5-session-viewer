"""Drain producers before storage, including parent-owned QObject destruction."""
from PySide6.QtCore import QThreadPool

from tomography_session_browser.services.volume.review_writer import ReviewWriter


class TomogramWorkers:
    def __init__(self, producers: tuple[QThreadPool, ...], writer: ReviewWriter,
                 history: QThreadPool) -> None:
        self.producers, self.writer, self.history = producers, writer, history
        self.closed = False

    def close(self, *_args) -> None:
        if self.closed:
            return
        self.closed = True
        for pool in self.producers:
            pool.waitForDone()
        self.writer.close()
        self.history.waitForDone()
