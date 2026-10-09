from __future__ import annotations

from pathlib import Path

from tomography_session_browser.domain.models import Session
from tomography_session_browser.domain.enums import SessionKind
from tomography_session_browser.diagnostics.profiling import NULL_PROFILE, LoadingProfiler
from tomography_session_browser.parsers.scan_progress import ScanProgress
from tomography_session_browser.parsers.session_scanner import SessionScanner
from tomography_session_browser.services.session_assembly import assemble_session


class SessionLoader:
    def __init__(self, scanner: SessionScanner | None = None) -> None:
        self._scanner = scanner or SessionScanner()

    def load(
        self,
        path: str | Path,
        *,
        profile: LoadingProfiler = NULL_PROFILE,
        progress: ScanProgress | None = None,
    ) -> Session:
        """Parse and assemble the session before returning it to callers.

        ``progress`` hears the existing sample/part labels across both steps.
        It is display only; a failing callback never stops a load.
        """

        source = Path(path)
        with profile.phase("validate_session_folder"):
            if not source.exists():
                raise FileNotFoundError(f"Session folder does not exist: {source}")
            if not source.is_dir():
                raise NotADirectoryError(f"Session path is not a folder: {source}")
        session = self._scanner.load(source, profile=profile, progress=progress)
        assemble_session(session, profile=profile, progress=progress)
        return session

    def classify(self, path: str | Path) -> SessionKind:
        source = Path(path)
        if not source.exists() or not source.is_dir():
            return SessionKind.UNKNOWN
        return self._scanner.classify(source)
