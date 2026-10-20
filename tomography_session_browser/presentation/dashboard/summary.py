"""Which summary page a scope gets, and what only that page needs (design review B3.1).

The presentation layer decides, once, so the widget dispatches on the type
instead of re-deriving the choice from flags:

* ``CollectionSummary``: collection data across several samples (a project,
  a group, a session, linked sessions);
* ``SampleSummary``: collection data of one sample, one linked sample, or one
  entity or tilt series in focus;
* ``AtlasSessionSummary``: atlases and no collection data;
* ``EmptySummary``: nothing detected.

The chart fields of ``DashboardModel`` stay where they are; a summary carries
only what its page adds.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from tomography_session_browser.presentation.dashboard.models import DashboardFilterModel

from tomography_session_browser.presentation.dashboard.sections.atlas import AtlasSheetModel, SessionRef
from tomography_session_browser.presentation.dashboard.sections.attention import NeedsAttentionModel
from tomography_session_browser.presentation.dashboard.sections.samples import BySampleRowModel


@dataclass(frozen=True, slots=True)
class CollectionSummary:
    by_sample: tuple[BySampleRowModel, ...]
    #: Session folders the scope draws on: what the header's link counts and
    #: Context's Source folders lists.
    source_folders: int = 0
    needs_attention: NeedsAttentionModel = field(default_factory=NeedsAttentionModel)


@dataclass(frozen=True, slots=True)
class SampleSummary:
    needs_attention: NeedsAttentionModel = field(default_factory=NeedsAttentionModel)
    card_routes: tuple[DashboardFilterModel, ...] = ()
    inferred_batch_route: DashboardFilterModel | None = None


@dataclass(frozen=True, slots=True)
class AtlasSessionSummary:
    needs_attention: NeedsAttentionModel = field(default_factory=NeedsAttentionModel)
    sheet: AtlasSheetModel = field(default_factory=AtlasSheetModel)
    #: The loaded sessions whose collection data is linked to these atlases.
    collection_sessions: tuple[SessionRef, ...] = ()


@dataclass(frozen=True, slots=True)
class EmptySummary:
    needs_attention: NeedsAttentionModel = field(default_factory=NeedsAttentionModel)


DashboardSummary = CollectionSummary | SampleSummary | AtlasSessionSummary | EmptySummary


def build_summary(
    *,
    has_atlases: bool,
    has_collection_data: bool,
    is_sample_scope: bool,
    by_sample: tuple[BySampleRowModel, ...],
    source_folders: int = 0,
    needs_attention: NeedsAttentionModel,
    atlas_sheet: AtlasSheetModel = AtlasSheetModel(),
    collection_sessions: tuple[SessionRef, ...] = (),
    card_routes: tuple[DashboardFilterModel, ...] = (),
    inferred_batch_route: DashboardFilterModel | None = None,
) -> DashboardSummary:
    """The page for a scope, from what it holds."""

    if not has_collection_data:
        return AtlasSessionSummary(needs_attention, atlas_sheet, collection_sessions) if has_atlases else EmptySummary(needs_attention)
    if is_sample_scope:
        return SampleSummary(needs_attention, card_routes, inferred_batch_route)
    return CollectionSummary(by_sample, source_folders, needs_attention)
