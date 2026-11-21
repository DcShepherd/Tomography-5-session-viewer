"""One attempt travels unchanged through parsing, preparation and publication."""
from __future__ import annotations
from collections.abc import Mapping
from dataclasses import dataclass, field
from threading import Event
from typing import TYPE_CHECKING

from tomography_session_browser.diagnostics.profiling import LoadingProfiler
from tomography_session_browser.domain.folder_type import ReadAs
from tomography_session_browser.domain.models import Session
from tomography_session_browser.domain.tomograms import ProcessingSource
from tomography_session_browser.presentation.workspace import Scope, Workspace
from tomography_session_browser.services.load_results import LoadAttempt
if TYPE_CHECKING:
    from tomography_session_browser.presentation.prepared_session import PreparedSessionUi


@dataclass(frozen=True, slots=True)
class LoadJob:
    attempt: LoadAttempt
    profile: LoadingProfiler
    replace: bool
    previous: Session | ProcessingSource | None = None
    #: The kept selections when the attempt started (``read_as_selections``): the worker reads no settings.
    selections: Mapping[str, ReadAs] = field(default_factory=dict, compare=False)


@dataclass(frozen=True, slots=True)
class LoadedSession:
    job: LoadJob
    session: Session | ProcessingSource


@dataclass(frozen=True, slots=True)
class LoadCandidate:
    loaded: LoadedSession
    workspace: Workspace
    scope: Scope
    cancelled: Event = field(default_factory=Event, compare=False, repr=False)


@dataclass(frozen=True, slots=True)
class PreparedLoad:
    candidate: LoadCandidate
    payload: PreparedSessionUi
