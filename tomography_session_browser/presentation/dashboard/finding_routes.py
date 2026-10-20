"""A finding may open one tab only when its counted objects are exactly its rows."""
from __future__ import annotations

from collections.abc import Mapping, Sequence

from tomography_session_browser.domain.identity import canonical_entity_key, entity_key_text
from tomography_session_browser.domain.models import Session, TiltSeries
from tomography_session_browser.presentation.dashboard.models import (
    DashboardEntityScope, DashboardFilterModel, DashboardScope, LinkedAtlasDashboardScope, WarningGroupModel,
)
from tomography_session_browser.presentation.dashboard.scope_value import DashboardScopeValue
from tomography_session_browser.presentation.tab_keys import TabKey
from tomography_session_browser.presentation.workspace import Entity, EntityKind, Workspace


_TABS = {
    EntityKind.ATLAS: TabKey.ATLAS, EntityKind.OVERVIEW: TabKey.OVERVIEW,
    EntityKind.SEARCH_MAP: TabKey.SEARCH_MAP, EntityKind.SEARCH_TILE: TabKey.SEARCH,
    EntityKind.BATCH_POSITION: TabKey.BATCH_POSITION, EntityKind.TILT_SERIES: TabKey.TILT_SERIES,
}
_TYPE_TABS = {
    "Atlas": TabKey.ATLAS, "Overview": TabKey.OVERVIEW, "SearchMap": TabKey.SEARCH_MAP,
    "SearchTile": TabKey.SEARCH, "BatchPosition": TabKey.BATCH_POSITION, "TiltSeries": TabKey.TILT_SERIES,
}


def session_atlas_record_keys(value: DashboardScopeValue | TiltSeries) -> frozenset[tuple[str, str]]:
    """Identify session-owned Atlas records for display without changing warning prefixes."""
    if isinstance(value, LinkedAtlasDashboardScope):
        value = value.source
    sessions = value if isinstance(value, list) else [value] if isinstance(value, Session) else []
    return frozenset(canonical_entity_key(session.atlas) for session in sessions if session.atlas is not None)


def finding_viewer_rows(
    value: DashboardScopeValue | TiltSeries, workspace: Workspace | None,
) -> dict[TabKey, Sequence[Entity]]:
    """Use the viewer's scope rules, including its omission of placeholder atlases.

    A focused entity summary borrows its parent's lists in the UI; its own
    explicit acquisitions are the only rows its findings are allowed to open.
    """
    if isinstance(value, LinkedAtlasDashboardScope):
        value = value.source
    if isinstance(value, DashboardEntityScope):
        return {TabKey.SEARCH_MAP: value.search_maps, TabKey.BATCH_POSITION: value.batch_positions,
                TabKey.TILT_SERIES: value.tilt_series}
    if isinstance(value, TiltSeries):
        return {TabKey.TILT_SERIES: [value]}
    sessions = value if isinstance(value, list) else [value] if isinstance(value, Session) else []
    workspace = workspace if workspace is not None else Workspace.build(sessions)
    return {tab: workspace.entities(kind, value) for kind, tab in _TABS.items()}


def finding_route(
    group: WarningGroupModel, scope: DashboardScope, rows: Mapping[TabKey, Sequence[Entity]],
) -> tuple[DashboardFilterModel | None, str]:
    """Translate recorded identity into list identity; never match warning text."""
    keys = group.entity_keys
    if not keys:
        return None, "Affected objects were not recorded for every message."
    if any(kind not in _TYPE_TABS for kind, _ in keys):
        return None, "This finding includes session or sample records, which have no viewer row."
    tabs = {_TYPE_TABS[kind] for kind, _ in keys}
    if len(tabs) != 1:
        return None, "Affected objects belong to several tabs."
    tab = next(iter(tabs))
    matched = [value for value in rows.get(tab, ()) if canonical_entity_key(value) in keys]
    if {canonical_entity_key(value) for value in matched} != keys:
        return None, "Some affected objects are not listed in this scope's viewer tabs."
    row_keys = frozenset(entity_key_text(value) for value in matched)
    if len(keys) != group.count or len(row_keys) != group.count:
        return None, "The affected-object count differs from the available rows."
    return DashboardFilterModel(group.label, group.count, tab, "", f"Show objects with {group.label.lower()}",
                                row_keys=row_keys, scope_label=scope.title), ""
