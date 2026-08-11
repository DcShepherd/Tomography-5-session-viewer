"""Compose a graphical PDF session report.

The report mirrors the on-screen Session-tab dashboard:

1. **Cover** — title, generation timestamp, count cards, tilt-series
   donut, severity chip strip, and a *grouped* warning summary.
2. **Linked-session / atlas overview** — only when relevant (multi-grid
   sessions or atlas-only screenings). Surfaces the linkage between
   atlas and the data-collection sessions that reference it.
3. **One section per data-collection sample**, each with:

   A. Graphical summary (count cards + tilt-series waffle + status donut)
   B. Atlas image (only the atlas associated with this sample)
   C. Overview images (grid)
   D. Search maps with overlays
   E. Tilt-series acquisition settings (key/value table)
   F. Tilt-series table (status badge + actual/expected + quality notes)

The generator is deliberately a *layout* layer; data shaping happens in
:mod:`session_presenter`, validation in
:mod:`services.tilt_series_validation`, warning categorisation in
:mod:`reports.warning_summary`, and graphical primitives in
:mod:`reports.graphics`. Adding a new section to the report should mean
new flowable code, not new domain logic.
"""

from __future__ import annotations

import logging
import re
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from statistics import median
from typing import Iterable, Mapping, Sequence

from reportlab.lib import colors
from reportlab.lib.pagesizes import LETTER, landscape
from reportlab.lib.units import inch
from reportlab.platypus import (
    CondPageBreak,
    KeepTogether,
    NextPageTemplate,
    PageBreak,
    Paragraph,
    Spacer,
    Table,
)

from tomography_session_browser.domain.enums import SessionKind
from tomography_session_browser.domain.markers import ImageMarker, MarkerType
from tomography_session_browser.domain.units import ANGSTROM_PER_PIXEL, DEGREE
from tomography_session_browser.domain.models import (
    Atlas,
    BatchPosition,
    Overview,
    Sample,
    SearchMap,
    Session,
    TiltSeries,
)
from tomography_session_browser.reports import graphics, styles
from tomography_session_browser.reports.pdf_export import write_pdf
from tomography_session_browser.reports.warning_summary import (
    WarningSummary,
    severity_counts,
    summarise_warnings,
)
from tomography_session_browser.services.marker_service import (
    MarkerContext,
    atlas_lod_markers,
    image_size_for_markers,
    markers_for_object,
    project_marker_to_size,
)
from tomography_session_browser.services.timeline_service import parse_datetime
from tomography_session_browser.services.acquisition_metadata import (
    ACQUISITION_SPOT_LABEL,
    LEGACY_SPOT_LABEL,
    SEARCH_SPOT_LABEL,
    format_target_defocus_values,
    summarise_acquisition_setting,
)
from tomography_session_browser.services.tilt_series_validation import (
    HARD_FAILURE_THRESHOLD,
    STATUS_COMPLETE,
    STATUS_FAILED,
    STATUS_INCOMPLETE,
    STATUS_UNKNOWN,
    TiltSeriesValidation,
    summarise_validations,
    validate_session_tilt_series,
)
from tomography_session_browser.ui.session_presenter import (
    AtlasCountModel,
    DashboardModel,
    compute_atlas_counts,
    dedupe_entities,
    search_map_acquisition_rollup,
    search_map_batch_positions,
    session_dashboard_model,
    session_warnings,
)

LOGGER = logging.getLogger(__name__)


# Reportlab Image can embed jpg/png natively; everything else needs a
# sibling rasterised version we look for at embed time.
_EMBEDDABLE_IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png"}

# Page dimensions. Computed once so all the layout maths agrees with what
# pdf_export.py sets up.
_PAGE_W, _PAGE_H = LETTER
_MARGIN = 0.6 * inch
_TOP_MARGIN = 0.7 * inch
_BOTTOM_MARGIN = 0.6 * inch
PORTRAIT_USABLE_W = _PAGE_W - 2 * _MARGIN
PORTRAIT_USABLE_H = _PAGE_H - _TOP_MARGIN - _BOTTOM_MARGIN
LANDSCAPE_USABLE_W = landscape(LETTER)[0] - 2 * _MARGIN
LANDSCAPE_USABLE_H = landscape(LETTER)[1] - _TOP_MARGIN - _BOTTOM_MARGIN


@dataclass(slots=True)
class ReportResult:
    path: Path
    page_count: int
    warnings: list[str]


@dataclass(frozen=True, slots=True)
class ProjectReportGroup:
    display_name: str
    automatic_name: str
    kind: str
    session_names: tuple[str, ...]
    session_paths: tuple[str, ...]


# =============================================================================
# Top-level entry point
# =============================================================================


def build_session_report(
    sessions: Sequence[Session],
    output_path: Path,
    *,
    include_thumbnails: bool = True,
    project_title: str | None = None,
    project_groups: Sequence[ProjectReportGroup] | None = None,
) -> ReportResult:
    """Render ``sessions`` to a PDF at ``output_path``.

    ``include_thumbnails=False`` keeps the report text-only (no atlas /
    overview / search-map images). The graphical summary widgets — count
    cards, donuts, status tiles — render either way; they don't depend
    on raster content.
    """

    if not sessions:
        raise ValueError("build_session_report requires at least one session")

    sessions = list(sessions)
    generation_warnings: list[str] = []

    # Pre-compute per-session validation + warning summaries so we can
    # share them between the cover (totals) and the per-sample sections
    # (per-sample detail). Keeps validation deterministic and avoids
    # double work on large sessions.
    per_session: list[_SessionContext] = [
        _SessionContext.build(session) for session in sessions
    ]

    flowables: list = []
    flowables.append(NextPageTemplate("portrait"))

    title = project_title or _report_title(sessions)
    footer = _footer_label(sessions, project_title=project_title)

    flowables.extend(_cover_page(per_session, project_title=project_title, project_groups=project_groups or ()))

    # Linked-session / atlas-only overview section if any of the sessions
    # is an atlas screening, or if multi-grid sessions reference one.
    linked_block = _linked_overview_section(per_session, include_thumbnails, generation_warnings)
    if linked_block:
        flowables.append(PageBreak())
        flowables.extend(linked_block)

    # Per-data-collection sections.
    for ctx in per_session:
        for sample in ctx.collection_samples:
            flowables.append(PageBreak())
            flowables.extend(
                _data_collection_section(
                    ctx,
                    sample,
                    include_thumbnails=include_thumbnails,
                    generation_warnings=generation_warnings,
                )
            )

    page_count = write_pdf(
        output_path,
        flowables,
        title=title,
        footer_label=footer,
    )
    return ReportResult(path=output_path, page_count=page_count, warnings=generation_warnings)


# =============================================================================
# Per-session context — pre-computed once and shared across sections
# =============================================================================


@dataclass(slots=True)
class _SessionContext:
    session: Session
    dashboard: DashboardModel
    # Validation results keyed by tilt series id.
    validations: dict[str, TiltSeriesValidation]
    # Samples that contain data-collection content (vs. atlas-only).
    collection_samples: list[Sample]
    # All warnings, deduped, with sample/object prefixes.
    warnings: list[str]
    warning_rows: list[WarningSummary]

    @classmethod
    def build(cls, session: Session) -> "_SessionContext":
        dashboard = session_dashboard_model(session)
        # Validate every tilt series across the session in one shot so
        # session-majority inference is consistent.
        all_tilt: list[TiltSeries] = list(session.tilt_series)
        for sample in session.samples:
            all_tilt.extend(sample.tilt_series)
        all_tilt = dedupe_entities(all_tilt)
        validations_list = validate_session_tilt_series(all_tilt)
        validations = {v.tilt_series_id: v for v in validations_list}

        collection_samples = [
            sample
            for sample in session.samples
            if (
                sample.search_maps
                or sample.batch_positions
                or sample.tilt_series
                or sample.overviews
            )
        ]
        # Atlas-only / single-collection sessions sometimes hold their
        # data-collection content directly on the session rather than on
        # a Sample. Synthesise a single "session-level" Sample in that
        # case so the per-sample section logic keeps working.
        if not collection_samples and (
            session.search_maps or session.batch_positions or session.tilt_series
        ):
            collection_samples.append(
                Sample(
                    id=session.id,
                    name=session.name,
                    path=session.path,
                    atlas=session.atlas,
                    overviews=session.overviews,
                    search_maps=session.search_maps,
                    batch_positions=session.batch_positions,
                    tilt_series=session.tilt_series,
                    metadata=session.metadata_summary,
                    warnings=list(session.warnings),
                )
            )

        warnings = session_warnings(session)
        warning_rows = summarise_warnings(warnings)

        return cls(
            session=session,
            dashboard=dashboard,
            validations=validations,
            collection_samples=collection_samples,
            warnings=warnings,
            warning_rows=warning_rows,
        )


# =============================================================================
# Cover page
# =============================================================================


def _cover_page(
    contexts: Sequence[_SessionContext],
    *,
    project_title: str | None = None,
    project_groups: Sequence[ProjectReportGroup] = (),
) -> list:
    flowables: list = []

    title = project_title or _report_title([ctx.session for ctx in contexts])
    flowables.append(Paragraph(_html_escape(title), styles.TITLE_STYLE))

    generated_at = datetime.now().strftime("%Y-%m-%d %H:%M")
    summary_line = f"Generated {generated_at}"
    if contexts:
        summary_line += " &middot; "
        summary_line += _cover_summary_line(contexts)
    flowables.append(Paragraph(summary_line, styles.SUBTITLE_STYLE))

    # Roll up totals across every session for the count cards & donut.
    totals = _aggregate_totals(contexts)
    cards = _cover_count_cards(totals)
    flowables.append(graphics.card_row(cards, total_width=PORTRAIT_USABLE_W))
    flowables.append(Spacer(1, 12))

    # Tilt-series outcome donut + legend, side by side.
    flowables.append(Paragraph("Tilt series outcomes", styles.H2_STYLE))
    flowables.append(_outcomes_block(totals))
    flowables.append(Spacer(1, 10))

    # Severity chip strip.
    flowables.append(Paragraph("Warnings overview", styles.H2_STYLE))
    flowables.append(_severity_chip_row(totals.severity_counts, totals.total_warnings))
    flowables.append(Spacer(1, 6))

    # Grouped warning summary table (the core "compact warnings" feature).
    flowables.append(_warning_summary_table(totals.warning_rows))
    flowables.append(Spacer(1, 6))

    if project_groups:
        flowables.append(Paragraph("Project groups", styles.H2_STYLE))
        flowables.append(_project_groups_table(project_groups))
        flowables.append(Spacer(1, 8))

    # Print-readability note. Keeps the cover honest about colour-only
    # signalling — text labels back up every coloured element above.
    if not totals.has_collection_data and not totals.has_atlas_data:
        flowables.append(
            Paragraph(
                "Report limitations: the loaded session(s) contained no "
                "atlas, overview, search map, batch position or tilt series "
                "content. Most sections will be empty.",
                styles.NOTE_STYLE,
            )
        )
    return flowables


def _project_groups_table(project_groups: Sequence[ProjectReportGroup]) -> Table:
    rows: list[list[str]] = [["Display name", "Type", "Original sessions", "Source paths"]]
    for group in project_groups:
        original = ", ".join(name for name in group.session_names if name) or group.automatic_name
        paths = "\n".join(group.session_paths)
        display_name = group.display_name
        if group.display_name != group.automatic_name:
            display_name += f"\n(auto: {group.automatic_name})"
        rows.append([display_name, group.kind, original, paths])
    return Table(
        [[Paragraph(_html_escape(cell).replace("\n", "<br/>"), styles.SMALL_STYLE) for cell in row] for row in rows],
        colWidths=[1.65 * inch, 1.0 * inch, 1.85 * inch, 2.5 * inch],
        hAlign="LEFT",
        style=[
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#eef2f6")),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.HexColor("#0f172a")),
            ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#cbd5e1")),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("LEFTPADDING", (0, 0), (-1, -1), 5),
            ("RIGHTPADDING", (0, 0), (-1, -1), 5),
            ("TOPPADDING", (0, 0), (-1, -1), 4),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ],
    )


def _cover_summary_line(contexts: Sequence[_SessionContext]) -> str:
    n = len(contexts)
    if n == 1:
        ctx = contexts[0]
        sample_count = len(ctx.collection_samples)
        sample_noun = "data collection sample" if sample_count == 1 else "data collection samples"
        return f"{_session_kind_label(ctx.session.kind)} session &middot; {sample_count} {sample_noun}"
    atlas = sum(1 for ctx in contexts if ctx.session.kind == SessionKind.ATLAS_SCREENING)
    return (
        f"{n} sessions &middot; {atlas} atlas, {n - atlas} data collection"
    )


def _session_kind_label(kind: SessionKind) -> str:
    if kind == SessionKind.ATLAS_SCREENING:
        return "atlas screening"
    if kind in {SessionKind.COLLECTION, SessionKind.SINGLE_COLLECTION}:
        return "data collection"
    return kind.value.replace("_", " ")


@dataclass(slots=True)
class _Totals:
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
    atlas_counts: AtlasCountModel = None  # type: ignore[assignment]


def _aggregate_totals(contexts: Sequence[_SessionContext]) -> _Totals:
    overviews = search_maps = batch_positions = tilt_series = 0
    complete = incomplete = failed = unknown = 0
    has_atlas = has_collection = False
    n_atlas = n_collection = 0

    for ctx in contexts:
        session = ctx.session
        # Counts come from the same presenter model that drives the Session
        # dashboard, so direct session lists and sample lists cannot inflate
        # the PDF cover independently.
        overviews += _dashboard_count(ctx.dashboard, "Overviews")
        search_maps += _dashboard_count(ctx.dashboard, "Search maps")
        batch_positions += _dashboard_count(ctx.dashboard, "Batch positions")
        tilt_series += ctx.dashboard.outcomes.total

        for v in ctx.validations.values():
            if v.status == STATUS_COMPLETE:
                complete += 1
            elif v.status == STATUS_INCOMPLETE:
                incomplete += 1
            elif v.status == STATUS_FAILED:
                failed += 1
            else:
                unknown += 1

        atlas_present = bool(session.atlas) or any(s.atlas for s in session.samples)
        if atlas_present:
            has_atlas = True
        if session.kind == SessionKind.ATLAS_SCREENING:
            n_atlas += 1
        else:
            n_collection += 1
        if (
            session.overviews
            or session.search_maps
            or session.batch_positions
            or session.tilt_series
            or any(
                s.overviews or s.search_maps or s.batch_positions or s.tilt_series
                for s in session.samples
            )
        ):
            has_collection = True

    # Aggregate warning summary across every session.
    aggregate_warnings: list[str] = []
    for ctx in contexts:
        aggregate_warnings.extend(ctx.warnings)
    warning_rows = summarise_warnings(aggregate_warnings)
    severities = severity_counts(warning_rows)

    n_linked_groups = sum(1 for ctx in contexts for _ in ctx.collection_samples)
    # The atlas counter sees every session in the loaded set so it can
    # apply cross-session linkage (a collection Sample1 with no own atlas
    # is linked to the screening session's Sample1 atlas).
    atlas_counts = compute_atlas_counts([ctx.session for ctx in contexts])
    return _Totals(
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
        total_warnings=len(aggregate_warnings),
        warning_rows=warning_rows,
        has_atlas_data=has_atlas,
        has_collection_data=has_collection,
        atlas_counts=atlas_counts,
    )


def _dashboard_count(dashboard: DashboardModel, label: str) -> int:
    row = next((card for card in dashboard.counts if card.label == label), None)
    return int(row.value) if row is not None else 0


def _cover_count_cards(totals: _Totals) -> list[graphics.CountCard]:
    """Top-of-cover count cards.

    Five cards in acquisition-hierarchy order: Atlases → Overviews →
    Search maps → Batch positions → Tilt series. The atlas count is
    sourced from :class:`AtlasCountModel` so it agrees with the
    dashboard ("12 Atlases" for the linked reference sessions, not
    14 — the screening session's root Atlas/ folder is not a sample
    atlas).
    """

    width = PORTRAIT_USABLE_W / 5
    return [
        graphics.CountCard(
            graphics.CountCardData(
                label="Atlases",
                value=totals.atlas_counts.samples_with_atlas_count,
                accent="session",
            ),
            width=width,
        ),
        graphics.CountCard(
            graphics.CountCardData(label="Overviews", value=totals.overviews, accent="session"),
            width=width,
        ),
        graphics.CountCard(
            graphics.CountCardData(label="Search maps", value=totals.search_maps, accent="session"),
            width=width,
        ),
        graphics.CountCard(
            graphics.CountCardData(label="Batch positions", value=totals.batch_positions, accent="session"),
            width=width,
        ),
        graphics.CountCard(
            graphics.CountCardData(
                label="Tilt series",
                value=totals.tilt_series,
                sub_label=f"{totals.complete} complete / {totals.failed} failed",
                accent="complete" if totals.failed == 0 and totals.tilt_series > 0 else "session",
            ),
            width=width,
        ),
    ]


def _outcomes_block(totals: _Totals) -> Table:
    segments = [
        graphics.StatusSegment("Complete", totals.complete, "complete"),
        graphics.StatusSegment("Incomplete", totals.incomplete, "incomplete"),
        graphics.StatusSegment("Failed", totals.failed, "failed"),
        graphics.StatusSegment("Unavailable", totals.unknown, "unknown"),
    ]
    donut = graphics.DonutChart(
        segments,
        diameter=1.4 * inch,
        center_label=str(totals.tilt_series),
        center_caption="tilt series",
    )
    legend = graphics.donut_legend(segments)
    table = Table(
        [[donut, legend]],
        colWidths=[1.5 * inch, PORTRAIT_USABLE_W - 1.5 * inch],
        hAlign="LEFT",
    )
    table.setStyle(
        [
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("LEFTPADDING", (0, 0), (-1, -1), 0),
            ("RIGHTPADDING", (0, 0), (-1, -1), 0),
        ]
    )
    return table


def _severity_chip_row(severities: dict[str, int], total: int) -> Table:
    items = [
        (f"{severities.get('error', 0)} errors", "error"),
        (f"{severities.get('warning', 0)} warnings", "warning"),
        (f"{severities.get('info', 0)} info", "info"),
        (f"{total} total", "neutral"),
    ]
    return graphics.chip_row(items, total_width=PORTRAIT_USABLE_W)


def _warning_summary_table(rows: list[WarningSummary]) -> Table:
    if not rows:
        return Table(
            [[Paragraph("No warnings emitted.", styles.SMALL_STYLE)]],
            colWidths=[PORTRAIT_USABLE_W],
            style=styles.DATA_TABLE_STYLE,
        )

    header = ["Severity", "Warning category", "Count", "Affected"]
    body: list[list] = []
    for row in rows:
        badge = graphics.StatusBadge(
            row.severity.title(),
            row.severity,
            width=0.65 * inch,
            height=12,
        )
        category = Paragraph(
            f"<b>{_html_escape(row.category)}</b><br/>"
            f'<font size="7" color="#475569">{_html_escape(row.explanation)}</font>',
            styles.SMALL_STYLE,
        )
        body.append([
            badge,
            category,
            Paragraph(str(row.count), styles.BODY_STYLE),
            Paragraph(
                f"{row.affected_items} item{'s' if row.affected_items != 1 else ''}",
                styles.SMALL_STYLE,
            ),
        ])
    paragraph_header = [Paragraph(_html_escape(h), styles.SMALL_STYLE) for h in header]
    return Table(
        [paragraph_header, *body],
        colWidths=[
            0.85 * inch,
            PORTRAIT_USABLE_W - 0.85 * inch - 0.7 * inch - 0.95 * inch,
            0.7 * inch,
            0.95 * inch,
        ],
        style=styles.DATA_TABLE_STYLE,
        repeatRows=1,
        hAlign="LEFT",
    )


# =============================================================================
# Linked-session / atlas overview
# =============================================================================


def _linked_overview_section(
    contexts: Sequence[_SessionContext],
    include_thumbnails: bool,
    generation_warnings: list[str],
) -> list:
    """Show a top-level summary for atlas / multi-grid sessions."""

    # Only render if we have at least one atlas-bearing session, or
    # multiple sessions linked together.
    if len(contexts) <= 1 and not any(
        ctx.session.kind == SessionKind.ATLAS_SCREENING for ctx in contexts
    ):
        return []

    flowables: list = [Paragraph("Linked-session overview", styles.H1_STYLE)]
    for ctx in contexts:
        flowables.extend(_session_overview_block(ctx, include_thumbnails, generation_warnings))
        flowables.append(Spacer(1, 8))
    return flowables


def _session_overview_block(
    ctx: _SessionContext,
    include_thumbnails: bool,
    generation_warnings: list[str],
) -> list:
    """Per-session block in the linked-session overview.

    Atlas-screening and data-collection sessions need different
    summaries: an atlas screening session has no overviews / search
    maps / tilt series at the session level, so showing those zero
    counts is just noise. Instead it gets a "Number of acquired
    atlases" line and a thumbnail grid of every per-grid atlas image
    on disk. Data-collection sessions keep the existing counts table
    plus the tilt-series status strip.
    """

    session = ctx.session
    is_atlas_session = session.kind == SessionKind.ATLAS_SCREENING

    flowables: list = [Paragraph(_html_escape(session.name), styles.H2_STYLE)]

    rows: list[tuple[str, str]] = [
        ("Kind", session.kind.value),
        ("Path", str(session.path)),
    ]
    sample_atlases = _resolved_sample_atlases(session)
    if is_atlas_session:
        rows.append(("Number of acquired atlases", str(len(sample_atlases))))
    else:
        rows.append(("Data collections", str(len(ctx.collection_samples))))
        rows.append(("Total search maps", str(_count_searchmaps(session))))
        rows.append(("Total overviews", str(_count_overviews(session))))
        rows.append(("Total tilt series", str(_count_tilt_series(session))))
    window = _session_acquisition_window(ctx)
    if window:
        rows.append(("Acquisition window", window))

    flowables.append(_kv_table(rows))

    # Tilt-series status mini-strip — only meaningful for sessions with
    # collected tilt series (never the case for an atlas screening
    # session, so it's silently skipped there).
    if not is_atlas_session and ctx.dashboard.outcomes.total > 0:
        outcomes = ctx.dashboard.outcomes
        segments = [
            graphics.StatusSegment("Complete", outcomes.complete, "complete"),
            graphics.StatusSegment("Incomplete", outcomes.incomplete, "incomplete"),
            graphics.StatusSegment("Failed", outcomes.failed, "failed"),
            graphics.StatusSegment("Unavailable", outcomes.unknown, "unknown"),
        ]
        flowables.append(Spacer(1, 4))
        flowables.append(
            graphics.StackedStatusBar(segments, width=PORTRAIT_USABLE_W, height=8)
        )
        flowables.append(Spacer(1, 2))
        flowables.append(
            Paragraph(
                f"<font color=\"#065f46\">●</font> {outcomes.complete} complete &nbsp;&nbsp; "
                f"<font color=\"#92400e\">●</font> {outcomes.incomplete} incomplete &nbsp;&nbsp; "
                f"<font color=\"#991b1b\">●</font> {outcomes.failed} failed &nbsp;&nbsp; "
                f"<font color=\"#1f2937\">●</font> {outcomes.unknown} unavailable",
                styles.SMALL_STYLE,
            )
        )

    # Atlas thumbnails. The session-level Atlas/ folder typically only
    # holds the .dm metadata; the actual images live under each
    # SampleN/Atlas/ subfolder. Iterate the per-sample atlases instead
    # of relying on ``session.atlas.image_path``.
    if include_thumbnails and sample_atlases:
        flowables.append(Spacer(1, 6))
        flowables.extend(
            _atlas_thumbnail_grid(sample_atlases, generation_warnings)
        )

    return flowables


def _resolved_sample_atlases(session: Session) -> list[tuple[str, Atlas]]:
    """Return ``(label, atlas)`` for every sample-level atlas in ``session``.

    Skips samples whose Atlas/ folder didn't yield an image (parser
    falls back to ``image_path = None`` in that case). The label is
    the sample's display name so the grid caption identifies which
    grid each thumbnail belongs to.
    """

    items: list[tuple[str, Atlas]] = []
    for sample in session.samples:
        if sample.atlas is None:
            continue
        if _embeddable_image_path(sample.atlas.image_path) is None:
            continue
        items.append((sample.name, sample.atlas))
    return items


def _atlas_thumbnail_grid(
    atlases: Sequence[tuple[str, Atlas]],
    generation_warnings: list[str],
) -> list:
    """Render per-sample atlas thumbnails as a 3-per-row grid."""

    flowables: list = []
    cells_per_row = 3
    cell_w = (PORTRAIT_USABLE_W - 2 * 8) / cells_per_row  # 8pt gutter between
    cell_h = PORTRAIT_USABLE_H * 0.22
    pending_row: list = []

    for label, atlas in atlases:
        embed = _embeddable_image_path(atlas.image_path)
        if embed is None:
            continue
        native = graphics.native_image_size(embed)
        if (
            native is None
            and atlas.mrc_metadata is not None
            and atlas.mrc_metadata.nx
            and atlas.mrc_metadata.ny
        ):
            native = (atlas.mrc_metadata.nx, atlas.mrc_metadata.ny)
        if native is None or native[0] <= 0 or native[1] <= 0:
            native = (1000, 1000)

        image = graphics.OverlayedImage(
            embed,
            native_size=native,
            markers=(),  # no overlays at the linked-overview thumbnail size
            max_width=cell_w,
            max_height=cell_h,
        )
        caption = (
            f"<b>{_html_escape(label)}</b><br/>"
            f"<font size=\"7\" color=\"#475569\">{native[0]} × {native[1]} px</font>"
        )
        cell = Table(
            [[image], [Paragraph(caption, styles.SMALL_STYLE)]],
            colWidths=[cell_w],
            style=[
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 0),
                ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                ("TOPPADDING", (0, 0), (-1, 0), 0),
                ("BOTTOMPADDING", (0, 0), (-1, 0), 2),
                ("TOPPADDING", (0, 1), (-1, 1), 0),
                ("BOTTOMPADDING", (0, 1), (-1, 1), 0),
            ],
        )
        pending_row.append(cell)
        if len(pending_row) == cells_per_row:
            flowables.append(_atlas_thumbnail_row(pending_row, cell_w))
            flowables.append(Spacer(1, 6))
            pending_row = []

    if pending_row:
        # Pad short rows so columns line up with the full-width rows
        # above.
        while len(pending_row) < cells_per_row:
            pending_row.append(Paragraph("", styles.SMALL_STYLE))
        flowables.append(_atlas_thumbnail_row(pending_row, cell_w))

    if not flowables:
        flowables.append(
            Paragraph(
                "No embeddable atlas images on disk for this session.",
                styles.NOTE_STYLE,
            )
        )
    return flowables


def _atlas_thumbnail_row(cells: list, cell_w: float) -> Table:
    return Table(
        [cells],
        colWidths=[cell_w] * len(cells),
        hAlign="LEFT",
        style=[
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("LEFTPADDING", (0, 0), (-1, -1), 0),
            ("RIGHTPADDING", (0, 0), (-1, -1), 8),
            ("TOPPADDING", (0, 0), (-1, -1), 0),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
        ],
    )


# =============================================================================
# Per-data-collection-sample section
# =============================================================================


def _subheading(label: str, *, min_remaining: float) -> list:
    """Heading flowables that don't strand at the bottom of a page.

    Returns ``[CondPageBreak(min_remaining), Paragraph(label, H2)]``
    — the conditional page break fires when there's less than
    ``min_remaining`` of vertical space left in the current frame,
    pushing the heading to a fresh page so it travels with its
    first-item. ``H2_STYLE.keepWithNext`` then keeps it glued to the
    flowable that follows.

    Use a per-section ``min_remaining`` so we don't insert blank
    pages for short sections (acquisition settings table) but
    reliably break before image-heavy ones (Search maps, Atlas).
    """

    return [
        CondPageBreak(min_remaining),
        Paragraph(_html_escape(label), styles.H2_STYLE),
    ]


def _data_collection_section(
    ctx: _SessionContext,
    sample: Sample,
    *,
    include_thumbnails: bool,
    generation_warnings: list[str],
) -> list:
    flowables: list = [Paragraph(f"Data collection: {_html_escape(sample.name)}", styles.H1_STYLE)]
    flowables.append(Paragraph(_html_escape(str(sample.path)), styles.SMALL_STYLE))
    flowables.append(Spacer(1, 6))

    # IDs of validator-flagged failed tilt series. Threaded through to
    # the marker pipeline so failed batches' exposure / template
    # overlays render in the red "failed" colour, matching the GUI.
    failed_tilt_ids = frozenset(
        v_id for v_id, v in ctx.validations.items() if v.status == STATUS_FAILED
    )

    # ------- A. graphical summary
    flowables.extend(_sample_graphical_summary(sample, ctx))
    flowables.append(Spacer(1, 8))

    # ------- B. atlas image
    atlas = sample.atlas or _fallback_session_atlas(ctx, sample)
    if include_thumbnails and atlas is not None:
        atlas_block = _atlas_image_block(
            atlas,
            label=f"Atlas for {sample.name}",
            generation_warnings=generation_warnings,
            scope_overviews=sample.overviews,
            scope_search_maps=sample.search_maps,
            scope_batch_positions=sample.batch_positions,
            scope_tilt_series=sample.tilt_series,
            failed_tilt_ids=failed_tilt_ids,
        )
        if atlas_block:
            # Atlas image is roughly 4-5" tall; need similar headroom
            # so the heading doesn't strand at the bottom of a page.
            flowables.extend(_subheading("Atlas", min_remaining=4.5 * inch))
            flowables.extend(atlas_block)
            flowables.append(Spacer(1, 8))

    # ------- C. overview images
    if include_thumbnails and sample.overviews:
        block = _overview_grid(
            sample.overviews,
            sample,
            generation_warnings,
            failed_tilt_ids=failed_tilt_ids,
        )
        if block:
            # First overview row is ~3" tall; reserve a bit more to
            # avoid pushing the row partly past the page break.
            flowables.extend(_subheading("Overviews", min_remaining=3.5 * inch))
            flowables.extend(block)
            flowables.append(Spacer(1, 8))

    # ------- D. search maps with overlays
    if include_thumbnails and sample.search_maps:
        # First search-map block ≈ image (4") + caption + meta table
        # (~1"). 5" of headroom keeps the heading + first image
        # together on the same page (Sang's "Search maps" stranded
        # on page 30 with the image on page 31 before this).
        flowables.extend(_subheading("Search maps", min_remaining=5.0 * inch))
        flowables.extend(
            _search_map_blocks(sample.search_maps, sample, ctx, generation_warnings)
        )
        flowables.append(Spacer(1, 8))

    # ------- E. tilt-series acquisition settings
    if sample.tilt_series:
        # Acquisition settings is a small kv table — ~1.5" suffices.
        flowables.extend(
            _subheading("Tilt series acquisition settings", min_remaining=1.8 * inch)
        )
        flowables.append(_acquisition_settings_table(sample.tilt_series, sample.batch_positions, ctx.validations))
        flowables.append(Spacer(1, 6))

        # ------- F. tilt-series table
        flowables.extend(_subheading("Tilt series", min_remaining=2.0 * inch))
        flowables.extend(_tilt_series_table(sample.tilt_series, ctx))
    return flowables


# ----------------------------------------------------------------- A. summary


def _tilt_outcome_sub_label(summary: Mapping[str, int]) -> str:
    """Compact, glyph-safe outcome copy that fits the count card."""

    parts = [f"{summary.get(STATUS_COMPLETE, 0)} complete"]
    for status, label in (
        (STATUS_INCOMPLETE, "incomplete"),
        (STATUS_FAILED, "failed"),
        (STATUS_UNKNOWN, "unavailable"),
    ):
        count = summary.get(status, 0)
        if count:
            parts.append(f"{count} {label}")
    return ", ".join(parts)


def _sample_graphical_summary(sample: Sample, ctx: _SessionContext) -> list:
    width = PORTRAIT_USABLE_W

    # Validations restricted to this sample's tilt series.
    sample_validations = [
        ctx.validations[v.id]
        for v in sample.tilt_series
        if v.id in ctx.validations
    ]
    summary = summarise_validations(sample_validations)

    cards = [
        graphics.CountCard(
            graphics.CountCardData(label="Overviews", value=len(sample.overviews), accent="session"),
            width=width / 4,
        ),
        graphics.CountCard(
            graphics.CountCardData(label="Search maps", value=len(sample.search_maps), accent="session"),
            width=width / 4,
        ),
        graphics.CountCard(
            graphics.CountCardData(label="Batch positions", value=len(sample.batch_positions), accent="session"),
            width=width / 4,
        ),
        graphics.CountCard(
            graphics.CountCardData(
                label="Tilt series",
                value=len(sample.tilt_series),
                sub_label=_tilt_outcome_sub_label(summary),
                accent="complete" if summary.get(STATUS_FAILED, 0) == 0 else "incomplete",
            ),
            width=width / 4,
        ),
    ]

    flowables: list = [graphics.card_row(cards, total_width=width), Spacer(1, 6)]

    # Donut chart + legend. The waffle grid was redundant alongside the
    # donut (both encoded the same status breakdown); only the donut and
    # its legend remain. The legend column gets dedicated horizontal
    # space — a spacer column between the chart and the labels — so the
    # numbers don't crowd the chart edge.
    if sample.tilt_series:
        segments = [
            graphics.StatusSegment("Complete", summary.get(STATUS_COMPLETE, 0), "complete"),
            graphics.StatusSegment("Incomplete", summary.get(STATUS_INCOMPLETE, 0), "incomplete"),
            graphics.StatusSegment("Failed", summary.get(STATUS_FAILED, 0), "failed"),
            graphics.StatusSegment("Unavailable", summary.get(STATUS_UNKNOWN, 0), "unknown"),
        ]
        donut_diameter = 1.4 * inch
        gap = 0.35 * inch  # breathing room between chart and legend
        legend_w = max(1.6 * inch, width - donut_diameter - gap - 0.3 * inch)
        donut = graphics.DonutChart(
            segments,
            diameter=donut_diameter,
            center_label=str(len(sample.tilt_series)),
            center_caption="tilt series",
        )
        legend = graphics.donut_legend(segments)
        chart_row = Table(
            [[donut, "", legend]],
            colWidths=[donut_diameter, gap, legend_w],
            hAlign="LEFT",
        )
        chart_row.setStyle(
            [
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("LEFTPADDING", (0, 0), (-1, -1), 0),
                ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                ("TOPPADDING", (0, 0), (-1, -1), 0),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
            ]
        )
        flowables.append(chart_row)

    # Search-map completion strips (one row per search map).
    if sample.search_maps:
        flowables.append(Spacer(1, 6))
        flowables.append(_search_map_completion_block(sample, ctx))

    return flowables


def _search_map_completion_block(sample: Sample, ctx: _SessionContext) -> Table:
    """Three-column row per search map: label · progress bar · text.

    The numbers are *exposure-area* progress (planned vs. acquired
    exposures across the linked batch positions of each search map),
    sourced from :func:`search_map_exposure_progress` so the GUI
    dashboard and the PDF report read the same value. Column widths
    size to the visible labels so ordinary ``SearchMap_...`` rows do
    not leave a large blank gap before the progress bar, while still
    keeping enough maximum width for longer Tomo5 labels such as
    "1. Sample alpha / Sample01_SearchMap_...".
    """

    bar_w = 2.4 * inch

    successful_tilt_ids = frozenset(
        v_id for v_id, v in ctx.validations.items() if v.status == STATUS_COMPLETE
    )

    rows: list[list] = []
    row_labels: list[str] = []
    for sm in sample.search_maps:
        rollup = search_map_acquisition_rollup(
            sm,
            sample.search_maps,
            sample.batch_positions,
            sample.tilt_series,
            ctx.validations,
            successful_tilt_ids=successful_tilt_ids,
        )
        if rollup.planned == 0 and rollup.acquired == 0 and rollup.failed_exposure_areas == 0:
            continue
        strip = graphics.CompletionStrip(
            acquired=rollup.acquired,
            planned=rollup.planned,
            failed=rollup.failed_exposure_areas,
            width=bar_w,
        )
        label = Paragraph(_html_escape(sm.name), styles.SMALL_STYLE)
        failed_bits: list[str] = []
        if rollup.failed_exposure_areas:
            exposure_noun = "failed exposure area" if rollup.failed_exposure_areas == 1 else "failed exposure areas"
            failed_bits.append(
                f"{rollup.failed_exposure_areas} {exposure_noun}"
            )
        if rollup.failed_batch_groups:
            failed_bits.append(
                f"{rollup.failed_batch_groups} failed batch position group"
                f"{'s' if rollup.failed_batch_groups != 1 else ''}"
            )
        status_text = (
            f"{rollup.acquired} acquired / {rollup.planned} planned exposure areas"
        )
        if failed_bits:
            status_text += (
                "<br/><font color=\"#991b1b\">"
                + " &middot; ".join(failed_bits)
                + "</font>"
            )
        text = Paragraph(
            status_text,
            styles.SMALL_STYLE,
        )
        rows.append([label, strip, text])
        row_labels.append(sm.name or sm.id)

    if not rows:
        return Table(
            [[Paragraph("No search map completion data.", styles.SMALL_STYLE)]],
            colWidths=[PORTRAIT_USABLE_W],
        )

    label_w = _search_map_completion_label_width(row_labels)
    text_w = PORTRAIT_USABLE_W - label_w - bar_w
    table = Table(
        rows,
        colWidths=[label_w, bar_w, text_w],
        hAlign="LEFT",
    )
    table.setStyle(
        [
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("LEFTPADDING", (0, 0), (-1, -1), 0),
            ("RIGHTPADDING", (0, 0), (-1, -1), 0),
            ("RIGHTPADDING", (0, 0), (0, -1), 8),
            ("LEFTPADDING", (2, 0), (2, -1), 8),
            ("TOPPADDING", (0, 0), (-1, -1), 2),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
        ]
    )
    return table


def _search_map_completion_label_width(labels: Sequence[str]) -> float:
    """Compact label column width for search-map completion rows."""

    from reportlab.pdfbase.pdfmetrics import stringWidth

    if not labels:
        return 1.55 * inch
    longest = max(stringWidth(label, "Helvetica", 8) for label in labels)
    desired = longest + 0.18 * inch
    return max(1.55 * inch, min(desired, 2.6 * inch))


# ----------------------------------------------------------------- B. atlas


def _atlas_image_block(
    atlas: Atlas,
    *,
    label: str,
    generation_warnings: list[str],
    scope_overviews: Sequence[Overview] = (),
    scope_search_maps: Sequence[SearchMap] = (),
    scope_batch_positions: Sequence[BatchPosition] = (),
    scope_tilt_series: Sequence[TiltSeries] = (),
    failed_tilt_ids: frozenset[str] = frozenset(),
) -> list:
    """Embed the atlas image with overlays scoped to one sample."""

    embed = _embeddable_image_path(atlas.image_path)
    if embed is None:
        # Atlas exists but has no embeddable image — emit a small note
        # rather than skipping silently. The trailing "(path)" hint is
        # only included when there *is* a known path, otherwise we'd
        # render the awkward "atlas image not available (not
        # available)" string the user reported.
        if atlas.image_path is not None:
            text = (
                f"{_html_escape(label)} — atlas image not available "
                f"({_html_escape(str(atlas.image_path))})."
            )
        else:
            text = f"{_html_escape(label)} — atlas image not available."
        return [Paragraph(text, styles.NOTE_STYLE)]

    try:
        ctx_markers = MarkerContext(
            overviews=tuple(scope_overviews),
            search_maps=tuple(scope_search_maps),
            batch_positions=tuple(scope_batch_positions),
            tilt_series=tuple(scope_tilt_series),
            failed_tilt_ids=failed_tilt_ids,
        )
        raw_markers = atlas_lod_markers(
            atlas,
            ctx_markers,
            include_detail_markers=False,
        )
    except Exception as exc:  # pragma: no cover — defensive
        LOGGER.debug("Atlas marker computation failed: %s", exc)
        raw_markers = []
    native, markers = _resolve_overlay_geometry(atlas, raw_markers, embed)

    max_w = PORTRAIT_USABLE_W
    max_h = PORTRAIT_USABLE_H * 0.55  # leave room for caption + surrounding sections

    overlayed = graphics.OverlayedImage(
        embed,
        native_size=native,
        markers=markers,
        max_width=max_w,
        max_height=max_h,
    )
    caption = (
        f"{_html_escape(label)}. Native size: {native[0]} × {native[1]} px. "
        f"Overlays: "
        f"{len(scope_overviews)} overview marker(s), "
        f"{len(scope_search_maps)} search map marker(s), "
        f"{len(scope_batch_positions)} batch position marker(s)."
    )
    return [
        graphics.keep_with_caption(
            [overlayed, Spacer(1, 6), graphics.AtlasMarkerPrintLegend()],
            caption,
        )
    ]


def _fallback_session_atlas(ctx: _SessionContext, sample: Sample) -> Atlas | None:
    """Use the session-level atlas if the sample doesn't have its own.

    Multi-grid sessions sometimes hold a single shared atlas at the
    session level rather than on each sample. The Atlas section in the
    sample block falls back to that so users still get a visual anchor.
    """

    if sample.atlas is not None:
        return sample.atlas
    if ctx.session.atlas is not None:
        return ctx.session.atlas
    return None


# ----------------------------------------------------------------- C. overviews


def _overview_grid(
    overviews: Sequence[Overview],
    sample: Sample,
    generation_warnings: list[str],
    failed_tilt_ids: frozenset[str] = frozenset(),
) -> list:
    flowables: list = []
    cells_per_row = 2
    cell_w = (PORTRAIT_USABLE_W - 12) / cells_per_row  # 12pt gutter between
    cell_h = PORTRAIT_USABLE_H * 0.32

    pending_row: list = []
    for overview in overviews:
        embed = _embeddable_image_path(overview.image_path)
        if embed is None:
            continue

        try:
            raw_markers = markers_for_object(
                overview,
                context=MarkerContext(
                    overviews=(overview,),
                    search_maps=tuple(sample.search_maps),
                    batch_positions=tuple(sample.batch_positions),
                    failed_tilt_ids=failed_tilt_ids,
                ),
            )
        except Exception:
            raw_markers = []
        native, markers = _resolve_overlay_geometry(overview, raw_markers, embed)

        image = graphics.OverlayedImage(
            embed,
            native_size=native,
            markers=markers,
            max_width=cell_w,
            max_height=cell_h,
        )
        caption = (
            f"<b>{_html_escape(overview.name)}</b><br/>"
            f"<font size=\"7\" color=\"#475569\">"
            f"{native[0]} × {native[1]} px &middot; "
            f"linked search maps: {len(overview.linked_search_map_ids)}"
            f" &middot; numeric labels: batch position/exposure area IDs"
            f"</font>"
        )
        # Inner Table (not KeepTogether) so the outer 2-cell row can
        # measure the cell height correctly.
        cell = Table(
            [[image], [Paragraph(caption, styles.SMALL_STYLE)]],
            colWidths=[cell_w],
            style=[
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 0),
                ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                ("TOPPADDING", (0, 0), (-1, 0), 0),
                ("BOTTOMPADDING", (0, 0), (-1, 0), 2),
                ("TOPPADDING", (0, 1), (-1, 1), 0),
                ("BOTTOMPADDING", (0, 1), (-1, 1), 0),
            ],
        )
        pending_row.append(cell)
        if len(pending_row) == cells_per_row:
            flowables.append(_overview_row(pending_row, cell_w))
            flowables.append(Spacer(1, 8))
            pending_row = []
    if pending_row:
        # Pad the last row so columns line up.
        while len(pending_row) < cells_per_row:
            pending_row.append(Paragraph("", styles.SMALL_STYLE))
        flowables.append(_overview_row(pending_row, cell_w))

    if not flowables:
        flowables.append(
            Paragraph(
                f"No embeddable overview images for {_html_escape(sample.name)}.",
                styles.NOTE_STYLE,
            )
        )
    return flowables


def _overview_row(cells: list, cell_w: float) -> Table:
    n = len(cells)
    return Table(
        [cells],
        colWidths=[cell_w] * n,
        hAlign="LEFT",
        style=[
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("LEFTPADDING", (0, 0), (-1, -1), 0),
            ("RIGHTPADDING", (0, 0), (-1, -1), 12),
            ("TOPPADDING", (0, 0), (-1, -1), 0),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
        ],
    )


# ----------------------------------------------------------------- D. search maps


def _search_map_blocks(
    search_maps: Sequence[SearchMap],
    sample: Sample,
    ctx: _SessionContext,
    generation_warnings: list[str],
) -> list:
    flowables: list = []
    for index, sm in enumerate(search_maps):
        embed = _embeddable_image_path(sm.image_path or sm.mrc_path)
        if embed is None:
            source = sm.image_path or sm.mrc_path
            generation_warnings.append(
                f"Search map {sm.name or sm.id} has no embeddable JPG/PNG/GIF image for the PDF report."
            )
            note = (
                f"<b>{_html_escape(sm.name)}</b> — no embeddable image "
                f"({_html_escape(str(source))})."
                if source is not None
                else f"<b>{_html_escape(sm.name)}</b> — no embeddable image."
            )
            flowables.append(Paragraph(note, styles.NOTE_STYLE))
            continue

        try:
            failed_tilt_ids = frozenset(
                v_id
                for v_id, v in ctx.validations.items()
                if v.status == STATUS_FAILED
            )
            raw_markers = markers_for_object(
                sm,
                context=MarkerContext(
                    search_maps=(sm,),
                    batch_positions=tuple(sample.batch_positions),
                    tilt_series=tuple(sample.tilt_series),
                    failed_tilt_ids=failed_tilt_ids,
                ),
                include_unresolved=False,
            )
        except Exception:
            raw_markers = []
        native, markers = _resolve_overlay_geometry(sm, raw_markers, embed, generation_warnings)
        stage_inferred_failed_count = sum(
            1
            for marker in raw_markers
            if marker.marker_type == MarkerType.TILT_SERIES
            and marker.metadata.get("inferred_from_stage") is True
        )

        # Aim for one search map per page when there are many overlays;
        # otherwise allow the layout engine to flow them.
        max_w = PORTRAIT_USABLE_W
        max_h = PORTRAIT_USABLE_H * (0.55 if len(markers) > 30 else 0.45)
        overlayed = graphics.OverlayedImage(
            embed,
            native_size=native,
            markers=markers,
            max_width=max_w,
            max_height=max_h,
        )

        # Per-search-map metadata block.
        linked_batches = _linked_batch_positions_for_search_map(sm, sample.batch_positions)
        rollup = search_map_acquisition_rollup(
            sm,
            search_maps,
            sample.batch_positions,
            sample.tilt_series,
            ctx.validations,
        )
        linked_tilt = rollup.tilt_series_ids
        n_complete = sum(
            1 for tid in linked_tilt
            if (v := ctx.validations.get(tid)) and v.status == STATUS_COMPLETE
        )
        n_incomplete = sum(
            1 for tid in linked_tilt
            if (v := ctx.validations.get(tid)) and v.status == STATUS_INCOMPLETE
        )
        n_failed = sum(
            1 for tid in linked_tilt
            if (v := ctx.validations.get(tid)) and v.status == STATUS_FAILED
        )
        meta_rows: list[tuple[str, str]] = [
            ("Image", _safe_path_text(sm.image_path)),
            ("Image size", f"{native[0]} × {native[1]} px"),
            ("Linked batch positions", str(len(linked_batches))),
            (
                "Linked tilt series",
                f"{len(linked_tilt)} "
                f"({n_complete} complete, {n_incomplete} incomplete, {n_failed} failed)",
            ),
        ]
        if sm.warnings:
            wsum = summarise_warnings(sm.warnings)
            meta_rows.append(("Warnings", _short_warning_summary(wsum)))

        meta_table = _kv_table(meta_rows)
        caption = _search_map_caption(
            index + 1,
            sm,
            sample,
            native,
            linked_batches,
            generation_warnings,
            inferred_failed_tilt_count=max(stage_inferred_failed_count, rollup.failed_exposure_areas if rollup.failed_batch_groups else 0),
            inferred_failed_batch_count=rollup.failed_batch_groups,
        )

        # The image + caption stay together (KeepTogether), but the
        # metadata table flows after it in the natural document order.
        # Wrapping image+caption+meta in a single KeepTogether tends to
        # blow past one page on dense search maps; letting the metadata
        # spill onto the next page is preferable to a layout overflow.
        flowables.append(
            KeepTogether(
                [
                    overlayed,
                    Spacer(1, 2),
                    Paragraph(caption, styles.CAPTION_STYLE),
                ]
            )
        )
        flowables.append(meta_table)
        flowables.append(Spacer(1, 12))
    return flowables


def _linked_batch_positions_for_search_map(
    search_map: SearchMap,
    batch_positions: Sequence[BatchPosition],
) -> list[BatchPosition]:
    """Resolve search-map batch links from the parsed metadata model.

    Tomography sessions can carry the association on either side of the
    relationship: ``SearchMap.linked_batch_position_ids`` or
    ``BatchPosition.linked_search_map_id``. Use only those explicit parsed
    links here; do not infer report captions from ordering.
    """

    linked = search_map_batch_positions(search_map, batch_positions)
    linked.sort(key=lambda batch: _batch_position_caption_sort_key(batch))
    return linked


def _linked_tilt_series_ids_for_search_map(
    search_map: SearchMap,
    linked_batches: Sequence[BatchPosition],
) -> list[str]:
    linked: list[str] = []
    seen: set[str] = set()
    for tilt_id in search_map.linked_tilt_series_ids or []:
        if tilt_id in seen:
            continue
        linked.append(tilt_id)
        seen.add(tilt_id)
    for batch in linked_batches:
        for tilt_id in batch.linked_tilt_series_ids or []:
            if tilt_id in seen:
                continue
            linked.append(tilt_id)
            seen.add(tilt_id)
    return linked


def _search_map_caption(
    figure_number: int,
    search_map: SearchMap,
    sample: Sample,
    native_size: tuple[int, int],
    linked_batches: Sequence[BatchPosition],
    generation_warnings: list[str],
    inferred_failed_tilt_count: int = 0,
    inferred_failed_batch_count: int = 0,
) -> str:
    overlay_clause = _search_map_overlay_caption_clause(
        search_map,
        sample,
        linked_batches,
        generation_warnings,
        inferred_failed_tilt_count=inferred_failed_tilt_count,
        inferred_failed_batch_count=inferred_failed_batch_count,
    )
    return (
        f"Figure {figure_number}. Search map {_html_escape(search_map.name)} for "
        f"data collection {_html_escape(sample.name)}. {_html_escape(overlay_clause)} "
        f"Native size: {native_size[0]} × {native_size[1]} px."
    )


def _search_map_overlay_caption_clause(
    search_map: SearchMap,
    sample: Sample,
    linked_batches: Sequence[BatchPosition],
    generation_warnings: list[str],
    inferred_failed_tilt_count: int = 0,
    inferred_failed_batch_count: int = 0,
) -> str:
    labels = [
        _batch_position_caption_label(batch)
        for batch in sorted(linked_batches, key=_batch_position_caption_sort_key)
    ]
    labels = _dedupe_preserving_order(labels)
    if len(labels) == 1:
        return (
            f"Overlays show batch position {labels[0]} and target areas "
            "(exposure area, focus, tracking). Numeric labels identify batch positions and exposure areas."
        )
    if len(labels) > 1:
        return (
            f"Overlays show batch positions {_format_natural_list(labels)} with "
            "target areas (exposure area, focus, tracking). Numeric labels identify batch positions and exposure areas."
        )
    if inferred_failed_tilt_count:
        exposure_noun = "exposure area" if inferred_failed_tilt_count == 1 else "exposure areas"
        batch_text = ""
        if inferred_failed_batch_count:
            batch_noun = "batch position group" if inferred_failed_batch_count == 1 else "batch position groups"
            batch_text = f" across {inferred_failed_batch_count} inferred failed {batch_noun}"
        return (
            f"Overlays show {inferred_failed_tilt_count} inferred failed {exposure_noun}"
            f"{batch_text}. Numeric labels identify available batch positions and exposure areas."
        )

    warning = (
        "Search map caption could not resolve associated batch positions for "
        f"{search_map.name or search_map.id} in data collection {sample.name}; "
        "using generic overlay caption."
    )
    LOGGER.warning(warning)
    generation_warnings.append(warning)
    return (
        "Overlays show batch positions and target areas (exposure area, focus, tracking). "
        "Numeric labels identify batch positions and exposure areas."
    )


def _batch_position_caption_label(batch: BatchPosition) -> str:
    label = batch.name or batch.id or "unknown"
    trailing_number = re.search(r"(\d+)(?!.*\d)", label)
    if trailing_number:
        return str(int(trailing_number.group(1)))
    return label


def _batch_position_caption_sort_key(batch: BatchPosition) -> tuple[int, tuple[int | str, ...]]:
    label = _batch_position_caption_label(batch)
    if label.isdigit():
        return (0, (int(label),))
    parts = re.split(r"(\d+)", label.casefold())
    return (1, tuple(int(part) if part.isdigit() else part for part in parts))


def _format_natural_list(values: Sequence[str]) -> str:
    if not values:
        return ""
    if len(values) == 1:
        return values[0]
    if len(values) == 2:
        return f"{values[0]} and {values[1]}"
    return f"{', '.join(values[:-1])} and {values[-1]}"


def _dedupe_preserving_order(values: Iterable[str]) -> list[str]:
    unique: list[str] = []
    seen: set[str] = set()
    for value in values:
        key = value.casefold()
        if key in seen:
            continue
        unique.append(value)
        seen.add(key)
    return unique


# ----------------------------------------------------------------- E. acquisition


def _acquisition_settings_table(
    tilt_series: Sequence[TiltSeries],
    batch_positions: Sequence[BatchPosition] = (),
    validations: Mapping[str, TiltSeriesValidation] | None = None,
) -> Table:
    """Compact two-column key/value summary of acquisition parameters."""

    validation_lookup = _validation_lookup(tilt_series, validations)
    pixel_sizes = sorted({t.pixel_size for t in tilt_series if t.pixel_size is not None})
    original = sorted({t.original_pixel_size for t in tilt_series if t.original_pixel_size is not None})
    binnings = sorted({t.binning for t in tilt_series if t.binning is not None})
    target_def = [t.target_defocus for t in tilt_series if t.target_defocus is not None]

    tilt_ranges = [t.tilt_range for t in tilt_series if t.tilt_range is not None]
    tilt_lo = min((r[0] for r in tilt_ranges), default=None)
    tilt_hi = max((r[1] for r in tilt_ranges), default=None)

    increments = _collect_increments(tilt_series)
    expected = _collect_expected_counts(tilt_series, validation_lookup)
    observed = _collect_observed_counts(tilt_series, validation_lookup)

    magnifications = sorted({
        v
        for t in tilt_series
        for v in _section_floats(t, "Magnification")
    })
    exposures = sorted({
        v
        for t in tilt_series
        for v in _section_floats(t, "ExposureTime")
    })
    detectors = sorted({
        v
        for t in tilt_series
        for v in _section_strings(t, "CameraName")
    })
    duration = _total_acquisition_duration(tilt_series)
    detector_text = _report_acquisition_setting(
        batch_positions,
        "Detector",
        fallback=detectors,
    )

    rows: list[tuple[str, str]] = [
        ("Pixel size", _format_pixel_size(pixel_sizes, ANGSTROM_PER_PIXEL)),
        ("Original pixel", _format_pixel_size(original, ANGSTROM_PER_PIXEL) if original else "n/a"),
        ("Binning", _format_set(binnings, suffix="×")),
        ("Magnification", _format_set([f"{v:g}" for v in magnifications])),
        ("Target defocus", format_target_defocus_values(target_def)),
        (
            "Tilt range",
            f"{tilt_lo:g}{DEGREE} to {tilt_hi:g}{DEGREE}"
            if tilt_lo is not None and tilt_hi is not None
            else "n/a",
        ),
        ("Tilt increment", _format_set([f"{v:g}{DEGREE}" for v in increments])),
        ("Expected images", _format_count_set(expected)),
        ("Observed images", _format_count_set(observed)),
        ("Exposure time", _format_set([f"{v:g} s" for v in exposures])),
        ("Detector", detector_text),
        (SEARCH_SPOT_LABEL, _report_acquisition_setting(batch_positions, SEARCH_SPOT_LABEL)),
        (ACQUISITION_SPOT_LABEL, _report_spot_size(tilt_series, batch_positions)),
        ("Probe mode", _report_acquisition_setting(batch_positions, "Probe mode")),
        ("Acquisition duration", duration),
    ]

    return _kv_table(rows, key_width=1.5 * inch)


# ----------------------------------------------------------------- F. tilt-series table


# The "Tilt series" column auto-sizes to fit the longest tilt name
# in the section. Any width it leaves on the table gets distributed
# across the other columns proportionally to their preferred sizes,
# so the table always exactly fills ``PORTRAIT_USABLE_W``.
#
# Increment and Notes used to live here but the user reads them off
# the GUI context panel, so the report keeps the columns the printed
# table needs and frees the saved width for the remaining ones.
_TILT_TABLE_HEADERS: tuple[str, ...] = (
    "Tilt series",
    "Status",
    "Images",
    "Expected",
    "Tilt range",
    "Pixel size",
    "Target def",
    "Defocus quality",
)
_TILT_TABLE_PREFERRED_WIDTHS: dict[str, float] = {
    "Status": 0.75 * inch,
    "Images": 0.55 * inch,
    "Expected": 0.65 * inch,
    "Tilt range": 1.05 * inch,
    "Pixel size": 0.75 * inch,
    "Target def": 0.7 * inch,
    "Defocus quality": 1.55 * inch,
}
_TILT_NAME_MIN_W = 0.8 * inch
_TILT_NAME_MAX_W = 1.5 * inch


def _compute_tilt_table_widths(tilt_series: Sequence[TiltSeries]) -> list[float]:
    """Return per-column widths summing to ``PORTRAIT_USABLE_W``.

    The first column shrinks to fit the longest tilt name (so a
    sample of short names like ``vellio_1_2`` doesn't reserve 1.5
    inches). The remaining width is distributed across the other
    columns proportionally to their preferred sizes — that keeps the
    visual emphasis on the wider columns (Tilt range / Defocus
    quality) and avoids any column collapsing too narrow.
    """

    from reportlab.pdfbase.pdfmetrics import stringWidth

    body_font = ("Helvetica", 8.5)
    header_font = ("Helvetica-Bold", 9)
    longest_name = max(
        (stringWidth(t.name or "", *body_font) for t in tilt_series),
        default=0.0,
    )
    header_w = stringWidth("Tilt series", *header_font)
    pad = 8.0  # cell padding (left + right) at table-style defaults
    desired_name_w = max(longest_name, header_w) + pad
    name_w = max(_TILT_NAME_MIN_W, min(desired_name_w, _TILT_NAME_MAX_W))

    remaining = PORTRAIT_USABLE_W - name_w
    preferred_total = sum(_TILT_TABLE_PREFERRED_WIDTHS.values())
    scale = remaining / preferred_total if preferred_total > 0 else 1.0

    widths: list[float] = []
    for label in _TILT_TABLE_HEADERS:
        if label == "Tilt series":
            widths.append(name_w)
        else:
            widths.append(_TILT_TABLE_PREFERRED_WIDTHS[label] * scale)
    return widths


def _tilt_series_table(
    tilt_series: Sequence[TiltSeries],
    ctx: _SessionContext,
) -> list:
    """Status-led table of tilt series with quality notes."""

    flowables: list = []
    header_row = [Paragraph(f"<b>{label}</b>", styles.SMALL_STYLE) for label in _TILT_TABLE_HEADERS]

    rows: list[list] = []
    for tilt in tilt_series:
        validation = ctx.validations.get(tilt.id)
        actual = validation.actual_count if validation else len(tilt.sections)
        expected = validation.expected_count if validation else None
        status = validation.status if validation else STATUS_UNKNOWN

        tilt_range_text = "n/a"
        if validation and validation.min_tilt is not None and validation.max_tilt is not None:
            tilt_range_text = (
                f"{validation.min_tilt:g}{DEGREE} to "
                f"{validation.max_tilt:g}{DEGREE}"
            )
        elif tilt.tilt_range is not None:
            tilt_range_text = (
                f"{tilt.tilt_range[0]:g}{DEGREE} to "
                f"{tilt.tilt_range[1]:g}{DEGREE}"
            )

        pixel = (
            f"{tilt.pixel_size:g} {ANGSTROM_PER_PIXEL}"
            if tilt.pixel_size is not None
            else "n/a"
        )
        target_def = format_target_defocus_values([tilt.target_defocus])
        defocus_quality = _defocus_quality(tilt)

        badge = graphics.StatusBadge(
            _status_label(status),
            status,
            width=0.62 * inch,
            height=12,
        )
        rows.append([
            Paragraph(_html_escape(tilt.name), styles.SMALL_STYLE),
            badge,
            Paragraph(str(actual), styles.SMALL_STYLE),
            Paragraph(str(expected) if expected is not None else "—", styles.SMALL_STYLE),
            Paragraph(tilt_range_text, styles.SMALL_STYLE),
            Paragraph(pixel, styles.SMALL_STYLE),
            Paragraph(target_def, styles.SMALL_STYLE),
            Paragraph(defocus_quality, styles.SMALL_STYLE),
        ])

    table = Table(
        [header_row, *rows],
        colWidths=_compute_tilt_table_widths(tilt_series),
        style=styles.DATA_TABLE_STYLE,
        repeatRows=1,
        hAlign="LEFT",
    )
    flowables.append(table)
    return flowables


# =============================================================================
# Small helpers
# =============================================================================


def _kv_table(rows: Sequence[tuple[str, str]], *, key_width: float = 1.4 * inch) -> Table:
    return Table(
        [
            [
                Paragraph(_html_escape(k), styles.SMALL_STYLE),
                Paragraph(_html_escape(v), styles.BODY_STYLE),
            ]
            for k, v in rows
        ],
        colWidths=[key_width, PORTRAIT_USABLE_W - key_width],
        style=styles.KEY_VALUE_TABLE_STYLE,
        hAlign="LEFT",
    )


def _resolve_overlay_geometry(
    source: Atlas | Overview | SearchMap,
    markers: Sequence[ImageMarker],
    embed_path: Path,
    generation_warnings: list[str] | None = None,
) -> tuple[tuple[int, int], list[ImageMarker]]:
    """Compute ``(native_size, projected_markers)`` for an overlay panel.

    Mirrors what the GUI's ``_project_marker`` does at render time:
    use the embedded image's actual pixel dimensions as the canvas and
    project every marker from its ``metadata['image_size']`` space
    into that canvas. Markers and image therefore share one
    coordinate system before the OverlayedImage flowable scales them
    by ``draw_size / native_size``.

    Falls back gracefully when Pillow can't read the JPG: we use
    ``image_size_for_markers`` (XML > MRC > cached) as the canvas
    and skip the projection step (markers already live in that
    coordinate system, so projection would be a no-op).
    """

    jpg_size = graphics.native_image_size(embed_path)
    if jpg_size is not None and jpg_size[0] > 0 and jpg_size[1] > 0:
        projected = [project_marker_to_size(m, jpg_size) for m in markers]
        return jpg_size, projected
    if generation_warnings is not None:
        generation_warnings.append(
            f"Could not read embedded raster image size for {embed_path}; using metadata dimensions for PDF overlays."
        )
    fallback = image_size_for_markers(source) or (1000, 1000)
    if fallback[0] <= 0 or fallback[1] <= 0:
        fallback = (1000, 1000)
    return fallback, list(markers)


def _embeddable_image_path(path: Path | None) -> Path | None:
    """Return ``path`` (or a sibling jpg/png) if it can be embedded.

    Returns ``None`` when neither the original path nor any sibling
    rasterised version exists. Reportlab can only embed jpg/png/gif
    natively; mrc files require the Qt-bound preview pipeline so we
    skip them rather than attempt rendering inside the report.
    """

    if path is None:
        return None
    p = Path(path)
    if not p.exists():
        # Try a sibling rasterised version (Tomo5 sometimes stores
        # both ``Atlas_1.mrc`` and ``Atlas_1.jpg``).
        for suffix in _EMBEDDABLE_IMAGE_SUFFIXES:
            sibling = p.with_suffix(suffix)
            if sibling.exists():
                return sibling
        return None
    if p.suffix.lower() in _EMBEDDABLE_IMAGE_SUFFIXES:
        return p
    for suffix in _EMBEDDABLE_IMAGE_SUFFIXES:
        sibling = p.with_suffix(suffix)
        if sibling.exists():
            return sibling
    return None


def _safe_path_text(path: Path | None) -> str:
    if path is None:
        return "not available"
    return str(path)


def _validation_to_tile_status(status: str) -> str:
    if status == STATUS_COMPLETE:
        return "complete"
    if status == STATUS_INCOMPLETE:
        return "incomplete"
    if status == STATUS_FAILED:
        return "failed"
    return "unknown"


def _status_label(status: str) -> str:
    return {
        STATUS_COMPLETE: "Complete",
        STATUS_INCOMPLETE: "Partial",
        STATUS_FAILED: "Failed",
        STATUS_UNKNOWN: "Unavailable",
    }.get(status, status.title())


def _short_warning_summary(rows: Sequence[WarningSummary]) -> str:
    parts = [f"{r.count}× {r.category}" for r in rows[:3]]
    if len(rows) > 3:
        parts.append(f"+{len(rows) - 3} more")
    return ", ".join(parts) if parts else "none"


def _report_acquisition_setting(
    batch_positions: Sequence[BatchPosition],
    label: str,
    *,
    fallback: Sequence[str] = (),
) -> str:
    text = summarise_acquisition_setting(
        [batch.metadata for batch in batch_positions],
        label,
        missing="n/a",
    )
    if text != "n/a":
        return text
    return _format_set([str(value) for value in fallback]) if fallback else "n/a"


def _report_spot_size(
    tilt_series: Sequence[TiltSeries],
    batch_positions: Sequence[BatchPosition],
) -> str:
    # MDOC records the spot size used for the actual tilt-series acquisition.
    # Prefer it whenever present; Batch/search.xml can also contain search,
    # tracking, or focus spot indices that should not override MDOC.
    tilt_text = summarise_acquisition_setting(
        [tilt.metadata for tilt in tilt_series],
        ACQUISITION_SPOT_LABEL,
        missing="n/a",
    )
    if tilt_text != "n/a":
        return tilt_text
    legacy_tilt_text = summarise_acquisition_setting(
        [tilt.metadata for tilt in tilt_series],
        LEGACY_SPOT_LABEL,
        missing="n/a",
    )
    if legacy_tilt_text != "n/a":
        return legacy_tilt_text
    return _report_acquisition_setting(batch_positions, ACQUISITION_SPOT_LABEL)


def _validation_lookup(
    tilt_series: Sequence[TiltSeries],
    validations: Mapping[str, TiltSeriesValidation] | None = None,
) -> dict[str, TiltSeriesValidation]:
    if validations is None:
        return {
            validation.tilt_series_id: validation
            for validation in validate_session_tilt_series(tilt_series)
        }
    return {
        tilt.id: validation
        for tilt in tilt_series
        if (validation := validations.get(tilt.id)) is not None
    }


def _format_set(values: Sequence, *, suffix: str = "") -> str:
    if not values:
        return "n/a"
    if len(values) == 1:
        return f"{values[0]}{suffix}"
    return f"{values[0]}{suffix} – {values[-1]}{suffix}"


def _format_count_set(values: Sequence[int]) -> str:
    if not values:
        return "n/a"
    unique = sorted(set(values))
    if len(unique) == 1:
        return str(unique[0])
    if len(unique) <= 4:
        return ", ".join(str(value) for value in unique)
    return f"Mixed ({len(unique)} counts)"


def _format_pixel_size(values: Sequence[float], unit: str) -> str:
    if not values:
        return "n/a"
    if len(values) == 1:
        return f"{values[0]:g} {unit}"
    return f"{values[0]:g} – {values[-1]:g} {unit}"


def _section_floats(tilt: TiltSeries, key: str) -> list[float]:
    out: list[float] = []
    for section in tilt.sections:
        v = section.metadata.get(key)
        if isinstance(v, (int, float)):
            out.append(float(v))
    return out


def _section_strings(tilt: TiltSeries, key: str) -> list[str]:
    out: list[str] = []
    for section in tilt.sections:
        v = section.metadata.get(key)
        if isinstance(v, str) and v.strip():
            out.append(v.strip())
    return out


def _collect_increments(tilt_series: Sequence[TiltSeries]) -> list[float]:
    seen: set[float] = set()
    for tilt in tilt_series:
        increments = []
        angles = sorted(_section_floats(tilt, "TiltAngle"))
        for a, b in zip(angles, angles[1:]):
            delta = round(abs(b - a), 2)
            if delta > 0:
                increments.append(delta)
        if increments:
            seen.add(round(median(increments), 2))
    return sorted(seen)


def _collect_expected_counts(
    tilt_series: Sequence[TiltSeries],
    validations: Mapping[str, TiltSeriesValidation],
) -> list[int]:
    seen: set[int] = set()
    for tilt in tilt_series:
        validation = validations.get(tilt.id)
        if validation is not None and validation.expected_count is not None and validation.expected_count > 0:
            seen.add(int(validation.expected_count))
    return sorted(seen)


def _collect_observed_counts(
    tilt_series: Sequence[TiltSeries],
    validations: Mapping[str, TiltSeriesValidation],
) -> list[int]:
    seen: set[int] = set()
    for tilt in tilt_series:
        validation = validations.get(tilt.id)
        if validation is not None:
            seen.add(int(validation.actual_count))
        elif tilt.sections:
            seen.add(len(tilt.sections))
        elif tilt.tilt_count and tilt.tilt_count > 0:
            seen.add(int(tilt.tilt_count))
    return sorted(seen)


def _total_acquisition_duration(tilt_series: Sequence[TiltSeries]) -> str:
    starts: list[datetime] = []
    ends: list[datetime] = []
    for tilt in tilt_series:
        s = _parse_dt(tilt.acquisition_time_start)
        e = _parse_dt(tilt.acquisition_time_end)
        if s is not None:
            starts.append(s)
        if e is not None:
            ends.append(e)
    if not starts or not ends:
        return "n/a"
    seconds = max((max(ends) - min(starts)).total_seconds(), 0)
    return _format_duration(seconds)


def _parse_dt(value: str | None) -> datetime | None:
    return parse_datetime(value)


def _format_duration(seconds: float) -> str:
    seconds = int(round(seconds))
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}h {m}m"
    if m:
        return f"{m}m {s}s"
    return f"{s}s"


def _defocus_quality(tilt: TiltSeries) -> str:
    """Same quality measure shown in the GUI context panel."""

    values = _section_floats(tilt, "Defocus")
    if not values:
        return "—"
    med = median(values)
    lo = min(values)
    hi = max(values)
    spread = hi - lo
    text = f"med {med:+.2f} µm, range {lo:+.2f} → {hi:+.2f}"
    if abs(spread) > 1.5 and len(values) >= 5:
        text += " · wide drift"
    return text


def _count_overviews(session: Session) -> int:
    return len(session.overviews) + sum(len(s.overviews) for s in session.samples)


def _count_searchmaps(session: Session) -> int:
    return len(session.search_maps) + sum(len(s.search_maps) for s in session.samples)


def _count_tilt_series(session: Session) -> int:
    return len(session.tilt_series) + sum(len(s.tilt_series) for s in session.samples)


def _session_acquisition_window(ctx: _SessionContext) -> str | None:
    start = ctx.dashboard.acquisition_start
    end = ctx.dashboard.acquisition_end
    if start and end:
        return f"{start} → {end}"
    if start:
        return f"from {start}"
    return None


def _report_title(sessions: Sequence[Session]) -> str:
    if len(sessions) == 1:
        return sessions[0].name or "Tomography Session Report"
    names = ", ".join(s.name for s in sessions if s.name)
    return f"Tomography Session Report ({names})" if names else "Tomography Session Report"


def _footer_label(sessions: Sequence[Session], *, project_title: str | None = None) -> str:
    """Footer used on every page — the project/session label."""

    if not sessions:
        return "Tomography Session Browser"
    if project_title:
        return f"{project_title} · Tomography Session Browser"
    first = sessions[0].name or "Tomography Session"
    if len(sessions) == 1:
        return f"{first} · Tomography Session Browser"
    return f"{first} (+{len(sessions) - 1} more) · Tomography Session Browser"


def _html_escape(text: str | None) -> str:
    if text is None:
        return ""
    return (
        str(text)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


__all__ = ["ProjectReportGroup", "ReportResult", "build_session_report"]


# Imports retained for type clarity / grep targets.
_ = (Sample, BatchPosition, Overview, SearchMap, MarkerType, defaultdict, re, HARD_FAILURE_THRESHOLD, colors)
