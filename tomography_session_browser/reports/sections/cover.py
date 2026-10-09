"""Sections cover report components."""
from __future__ import annotations
from datetime import datetime
from typing import Sequence
from reportlab.platypus import Paragraph, Spacer, Table
from tomography_session_browser.domain.display_names import count_phrase
from tomography_session_browser.domain.enums import SessionKind
from tomography_session_browser.presentation.workspace import EntityKind
from tomography_session_browser.presentation.report_scope import ProjectReportGroup
from tomography_session_browser.reports import graphics, styles
from tomography_session_browser.reports.pdf_export import PORTRAIT_USABLE_W
from tomography_session_browser.reports.constants import CARD_INNER_W, PROJECT_BADGE_MEANINGS
from tomography_session_browser.reports.context import ReportSection, SessionContext
from tomography_session_browser.reports.formatting import acquisition_window_text, report_title, session_kind_label
from tomography_session_browser.reports.layout import meta_row, stat_card_data, stat_cards_block
from tomography_session_browser.reports.sections.contents import contents_card
from tomography_session_browser.reports.sections.totals import Totals, aggregate_totals, dashboard_tile_statuses, report_atlas_statuses
from tomography_session_browser.reports.sections.warnings import warning_summary_table


def cover_page(
    contexts: Sequence[SessionContext],
    *,
    project_title: str | None = None,
    project_groups: Sequence[ProjectReportGroup] = (),
    generated_at: datetime | None = None,
    sections: Sequence[ReportSection] = (),
) -> list:
    generated_at = generated_at or datetime.now()
    title = project_title or report_title([ctx.session for ctx in contexts])
    totals = aggregate_totals(contexts)

    # A title that would take more than two display lines steps down a size.
    title_style = (
        styles.TITLE_STYLE
        if styles.string_width(title, styles.SANS_SEMIBOLD, styles.TYPE_DISPLAY) <= 2 * PORTRAIT_USABLE_W
        else styles.SECTION_TITLE_STYLE
    )
    flowables: list = [
        Paragraph(graphics.html_escape(cover_eyebrow(contexts)), styles.EYEBROW_STYLE),
        Spacer(1, 2),
        graphics.AnchoredParagraph(graphics.html_escape(title), title_style, anchor="summary", outline="Summary", level=0),
        meta_row(cover_meta(contexts, generated_at)),
        Spacer(1, 12),
    ]

    flowables.extend(stat_cards_block(cover_stat_cards(contexts, totals)))
    flowables.append(Spacer(1, styles.CARD_GAP))

    # The Tilt series card already states the outcomes, and the contents
    # shows them per dataset, so the summary has no separate outcome chart.
    contents = contents_card(sections, width=PORTRAIT_USABLE_W)
    if contents is not None:
        flowables.append(contents)
        flowables.append(Spacer(1, styles.CARD_GAP))

    flowables.append(warning_summary_table(totals.warning_rows, severities=totals.severity_counts))

    if project_groups:
        flowables.append(Spacer(1, styles.CARD_GAP))
        flowables.append(project_groups_table(project_groups))

    # Say so when there is nothing to report rather than leaving empty cards.
    if not totals.has_collection_data and not totals.has_atlas_data:
        flowables.append(Spacer(1, 6))
        flowables.append(
            Paragraph(
                "Report limitations: the loaded session(s) contained no "
                "atlas, overview, search map, batch position or tilt series "
                "content. Most sections will be empty.",
                styles.NOTE_STYLE,
            )
        )
    return flowables


def cover_eyebrow(contexts: Sequence[SessionContext]) -> str:
    atlas = sum(1 for ctx in contexts if ctx.session.kind == SessionKind.ATLAS_SCREENING)
    datasets = sum(len(ctx.collection_samples) for ctx in contexts)
    if atlas and not datasets:
        return "Atlas screening report"
    if datasets and not atlas:
        return "Data collection report"
    return "Session report"


def cover_meta(contexts: Sequence[SessionContext], generated_at: datetime) -> list[tuple[str, str | None]]:
    datasets = sum(len(ctx.collection_samples) for ctx in contexts)
    items: list[tuple[str, str | None]] = []
    if len(contexts) == 1:
        items.append(("Kind", f"{session_kind_label(contexts[0].session.kind)} session"))
    else:
        atlas = sum(1 for ctx in contexts if ctx.session.kind == SessionKind.ATLAS_SCREENING)
        items.append(("Sessions", f"{len(contexts)} ({atlas} atlas, {len(contexts) - atlas} data collection)"))
    if datasets:
        items.append(("Datasets", f"{datasets:,}"))
    windows = [ctx for ctx in contexts if ctx.dashboard.acquisition_start]
    if len(windows) == 1:
        items.append(("Acquisition", acquisition_window_text(windows[0].dashboard)))
    items.append(("Generated", generated_at.strftime("%Y-%m-%d %H:%M")))
    return items


def project_groups_table(project_groups: Sequence[ProjectReportGroup]) -> Table:
    """Project groups as the project tree shows them: badge, name, sessions."""

    rows: list = []
    for index, group in enumerate(project_groups):
        name = f"<b>{graphics.html_escape(group.display_name)}</b>"
        if group.display_name != group.automatic_name:
            name += f' <font color="{styles.css_hex(styles.TEXT_MUTED)}">(auto: {graphics.html_escape(group.automatic_name)})</font>'
        original = ", ".join(name for name in group.session_names if name) or group.automatic_name
        detail = f"{PROJECT_BADGE_MEANINGS.get(group.kind, group.kind)} · {graphics.html_escape(original)}"
        line = graphics.plain_table(
            [[graphics.ProjectBadge(group.kind), [Paragraph(name, styles.BODY_STYLE), Paragraph(detail, styles.MUTED_STYLE)]]],
            [26, CARD_INNER_W - 26],
            valign="TOP",
            extra_style=[("TOPPADDING", (0, 0), (0, 0), 1.5)],
        )
        rows.append(line)
    return graphics.card(rows, width=PORTRAIT_USABLE_W, title="Project groups", badge=count_phrase(len(project_groups), "group"))


def cover_stat_cards(contexts: Sequence[SessionContext], totals: Totals) -> list[tuple[graphics.StatCardData, int]]:
    """Cover count cards in acquisition-hierarchy order.

    Values are the totals the cover has always used (the atlas count from
    :class:`AtlasCountModel`, the rest from the dashboard models); chips and
    bands are the dashboard cards' own tile statuses.
    """

    # One tile per distinct atlas image, as the dashboard's Atlases card
    # counts them; the card's number and its chips describe the same atlases.
    atlas_statuses = report_atlas_statuses(contexts)
    entries = [
        ("Atlases", len(atlas_statuses), atlas_statuses),
        ("Overviews", totals.overviews, dashboard_tile_statuses(contexts, EntityKind.OVERVIEW)),
        ("Search maps", totals.search_maps, dashboard_tile_statuses(contexts, EntityKind.SEARCH_MAP)),
        ("Batch positions", totals.batch_positions, dashboard_tile_statuses(contexts, EntityKind.BATCH_POSITION)),
        ("Tilt series", totals.tilt_series, dashboard_tile_statuses(contexts, EntityKind.TILT_SERIES)),
    ]
    return [(stat_card_data(label, value, statuses), value) for label, value, statuses in entries]

