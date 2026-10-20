"""Build the scoped Atlas summary rows."""
from __future__ import annotations

from dataclasses import dataclass, replace
from enum import StrEnum

from tomography_session_browser.domain.traversal import is_single_atlas_placeholder
from tomography_session_browser.presentation.atlas_rows import atlas_row, collection_members
from tomography_session_browser.presentation.dashboard.atlas_counts import canonical_atlas_key
from tomography_session_browser.presentation.dashboard.models import (
    AtlasCountModel,
    AtlasRowModel,
    AtlasSummaryModel,
    DashboardScope,
)
from tomography_session_browser.presentation.dashboard.scopes import scope_samples
from tomography_session_browser.presentation.workspace import Workspace, display_sample_name


def atlas_summary_from_scope(
    scope: DashboardScope, atlas_counts: AtlasCountModel, workspace: Workspace | None = None
) -> AtlasSummaryModel:
    """The scope's atlases as rows, from the already-resolved scope atlases.

    ``atlas_counts`` carries the deduplicated four-way counts derived from the
    original scope input (a Session, a list of Sessions, a Sample, or a
    LinkedSampleGroup); the rows stay the scope's own atlases, each once.
    """

    rows: list[AtlasRowModel] = []
    seen_atlas_keys: set[str] = set()
    for sample_label, atlas in scope.atlases:
        key = canonical_atlas_key(atlas)
        if key in seen_atlas_keys:
            continue
        seen_atlas_keys.add(key)
        rows.append(atlas_row(sample_label, atlas, workspace, samples=scope_samples(scope)))
    return AtlasSummaryModel(rows=rows, counts=atlas_counts)


class AtlasFilter(StrEnum):
    """The contact sheet's segments; the value is the segment's label."""

    ALL = "All"
    MISSING = "Missing"
    WITH_COLLECTION = "With collection"


@dataclass(frozen=True, slots=True)
class AtlasSheetModel:
    """An atlas session's contact sheet: a tile for every atlas the scope holds.

    That is every atlas the Atlas tab lists, and also the "Single Atlas"
    placeholder when the scope has one: it is not a grid, so it is not counted
    with the others, but its image (or the absence of it) is something to see.
    """

    tiles: tuple[AtlasRowModel, ...] = ()

    def tiles_for(self, name: AtlasFilter) -> tuple[AtlasRowModel, ...]:
        if name is AtlasFilter.MISSING:
            return tuple(tile for tile in self.tiles if tile.missing)
        if name is AtlasFilter.WITH_COLLECTION:
            return tuple(tile for tile in self.tiles if tile.collection_sample_ids)
        return self.tiles

    def count(self, name: AtlasFilter) -> int:
        return len(self.tiles_for(name))


def atlas_sheet(scope: DashboardScope, summary_rows: list[AtlasRowModel], workspace: Workspace | None = None) -> AtlasSheetModel:
    """The scope's atlas rows, with a tile first for each Single Atlas placeholder
    the rows do not already hold (a scope of that sample alone does)."""

    held = {row.atlas_id for row in summary_rows}
    placeholders: list[AtlasRowModel] = []
    for sample in scope_samples(scope):
        atlas = sample.atlas
        if atlas is None or not is_single_atlas_placeholder(sample) or atlas.id in held:
            continue
        held.add(atlas.id)
        placeholders.append(atlas_row(display_sample_name(sample.name), atlas, workspace, samples=scope_samples(scope)))
    # The tree numbers its samples ("1. Specimen_alpha"); a tile names the atlas.
    named = [replace(row, sample_label=display_sample_name(row.sample_label)) for row in summary_rows]
    return AtlasSheetModel((*placeholders, *named))


@dataclass(frozen=True, slots=True)
class SessionRef:
    """A loaded session, named to be opened (``id`` is what the window finds it by)."""

    id: str
    name: str


def collection_sessions(scope: DashboardScope, workspace: Workspace | None) -> tuple[SessionRef, ...]:
    """The loaded sessions whose collection data is linked to this scope's atlases,
    in the order the project holds them."""

    if workspace is None:
        return ()
    members = [member for _label, atlas in scope.atlases
               for member in collection_members(atlas, workspace, samples=scope_samples(scope))]
    return tuple(
        SessionRef(session.id, session.name)
        for session in workspace.sessions
        if any(member is sample for sample in session.samples for member in members)
    )
