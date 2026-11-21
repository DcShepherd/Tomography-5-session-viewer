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
        #: The folders of this pass, in order: every request, then a retry's own.
        self._pass: tuple[str, ...] = tuple(self.requests)
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
        self._pass = tuple(key for key in self.requests if self._outcomes[key].retryable)
        self._queue.extend(self._pass)
        return bool(self._queue)

    def unanswered(self) -> tuple[str, ...]:
        """The folders whose MRC files need the user's selection (rule 5), in request order."""

        return tuple(key for key in self.requests
                     if key in self._outcomes and self._outcomes[key].kind == OutcomeKind.UNANSWERED)

    def skip(self, key: str, note: str) -> None:
        """An unanswered folder that the user does not load: skipped, with ``note`` as the reason."""

        outcome = self._outcomes[key]
        if outcome.kind != OutcomeKind.UNANSWERED:
            raise ValueError("Only an unanswered folder is skipped by a selection.")
        self._outcomes[key] = LoadOutcome(outcome.attempt, OutcomeKind.SKIPPED, outcome.name, note=note)

    def again(self, keys: Iterable[str]) -> bool:
        """Queue answered folders once more in this batch, as a pass of their own."""

        if self.closed or self.running:
            return False
        selected = tuple(key for key in keys if key in self.requests
                         and self._outcomes.get(key, None) is not None
                         and self._outcomes[key].kind == OutcomeKind.UNANSWERED)
        if not selected:
            return False
        self._pass = selected
        self._queue.extend(selected)
        return True

    def pass_position(self, attempt: LoadAttempt) -> tuple[int, int]:
        """Where an attempt sits in its pass: (number from 1, folders in the pass).

        A retry is its own pass. A folder found to be a duplicate while it
        loads keeps its place; folders refused before the load are not
        requests, so they are not counted.
        """

        key = attempt.request.key
        return (self._pass.index(key) + 1, len(self._pass)) if key in self._pass else (1, 1)

    @property
    def result(self) -> LoadBatchResult:
        return LoadBatchResult(self.id, tuple(self.requests.values()),
            tuple(self._outcomes[key] for key in self.requests if key in self._outcomes),
            self.admission_skips, self.running)
