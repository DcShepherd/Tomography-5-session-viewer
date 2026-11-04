"""One running and one replaceable pending value per project, independent of Qt."""
from concurrent.futures import Future
from threading import Condition, Thread

from tomography_session_browser.domain.tomogram_tags import TagProject
from tomography_session_browser.services.tomogram_tags.store import TagConflict, TagProjectStore, TagWriteReceipt


class TagProjectWriter:
    def __init__(self, store: TagProjectStore, digest: str | None, revision: int = -1):
        self.store = store
        self.committed_digest = digest
        self.committed_revision = revision
        self.condition = Condition()
        self.pending: tuple[TagProject, Future[TagWriteReceipt]] | None = None
        self.running = False
        self.closed = False
        self.conflict: str | None = None
        self.highest_submitted = revision
        self.active: tuple[TagProject, Future[TagWriteReceipt]] | None = None
        self.thread = Thread(target=self.run, name="tag-project-writer", daemon=True)
        self.thread.start()

    def submit(self, project: TagProject) -> Future[TagWriteReceipt]:
        with self.condition:
            if self.closed:
                raise RuntimeError("The project writer is closed.")
            if self.conflict:
                raise TagConflict(self.conflict)
            for item in (self.pending, self.active):
                if item is not None and item[0].revision == project.revision:
                    if item[0] != project:
                        raise ValueError("Different project values cannot share a revision.")
                    return item[1]
            if project.revision < self.highest_submitted or project.revision <= self.committed_revision:
                raise ValueError("Project revisions must not decrease or repeat a committed revision.")
            self.highest_submitted = project.revision
            future = self.pending[1] if self.pending else Future()
            self.pending = project, future
            self.condition.notify_all()
            return future

    def run(self) -> None:
        while True:
            with self.condition:
                self.condition.wait_for(lambda: self.pending is not None or self.closed)
                if self.pending is None:
                    return
                project, future = self.pending
                self.pending, self.running = None, True
                self.active = project, future
            try:
                receipt = self.store.save(project, self.committed_digest)
                # Advance on this worker before futures or GUI callbacks can observe it.
                self.committed_digest, self.committed_revision = receipt.digest, receipt.revision
            except Exception as exc:
                abandoned = None
                with self.condition:
                    if isinstance(exc, TagConflict):
                        self.conflict = str(exc)
                        abandoned, self.pending = self.pending, None
                future.set_exception(exc)
                if abandoned:
                    abandoned[1].set_exception(exc)
            else:
                future.set_result(receipt)
            finally:
                with self.condition:
                    self.running = False
                    self.active = None
                    self.condition.notify_all()

    def flush(self, timeout: float | None = None) -> bool:
        with self.condition:
            return self.condition.wait_for(lambda: not self.running and self.pending is None, timeout)

    def close(self, timeout: float | None = None) -> bool:
        """A timeout leaves the lane operational for a cancellable close preflight."""
        if not self.flush(timeout):
            return False
        with self.condition:
            # Submission could race the first flush; do not retire queued work.
            if self.running or self.pending:
                return False
            self.closed = True
            self.condition.notify_all()
        self.thread.join(timeout)
        return not self.thread.is_alive()
