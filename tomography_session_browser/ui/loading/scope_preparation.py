"""Latest-request scope preparation; widgets are published in one GUI turn."""
from __future__ import annotations
from dataclasses import dataclass, replace
import logging
from typing import Any, Callable
from PySide6.QtCore import QThreadPool
from tomography_session_browser.presentation.navigation_history import HistoryLocation
from tomography_session_browser.presentation.prepared_session import PreparedSessionUi, prepare_session_ui_payload
from tomography_session_browser.presentation.tab_keys import TabKey
from tomography_session_browser.presentation.workspace import Scope, Workspace
from tomography_session_browser.presentation.dashboard.models import DashboardModel
from tomography_session_browser.presentation.viewer_tabs import PreparedViewerTabs
from tomography_session_browser.services.timeline_service import SessionTimeline
from tomography_session_browser.ui.loading.tasks import BackgroundTask, TaskSignals


@dataclass(frozen=True, slots=True)
class ScopeRequest:
    generation: int
    workspace: Workspace
    scope: Scope
    viewer_scope: Scope
    value: Any
    label: str
    #: The tab to show once the scope is published: the active one for an
    #: ordinary tree selection, ``TabKey.SESSION`` for a summary's own link.
    #: Applied by the publication owner, never by the worker.
    destination_tab: TabKey
    #: Where the reviewer came from, for an explicit route that records
    #: history. Recorded after the scope publishes; dropped with a request
    #: that is superseded, cancelled or fails.
    departure: HistoryLocation | None = None
    cached_dashboard: tuple[DashboardModel, SessionTimeline | None] | None = None
    cached_viewer_tabs: PreparedViewerTabs | None = None


class ScopePreparation:
    """One running operation and one replaceable pending request per window."""
    def __init__(self, *, publish: Callable[[ScopeRequest, PreparedSessionUi], None],
                 report_error: Callable[[str], None]) -> None:
        self.signals = TaskSignals()
        self.publish, self.report_error = publish, report_error
        self.generation = 0
        self.running: ScopeRequest | None = None
        self.pending: ScopeRequest | None = None

    def invalidate(self) -> None:
        self.generation += 1
        self.pending = None

    def request(self, workspace: Workspace, scope: Scope, viewer_scope: Scope, value: Any, label: str,
                destination_tab: TabKey, *, departure: HistoryLocation | None = None,
                cached_dashboard: tuple[DashboardModel, SessionTimeline | None] | None = None,
                cached_viewer_tabs: PreparedViewerTabs | None = None) -> None:
        self.generation += 1
        self.pending = ScopeRequest(self.generation, workspace, scope, viewer_scope, value, label, destination_tab,
                                    departure, cached_dashboard, cached_viewer_tabs)
        self.start_pending()

    def start_pending(self) -> None:
        if self.running is not None or self.pending is None:
            return
        request, self.pending = self.pending, None
        self.running = request
        def work() -> PreparedSessionUi:
            # Share read-only indexes, but dataclass init=False memos start empty.
            # Rebuilding scientific links and ownership for every click is unnecessary.
            workspace = replace(request.workspace)
            return prepare_session_ui_payload(workspace, request.scope, viewer_scope=request.viewer_scope,
                                              cached_dashboard=request.cached_dashboard,
                                              cached_viewer_tabs=request.cached_viewer_tabs)
        QThreadPool.globalInstance().start(BackgroundTask(
            work, context=request, signals=self.signals, logger=logging.getLogger(__name__),
            failure_log="Could not prepare review scope"))

    def finished(self, request: ScopeRequest, payload: PreparedSessionUi) -> None:
        self.running = None
        if request.generation == self.generation:
            self.publish(request, payload)
        self.start_pending()

    def failed(self, request: ScopeRequest, error: str) -> None:
        self.running = None
        if request.generation == self.generation:
            self.report_error(f"Could not prepare {request.label}: {error}")
        self.start_pending()
