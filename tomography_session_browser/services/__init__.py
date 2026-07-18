from __future__ import annotations

from typing import Any


def __getattr__(name: str) -> Any:
    if name == "SessionLoader":
        from tomography_session_browser.services.session_loader import SessionLoader

        return SessionLoader
    raise AttributeError(name)

__all__ = ["SessionLoader"]
