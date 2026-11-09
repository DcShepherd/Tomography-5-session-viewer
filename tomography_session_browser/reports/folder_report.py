"""The folder report (D4 §5.6): a report with no acquisition selection.

A cover, then one section for each ticked processing folder with every one of
its rows. The blocks are the session report's (``reports/sections/tomograms``):
facts from the shared Summary presenter over the folder's ``section_input``,
recorded values, attention, tags and notes, and central slices. The table
names each resolved row's verified parent and, for a row that is not placed,
its reason. Nothing here selects rows: ``select_report_tomograms`` made every
row of a ticked folder a member.
"""
from __future__ import annotations

from collections import Counter
from datetime import datetime
from pathlib import Path

from reportlab.platypus import NextPageTemplate, PageBreak, Paragraph, Spacer

from tomography_session_browser.domain.display_names import count_phrase
from tomography_session_browser.presentation.dashboard.summary import summary_from_input
from tomography_session_browser.presentation.report_tomograms import (
    ReportRow, RowState, ScopedTomograms, TomogramSection, exclusion_reason, section_input, section_sources,
)
from tomography_session_browser.reports import graphics, styles
from tomography_session_browser.reports.formatting import footer_label
from tomography_session_browser.reports.layout import meta_row
from tomography_session_browser.reports.pdf_export import PORTRAIT_USABLE_W, ReportResult, write_pdf
from tomography_session_browser.reports.sections.slices import central_slices, cover_cards, numbered_tiles
from tomography_session_browser.reports.sections.tomograms import (
    attention, data_table, facts_row, labelled, recorded_values, tags_and_notes, versions_text,
)
from tomography_session_browser.reports.tomogram_slices import ReportSlices, slice_keywords
from tomography_session_browser.services.output_boundary import check_output_path
from tomography_session_browser.services.tomogram_links import COUNT_LABELS

NO_ACQUISITION = "The app had no acquisition session, so no tomogram has a link."
SNAPSHOT_NOTE = "Tomogram values are from the time when the report dialog opened."
NOT_LINKED = "Not linked"

#: Cover rows: each state once, in this order, with a short print label.
STATE_LABELS: dict[RowState, str] = {
    'placed': "Linked to a dataset",
    'nonunique_owner': "Linked, tilt series in more than one dataset",
    'unrepresented_owner': "Linked, tilt series in the session, not in a grid",
    'ambiguous': "More than one tilt series",
    'unconfirmed_name': "Same name, not confirmed",
    'declined_name': "Same name, declined by the reviewer",
    'revoked_name': "Same name, removed by the reviewer",
    'no_match': "No match",
    'incomplete': "Link data not complete",
}


def build_folder_report(scoped: ScopedTomograms, output_path: Path, *, title: str,
                        protected_roots: tuple[Path, ...] = (), slices: ReportSlices | None = None,
                        generation_warnings: list[str] | None = None) -> ReportResult:
    """Compose and publish a folder report."""
    roots = (*protected_roots, *(root.resolve() for source in scoped.sources for root in source.source.protected_roots))
    check_output_path(output_path, roots)
    styles.ensure_fonts()
    warnings = list(generation_warnings or ())
    generated_at = datetime.now()
    flowables: list = [NextPageTemplate("portrait"), *folder_cover(scoped, title, generated_at, slices)]
    for number, section in enumerate(scoped.sections, 1):
        flowables += [PageBreak(), *folder_section(section, scoped, (number, len(scoped.sections)), slices)]
    page_count = write_pdf(
        output_path, flowables, title=title,
        footer_label=footer_label((), project_title=title, generated_at=generated_at),
        protected_roots=roots,
        keywords=slice_keywords(numbered_tiles(scoped, slices)) if slices is not None else None)
    return ReportResult(path=output_path, page_count=page_count, warnings=warnings)


# ------------------------------------------------------------------ cover

def folder_cover(scoped: ScopedTomograms, title: str, generated_at: datetime, slices: ReportSlices | None) -> list:
    rows = scoped.members
    flowables: list = [
        Paragraph("FOLDER REPORT", styles.EYEBROW_STYLE),
        Spacer(1, 2),
        graphics.AnchoredParagraph(graphics.html_escape(title), styles.TITLE_STYLE, anchor="summary",
                                   outline="Summary", level=0),
        meta_row([("Folders", str(len(scoped.sources))), ("Tomograms", f"{len(rows):,}"),
                  ("Generated", generated_at.strftime("%Y-%m-%d %H:%M"))]),
        Spacer(1, 10),
    ]
    linked = sum(row.link.status == 'resolved' for row in rows)
    cards = [graphics.StatCardData("Tomograms", f"{len(rows):,}", [], [])]
    if scoped.acquisition_sessions:    # with none, the sentence below says it once
        cards.append(graphics.StatCardData("Linked", f"{linked:,}", [], []))
    if slices is not None:
        cards += cover_cards(slices)
    body: list = [graphics.stat_card_row(cards, total_width=PORTRAIT_USABLE_W - 2 * styles.CARD_PADDING, columns=4)]
    if not scoped.acquisition_sessions:
        body.append(Paragraph(graphics.html_escape(NO_ACQUISITION), styles.SMALL_STYLE))
    else:
        body.append(labelled("Acquisition sessions loaded:", f"{scoped.acquisition_sessions}."))
        counts = Counter(row.state for row in rows)
        body += [Spacer(1, 4), data_table(["Link state", "Tomograms"],
                                          [[label, f"{counts[state]:,}"] for state, label in STATE_LABELS.items()
                                           if counts[state]])]
    by_folder = Counter(row.tomogram.source_id for row in rows)
    body.append(labelled("Processing folders:", " · ".join(
        f"{source.label} ({source.source.pipeline}), {count_phrase(by_folder[source.source.id], 'tomogram')}"
        for source in scoped.sources) + "."))
    if scoped.folders_left_out:
        body.append(Paragraph(graphics.html_escape(
            f"The report does not include {count_phrase(scoped.folders_left_out, 'other loaded folder')}."),
            styles.SMALL_STYLE))
    # Every ticked folder is printed in full, so each one with unsaved edits qualifies.
    unsaved = [source.label for source in scoped.sources if source.tags.unsaved]
    if unsaved:
        body.append(labelled("Tags:", f"{', '.join(unsaved)} had tag changes that the reviewer did not save. "
                                       "The report shows them as they were on the screen."))
    body.append(Paragraph(graphics.html_escape(SNAPSHOT_NOTE), styles.MUTED_STYLE))
    flowables += [graphics.card(body, width=PORTRAIT_USABLE_W, title="Tomograms"), Spacer(1, styles.CARD_GAP),
                  contents(scoped)]
    return flowables


def contents(scoped: ScopedTomograms):
    page_w = 34.0
    cells = []
    for section in scoped.sections:
        source = section_sources(section, scoped)[0]
        detail = f"{source.source.pipeline} · {count_phrase(len(section.members), 'tomogram')}"
        cells.append([Paragraph(f"<b>{graphics.html_escape(section.title)}</b><br/>{graphics.html_escape(detail)}",
                                styles.SMALL_STYLE),
                      graphics.PageRef(section.anchor, width=page_w - 4)])
    inner = PORTRAIT_USABLE_W - 2 * styles.CARD_PADDING
    table = graphics.plain_table(cells, [inner - page_w, page_w])
    return graphics.card([table], width=PORTRAIT_USABLE_W, title="Contents",
                         badge=count_phrase(len(scoped.sections), "folder"))


# ------------------------------------------------------------------ folder section

def link_cell(row: ReportRow) -> str:
    if row.link.status != 'resolved':
        return NOT_LINKED
    return COUNT_LABELS.get(row.link.step, "") if row.link.step else ""


def folder_table(section: TomogramSection, scoped: ScopedTomograms):
    """Every row: its verified parent, its unique dataset, and the reason when it is not placed.

    With no acquisition loaded every row is "Not linked" for the one reason the
    cover gives, so the table has no parent, dataset or reason columns.
    """
    if not scoped.acquisition_sessions:
        return data_table(["Tomogram", "Link", "Versions"],
                          [[row.tomogram.name, NOT_LINKED, versions_text(row)] for row in section.members])
    return data_table(["Tomogram", "Tilt series", "Dataset", "Link", "Versions", "Note"],
                      [[row.tomogram.name, row.parent_label or "", row.dataset_label or "", link_cell(row),
                        versions_text(row), exclusion_reason(row)] for row in section.members])


def folder_section(section: TomogramSection, scoped: ScopedTomograms, position: tuple[int, int],
                   slices: ReportSlices | None) -> list:
    source = section_sources(section, scoped)[0]
    flowables: list = [
        Paragraph(graphics.html_escape(f"Folder {position[0]} of {position[1]}"), styles.EYEBROW_STYLE),
        Spacer(1, 2),
        graphics.AnchoredParagraph(graphics.html_escape(section.title), styles.SECTION_TITLE_STYLE,
                                   anchor=section.anchor, outline=section.title, level=0),
        meta_row([("Pipeline", source.source.pipeline), ("Tomograms", f"{len(section.members):,}")]),
        Spacer(1, 10),
    ]
    if not section.members:
        return [*flowables, Paragraph("This folder has no tomograms.", styles.SMALL_STYLE)]
    summary = summary_from_input(section_input(section, scoped))
    flowables += [facts_row(summary), Spacer(1, styles.CARD_GAP),
                  graphics.section_heading("Tomograms", anchor=f"{section.anchor}-tomograms",
                                           meta=count_phrase(len(section.members), "tomogram")),
                  folder_table(section, scoped), Spacer(1, 8), *recorded_values(section.members)]
    flagged = attention(section, scoped, summary)
    if flagged:
        flowables += [Spacer(1, 8), *flagged]
    tags = tags_and_notes(section, scoped)
    if tags:
        flowables += [Spacer(1, 8), *tags]
    if slices is not None:
        flowables += [Spacer(1, 8), *central_slices(section, slices)]
    return flowables
