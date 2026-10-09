"""A load as it travels from the queue, through the worker, to the UI steps."""
from __future__ import annotations

from dataclasses import dataclass
from typing import NamedTuple

from tomography_session_browser.diagnostics.profiling import LoadingProfiler
from tomography_session_browser.domain.models import Session


class LoadRequest(NamedTuple):
    """One folder to load, and how.

    ``replace`` starts a new project instead of adding to it, ``quiet``
    leaves the load out of the closing toast, and ``animate`` lets the
    dashboard animate in. A tuple, so a queue of requests reads as one.
    """

    path: str
    replace: bool = True
    quiet: bool = False
    animate: bool = True


@dataclass(frozen=True, slots=True)
class LoadJob:
    """A request being loaded, with the profile built for it on the UI thread."""

    request: LoadRequest
    profile: LoadingProfiler


@dataclass(frozen=True, slots=True)
class LoadedSession:
    """A job whose folder parsed, waiting for its UI to be prepared and built."""

    job: LoadJob
    session: Session
