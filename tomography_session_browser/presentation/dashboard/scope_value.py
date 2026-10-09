"""The value a dashboard is built from, and its acquisition timeline.

A tree selection (a project group, a session, a sample) becomes the value
``session_dashboard_model`` takes: a session borrows its linked atlases
through ``LinkedAtlasDashboardScope``, and a group of several sessions
becomes their list. ``timeline_for_scope_value`` builds the timeline the
dashboard shows for that value.
"""
from __future__ import annotations

import logging
from collections.abc import Callable

from tomography_session_browser.domain.identity import dedupe_entities
from tomography_session_browser.domain.models import Atlas, Sample, Session, TiltSeries
from tomography_session_browser.presentation.dashboard.models import DashboardEntityScope, LinkedAtlasDashboardScope
from tomography_session_browser.presentation.project_tree import ProjectTreeGroup
from tomography_session_browser.presentation.scopes import LinkedSampleGroup
from tomography_session_browser.presentation.workspace import Scope, Workspace
from tomography_session_browser.services.timeline_service import (
    SessionTimeline,
    build_acquisition_timeline,
    build_session_timeline,
    detect_inferred_pauses,
)

#: The window's logger, as before the move: the timeline warning keeps its name.
LOGGER = logging.getLogger("tomography_session_browser.ui.main_window")

#: What a dashboard is built from: a session, sample or linked sample on its
#: own; a session or sample borrowing its linked atlases; one search map or
#: batch position in focus; or several sessions together.
DashboardScopeValue = (
    Session | Sample | LinkedSampleGroup | LinkedAtlasDashboardScope | DashboardEntityScope | list[Session]
)


def dashboard_scope_value(
    workspace: Workspace, scope: Scope, atlas_label: Callable[[Atlas], str]
) -> DashboardScopeValue:
    if isinstance(scope, ProjectTreeGroup):
        if len(scope.sessions) == 1:
            return scope.sessions[0]
        return list(scope.sessions)
    if isinstance(scope, Session):
        linked_atlases = workspace.linked_atlases(scope)
        if linked_atlases:
            return LinkedAtlasDashboardScope(
                source=scope,
                atlases=[(atlas_label(atlas), atlas) for atlas in linked_atlases],
            )
    if isinstance(scope, Sample):
        linked_atlases = workspace.linked_atlases(scope)
        if linked_atlases:
            return LinkedAtlasDashboardScope(
                source=scope,
                atlases=[(atlas_label(atlas), atlas) for atlas in linked_atlases],
            )
    if isinstance(scope, Session | Sample | LinkedSampleGroup):
        return scope
    if len(workspace.sessions) == 1:
        return workspace.sessions[0]
    return list(workspace.sessions)


def timeline_for_scope_value(scope_value: DashboardScopeValue) -> SessionTimeline | None:
    try:
        if isinstance(scope_value, LinkedAtlasDashboardScope):
            return timeline_for_scope_value(scope_value.source)
        if isinstance(scope_value, Session):
            return build_session_timeline(scope_value)
        if isinstance(scope_value, list):
            segments: list = []
            pauses: list = []
            for session in scope_value:
                tl = build_session_timeline(session)
                segments.extend(tl.segments)
                pauses.extend(tl.pauses)
            return SessionTimeline(segments=tuple(segments), pauses=tuple(pauses))
        if isinstance(scope_value, Sample):
            return timeline_from_tilt_series(list(scope_value.tilt_series))
        if isinstance(scope_value, LinkedSampleGroup):
            return timeline_from_tilt_series(list(scope_value.tilt_series))
        if isinstance(scope_value, DashboardEntityScope):
            return timeline_from_tilt_series(list(scope_value.tilt_series))
    except Exception:  # pragma: no cover - defensive; UI falls back gracefully
        LOGGER.warning("Timeline model build failed", exc_info=True)
    return None


def timeline_from_tilt_series(tilt_series: list[TiltSeries]) -> SessionTimeline:
    """Build a SessionTimeline from a flat tilt-series list.

    ``build_session_timeline`` only accepts a Session, but Sample- and
    LinkedSampleGroup-scoped dashboards need the same timeline data without
    materialising a fake Session. We replicate the per-series collation here.
    """

    segments = []
    pauses = []
    for tilt in dedupe_entities(tilt_series):
        segment = build_acquisition_timeline(tilt)
        if segment is None:
            continue
        segments.append(segment)
        pauses.extend(detect_inferred_pauses(segment))
    segments.sort(key=lambda s: s.start)
    pauses.sort(key=lambda p: p.start)
    return SessionTimeline(segments=tuple(segments), pauses=tuple(pauses))
