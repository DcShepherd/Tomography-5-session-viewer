"""Qt-free viewer rows, statuses and preview sources for one explicit scope."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from tomography_session_browser.diagnostics.profiling import LoadingProfiler, NULL_PROFILE
from tomography_session_browser.domain.models import SearchTile
from tomography_session_browser.parsers.path_utils import natural_key
from tomography_session_browser.presentation.workspace import EntityKind, Scope, Workspace
from tomography_session_browser.services.item_status import ItemListStatus, build_item_status_context, item_list_status
from tomography_session_browser.services.preview_sources import (
    PreviewSources, atlas_preview_path, batch_position_preview_path,
    overview_preview_path, search_map_preview_path, search_tile_preview_path,
    tilt_series_preview_path,
)
from tomography_session_browser.services.tilt_series_validation import TiltSeriesValidation
from tomography_session_browser.presentation.tab_keys import TabKey


VIEWER_TAB_SOURCES = {
    TabKey.ATLAS: atlas_preview_path,
    TabKey.OVERVIEW: overview_preview_path,
    TabKey.SEARCH_MAP: search_map_preview_path,
    TabKey.SEARCH: search_tile_preview_path,
    TabKey.BATCH_POSITION: batch_position_preview_path,
    TabKey.TILT_SERIES: tilt_series_preview_path,
}
_STATUS_TABS = {TabKey.SEARCH_MAP, TabKey.SEARCH, TabKey.BATCH_POSITION, TabKey.TILT_SERIES}


@dataclass(slots=True)
class PreparedViewerTab:
    label: str
    items: list[Any]
    statuses: dict[str, ItemListStatus]
    sources: dict[str, PreviewSources]


@dataclass(slots=True)
class PreparedViewerTabs:
    tabs: dict[str, PreparedViewerTab]
    tilt_validations: dict[str, TiltSeriesValidation]
    availability: dict[Path, bool] = field(default_factory=dict)

    def files_unchanged(self) -> bool:
        return all(path.exists() == existed for path, existed in self.availability.items())


def search_tile_sort_key(value: SearchTile) -> tuple[Any, ...]:
    fallback_path = value.image_path or value.xml_path
    return (
        natural_key(value.name or ""),
        value.tile_index if value.tile_index is not None else 10**9,
        natural_key(fallback_path.name if fallback_path is not None else ""),
        value.id,
    )


def prepare_viewer_tabs(
    workspace: Workspace,
    scope: Scope,
    *,
    profile: LoadingProfiler = NULL_PROFILE,
) -> PreparedViewerTabs:
    """Prepare all tabs once, retaining row order and per-grid validation.

    Preview sources are paths only. Pixel loading and marker construction stay
    with the viewer, which can keep hidden tabs lazy and restore view state.
    """
    availability: dict[Path, bool] = {}
    def exists(path: Path) -> bool:
        if path not in availability:
            availability[path] = path.exists()
        return availability[path]

    with profile.phase("prepare_viewer_tab_items"):
        atlases = workspace.entities(EntityKind.ATLAS, scope)
        overviews = workspace.entities(EntityKind.OVERVIEW, scope)
        search_maps = workspace.entities(EntityKind.SEARCH_MAP, scope)
        search_tiles = sorted(workspace.entities(EntityKind.SEARCH_TILE, scope), key=search_tile_sort_key)
        batch_positions = workspace.entities(EntityKind.BATCH_POSITION, scope)
        tilt_series = workspace.entities(EntityKind.TILT_SERIES, scope)
        tab_items: dict[TabKey, list[Any]] = {
            TabKey.ATLAS: atlases,
            TabKey.OVERVIEW: overviews,
            TabKey.SEARCH_MAP: search_maps,
            TabKey.SEARCH: search_tiles,
            TabKey.BATCH_POSITION: batch_positions,
            TabKey.TILT_SERIES: tilt_series,
        }
    with profile.phase("prepare_item_status_context"):
        status_context = build_item_status_context(
            search_maps=search_maps,
            search_tiles=search_tiles,
            batch_positions=batch_positions,
            tilt_series=tilt_series,
            samples=workspace.samples,
            exists=exists,
        )
    tabs: dict[str, PreparedViewerTab] = {}
    for label, items in tab_items.items():
        phase_label = label.lower().replace(" ", "_")
        statuses: dict[str, ItemListStatus] = {}
        if label in _STATUS_TABS:
            with profile.phase(f"prepare_{phase_label}_statuses", count=len(items)):
                statuses = {
                    getattr(value, "id", str(index)): item_list_status(value, status_context)
                    for index, value in enumerate(items)
                }
        with profile.phase(f"prepare_{phase_label}_preview_sources", count=len(items)):
            source_for = VIEWER_TAB_SOURCES[label]
            sources = {str(getattr(value, "id", str(index))): source_for(value, exists=exists) for index, value in enumerate(items)}
        tabs[label] = PreparedViewerTab(label, items, statuses, sources)
    return PreparedViewerTabs(tabs, status_context.validations, availability)
