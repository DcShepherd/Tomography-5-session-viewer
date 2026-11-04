from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from tomography_session_browser.services.session_loader import SessionLoader


def __getattr__(name: str) -> type[SessionLoader]:
    if name == "SessionLoader":
        from tomography_session_browser.services.session_loader import SessionLoader

        return SessionLoader
    raise AttributeError(name)

__all__ = ["SessionLoader"]
