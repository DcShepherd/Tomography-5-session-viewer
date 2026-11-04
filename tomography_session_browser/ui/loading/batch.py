"""One queue and one current result per source; no workspace or publication logic."""
from collections import deque
from collections.abc import Iterable
from pathlib import Path
import uuid

from tomography_session_browser.services.load_results import (
    LoadAttempt, LoadBatchResult, LoadIntent, LoadOutcome, LoadRequest, OutcomeKind,
)
from tomography_session_browser.services.session_intake import DroppedFolder


class LoadBatch:
    def __init__(self, requests: Iterable[LoadRequest], *, has_sources: bool,
                 admission_skips: tuple[DroppedFolder, ...] = ()) -> None:
        self.id = uuid.uuid4().hex
        self.started_empty = not has_sources
        self.requests = {request.key: request for request in requests}
        self.admission_skips = admission_skips
        self._queue = deque(self.requests)
        self._attempt_numbers: dict[str, int] = {}
        self._outcomes: dict[str, LoadOutcome] = {}
        self.current: LoadAttempt | None = None
        self.closed = False

    @property
    def running(self) -> bool:
        return not self.closed and bool(self.current or self._queue)

    def next_attempt(self) -> LoadAttempt | None:
        if self.closed or not self._queue:
            return None
        if self.current is not None:
            raise RuntimeError("A load attempt must finish before the next starts.")
        key = self._queue.popleft()
        number = self._attempt_numbers.get(key, 0) + 1
        self._attempt_numbers[key] = number
        self.current = LoadAttempt(self.id, self.requests[key], number)
        return self.current

    def accepts(self, attempt: LoadAttempt) -> bool:
        return not self.closed and self.current == attempt

    def replaces_project(self, attempt: LoadAttempt) -> bool:
        if attempt.request.intent == LoadIntent.REPLACE:
            return True
        return (attempt.request.intent == LoadIntent.IMPORT and self.started_empty
                and not any(item.published for item in self._outcomes.values()))

    def record(self, outcome: LoadOutcome) -> bool:
        if not self.accepts(outcome.attempt):
            return False
        self._outcomes[outcome.attempt.request.key] = outcome
        self.current = None
        return True

    def cancel_pending(self) -> None:
        while self._queue:
            key = self._queue.popleft()
            attempt = LoadAttempt(self.id, self.requests[key], self._attempt_numbers.get(key, 0))
            self._outcomes[key] = LoadOutcome(attempt, OutcomeKind.CANCELLED, Path(attempt.request.path).name)

    def cancel(self) -> None:
        if self.current is not None:
            self.record(LoadOutcome(self.current, OutcomeKind.CANCELLED, Path(self.current.request.path).name))
        self.cancel_pending()
        self.closed = True

    def retry(self) -> bool:
        if self.closed or self.running:
            return False
        self._queue.extend(key for key in self.requests if self._outcomes[key].retryable)
        return bool(self._queue)

    @property
    def result(self) -> LoadBatchResult:
        return LoadBatchResult(self.id, tuple(self.requests.values()),
            tuple(self._outcomes[key] for key in self.requests if key in self._outcomes),
            self.admission_skips, self.running)
