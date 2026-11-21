from __future__ import annotations

from collections.abc import Callable, Mapping
from pathlib import Path

from tomography_session_browser.domain.folder_type import FolderQuestion, ReadAs, TypeFoundBy
from tomography_session_browser.domain.identity import path_identity_key
from tomography_session_browser.parsers.processing_intake import processing_pipelines, refusal_reason, undecided_files
from tomography_session_browser.domain.models import Session
from tomography_session_browser.domain.enums import SessionKind
from tomography_session_browser.diagnostics.profiling import NULL_PROFILE, LoadingProfiler
from tomography_session_browser.parsers.scan_progress import ScanProgress
from tomography_session_browser.parsers.session_scanner import SessionScanner
from tomography_session_browser.services.session_assembly import assemble_session
from tomography_session_browser.services.session_intake import FolderRefused, TypeNotSelected


def _no_selections() -> Mapping[str, ReadAs]:
    return {}


class SessionLoader:
    """Classifies and loads one folder.

    ``selections`` gives the user's kept selections by folder identity key
    (``read_as_selections``). It is asked each time, so a selection made while
    the app runs counts at the next check or load.
    """

    def __init__(self, scanner: SessionScanner | None = None, *,
                 selections: Callable[[], Mapping[str, ReadAs]] = _no_selections) -> None:
        self._scanner = scanner or SessionScanner()
        self._selections = selections

    def load(
        self,
        path: str | Path,
        *,
        profile: LoadingProfiler = NULL_PROFILE,
        progress: ScanProgress | None = None,
    ) -> Session:
        """Parse and assemble the session before returning it to callers.

        ``progress`` hears the existing sample/part labels across both steps.
        It is display only; a failing callback never stops a load. A folder
        whose MRC files no record decides loads only by a kept selection
        (``question``): without one, ``TypeNotSelected``.
        """

        source = Path(path)
        with profile.phase("validate_session_folder"):
            if not source.exists():
                raise FileNotFoundError(f"Session folder does not exist: {source}")
            if not source.is_dir():
                raise NotADirectoryError(f"Session path is not a folder: {source}")
            # A Recent row skips the check, and the scanner classifies again by
            # itself, so a refused folder is refused here too (owner D2).
            if (reason := refusal_reason(source)) is not None and self.classify(source) == SessionKind.UNKNOWN:
                raise FolderRefused(reason)
            if (question := self.question(source)) is not None:
                raise TypeNotSelected(question)
        session = self._scanner.load(source, profile=profile, progress=progress)
        if self.kept_selection(source) == ReadAs.TILT_SERIES:
            session.found_by = TypeFoundBy.SELECTION
        assemble_session(session, profile=profile, progress=progress)
        return session

    def selection(self, path: str | Path) -> ReadAs | None:
        """The user's kept selection for the folder at ``path``, if any (owner D8 a)."""

        return self._selections().get(path_identity_key(Path(path)))

    def kept_selection(self, path: str | Path) -> ReadAs | None:
        """The kept selection, when the folder still has MRC files that no record decides."""

        selection = self.selection(path)
        return selection if selection is not None and undecided_files(Path(path)) else None

    def question(self, path: str | Path) -> FolderQuestion | None:
        """The folder's MRC files to ask about, when no record decides them and no selection is kept (rule 5)."""

        source = Path(path)
        if self.selection(source) is not None or self.classify(source) in (SessionKind.PROCESSING, SessionKind.UNKNOWN):
            return None
        files = undecided_files(source)
        return FolderQuestion(source, files) if files else None

    def classify(self, path: str | Path) -> SessionKind:
        source = Path(path)
        if not source.exists() or not source.is_dir():
            return SessionKind.UNKNOWN
        # Explicit acquisition metadata wins; the acquisition scanner's broad
        # MRC fallback must not consume a recognised processing output folder.
        if not any((source / name).is_file() for name in ("ScreeningSession.dm", "Session.dm")):
            if processing_pipelines(source, selected=self.selection(source) == ReadAs.TOMOGRAMS):
                return SessionKind.PROCESSING
            # Warp output with no Warp project above it: its volumes are not tilt series.
            if refusal_reason(source) is not None:
                return SessionKind.UNKNOWN
        return self._scanner.classify(source)
