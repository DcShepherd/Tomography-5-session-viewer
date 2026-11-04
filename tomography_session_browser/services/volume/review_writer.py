"""Bounded serial metadata mutations; progress never depends on a GUI callback."""
from __future__ import annotations

from collections import OrderedDict
from concurrent.futures import Future
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from threading import Condition, Lock, Thread
from collections.abc import Callable
from typing import TypeVar

from tomography_session_browser.services.volume.review_repository import MAX_RECORD_BYTES, ReviewRepository

STATE_CAPACITY = 256
DURABLE_CAPACITY = 16
T = TypeVar("T")


@dataclass(frozen=True, slots=True)
class StateIdentity:
    directory: Path
    kind: str
    identity: str


@dataclass(frozen=True, slots=True)
class ReviewWrite:
    repository: ReviewRepository
    kind: str
    identity: str
    state: dict
    extra: dict
    remove: bool = False

    def perform(self) -> None:
        if self.remove:
            self.repository.remove_capture_id(self.identity)
        else:
            self.repository.write(self.kind, self.identity, self.state, **self.extra)


def metadata_size(value: object) -> int:
    """Conservative payload bound without serialising JSON on the submitting thread."""
    if isinstance(value, str):
        return 6 * len(value) + 2
    if isinstance(value, (tuple, list)):
        return 2 + sum(metadata_size(item) + 1 for item in value)
    if isinstance(value, dict):
        return 2 + sum(metadata_size(key) + metadata_size(item) + 2 for key, item in value.items())
    return 32


class ReviewWriter:
    """One in-flight operation, bounded pending identities, worker-side draining.

    State submissions for one pending identity share one acknowledgement: it
    completes after the latest replacement is durable. Captures/deletions never
    coalesce. The short-lived worker exits when idle, including on parent teardown.
    """

    def __init__(self, *, state_capacity: int = STATE_CAPACITY, durable_capacity: int = DURABLE_CAPACITY) -> None:
        self.state_capacity, self.durable_capacity = state_capacity, durable_capacity
        self.condition = Condition()
        self.operation = Lock()
        self.pending: OrderedDict[StateIdentity | int, tuple[ReviewWrite, Future[None]]] = OrderedDict()
        self.running = False
        self.closed = False
        self.thread: Thread | None = None
        self.revision = 0

    def submit(self, request: ReviewWrite) -> Future[None]:
        if metadata_size((request.state, request.extra)) > MAX_RECORD_BYTES:
            raise ValueError("Review metadata exceeds the size limit.")
        request = ReviewWrite(request.repository, request.kind, request.identity,
                              deepcopy(request.state), deepcopy(request.extra), request.remove)
        state = request.kind in ("view", "opened") and not request.remove
        with self.condition:
            if self.closed:
                raise ValueError("Review storage is closing.")
            self.revision += 1
            key = (StateIdentity(request.repository.directory, request.kind, request.identity) if state
                   else self.revision)
            previous = self.pending.get(key)
            if previous is None:
                count = sum(isinstance(value, StateIdentity) == state for value in self.pending)
                if count >= (self.state_capacity if state else self.durable_capacity):
                    raise ValueError("Review storage is busy. Retry after pending records finish.")
            future: Future[None] = previous[1] if previous else Future()
            self.pending[key] = request, future
            self.pending.move_to_end(key)
            if not self.running:
                self.running = True
                self.thread = Thread(target=self.run, name="tomogram-review-writer", daemon=True)
                self.thread.start()
            return future

    def run(self) -> None:
        while True:
            with self.condition:
                if not self.pending:
                    self.running = False
                    self.condition.notify_all()
                    return
                _, (request, future) = self.pending.popitem(last=False)
            try:
                with self.operation:
                    request.perform()
            except Exception as exc:
                future.set_exception(exc)
            else:
                future.set_result(None)

    def flush(self) -> None:
        """Worker/read-side barrier. Never call from an interactive GUI handler."""
        with self.condition:
            self.condition.wait_for(lambda: not self.running)

    def read(self, work: Callable[[], T]) -> T:
        """Read-side barrier on a metadata worker, never on the GUI thread."""
        self.flush()
        # A new write can arrive after flush. Keep its unlink/replace clear of
        # open read handles (Windows denies deletion while such handles exist).
        with self.operation:
            return work()

    def close(self) -> None:
        with self.condition:
            self.closed = True
        self.flush()
