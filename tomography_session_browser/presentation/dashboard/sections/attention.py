"""What a summary page asks the reviewer to look at (design review B3.1).

Two kinds of row, kept apart. *Outcome* rows are acquisitions that went wrong
(failed tilt series, incomplete search maps): a count, the deciding fact, who
holds them, and a route to the rows in their own tab. *Finding* rows are
metadata observations, in the shared warning classification's words, counts
and severities. Counts are affected objects, and the two kinds overlap.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace

from tomography_session_browser.domain.identity import canonical_entity_key
from tomography_session_browser.presentation.dashboard.analysis import ScopeAnalysis
from tomography_session_browser.presentation.dashboard.filters import scoped_filter
from tomography_session_browser.presentation.dashboard.finding_routes import finding_route
from tomography_session_browser.presentation.dashboard.models import (
    DashboardFilterModel,
    DashboardScope,
    TILE_STATUS_FAILED,
    TILE_STATUS_MISSING,
    TILE_STATUS_WARNING,
    WarningGroupModel,
)
from tomography_session_browser.presentation.dashboard.sections.atlas import AtlasFilter, AtlasSheetModel
from tomography_session_browser.presentation.dashboard.sections.batches import group_matches_any_batch
from tomography_session_browser.presentation.dashboard.sections.samples import BySampleRowModel
from tomography_session_browser.presentation.dashboard.sections.status import search_map_is_incomplete
from tomography_session_browser.presentation.tab_keys import TabKey
from tomography_session_browser.presentation.workspace import Entity, EntityKind
from tomography_session_browser.services.item_status import (
    STATUS_INCOMPLETE_LABEL,
    ItemStatusContext,
    filter_tag_query,
    status_tag,
    search_map_tile_counts,
    validation_status_tag,
)
from tomography_session_browser.services.tilt_series_validation import STATUS_FAILED
from tomography_session_browser.services.warning_classification import split_warning_scope

#: A finding names its objects inline up to this many.
INLINE_NAMES_MAX = 3
#: A message longer than this is not repeated beside the names: it is the
#: finding's explanation (its View has it), and the names alone say which.
INLINE_MESSAGE_MAX = 48
#: An entity name that says nothing without its owner ("<sample> / Atlas").
_GENERIC_NAMES = frozenset({"atlas"})
EVERY_TILT_SERIES = "every tilt series"


@dataclass(frozen=True, slots=True)
class SampleCount:
    """How many of a row's objects one sample holds."""

    sample: str
    count: int


@dataclass(frozen=True, slots=True)
class OpenObjectRoute:
    """Opens one object in its own tab, re-scoping when that tab's list lacks it.

    For an outcome whose object is no row of the current list (the Single Atlas
    is not one of a session's atlases), where a filter would open nothing.
    """

    tab: str
    object_id: str
    tooltip: str


@dataclass(frozen=True, slots=True)
class SheetFilterRoute:
    """Narrows the page's own contact sheet to the objects the outcome counts."""

    filter: AtlasFilter
    tooltip: str


#: Where an outcome row's link goes.
OutcomeRoute = DashboardFilterModel | OpenObjectRoute | SheetFilterRoute


@dataclass(frozen=True, slots=True)
class OutcomeRowModel:
    kind: EntityKind
    #: ``TILE_STATUS_FAILED`` or ``TILE_STATUS_WARNING`` (``TILE_STATUS_MISSING``
    #: for an atlas image): the row's tone.
    status: str
    count: int
    #: "5 tilt series failed".
    label: str
    #: What decided it, when every object agrees: "1 of 35 frames each".
    fact: str
    by_sample: tuple[SampleCount, ...]
    #: Opens exactly these objects.
    route: OutcomeRoute


@dataclass(frozen=True, slots=True)
class FindingRowModel:
    label: str
    #: Affected objects, as the dashboard and the PDF cover count them.
    count: int
    severity: str  # "info" | "warning" | "error"
    #: "every tilt series", or up to three objects named; "" otherwise.
    qualifier: str
    #: The raw messages behind the row, for its expanded view.
    items: tuple[str, ...]
    route: DashboardFilterModel | None = None
    route_reason: str = "Affected objects were not recorded for every message."


@dataclass(frozen=True, slots=True)
class NeedsAttentionModel:
    outcomes: tuple[OutcomeRowModel, ...] = ()
    #: Inferred failed batch groups hold the failed series they were named
    #: for: they are not further failures, and the page says so.
    inferred_group_labels: tuple[str, ...] = ()
    inferred_tilt_series: int = 0
    findings: tuple[FindingRowModel, ...] = ()

    @property
    def is_empty(self) -> bool:
        return not (self.outcomes or self.findings)


def needs_attention(
    scope: DashboardScope,
    analysis: ScopeAnalysis,
    warning_groups: Sequence[WarningGroupModel],
    by_sample: Sequence[BySampleRowModel],
    status_context: ItemStatusContext,
    viewer_rows: Mapping[TabKey, Sequence[Entity]],
    *,
    session_atlas_keys: frozenset[tuple[str, str]],
) -> NeedsAttentionModel:
    orphan_groups = [
        group
        for group in analysis.inferred_batch_groups
        if not group_matches_any_batch(group, scope.batch_positions)
    ]
    return NeedsAttentionModel(
        outcomes=tuple(_outcome_rows(scope, analysis, by_sample, status_context)),
        inferred_group_labels=tuple(group.label for group in orphan_groups),
        inferred_tilt_series=sum(len(group.tilt_series) for group in orphan_groups),
        findings=tuple(_finding_rows(scope, warning_groups, viewer_rows, session_atlas_keys)),
    )


# ------------------------------------------------------------------ outcome rows


def with_atlas_outcome(attention: NeedsAttentionModel, sheet: AtlasSheetModel) -> NeedsAttentionModel:
    """``attention`` with a row for the atlases whose image files were not found.

    On an atlas session this is the acquisition outcome: a tile in the sheet
    shows no picture. One missing atlas opens in the Atlas tab; several narrow
    the sheet to them (a filter of the Atlas tab's list would leave out one the
    list does not hold, the Single Atlas, and so disagree with the count).
    """

    missing = sheet.tiles_for(AtlasFilter.MISSING)
    if not missing:
        return attention
    count = len(missing)
    named = ", ".join(tile.sample_label for tile in missing) if count <= INLINE_NAMES_MAX else ""
    route: OutcomeRoute
    if count == 1:
        route = OpenObjectRoute(TabKey.ATLAS, missing[0].atlas_id, f"Open {missing[0].sample_label} in the Atlas tab")
    else:
        route = SheetFilterRoute(AtlasFilter.MISSING, "Show only the atlases whose image files were not found")
    row = OutcomeRowModel(
        kind=EntityKind.ATLAS,
        status=TILE_STATUS_MISSING,
        count=count,
        label=f"{count} atlas {'image' if count == 1 else 'images'} missing",
        fact=" · ".join(part for part in (named, "contact-sheet image file not found") if part),
        by_sample=(),
        route=route,
    )
    return replace(attention, outcomes=(*attention.outcomes, row))


def _outcome_rows(
    scope: DashboardScope,
    analysis: ScopeAnalysis,
    by_sample: Sequence[BySampleRowModel],
    status_context: ItemStatusContext,
) -> list[OutcomeRowModel]:
    rows: list[OutcomeRowModel] = []

    failed = [
        analysis.validation_by_id[tilt.id]
        for tilt in scope.tilt_series
        if tilt.id in analysis.validation_by_id and analysis.validation_by_id[tilt.id].status == STATUS_FAILED
    ]
    if failed:
        rows.append(
            OutcomeRowModel(
                kind=EntityKind.TILT_SERIES,
                status=TILE_STATUS_FAILED,
                count=len(failed),
                label=f"{len(failed)} tilt series failed",
                fact=_frames_fact([(v.actual_count, v.expected_count) for v in failed]),
                by_sample=_by_sample(by_sample, "tilt_failed"),
                route=DashboardFilterModel(
                    "Failed",
                    len(failed),
                    TabKey.TILT_SERIES,
                    filter_tag_query(validation_status_tag(STATUS_FAILED)),
                    "Show failed tilt series",
                ),
            )
        )

    incomplete_maps = [
        search_map for search_map in scope.search_maps if search_map_is_incomplete(search_map, status_context)
    ]
    if incomplete_maps:
        noun = "search map" if len(incomplete_maps) == 1 else "search maps"
        rows.append(
            OutcomeRowModel(
                kind=EntityKind.SEARCH_MAP,
                status=TILE_STATUS_WARNING,
                count=len(incomplete_maps),
                label=f"{len(incomplete_maps)} {noun} incomplete",
                fact=_mosaic_fact([search_map_tile_counts(search_map) for search_map in incomplete_maps]),
                by_sample=_by_sample(by_sample, "incomplete_search_maps"),
                route=DashboardFilterModel(
                    "Incomplete",
                    len(incomplete_maps),
                    TabKey.SEARCH_MAP,
                    filter_tag_query(status_tag(STATUS_INCOMPLETE_LABEL)),
                    "Show incomplete search maps",
                ),
            )
        )
    return [replace(row, route=scoped_filter(row.route, scope))
            if isinstance(row.route, DashboardFilterModel) else row for row in rows]


def _by_sample(rows: Sequence[BySampleRowModel], attribute: str) -> tuple[SampleCount, ...]:
    return tuple(
        SampleCount(row.label, getattr(row, attribute)) for row in rows if getattr(row, attribute)
    )


def _frames_fact(counts: Sequence[tuple[int, int | None]]) -> str:
    """"1 of 35 frames each" when the failed series agree; a span of frames
    when only the recorded count varies; nothing when the expected count does."""

    expected = {expected for _actual, expected in counts}
    if len(expected) != 1 or None in expected:
        return ""
    total = expected.pop()
    actual = sorted({actual for actual, _expected in counts})
    if len(actual) == 1:
        return f"{actual[0]} of {total} frames each"
    return f"{actual[0]} to {actual[-1]} of {total} frames"


def _mosaic_fact(counts: Sequence[tuple[int, int]]) -> str:
    """The same rule for mosaic tiles, over the maps whose coverage is partial."""

    partial = [(planned, acquired) for planned, acquired in counts if planned > acquired]
    if len(partial) != len(counts) or len({planned for planned, _acquired in partial}) != 1:
        return ""
    planned = partial[0][0]
    acquired = sorted({acquired for _planned, acquired in partial})
    if len(acquired) == 1:
        return f"{acquired[0]} of {planned} mosaic tiles each"
    return f"{acquired[0]} to {acquired[-1]} of {planned} mosaic tiles"


# ------------------------------------------------------------------ finding rows


def _finding_rows(scope: DashboardScope, groups: Sequence[WarningGroupModel],
                  viewer_rows: Mapping[TabKey, Sequence[Entity]],
                  session_atlas_keys: frozenset[tuple[str, str]]) -> list[FindingRowModel]:
    tilt_keys = frozenset(canonical_entity_key(tilt) for tilt in scope.tilt_series)
    findings = []
    for group in groups:
        route, reason = finding_route(group, scope, viewer_rows)
        qualifier = finding_qualifier(group, tilt_keys)
        if group.entity_keys and group.entity_keys & session_atlas_keys:
            qualifier = " · ".join(part for part in (qualifier, "includes a session-level atlas record") if part)
        findings.append(FindingRowModel(
            label=group.label,
            count=group.count,
            severity=group.severity,
            qualifier=qualifier,
            items=tuple(group.items),
            route=route,
            route_reason=reason,
        ))
    return findings


def finding_qualifier(group: WarningGroupModel, tilt_keys: frozenset[tuple[str, str]]) -> str:
    if _covers_every_tilt_series(group, tilt_keys):
        return EVERY_TILT_SERIES
    if group.count <= INLINE_NAMES_MAX:
        return _inline_names(group.items)
    return ""


def _covers_every_tilt_series(group: WarningGroupModel, tilt_keys: frozenset[tuple[str, str]]) -> bool:
    """All four must hold, or the qualifier is left off.

    Every member is attributed; the objects are exactly the scope's tilt series
    (canonical keys carry the entity type, so equality also proves none is
    anything else); there is more than one, so the words say something; and the
    displayed count equals the number of keys (the shared counting rule counts
    distinct warning prefixes, which two same-named series would share). Matching
    counts or names alone never qualify.
    """

    keys = group.entity_keys
    return (
        keys is not None
        and len(tilt_keys) > 1
        and keys == tilt_keys
        and group.count == len(keys)
    )


def _inline_names(items: Sequence[str]) -> str:
    """"<message>: <name>, <name>" for the few objects a finding covers.

    Display text only, from the warnings' own prefixes: never identity, never
    a count. A name that is generic alone ("Atlas") takes its owner ("Specimen_slot").
    Several different messages, or one too long to repeat, list the names alone.
    """

    names: dict[str, None] = {}
    messages: dict[str, None] = {}
    for raw in items:
        prefix, message = split_warning_scope(raw)
        name = _object_name(prefix)
        if name:
            names.setdefault(name, None)
        messages.setdefault(message.rstrip("."), None)
    if not names:
        return ""
    listed = ", ".join(names)
    message = next(iter(messages))
    return f"{message}: {listed}" if len(messages) == 1 and len(message) <= INLINE_MESSAGE_MAX else listed


def _object_name(prefix: str) -> str:
    parts = [part.strip() for part in prefix.split("/") if part.strip()]
    if not parts:
        return ""
    if parts[-1].casefold() in _GENERIC_NAMES and len(parts) > 1:
        return parts[-2]
    return parts[-1]
