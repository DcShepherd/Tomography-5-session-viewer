from __future__ import annotations

from pathlib import Path

from tomography_session_browser.domain.models import Session
from tomography_session_browser.domain.enums import SessionKind
from tomography_session_browser.services.loading_profiler import LoadingProfiler
from tomography_session_browser.parsers.session_scanner import SessionScanner


class SessionLoader:
    def __init__(self, scanner: SessionScanner | None = None) -> None:
        self._scanner = scanner or SessionScanner()

    def load(self, path: str | Path, *, profile: LoadingProfiler | None = None) -> Session:
        source = Path(path)
        with (profile.phase("validate_session_folder") if profile is not None else _null_phase()):
            if not source.exists():
                raise FileNotFoundError(f"Session folder does not exist: {source}")
            if not source.is_dir():
                raise NotADirectoryError(f"Session path is not a folder: {source}")
        return self._scanner.load(source, profile=profile)

    def classify(self, path: str | Path) -> SessionKind:
        source = Path(path)
        if not source.exists() or not source.is_dir():
            return SessionKind.UNKNOWN
        return self._scanner.classify(source)


class _null_phase:
    def __enter__(self) -> None:
        return None

    def __exit__(self, *args: object) -> None:
        return None
