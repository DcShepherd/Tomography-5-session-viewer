"""Which of a set of dropped or chosen folders can load, and why not.

Session folders arrive two ways: dropped onto the project panel, or queued in
the Load sessions window. Both ask the same question before anything loads:
is each path a Tomography 5 session folder the project does not hold yet?
This module answers it once, for both, so the drop feedback and the window
cannot disagree about a folder.

It only looks. Classification is the loader's (``SessionLoader.classify``,
passed in as ``classify``); nothing here parses, links or changes a folder,
and the source folders stay read-only. The words it returns are display text
for the drop feedback, the window and the receipt.
"""

from __future__ import annotations

from collections.abc import Callable, Collection, Iterable
from dataclasses import dataclass, field, replace
from enum import StrEnum
from pathlib import Path
import time
from typing import Generic, Protocol, TypeVar, runtime_checkable

from tomography_session_browser.domain.display_names import count_phrase
from tomography_session_browser.domain.enums import SessionKind
from tomography_session_browser.domain.folder_type import FolderQuestion, ReadAs
from tomography_session_browser.domain.identity import path_identity_key
from tomography_session_browser.parsers.processing_intake import refusal_reason, source_root

#: How long a drag may spend classifying before the rest wait for the drop.
#: Classification is cheap on a local disk, but a slow network share must not
#: stall the cursor.
DRAG_CHECK_BUDGET_S = 0.15

NOT_A_SESSION_REASON = "No supported acquisition or processing metadata in this folder"
SESSION_FILE_REASON = "A session file — drop its folder instead"
MISSING_REASON = "Folder not found"
UNREADABLE_REASON = "This folder could not be read"
ALREADY_LOADED_REASON = "Already in the project"
#: The receipt's reason for a folder whose question was not answered (the load stopped first).
TYPE_NOT_SELECTED_REASON = "You did not select the type of its MRC files"
#: A dragged folder that the load will ask about (owner D14 a): the pop-up shows after the drop.
SELECT_AFTER_RELEASE = "Select after you release"
#: The words for a selection in a row or a Context line ("Read as: tomograms · your selection").
READ_AS_WORDS = {ReadAs.TOMOGRAMS: "tomograms", ReadAs.TILT_SERIES: "tilt series"}


class FolderRefused(ValueError):
    """A folder the loader refuses, for the reason in its message (``refusal_reason``).

    A path that skips the check (a Recent row) is refused when it loads, and
    the load result shows this message as the reason.
    """


class TypeNotSelected(ValueError):
    """A folder whose MRC files no record decides, loaded with no selection kept (rule 5).

    The loader does not guess. The window asks the user (``question``) and loads
    the folder again with the selection.
    """

    def __init__(self, question: FolderQuestion) -> None:
        super().__init__("Select the type of the MRC files in this folder.")
        self.question = question


class FolderCheck(StrEnum):
    READY = "ready"
    ALREADY_LOADED = "already_loaded"
    SESSION_FILE = "session_file"
    MISSING = "missing"
    NOT_A_SESSION = "not_a_session"
    #: Not looked at yet: the drag ran out of time (``DRAG_CHECK_BUDGET_S``).
    UNCHECKED = "unchecked"


_REASONS: dict[FolderCheck, str] = {
    FolderCheck.ALREADY_LOADED: ALREADY_LOADED_REASON,
    FolderCheck.SESSION_FILE: SESSION_FILE_REASON,
    FolderCheck.MISSING: MISSING_REASON,
    FolderCheck.NOT_A_SESSION: NOT_A_SESSION_REASON,
}


@dataclass(frozen=True)
class DroppedFolder:
    """One path as it arrived, and what the check found."""

    path: str
    status: FolderCheck
    kind: SessionKind | None = None
    #: For a session file: the folder that holds it (the window offers it).
    parent_folder: str | None = None
    #: Overrides the status's usual reason (an unreadable folder).
    note: str = ""
    #: How many MRC files no record decides, with no selection kept: the load asks (rule 5).
    asks: int = 0
    #: The user's kept selection for such a folder (owner D8 a).
    selection: ReadAs | None = None

    @property
    def name(self) -> str:
        return Path(self.path).name or self.path

    @property
    def ready(self) -> bool:
        return self.status == FolderCheck.READY

    @property
    def problem(self) -> bool:
        return self.status not in (FolderCheck.READY, FolderCheck.UNCHECKED)

    @property
    def badge(self) -> str:
        """The project tree's badge for what this folder holds, if known."""

        if self.asks:
            return "?"
        if self.kind == SessionKind.PROCESSING:
            return "PR"
        if self.kind == SessionKind.ATLAS_SCREENING:
            return "AT"
        if self.kind in (SessionKind.MULTIGRID, SessionKind.COLLECTION, SessionKind.SINGLE_COLLECTION):
            return "DC"
        return ""

    @property
    def kind_label(self) -> str:
        if self.asks:
            return "Select at load"
        if self.kind == SessionKind.PROCESSING:
            return "Processing folder"
        if self.status == FolderCheck.UNCHECKED:
            return "Checked on drop"
        if self.kind == SessionKind.ATLAS_SCREENING:
            return "Atlas screening"
        if self.kind == SessionKind.MULTIGRID:
            return "Multi-grid collection"
        if self.kind in (SessionKind.COLLECTION, SessionKind.SINGLE_COLLECTION):
            return "Data collection"
        if self.status == FolderCheck.SESSION_FILE:
            return "File"
        # Only a finished check can say this. A missing or unreadable folder was
        # never classified, so it has no kind to name; its reason says why.
        return "Not a session" if self.kind == SessionKind.UNKNOWN else ""

    @property
    def type_note(self) -> str:
        """What the row says about a folder whose MRC files no record decides; empty for others."""

        if self.asks:
            return f"The app cannot find the type of {count_phrase(self.asks, 'file')}."
        if self.selection is not None:
            return f"Read as: {READ_AS_WORDS[self.selection]} · your selection"
        return ""

    @property
    def reason(self) -> str:
        """Why it will not load; empty when it will."""

        return self.note or _REASONS.get(self.status, "")

    @property
    def retryable(self) -> bool:
        """Its result can change on disk, so supplying its path again checks it again (D17).

        A session file, an already loaded source and a ready folder cannot
        become something else by a second look; a missing folder or one with
        no session in it can.
        """

        return self.status in (FolderCheck.MISSING, FolderCheck.NOT_A_SESSION)


@runtime_checkable
class FolderTyping(Protocol):
    """What the check asks the loader about MRC files that no record decides (``SessionLoader``)."""

    def question(self, path: str | Path) -> FolderQuestion | None: ...

    def kept_selection(self, path: str | Path) -> ReadAs | None: ...


def folder_typing(loader: object) -> FolderTyping | None:
    """``loader`` when it can say which folders ask (rule 5); a loader that only classifies cannot."""

    return loader if isinstance(loader, FolderTyping) else None


def source_key(path: str | Path) -> str:
    """The key of the source a path loads: a Scipion job folder loads its ``extra``
    folder, and a Warp output folder its Warp project (``source_root``)."""

    return path_identity_key(source_root(Path(path)))


def names_a_file(path: Path) -> bool:
    """``path`` is a file, not a folder: an existing file, or a ``.dm``
    session file (even a missing one)."""

    return path.is_file() or (path.suffix.casefold() == ".dm" and not path.is_dir())


def check_folder(
    raw_path: str | Path,
    classify: Callable[[Path], SessionKind],
    *,
    loaded_keys: Collection[str] = (),
    key: str | None = None,
    read_as: FolderTyping | None = None,
) -> DroppedFolder:
    """Check one path: exists, is a folder, holds a session, is not loaded.

    ``key`` is the path's ``source_key`` when the caller has it already; it is
    not worked out a second time. With ``read_as``, a folder whose MRC files no
    record decides says that the load asks, or shows the kept selection.
    """

    path = Path(raw_path)
    text = str(path)
    try:
        if names_a_file(path):
            parent = path.parent
            parent_folder = str(parent) if parent.is_dir() else None
            return DroppedFolder(text, FolderCheck.SESSION_FILE, parent_folder=parent_folder)
        if not path.is_dir():
            return DroppedFolder(text, FolderCheck.MISSING)
        kind = classify(path)
        # The classifier says Unknown for a refused folder; the row says why.
        note = (refusal_reason(path) or "") if kind == SessionKind.UNKNOWN else ""
        asks, selection = 0, None
        if read_as is not None and kind != SessionKind.UNKNOWN:
            question = read_as.question(path)
            asks = len(question.files) if question is not None else 0
            selection = None if question is not None else read_as.kept_selection(path)
    except OSError:
        return DroppedFolder(text, FolderCheck.NOT_A_SESSION, note=UNREADABLE_REASON)
    if kind == SessionKind.UNKNOWN:
        return DroppedFolder(text, FolderCheck.NOT_A_SESSION, kind=kind, note=note)
    if (source_key(path) if key is None else key) in loaded_keys:
        return DroppedFolder(text, FolderCheck.ALREADY_LOADED, kind=kind)
    return DroppedFolder(text, FolderCheck.READY, kind=kind, asks=asks, selection=selection)


@dataclass(frozen=True)
class SessionIntake:
    """The folders of one drop, or of the window's queue, once checked."""

    folders: tuple[DroppedFolder, ...]
    #: One record for every supplied path, in order, when the intake came from
    #: ``admit``; a drop finishes its unchecked records from these.
    records: tuple["Admitted", ...] = field(default=(), compare=False)

    @property
    def ready(self) -> tuple[DroppedFolder, ...]:
        return tuple(folder for folder in self.folders if folder.ready)

    @property
    def problems(self) -> tuple[DroppedFolder, ...]:
        return tuple(folder for folder in self.folders if folder.problem)

    @property
    def unchecked(self) -> tuple[DroppedFolder, ...]:
        return tuple(folder for folder in self.folders if folder.status == FolderCheck.UNCHECKED)

    @property
    def ready_paths(self) -> tuple[str, ...]:
        return tuple(folder.path for folder in self.ready)

    @property
    def can_load(self) -> bool:
        """Whether a drop could load anything (unchecked folders might)."""

        return bool(self.ready or self.unchecked)

    @property
    def tone(self) -> str:
        """``valid``, ``mixed`` or ``invalid``: the drop feedback's colour."""

        if not self.can_load:
            return "invalid"
        return "mixed" if self.problems else "valid"

    @property
    def title(self) -> str:
        ready, unchecked, problems = len(self.ready), len(self.unchecked), len(self.problems)
        if not self.can_load:
            if problems and all(folder.status == FolderCheck.ALREADY_LOADED for folder in self.problems):
                return "Already in the project"
            return "Nothing here can load"
        if problems:
            return f"{ready} of {len(self.folders)} folders can load"
        if unchecked:
            return f"Release to check and load {count_phrase(ready + unchecked, 'folder')}"
        return f"Release to load {count_phrase(ready, 'session')}"

    @property
    def detail(self) -> str:
        ready, unchecked, problems = len(self.ready), len(self.unchecked), len(self.problems)
        if not self.can_load:
            if self.title == "Already in the project":
                return "These sessions are loaded already"
            return "Drop acquisition or supported processing folders"
        if problems:
            return f"Release to load {ready} · {problems} will be skipped"
        if unchecked:
            return f"{count_phrase(unchecked, 'folder')} will be checked on release"
        if ready > 1:
            return "Sessions that share an atlas are linked"
        return "It joins the current project"

    @property
    def footnote(self) -> str:
        return "" if self.can_load else "Releasing here leaves the project unchanged"


T = TypeVar("T")


@dataclass(frozen=True)
class QueueEntry(Generic[T]):
    """One queued source: the spelling supplied first, its source key, its check
    and a token the caller owns.

    ``key`` is ``None`` only while the check is ``UNCHECKED``. The token is the
    caller's (the window keys its rows by it); this module never reads it, and a
    new entry has none until the caller gives it one.
    """

    spelling: str
    key: str | None
    folder: DroppedFolder
    token: T | None = None


@dataclass(frozen=True)
class Admitted:
    """What one supplied path came to: its spelling, its source key and its check."""

    spelling: str
    key: str | None
    folder: DroppedFolder


@dataclass(frozen=True)
class Admission(Generic[T]):
    """The proposed queue, and one record for every supplied path, in order."""

    entries: tuple[QueueEntry[T], ...]
    records: tuple[Admitted, ...]

    @property
    def intake(self) -> SessionIntake:
        """The proposed queue as an intake, with the records kept for a later drop."""

        return SessionIntake(tuple(entry.folder for entry in self.entries), self.records)

    @property
    def supplied(self) -> SessionIntake:
        """The supplied paths alone, one per source, first spelling first: what a drag shows."""

        seen: set[str] = set()
        folders: list[DroppedFolder] = []
        for record in self.records:
            mark = record.key if record.key is not None else path_identity_key(record.spelling)
            if mark not in seen:
                seen.add(mark)
                folders.append(record.folder)
        return SessionIntake(tuple(folders), self.records)


def _at(folder: DroppedFolder, spelling: str) -> DroppedFolder:
    return folder if folder.path == spelling else replace(folder, path=spelling)


def _merge(queue: list[QueueEntry[T]], key: str | None, folder: DroppedFolder) -> None:
    """Keep one entry per source: the earliest in the queue, which takes ``folder``."""

    if key is None:
        return
    same = [index for index, entry in enumerate(queue) if entry.key == key]
    if not same:
        return
    first = queue[same[0]]
    survivor = replace(first, folder=_at(folder, first.spelling))
    queue[:] = [survivor if index == same[0] else entry
                for index, entry in enumerate(queue) if index == same[0] or entry.key != key]


def _stored(queue: list[QueueEntry[T]], text_key: str) -> int | None:
    return next((index for index, entry in enumerate(queue) if path_identity_key(entry.spelling) == text_key), None)


def admit(
    entries: Iterable[QueueEntry[T]],
    paths: Iterable[str | Path],
    classify: Callable[[Path], SessionKind],
    *,
    loaded_keys: Collection[str] = (),
    budget_s: float | None = None,
    known: Iterable[Admitted] = (),
    clock: Callable[[], float] = time.monotonic,
    read_as: FolderTyping | None = None,
) -> Admission[T]:
    """Admit ``paths`` to the queue ``entries``. Pure: it returns a result and changes nothing.

    Browse, a Recent chip, a drag, a drop and the Project window's drop all come
    here, so one loop decides what is looked at. For each supplied path, in order:

    * a spelling the queue holds, whose check cannot change (``retryable`` is
      false), takes the stored result with no file-system call;
    * any other path gets a fresh source key and then a check. Within one call a
      source is checked once: paths that reach the same key share the check.
      A stored, retryable entry is updated in place (D17, a retry);
    * entries that reach the same key merge. The earliest entry in the queue
      survives, keeps its token and spelling, and takes the fresh check; the
      order of the supplied paths cannot change which one that is.

    Unrelated entries are not looked at again and keep their order. With
    ``budget_s`` a path still waiting when the time is spent gets no key and an
    ``UNCHECKED`` check (a drag must not stall on a slow share). ``known`` holds
    the records of that drag: a drop reuses the completed ones and finishes the
    rest, so their identity is computed then, not frozen at the drag.
    """

    started = clock()
    queue: list[QueueEntry[T]] = list(entries)
    completed = {path_identity_key(record.spelling): record for record in known
                 if record.folder.status != FolderCheck.UNCHECKED}
    checked: dict[str, DroppedFolder] = {record.key: record.folder for record in completed.values() if record.key is not None}
    this_call: dict[str, Admitted] = {}
    records: list[Admitted] = []
    for raw_path in paths:
        spelling = str(Path(raw_path))
        text_key = path_identity_key(spelling)
        if text_key in this_call:
            earlier = this_call[text_key]
            records.append(Admitted(spelling, earlier.key, _at(earlier.folder, spelling)))
            continue
        index = _stored(queue, text_key)
        if index is not None and not queue[index].folder.retryable:
            entry = queue[index]
            record = Admitted(spelling, entry.key, _at(entry.folder, spelling))
            this_call[text_key] = record
            records.append(record)
            continue
        if text_key in completed:
            done = completed[text_key]
            record = Admitted(spelling, done.key, _at(done.folder, spelling))
        elif budget_s is not None and checked and clock() - started > budget_s:
            record = Admitted(spelling, None, DroppedFolder(spelling, FolderCheck.UNCHECKED))
        else:
            key = source_key(spelling)
            if key not in checked:
                checked[key] = check_folder(spelling, classify, loaded_keys=loaded_keys, key=key, read_as=read_as)
            record = Admitted(spelling, key, _at(checked[key], spelling))
        this_call[text_key] = record
        records.append(record)
        if index is not None:
            entry = queue[index]
            queue[index] = replace(entry, key=record.key, folder=_at(record.folder, entry.spelling))
            _merge(queue, record.key, record.folder)
        elif record.key is not None and any(entry.key == record.key for entry in queue):
            _merge(queue, record.key, record.folder)
        else:
            queue.append(QueueEntry(spelling, record.key, record.folder))
    return Admission(tuple(queue), tuple(records))


def replace_entry(
    entries: Iterable[QueueEntry[T]],
    index: int,
    path: str | Path,
    classify: Callable[[Path], SessionKind],
    *,
    loaded_keys: Collection[str] = (),
    read_as: FolderTyping | None = None,
) -> tuple[QueueEntry[T], ...]:
    """Put ``path`` in place of entry ``index`` ("Use parent folder"), checking only ``path``.

    The entry keeps its position and token; if another entry is the same
    source, the earlier of the two survives, as in ``admit``.
    """

    queue = list(entries)
    spelling = str(Path(path))
    key = source_key(spelling)
    folder = check_folder(spelling, classify, loaded_keys=loaded_keys, key=key, read_as=read_as)
    queue[index] = QueueEntry(spelling, key, folder, queue[index].token)
    _merge(queue, key, folder)
    return tuple(queue)


def assess_paths(
    paths: Iterable[str | Path],
    classify: Callable[[Path], SessionKind],
    *,
    loaded_keys: Collection[str] = (),
    budget_s: float | None = None,
    clock: Callable[[], float] = time.monotonic,
    read_as: FolderTyping | None = None,
) -> SessionIntake:
    """Check each distinct source, in order: ``admit`` to an empty queue.

    The first spelling of a source stands for it. With ``budget_s``, paths still
    waiting when the time is spent are left ``UNCHECKED``; ``recheck_unchecked``
    finishes them at the drop.
    """

    return admit((), paths, classify, loaded_keys=loaded_keys, budget_s=budget_s, clock=clock, read_as=read_as).intake


def recheck_unchecked(
    intake: SessionIntake,
    classify: Callable[[Path], SessionKind],
    *,
    loaded_keys: Collection[str] = (),
    read_as: FolderTyping | None = None,
) -> SessionIntake:
    """Finish a drag's check at the drop: look at what it had no time for, and nothing else."""

    if not intake.unchecked:
        return intake
    # An intake built by hand has no records: its folders are what was supplied.
    records = intake.records or tuple(Admitted(folder.path, None, folder) for folder in intake.folders)
    return admit((), [record.spelling for record in records], classify, loaded_keys=loaded_keys, known=records,
                 read_as=read_as).intake
