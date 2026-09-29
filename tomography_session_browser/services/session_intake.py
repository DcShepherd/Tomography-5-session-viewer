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
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
import time

from tomography_session_browser.domain.display_names import count_phrase
from tomography_session_browser.domain.enums import SessionKind

#: How long a drag may spend classifying before the rest wait for the drop.
#: Classification is cheap on a local disk, but a slow network share must not
#: stall the cursor.
DRAG_CHECK_BUDGET_S = 0.15

NOT_A_SESSION_REASON = "No Tomography 5 metadata in this folder"
SESSION_FILE_REASON = "A session file — drop its folder instead"
MISSING_REASON = "Folder not found"
UNREADABLE_REASON = "This folder could not be read"
ALREADY_LOADED_REASON = "Already in the project"


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

        if self.kind == SessionKind.ATLAS_SCREENING:
            return "AT"
        if self.kind in (SessionKind.MULTIGRID, SessionKind.COLLECTION, SessionKind.SINGLE_COLLECTION):
            return "DC"
        return ""

    @property
    def kind_label(self) -> str:
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
        return "Not a session"

    @property
    def reason(self) -> str:
        """Why it will not load; empty when it will."""

        return self.note or _REASONS.get(self.status, "")


def path_identity_key(path: str | Path) -> str:
    """The key two spellings of one folder share (as the project tree uses)."""

    value = Path(path)
    if not value.is_absolute():
        value = Path.cwd() / value
    return str(value).replace("\\", "/").casefold()


def check_folder(
    raw_path: str | Path,
    classify: Callable[[Path], SessionKind],
    *,
    loaded_keys: Collection[str] = (),
) -> DroppedFolder:
    """Check one path: exists, is a folder, holds a session, is not loaded."""

    path = Path(raw_path)
    text = str(path)
    try:
        if path.is_file() or (path.suffix.casefold() == ".dm" and not path.is_dir()):
            parent = path.parent
            parent_folder = str(parent) if parent.is_dir() else None
            return DroppedFolder(text, FolderCheck.SESSION_FILE, parent_folder=parent_folder)
        if not path.is_dir():
            return DroppedFolder(text, FolderCheck.MISSING)
        kind = classify(path)
    except OSError:
        return DroppedFolder(text, FolderCheck.NOT_A_SESSION, note=UNREADABLE_REASON)
    if kind == SessionKind.UNKNOWN:
        return DroppedFolder(text, FolderCheck.NOT_A_SESSION, kind=kind)
    if path_identity_key(path) in loaded_keys:
        return DroppedFolder(text, FolderCheck.ALREADY_LOADED, kind=kind)
    return DroppedFolder(text, FolderCheck.READY, kind=kind)


@dataclass(frozen=True)
class SessionIntake:
    """The folders of one drop, or of the window's queue, once checked."""

    folders: tuple[DroppedFolder, ...]

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
            return "Drop Tomography 5 session folders, not files or exports"
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


def assess_paths(
    paths: Iterable[str | Path],
    classify: Callable[[Path], SessionKind],
    *,
    loaded_keys: Collection[str] = (),
    budget_s: float | None = None,
    clock: Callable[[], float] = time.monotonic,
) -> SessionIntake:
    """Check each distinct path, in order.

    With ``budget_s``, paths still waiting when the time is spent are left
    ``UNCHECKED`` (a drag must not stall on a slow share); the drop checks
    them again without a budget.
    """

    started = clock()
    seen: set[str] = set()
    folders: list[DroppedFolder] = []
    for raw_path in paths:
        key = path_identity_key(raw_path)
        if key in seen:
            continue
        seen.add(key)
        if budget_s is not None and folders and clock() - started > budget_s:
            folders.append(DroppedFolder(str(Path(raw_path)), FolderCheck.UNCHECKED))
            continue
        folders.append(check_folder(raw_path, classify, loaded_keys=loaded_keys))
    return SessionIntake(tuple(folders))


def recheck_unchecked(
    intake: SessionIntake,
    classify: Callable[[Path], SessionKind],
    *,
    loaded_keys: Collection[str] = (),
) -> SessionIntake:
    """Finish a drag's check at the drop: look at what it had no time for."""

    if not intake.unchecked:
        return intake
    return SessionIntake(
        tuple(
            check_folder(folder.path, classify, loaded_keys=loaded_keys)
            if folder.status == FolderCheck.UNCHECKED
            else folder
            for folder in intake.folders
        )
    )


@dataclass(frozen=True)
class DropReceipt:
    """What the project panel says once a drop's load has finished."""

    loaded_count: int
    skipped: tuple[DroppedFolder, ...]

    @property
    def title(self) -> str:
        return f"Loaded {count_phrase(self.loaded_count, 'session')}"

    @property
    def skipped_title(self) -> str:
        return f"{count_phrase(len(self.skipped), 'folder')} skipped" if self.skipped else ""

    @property
    def skipped_lines(self) -> tuple[str, ...]:
        return tuple(f"{folder.name} — {folder.reason}" for folder in self.skipped)
