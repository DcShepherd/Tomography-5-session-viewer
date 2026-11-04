"""What the loader prepares off the UI thread before it builds any widget."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from tomography_session_browser.presentation.dashboard.models import DashboardModel
from tomography_session_browser.presentation.tomogram_tabs import PreparedTomogramTab, prepare_tomogram_tab

from tomography_session_browser.diagnostics.profiling import NULL_PROFILE, LoadingProfiler
from tomography_session_browser.presentation.dashboard.dashboard import session_dashboard_model
from tomography_session_browser.presentation.dashboard.scope_value import dashboard_scope_value, timeline_for_scope_value
from tomography_session_browser.presentation.viewer_tabs import PreparedViewerTab, PreparedViewerTabs, prepare_viewer_tabs
from tomography_session_browser.presentation.workspace import Scope, Workspace
from tomography_session_browser.presentation.tomogram_selection import acquisition_scope
from tomography_session_browser.services.timeline_service import SessionTimeline


@dataclass(slots=True)
class PreparedSessionUi:
    dashboard_model: DashboardModel | None
    timeline: SessionTimeline | None
    viewer_tabs: dict[str, PreparedViewerTab]
    availability: dict[Path, bool] = field(default_factory=dict)
    tomograms: PreparedTomogramTab = field(default_factory=PreparedTomogramTab)


def prepare_session_ui_payload(
    workspace: Workspace,
    scope: Scope,
    *,
    profile: LoadingProfiler = NULL_PROFILE,
    viewer_scope: Scope = None,
    cached_dashboard: tuple[DashboardModel, SessionTimeline | None] | None = None,
    cached_viewer_tabs: PreparedViewerTabs | None = None,
) -> PreparedSessionUi:
    """Build expensive non-widget models for the initial UI in a worker."""

    review_scope = viewer_scope if viewer_scope is not None else scope
    scope = acquisition_scope(review_scope)
    with profile.phase("prepare_dashboard_model"):
        if cached_dashboard is None:
            scope_value = dashboard_scope_value(workspace, review_scope, workspace.atlas_label)
            dashboard_model = session_dashboard_model(scope_value, workspace=workspace) if workspace.has_sources else None
            timeline = timeline_for_scope_value(scope_value)
        else:
            dashboard_model, timeline = cached_dashboard
    if cached_viewer_tabs is not None and cached_viewer_tabs.files_unchanged():
        prepared_tabs = cached_viewer_tabs
    else:
        prepared_tabs = prepare_viewer_tabs(workspace, scope, profile=profile)
    return PreparedSessionUi(
        dashboard_model=dashboard_model,
        timeline=timeline,
        viewer_tabs=prepared_tabs.tabs,
        availability=prepared_tabs.availability,
        tomograms=prepare_tomogram_tab(workspace, review_scope),
    )
