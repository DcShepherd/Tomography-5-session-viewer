"""Bind a dashboard filter to the rows its summary actually analysed."""
from __future__ import annotations

from dataclasses import replace
from collections.abc import Mapping, Sequence

from tomography_session_browser.domain.identity import entity_key_text
from tomography_session_browser.presentation.dashboard.models import DashboardFilterModel, DashboardScope
from tomography_session_browser.presentation.dashboard.analysis import ScopeAnalysis
from tomography_session_browser.presentation.dashboard.sections.batches import group_matches_any_batch
from tomography_session_browser.presentation.tab_keys import TabKey
from tomography_session_browser.presentation.workspace import Entity


def scoped_filter(route: DashboardFilterModel, scope: DashboardScope) -> DashboardFilterModel:
    entities = {
        TabKey.TILT_SERIES: scope.tilt_series,
        TabKey.SEARCH_MAP: scope.search_maps,
        TabKey.BATCH_POSITION: scope.batch_positions,
        TabKey.OVERVIEW: scope.overviews,
        TabKey.ATLAS: [atlas for _label, atlas in scope.atlases],
    }[TabKey(route.tab)]
    return replace(route, row_keys=frozenset(entity_key_text(value) for value in entities), scope_label=scope.title)


def sample_card_routes(
    scope: DashboardScope, analysis: ScopeAnalysis, rows: Mapping[TabKey, Sequence[Entity]],
) -> tuple[tuple[DashboardFilterModel, ...], DashboardFilterModel | None]:
    """Action populations, separate from scientific totals that include inferred groups.

    Intersect the analysed scope with the viewer's actual population: focused
    summaries cannot borrow sibling rows, and placeholder atlases cannot promise
    a row the viewer omits. Identity comes from the shared canonical row key.
    """
    routes = []
    for tab, label in ((TabKey.BATCH_POSITION, "Recorded batch positions"), (TabKey.SEARCH_MAP, "Search maps"),
                       (TabKey.OVERVIEW, "Overviews"), (TabKey.ATLAS, "Atlases")):
        route = scoped_filter(DashboardFilterModel(label, 0, tab, "", f"Show all {label.lower()} in this scope"), scope)
        available = frozenset(entity_key_text(value) for value in rows.get(tab, ()))
        keys = (route.row_keys or frozenset()) & available
        routes.append(replace(route, count=len(keys), row_keys=keys))
    groups = [group for group in analysis.inferred_batch_groups
              if not group_matches_any_batch(group, scope.batch_positions)]
    inferred_keys = frozenset(entity_key_text(tilt) for group in groups for tilt in group.tilt_series)
    inferred_keys &= frozenset(entity_key_text(value) for value in rows.get(TabKey.TILT_SERIES, ()))
    inferred = None
    if inferred_keys:
        label = f"{len(groups)} inferred failed {'group' if len(groups) == 1 else 'groups'} · {len(inferred_keys)} tilt series"
        inferred = DashboardFilterModel(label, len(inferred_keys), TabKey.TILT_SERIES, "",
                                       "Batch position metadata is absent. Groups are inferred from failed tilt-series names; no recorded Batch position link is created.",
                                       row_keys=inferred_keys, scope_label=scope.title)
    return tuple(routes), inferred
