"""Qt-free viewer rows, statuses and preview sources for one explicit scope."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from tomography_session_browser.diagnostics.profiling import LoadingProfiler, NULL_PROFILE
from tomography_session_browser.domain.identity import entity_key_text
from tomography_session_browser.domain.models import SearchTile
from tomography_session_browser.parsers.path_utils import natural_key
from tomography_session_browser.presentation.viewer_entries import Entry, entries_for_tab
from tomography_session_browser.presentation.workspace import EntityKind, Scope, Workspace
from tomography_session_browser.presentation.atlas_rows import atlas_list_status, atlas_row
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


@dataclass(slots=True)
class PreparedViewerTab:
    """One tab's rows, with each row's status and preview source by row key.

    ``statuses`` and ``sources`` are keyed by ``domain.identity.entity_key_text``
    of the row's object, never by its ``.id``: two objects can share an ``id``
    and differ in path, and each must keep its own status and picture.
    """

    label: str
    items: list[Any]
    statuses: dict[str, ItemListStatus]
    sources: dict[str, PreviewSources]
    #: The list as it is shown: each object, and the headers and dividers
    #: between them (``presentation.viewer_entries``). ``items`` is in the
    #: order these entries show them.
    entries: tuple[Entry, ...] = ()


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
        with profile.phase(f"prepare_{phase_label}_statuses", count=len(items)):
            if label == TabKey.ATLAS:
                statuses = {entity_key_text(value): atlas_list_status(
                    value, atlas_row(workspace.viewer_atlas_label(value), value, workspace),
                    available=value.image_path is not None and exists(value.image_path),
                ) for value in items}
            else:
                statuses = {entity_key_text(value): item_list_status(value, status_context) for value in items}
        with profile.phase(f"prepare_{phase_label}_preview_sources", count=len(items)):
            source_for = VIEWER_TAB_SOURCES[label]
            sources = {entity_key_text(value): source_for(value, exists=exists) for value in items}
        with profile.phase(f"prepare_{phase_label}_entries", count=len(items)):
            grouped = entries_for_tab(
                label,
                items,
                owner_of=workspace.row_owner,
                statuses=statuses,
                batches=batch_positions,
                inferred_labels=status_context.inferred_batch_labels,
            )
        tabs[label] = PreparedViewerTab(label, grouped.items, statuses, sources, grouped.entries)
    return PreparedViewerTabs(tabs, status_context.validations, availability)
