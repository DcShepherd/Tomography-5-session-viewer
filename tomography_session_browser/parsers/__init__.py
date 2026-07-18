from __future__ import annotations

from typing import Any


def __getattr__(name: str) -> Any:
    if name == "SessionScanner":
        from tomography_session_browser.parsers.session_scanner import SessionScanner

        return SessionScanner
    raise AttributeError(name)

__all__ = ["SessionScanner"]
