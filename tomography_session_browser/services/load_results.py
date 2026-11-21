"""Load intent, attempt identity and outcomes, independent of Qt and widgets."""
from dataclasses import dataclass, field
from enum import StrEnum
import errno

from tomography_session_browser.domain.identity import path_identity_key
from tomography_session_browser.services.session_intake import DroppedFolder, FolderRefused


class LoadIntent(StrEnum):
    IMPORT = "import"
    REPLACE = "replace"
    RELOAD = "reload"


@dataclass(frozen=True, slots=True)
class LoadRequest:
    path: str
    intent: LoadIntent = LoadIntent.IMPORT
    quiet: bool = False
    animate: bool = True
    key: str = field(init=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "key", path_identity_key(self.path))


@dataclass(frozen=True, slots=True)
class LoadAttempt:
    batch_id: str
    request: LoadRequest
    number: int


class LoadStage(StrEnum):
    PARSE = "parse"
    PREPARATION = "preparation"
    PUBLICATION = "publication"


class FailureCategory(StrEnum):
    MISSING = "missing"
    UNREADABLE = "unreadable"
    UNREACHABLE = "unreachable"
    PREPARATION = "preparation"
    PUBLICATION = "publication"
    #: The loader refused the folder; the diagnostic is the reason to show.
    REFUSED = "refused"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class LoadFailure:
    attempt: LoadAttempt
    stage: LoadStage
    category: FailureCategory
    diagnostic: str


def classify_failure(attempt: LoadAttempt, stage: LoadStage, error: Exception) -> LoadFailure:
    """Classify before flattening; wording and network-looking paths prove nothing."""
    category = FailureCategory.UNKNOWN
    if stage == LoadStage.PREPARATION:
        category = FailureCategory.PREPARATION
    elif stage == LoadStage.PUBLICATION:
        category = FailureCategory.PUBLICATION
    elif isinstance(error, FolderRefused):
        category = FailureCategory.REFUSED
    elif isinstance(error, OSError) and (error.errno in {
        errno.ENETDOWN, errno.ENETUNREACH, errno.EHOSTUNREACH, errno.ECONNREFUSED, errno.ECONNRESET,
    } or getattr(error, "winerror", None) in {53, 64, 67, 1201, 1222, 1231, 1232}):
        category = FailureCategory.UNREACHABLE
    elif isinstance(error, FileNotFoundError):
        category = FailureCategory.MISSING
    elif isinstance(error, PermissionError):
        category = FailureCategory.UNREADABLE
    elif isinstance(error, OSError):
        if error.errno in {errno.EACCES, errno.EPERM}:
            category = FailureCategory.UNREADABLE
        elif error.errno == errno.ENOENT:
            category = FailureCategory.MISSING
    return LoadFailure(attempt, stage, category, str(error))


class OutcomeKind(StrEnum):
    LOADED = "loaded"
    RELOADED = "reloaded"
    FAILED = "failed"
    RETAINED = "retained"
    SKIPPED = "skipped"
    CANCELLED = "cancelled"
    #: The folder's MRC files need the user's selection (rule 5); nothing from it is published.
    UNANSWERED = "unanswered"


@dataclass(frozen=True, slots=True)
class LoadOutcome:
    attempt: LoadAttempt
    kind: OutcomeKind
    name: str
    failure: LoadFailure | None = None
    replaced_project: bool = False
    #: Why a skipped folder was skipped, when not because it is loaded already.
    note: str = ""

    def __post_init__(self) -> None:
        needs_failure = self.kind in (OutcomeKind.FAILED, OutcomeKind.RETAINED)
        if needs_failure != (self.failure is not None):
            raise ValueError("Only failed or retained outcomes carry a failure.")
        if self.failure is not None and self.failure.attempt != self.attempt:
            raise ValueError("The failure belongs to another load attempt.")

    @property
    def published(self) -> bool:
        return self.kind in (OutcomeKind.LOADED, OutcomeKind.RELOADED)

    @property
    def retryable(self) -> bool:
        return self.kind in (OutcomeKind.FAILED, OutcomeKind.RETAINED)


@dataclass(frozen=True, slots=True)
class LoadBatchResult:
    batch_id: str
    requests: tuple[LoadRequest, ...]
    outcomes: tuple[LoadOutcome, ...]
    admission_skips: tuple[DroppedFolder, ...]
    running: bool

    @property
    def unresolved(self) -> bool:
        return bool(self.admission_skips or any(
            item.retryable or item.kind in (OutcomeKind.SKIPPED, OutcomeKind.UNANSWERED) for item in self.outcomes))

    @property
    def published_count(self) -> int:
        return sum(item.published for item in self.outcomes)
