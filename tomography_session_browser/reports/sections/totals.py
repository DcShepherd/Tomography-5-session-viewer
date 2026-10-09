"""Sections totals report components."""
from __future__ import annotations
from dataclasses import dataclass
from typing import Sequence
from tomography_session_browser.domain.traversal import has_collection_data
from tomography_session_browser.domain.enums import SessionKind
from tomography_session_browser.presentation.workspace import EntityKind
from tomography_session_browser.services.warning_classification import WarningSummary, severity_counts, summarise_warnings
from tomography_session_browser.presentation.dashboard.atlas_counts import compute_atlas_counts
from tomography_session_browser.presentation.dashboard.models import AtlasCountModel, DashboardModel
from tomography_session_browser.presentation.dashboard.sections.status import atlas_tile_status
from tomography_session_browser.reports.context import SessionContext, atlas_count_scope


@dataclass(slots=True)
class Totals:
    n_sessions: int
    n_atlas_sessions: int
    n_collection_sessions: int
    n_linked_groups: int
    overviews: int
    search_maps: int
    batch_positions: int
    tilt_series: int
    complete: int
    incomplete: int
    failed: int
    unknown: int
    severity_counts: dict[str, int]
    total_warnings: int
    warning_rows: list[WarningSummary]
    has_atlas_data: bool
    has_collection_data: bool
    # Distinct, deduplicated atlas counts. Populated via
    # ``compute_atlas_counts`` over the full session set so the cover
    # uses the same numbers the dashboard does.
    atlas_counts: AtlasCountModel


def aggregate_totals(contexts: Sequence[SessionContext]) -> Totals:
    overviews = search_maps = batch_positions = tilt_series = 0
    complete = incomplete = failed = unknown = 0
    has_atlas = has_collection = False
    n_atlas = n_collection = 0

    for ctx in contexts:
        session = ctx.session
        # Counts come from the same presenter model that drives the Session
        # dashboard, so direct session lists and sample lists cannot inflate
        # the PDF cover independently.
        overviews += dashboard_count(ctx.dashboard, EntityKind.OVERVIEW)
        search_maps += dashboard_count(ctx.dashboard, EntityKind.SEARCH_MAP)
        batch_positions += dashboard_count(ctx.dashboard, EntityKind.BATCH_POSITION)
        outcomes = ctx.dashboard.outcomes
        tilt_series += outcomes.total
        complete += outcomes.complete
        incomplete += outcomes.incomplete
        failed += outcomes.failed
        unknown += outcomes.unknown

        atlas_present = bool(ctx.sample_atlases) or bool(session.atlas) or any(s.atlas for s in session.samples)
        if atlas_present:
            has_atlas = True
        if session.kind == SessionKind.ATLAS_SCREENING:
            n_atlas += 1
        else:
            n_collection += 1
        if any(has_collection_data(holder) for holder in (session, *session.samples)):
            has_collection = True

    # Aggregate warning summary across every session.
    aggregate_warnings: list[str] = []
    for ctx in contexts:
        aggregate_warnings.extend(ctx.warnings)
    warning_rows = summarise_warnings(aggregate_warnings)
    severities = severity_counts(warning_rows)

    n_linked_groups = sum(1 for ctx in contexts for _ in ctx.collection_samples)
    # Count only exported context. Do not attach a second display copy when
    # an explicitly exported screening sample already represents that atlas.
    represented = frozenset(
        id(sample.atlas) for ctx in contexts for sample in ctx.session.samples
        if sample.atlas is not None
    )
    atlas_counts = compute_atlas_counts([atlas_count_scope(ctx, represented) for ctx in contexts])
    return Totals(
        n_sessions=len(contexts),
        n_atlas_sessions=n_atlas,
        n_collection_sessions=n_collection,
        n_linked_groups=n_linked_groups,
        overviews=overviews,
        search_maps=search_maps,
        batch_positions=batch_positions,
        tilt_series=tilt_series,
        complete=complete,
        incomplete=incomplete,
        failed=failed,
        unknown=unknown,
        severity_counts=severities,
        total_warnings=sum(severities.values()),
        warning_rows=warning_rows,
        has_atlas_data=has_atlas,
        has_collection_data=has_collection,
        atlas_counts=atlas_counts,
    )


def dashboard_count(dashboard: DashboardModel, kind: EntityKind) -> int:
    row = next((card for card in dashboard.counts if card.kind is kind), None)
    return int(row.value) if row is not None else 0


def dashboard_tile_statuses(contexts: Sequence[SessionContext], kind: EntityKind) -> list[str]:
    return [
        tile.status
        for ctx in contexts
        for card in ctx.dashboard.counts
        if card.kind is kind
        for tile in card.items
    ]


def report_atlas_statuses(contexts: Sequence[SessionContext]) -> list[str]:
    """One tile per distinct atlas image in the report, by the dashboard's rule.

    Screening sessions contribute their dashboard Atlases card; a dataset
    contributes the atlas resolved for it, unless that atlas is already one
    of the screening session's.
    """

    seen: set[str] = set()
    statuses: list[str] = []
    for ctx in contexts:
        for card in ctx.dashboard.counts:
            if card.kind is not EntityKind.ATLAS:
                continue
            for tile in card.items:
                if tile.id not in seen:
                    seen.add(tile.id)
                    statuses.append(tile.status)
    for ctx in contexts:
        for sample in ctx.collection_samples:
            atlas = ctx.sample_atlases.get(id(sample))
            if atlas is None:
                continue
            key = atlas.id or f"object-{id(atlas)}"
            if key not in seen:
                seen.add(key)
                statuses.append(atlas_tile_status(atlas))
    return statuses

