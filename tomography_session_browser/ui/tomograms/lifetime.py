"""Drain producers before storage, including parent-owned QObject destruction."""
from PySide6.QtCore import QThreadPool

from tomography_session_browser.services.volume.review_writer import ReviewWriter


class TomogramWorkers:
    def __init__(self, producers: tuple[QThreadPool, ...], writer: ReviewWriter,
                 history: QThreadPool) -> None:
        self.producers, self.writer, self.history = producers, writer, history
        self.closed = False

    def close(self, *_args) -> None:
        # Every call drains the lanes, the one at destruction too: work started
        # after shutdown must end before Qt deletes its pool, whose destructor
        # waits with the GIL held and so never returns. The writer closes once.
        for pool in self.producers:
            pool.waitForDone()
        if not self.closed:
            self.closed = True
            self.writer.close()
        self.history.waitForDone()
