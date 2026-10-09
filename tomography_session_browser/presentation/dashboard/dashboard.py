"""Compose the Session dashboard from the scope, its analysis and section builders."""
from __future__ import annotations

from collections.abc import Callable, Sequence
import logging
from time import perf_counter
from typing import Any

from tomography_session_browser.presentation.dashboard.analysis import ScopeAnalysis
from tomography_session_browser.presentation.dashboard.atlas_counts import compute_atlas_counts
from tomography_session_browser.presentation.dashboard.models import (
    BatchPositionProgressModel,
    DashboardFilterModel,
    DashboardModel,
    DashboardScope,
    StatCardModel,
    StatusTileModel,
    TILE_STATUS_NEUTRAL,
    TiltSeriesOutcomeModel,
    WarningGroupModel,
    WarningRowModel,
)
from tomography_session_browser.presentation.dashboard.scopes import scope_from, scope_per_sample_counts
from tomography_session_browser.presentation.dashboard.sections.atlas import atlas_summary_from_scope
from tomography_session_browser.presentation.dashboard.sections.batches import batch_position_progress_rows
from tomography_session_browser.presentation.dashboard.sections.metrics import defocus_plot_model, dose_information_plot_model
from tomography_session_browser.presentation.dashboard.sections.search_maps import search_map_overview_rows, unresolved_inferred_failed_batch_groups
from tomography_session_browser.presentation.dashboard.sections.status import (
    atlas_tile_status,
    outcomes_for,
    overview_status,
    search_map_status,
    summarise_statuses,
    tile_status_from_validation_status,
    tile_tooltip,
    validation_tooltip,
)
from tomography_session_browser.presentation.dashboard.sections.timeline import (
    scope_acquisition_window,
    scope_microscope_name,
    timeline_items_by_sample,
    timeline_items_for_scope,
)
from tomography_session_browser.presentation.warnings import dashboard_warning_groups, warning_severity
from tomography_session_browser.presentation.workspace import EntityKind, Workspace
from tomography_session_browser.services.item_status import (
    TAG_MISSING_MDOC,
    TAG_ORPHANED_FAILED,
    filter_tag_query,
    tilt_series_lacks_mdoc,
    validation_status_tag,
)
from tomography_session_browser.services.tilt_series_validation import STATUS_FAILED, STATUS_INCOMPLETE, STATUS_UNKNOWN
from tomography_session_browser.services.warning_classification import split_warning_scope
from tomography_session_browser.presentation.tab_keys import TabKey

LOGGER = logging.getLogger("tomography_session_browser.ui.session_presenter")


def session_dashboard_model(value: Any, *, workspace: Workspace | None = None) -> DashboardModel:
    """Build a typed model for the Session-tab dashboard.

    Accepts ``Session``, ``Sample``, ``LinkedSampleGroup``, or a list of
    ``Session`` instances and dispatches to the appropriate scope builder.
    Aggregates ``session_counts`` / ``session_warnings`` semantics so the
    dashboard, the text summary, and the warnings panel stay consistent.
    """

    return analysed_dashboard_model(value, workspace=workspace)[0]


def analysed_dashboard_model(value: Any, *, workspace: Workspace | None = None) -> tuple[DashboardModel, ScopeAnalysis]:
    """The dashboard model, and the scope analysis it was drawn from, for a
    caller that reads the same verdicts (the PDF report)."""

    profile_start = perf_counter()
    scope = scope_from(value, workspace)

    # Validate every tilt series once, each against its own grid, and share
    # the verdicts across the dashboard (cards, donut, batch rows, search-map
    # rollups, timeline, warnings) so the inference stays consistent.
    validations_start = perf_counter()
    analysis = ScopeAnalysis.of(scope)
    validations_end = perf_counter()
    batch_position_rows = batch_position_progress_rows(
        scope.batch_positions,
        scope.tilt_series,
        analysis.validation_by_id,
        inferred_batch_groups=analysis.inferred_batch_groups,
    )
    cards = [
        _entity_card(scope, EntityKind.ATLAS, "Atlases", TabKey.ATLAS, [atlas for _label, atlas in scope.atlases], atlas_tile_status),
        _entity_card(scope, EntityKind.OVERVIEW, "Overviews", TabKey.OVERVIEW, scope.overviews, overview_status),
        _entity_card(scope, EntityKind.SEARCH_MAP, "Search maps", TabKey.SEARCH_MAP, scope.search_maps, search_map_status),
        _batch_position_card(scope, batch_position_rows),
        _tilt_series_card(scope, analysis),
    ]

    outcomes = outcomes_for(scope.tilt_series, analysis.validations)
    defocus_start = perf_counter()
    defocus_readout = defocus_plot_model(scope)
    dose_start = perf_counter()
    dose_information = dose_information_plot_model(scope)
    dose_end = perf_counter()

    # Each row reports *exposure-area* progress (planned vs. acquired
    # exposures across the linked batch positions), a different quantity
    # from the search map's mosaic tile count: a 60-tile mosaic may hold
    # one batch with 6 planned exposures.
    sm_overview = search_map_overview_rows(
        scope.search_maps,
        scope.batch_positions,
        scope.tilt_series,
        analysis.validations,
        successful_tilt_ids=analysis.successful_tilt_ids,
        owners=analysis.owners,
        inferred_groups=analysis.inferred_batch_groups,
    )
    warning_rows, warning_groups = _warnings(scope, analysis)
    timeline_items = (
        timeline_items_for_scope(
            scope.batch_positions,
            scope.tilt_series,
            analysis.validation_by_id,
            inferred_batch_groups=analysis.inferred_batch_groups,
        )
        if scope.is_sample_scope
        else timeline_items_by_sample(scope.sample_groups, analysis.validation_by_id)
    )
    completeness, completeness_label = _completeness(warning_groups)
    start, end, duration = scope_acquisition_window(scope)
    has_atlases = bool(scope.atlases)
    # The original ``value`` carries information the flattened scope
    # has thrown away (which sessions are atlas-screening, what the
    # cross-session linker decided), so the atlas counter takes the
    # original input rather than the scope.
    atlas_counts = compute_atlas_counts(value)

    model = DashboardModel(
        title=scope.title,
        path=scope.path,
        kind=scope.kind,
        microscope=scope_microscope_name(scope),
        acquisition_start=start,
        acquisition_end=end,
        acquisition_duration=duration,
        completeness=completeness,
        completeness_label=completeness_label,
        counts=cards,
        outcomes=outcomes,
        defocus_readout=defocus_readout,
        dose_information=dose_information,
        search_map_overview=sm_overview,
        filters=_filters(scope, outcomes, analysis),
        warnings=warning_rows,
        warning_groups=warning_groups,
        batch_positions=batch_position_rows,
        timeline_items=timeline_items,
        sample_count=len(scope.sample_groups),
        is_sample_scope=scope.is_sample_scope,
        has_atlases=has_atlases,
        has_collection_data=any(card.value > 0 for card in cards if card.kind is not EntityKind.ATLAS),
        atlas_summary=atlas_summary_from_scope(scope, atlas_counts) if has_atlases else None,
    )
    if LOGGER.isEnabledFor(logging.DEBUG):
        LOGGER.debug(
            "dashboard model timings scope=%s kind=%s tilt_series=%d "
            "validation_ms=%.1f defocus_ms=%.1f dose_ms=%.1f total_ms=%.1f "
            "defocus_points=%d dose_points=%d",
            scope.title,
            scope.kind,
            len(scope.tilt_series),
            (validations_end - validations_start) * 1000.0,
            (dose_start - defocus_start) * 1000.0,
            (dose_end - dose_start) * 1000.0,
            (perf_counter() - profile_start) * 1000.0,
            len(defocus_readout.points),
            len(dose_information.points),
        )
    return model, analysis


# ---------------------------------------------------------------- count cards


def _card(
    scope: DashboardScope,
    kind: EntityKind,
    label: str,
    destination: str,
    value: int,
    tiles: list[StatusTileModel],
) -> StatCardModel:
    return StatCardModel(
        label=label,
        value=value,
        sparkline=scope_per_sample_counts(scope, label) if len(scope.sample_groups) > 1 else [],
        items=tiles,
        status_summary=summarise_statuses(tiles),
        destination=destination,
        kind=kind,
    )


def _entity_card(
    scope: DashboardScope,
    kind: EntityKind,
    label: str,
    destination: str,
    items: Sequence[Any],
    status_of: Callable[[Any], str],
) -> StatCardModel:
    """A count card whose tiles each take their own entity's status."""

    tiles: list[StatusTileModel] = []
    for index, item in enumerate(items):
        status = status_of(item)
        tiles.append(
            StatusTileModel(
                id=getattr(item, "id", str(index)),
                name=getattr(item, "name", None) or getattr(item, "id", f"item-{index}"),
                status=status,
                tooltip=tile_tooltip(item, status, label),
                destination=destination,
            )
        )
    return _card(scope, kind, label, destination, len(items), tiles)


def _batch_position_card(scope: DashboardScope, rows: list[BatchPositionProgressModel]) -> StatCardModel:
    """Batch tiles take the validated batch rows' colours, in every scope.

    Inferred orphan rows are shown only in a sample scope, as before.
    """

    recorded_ids = {batch.id for batch in scope.batch_positions}
    card_rows = [row for row in rows if scope.is_sample_scope or row.id in recorded_ids]
    tiles = [
        StatusTileModel(
            id=row.id,
            name=row.label,
            status=row.status,
            tooltip=row.tooltip,
            destination=TabKey.BATCH_POSITION,
        )
        for row in card_rows
    ]
    return _card(scope, EntityKind.BATCH_POSITION, "Batch positions", TabKey.BATCH_POSITION, len(card_rows), tiles)


def _tilt_series_card(scope: DashboardScope, analysis: ScopeAnalysis) -> StatCardModel:
    """Tilt-series tiles take the validator's verdict, and its tooltip."""

    label = "Tilt series"
    tiles: list[StatusTileModel] = []
    for index, item in enumerate(scope.tilt_series):
        item_id = getattr(item, "id", None)
        validation = analysis.validation_by_id.get(item_id) if item_id is not None else None
        if validation is None:
            status = TILE_STATUS_NEUTRAL
            tooltip = tile_tooltip(item, status, label)
        else:
            status = tile_status_from_validation_status(validation.status)
            tooltip = validation_tooltip(validation)
        tiles.append(
            StatusTileModel(
                id=getattr(item, "id", str(index)),
                name=getattr(item, "name", None) or getattr(item, "id", f"item-{index}"),
                status=status,
                tooltip=tooltip,
                destination=TabKey.TILT_SERIES,
            )
        )
    return _card(scope, EntityKind.TILT_SERIES, label, TabKey.TILT_SERIES, len(scope.tilt_series), tiles)


# ---------------------------------------------------------------- warnings and header


def _warnings(scope: DashboardScope, analysis: ScopeAnalysis) -> tuple[list[WarningRowModel], list[WarningGroupModel]]:
    """The warning rows, and their groups: the scope's own warnings plus one
    for each inferred failed batch no search map could take."""

    rows: list[WarningRowModel] = []
    for raw in scope.warnings:
        prefix, message = split_warning_scope(raw)
        sample_name = prefix.split("/")[0].strip() or None
        # The shared classifier decides severity here too. A local substring
        # rule used to, so one warning could be coloured "info" on this row and
        # ordered as a "warning" in the group above it.
        rows.append(WarningRowModel(severity=warning_severity(raw), sample=sample_name, message=message))

    derived: list[str] = []
    for group in unresolved_inferred_failed_batch_groups(
        scope.search_maps,
        scope.batch_positions,
        scope.tilt_series,
        analysis.validation_by_id,
        analysis.owners,
        inferred_groups=analysis.inferred_batch_groups,
    ):
        names = ", ".join(tilt.name or tilt.id for tilt in group.tilt_series)
        message = (
            f"Failed batch {group.label} could not be associated with a search map from "
            f"explicit links or image-stage metadata ({names})."
        )
        rows.append(WarningRowModel(severity="warning", sample=None, message=message))
        derived.append(message)
    return rows, dashboard_warning_groups([*scope.warnings, *derived])


def _completeness(warning_groups: list[WarningGroupModel]) -> tuple[str, str]:
    """The header's state and label, from affected objects as the warning
    groups and the PDF cover count them (raw strings said "2,805 warnings"
    for 85 tilt series)."""

    error_count = sum(group.count for group in warning_groups if group.severity == "error")
    warn_count = sum(group.count for group in warning_groups if group.severity == "warning")
    if error_count:
        return "error", f"{error_count} error{'s' if error_count != 1 else ''}"
    if warn_count:
        return "warning", f"{warn_count} warning{'s' if warn_count != 1 else ''}"
    return "complete", "Complete"


def _filters(
    scope: DashboardScope,
    outcomes: TiltSeriesOutcomeModel,
    analysis: ScopeAnalysis,
) -> list[DashboardFilterModel]:
    """The tilt-series filter chips. Each query is an exact row tag
    (``is:<tag>``) set by ``item_status`` from the same predicate that
    produced the count beside it."""

    missing_mdoc_count = sum(1 for tilt in scope.tilt_series if tilt_series_lacks_mdoc(tilt))
    return [
        DashboardFilterModel("All", outcomes.total, TabKey.TILT_SERIES, "", "Show all tilt series"),
        DashboardFilterModel("Failed", outcomes.failed, TabKey.TILT_SERIES, filter_tag_query(validation_status_tag(STATUS_FAILED)), "Show failed tilt series"),
        DashboardFilterModel("Incomplete", outcomes.incomplete, TabKey.TILT_SERIES, filter_tag_query(validation_status_tag(STATUS_INCOMPLETE)), "Show incomplete tilt series"),
        DashboardFilterModel("Unknown", outcomes.unknown, TabKey.TILT_SERIES, filter_tag_query(validation_status_tag(STATUS_UNKNOWN)), "Show tilt series with unknown status"),
        DashboardFilterModel("Missing MDOC", missing_mdoc_count, TabKey.TILT_SERIES, filter_tag_query(TAG_MISSING_MDOC), "Show tilt series with missing or unusable MDOC metadata"),
        DashboardFilterModel(
            "Orphaned failed tilt series",
            analysis.orphaned_failed_tilts,
            TabKey.TILT_SERIES,
            filter_tag_query(TAG_ORPHANED_FAILED),
            "Show failed tilt series whose batch position was inferred from the name",
        ),
    ]
