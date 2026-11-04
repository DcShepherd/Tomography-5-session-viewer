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

from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING
from tomography_session_browser.domain.acquisition import LinkStep
from tomography_session_browser.presentation.recorded_metrics import MetricRow, metric_row
from tomography_session_browser.domain.tomograms import ProcessingSource
from tomography_session_browser.presentation.dashboard.index_chart import IndexChartModel
from tomography_session_browser.presentation.dashboard.processing_charts import processing_charts
from tomography_session_browser.presentation.dashboard.processing_volumes import VolumeRow, volume_inventory
from tomography_session_browser.services.tomogram_links import TomogramLink
from tomography_session_browser.presentation.tomogram_review_state import ProcessingReviewCounts, review_counts

if TYPE_CHECKING:
    from tomography_session_browser.presentation.dashboard.models import DashboardFilterModel
    from tomography_session_browser.presentation.dashboard.tomogram_summary_input import TomogramSummaryInput
    from tomography_session_browser.presentation.workspace import Workspace, Scope

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


@dataclass(frozen=True, slots=True)
class ProcessingChartKey:
    scope: str
    order: tuple[str, ...]
    sort: str
    sources: tuple[ProcessingSource, ...]
    version: str | None
    links: tuple[TomogramLink, ...]


@dataclass(frozen=True, slots=True)
class ProcessingSummary:
    input: TomogramSummaryInput
    tomogram_count: int
    row_keys: frozenset[str] = frozenset()
    attention_keys: frozenset[str] = frozenset()
    usable_count: int = 0
    version_count: int = 0
    warnings: tuple[str, ...] = ()
    metric_rows: tuple[MetricRow, ...] = ()
    pipeline: str = "Warp"
    link_counts: tuple[tuple[LinkStep, int], ...] = ()
    charts: tuple[IndexChartModel, ...] = ()
    volume_rows: tuple[VolumeRow, ...] = ()
    chart_key: ProcessingChartKey | None = None
    version_counts: tuple[tuple[str, int], ...] = ()
    link_status_counts: tuple[tuple[str, int], ...] = ()
    alignment_count: int = 0
    has_recorded_values: bool = False
    pixel_residual_count: int = 0
    review: ProcessingReviewCounts = field(default_factory=ProcessingReviewCounts)
    geometry_count: int = 0
    name_proposal_count: int = 0
    notice: str = ""
    missing_version_count: int = 0
    source_count: int = 1
    version: str | None = None


def processing_summary(source: ProcessingSource, workspace: Workspace, sort: str = "Name", *, scope: Scope = None) -> ProcessingSummary:
    from tomography_session_browser.presentation.dashboard.tomogram_summary_input import summary_input
    payload = summary_input(workspace, scope, sort)
    return summary_from_input(replace(payload, rows=tuple(row for row in payload.rows if row.tomogram.source_id == source.id),
                                    sources=(source,)))


def summary_from_input(payload: TomogramSummaryInput) -> ProcessingSummary:
    from collections import Counter
    from tomography_session_browser.presentation.tomogram_tabs import TomogramRow, needs_attention
    from tomography_session_browser.services.tomogram_links import link_step_counts
    rows = tuple(TomogramRow(row.tomogram, row.group, row.link) for row in payload.rows)
    items = tuple(row.tomogram for row in rows)
    volume_items = tuple(replace(row.tomogram, volumes=(row.volume,) if row.volume else ()) for row in payload.rows) if payload.version else items
    chart_rows = tuple(TomogramRow(item, row.group, row.link) for item, row in zip(volume_items, rows, strict=True))
    sources = payload.sources
    versions = Counter(volume.version for item in volume_items for volume in item.volumes)
    statuses = Counter(row.link.status for row in rows)
    key = ProcessingChartKey(payload.key, tuple(item.id for item in items), payload.sort, sources, payload.version, tuple(row.link for row in rows))
    return ProcessingSummary(
        input=payload, tomogram_count=len(rows), row_keys=frozenset(item.id for item in items),
        attention_keys=frozenset(row.tomogram.id for row in rows if needs_attention(row)),
        usable_count=sum(any(volume.grid is not None for volume in item.volumes) for item in volume_items),
        version_count=sum(versions.values()), warnings=tuple(warning for source in sources for warning in source.warnings),
        metric_rows=tuple(metric_row(item) for item in items), pipeline=', '.join(dict.fromkeys(source.pipeline for source in sources)),
        link_counts=link_step_counts(row.link for row in rows), charts=processing_charts(chart_rows),
        volume_rows=volume_inventory(volume_items), chart_key=key,
        version_counts=tuple(sorted(versions.items())), link_status_counts=tuple(sorted(statuses.items())),
        alignment_count=sum(item.processing.alignment != "Not recorded" for item in items),
        has_recorded_values=any(item.processing.metrics or item.processing.warnings or item.processing.alignment != "Not recorded" for item in items),
        pixel_residual_count=sum(any(metric.definition.startswith("imod.tiltalign.") and metric.unit == "pixels"
                                    for metric in item.processing.metrics) for item in items),
        geometry_count=len({(volume.grid, volume.dtype) for item in volume_items for volume in item.volumes}),
        name_proposal_count=sum(row.link.step == 'E' and row.link.status == 'unresolved' and
                                bool(row.link.proposal_signature) for row in rows),
        review=review_counts(row.review for row in payload.rows),
        missing_version_count=sum(row.volume is None for row in payload.rows) if payload.version else 0,
        source_count=len(sources), version=payload.version, notice=payload.notice)


DashboardSummary = CollectionSummary | SampleSummary | AtlasSessionSummary | EmptySummary | ProcessingSummary


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
