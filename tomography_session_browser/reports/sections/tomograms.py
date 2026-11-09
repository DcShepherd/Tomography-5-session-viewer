"""The Tomograms blocks of a report (D4 §5.4): one section per dataset or folder, and the cover card.

Every block composes the accepted, already reduced ``ScopedTomograms``: the
facts come from the shared Summary presenter over the section's own input,
metric cells from ``metric_row``, attention from the Summary's own predicate.
Nothing here selects rows, chooses a unit or parses display text. Print text
is short descriptive STE and holds no screen instruction (F20).
"""
from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence

from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import inch
from reportlab.platypus import CondPageBreak, Paragraph, Spacer, Table

from tomography_session_browser.domain.display_names import count_phrase
from tomography_session_browser.presentation.dashboard.summary import ProcessingSummary, summary_from_input
from tomography_session_browser.presentation.dashboard.tag_summary import TagContent, number_text, tag_summary
from tomography_session_browser.presentation.dashboard.tomogram_summary_facts import outcome_facts
from tomography_session_browser.presentation.recorded_metrics import METRIC_COLUMNS, metric_number, metric_row
from tomography_session_browser.presentation.report_tomograms import (
    ReportRow, ScopedTomograms, TomogramSection, exclusion_reason, incomplete_note, print_warning,
    section_input, section_sources,
)
from tomography_session_browser.presentation.tomogram_calibration import tomogram_warnings
from tomography_session_browser.presentation.tomogram_versions import review_versions
from tomography_session_browser.reports import graphics, styles
from tomography_session_browser.reports.layout import longest_word, shrink_widths, spread_widths
from tomography_session_browser.reports.pdf_export import PORTRAIT_USABLE_W
from tomography_session_browser.reports.sections.slices import central_slices, cover_cards as slice_cover_cards
from tomography_session_browser.reports.tomogram_slices import ReportSlices
from tomography_session_browser.services.tomogram_links import COUNT_LABELS

CELL_PAD = 4.0
#: Tag columns in the table; the other tags print with each tomogram's note (print layout only).
TAG_COLUMNS = 4
TAG_KEY = ("An empty cell has no tag. 0 is a count of zero. Tagged is a tag without a count. "
           "A total adds all counts of one project, also counts that are not complete.")
OTHER_TAGS_KEY = f"The table shows the first {TAG_COLUMNS} tags. Other tags show below, with each tomogram."
#: A tomogram's heading in Other tags and notes stays on the page of its first line.
ENTRY_HEADING_STYLE = ParagraphStyle("ReportEntryHeading", parent=styles.SMALL_STYLE, keepWithNext=1)
#: Said once on the cover, so no tomogram block is read as current state.
SNAPSHOT_NOTE = ("Tomogram values are from the time when the report dialog opened. "
                 "The acquisition warning totals do not include these notes.")


# ------------------------------------------------------------------ tables

def _widths(header: Sequence[str], rows: Sequence[Sequence[str]], total: float = PORTRAIT_USABLE_W) -> list[float]:
    """Natural widths when they fit; otherwise long text wraps between words first."""
    pad = 2 * CELL_PAD
    size = styles.TABLE_CELL_STYLE.fontSize
    columns = list(zip(header, *rows, strict=True))
    natural = [max(styles.string_width(text, styles.SANS, size) for text in column[1:] or ("",)) + pad for column in columns]
    natural = [max(width, styles.string_width(label.upper(), styles.SANS_SEMIBOLD, styles.TYPE_CAPTION) + pad)
               for width, label in zip(natural, header, strict=True)]
    narrowest = [max(longest_word(column, styles.SANS, size), longest_word((label.upper(),), styles.SANS_SEMIBOLD,
                                                                           styles.TYPE_CAPTION)) + pad
                 for column, label in zip(columns, header, strict=True)]
    if sum(natural) <= total:
        return spread_widths(natural, total, [1.0] * len(natural))
    minimums = _fit_minimums([min(a, b) for a, b in zip(narrowest, natural, strict=True)], total)
    return shrink_widths(natural, minimums, total)


def _fit_minimums(minimums: Sequence[float], total: float) -> list[float]:
    """Cap the widest minimums at one level so all fit; only their very long words break (GR-04).

    Without this, one 120-character tag name scaled every column below its own
    longest word, so “Warp A” printed one letter per line.
    """
    if sum(minimums) <= total:
        return list(minimums)
    ordered, remaining = sorted(minimums), total
    for index, width in enumerate(ordered):
        share = remaining / (len(ordered) - index)
        if width > share:
            return [min(minimum, share) for minimum in minimums]
        remaining -= width
    return list(minimums)


def data_table(header: Sequence[str], rows: Sequence[Sequence[str]]) -> Table:
    """A plain text table with a repeating header, in the tilt table's style."""
    cells = [[Paragraph(graphics.html_escape(text), styles.TABLE_CELL_STYLE) for text in row] for row in rows]
    return Table(
        [[Paragraph(graphics.html_escape(label), styles.TABLE_HEADER_STYLE) for label in header], *cells],
        colWidths=_widths(header, rows), repeatRows=1, hAlign="LEFT",
        style=[
            ("BACKGROUND", (0, 0), (-1, 0), styles.SURFACE_ALT),
            ("LINEABOVE", (0, 0), (-1, 0), styles.HAIRLINE, styles.BORDER),
            ("LINEBELOW", (0, 0), (-1, 0), styles.HAIRLINE, styles.BORDER_STRONG),
            ("LINEBELOW", (0, 1), (-1, -1), 0.4, styles.BORDER),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("LEFTPADDING", (0, 0), (-1, -1), CELL_PAD),
            ("RIGHTPADDING", (0, 0), (-1, -1), CELL_PAD),
            ("TOPPADDING", (0, 0), (-1, -1), 2.6),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 2.6),
        ],
    )


def labelled(label: str, text: str) -> Paragraph:
    return Paragraph(f"<b>{graphics.html_escape(label)}</b> {graphics.html_escape(text)}", styles.SMALL_STYLE)


def _subheading(text: str) -> Paragraph:
    return Paragraph(f"<b>{graphics.html_escape(text)}</b>", styles.SMALL_STYLE)


# ------------------------------------------------------------------ section blocks

def folder_labels(scoped: ScopedTomograms) -> dict[str, str]:
    return {source.source.id: source.label for source in scoped.sources}


def section_meta(section: TomogramSection, scoped: ScopedTomograms, summary: ProcessingSummary | None) -> str:
    excluded = len({row.key for row in section.notes})
    if not section.members:
        return " · ".join(part for part in ("0 linked tomograms", count_phrase(excluded, "not counted", "not counted")
                                            if excluded else "") if part)
    sources = section_sources(section, scoped)
    where = sources[0].label if len(sources) == 1 else count_phrase(len(sources), "folder")
    parts = [count_phrase(len(section.members), "linked tomogram"), where]
    if summary is not None and not summary.has_recorded_values:
        parts.append("volumes only")
    return " · ".join(parts)


def coverage_line(section: TomogramSection) -> Paragraph | None:
    if not section.tilt_keys:
        return None
    linked = len({row.link.target_key for row in section.members} & set(section.tilt_keys))
    return labelled("Tilt series with a linked tomogram:", f"{linked} of {len(set(section.tilt_keys))}")


def facts_row(summary: ProcessingSummary) -> Table:
    """Versions, Volume, Alignment and Parent link from the shared facts, with their detail lines."""
    facts = outcome_facts(summary)
    width = (PORTRAIT_USABLE_W - styles.CARD_GAP * (len(facts) - 1)) / len(facts)
    cells = [graphics.card([Paragraph(graphics.html_escape(fact.value), styles.VALUE_STYLE),
                            *([Paragraph(graphics.html_escape(fact.detail), styles.MUTED_STYLE)] if fact.detail else [])],
                           width=width, title=fact.label)
             for fact in facts]
    return graphics.side_by_side(cells, [width] * len(cells))


def versions_text(row: ReportRow) -> str:
    return ", ".join(volume.version for volume in review_versions(row.tomogram)) or "None"


def tomogram_table(section: TomogramSection, scoped: ScopedTomograms, *, folder_column: bool) -> Table:
    labels = folder_labels(scoped)
    header = ["Tomogram", "Tilt series", *(["Folder"] if folder_column else []), "Link", "Versions"]
    rows = [[row.tomogram.name, row.parent_label or "", *([labels[row.tomogram.source_id]] if folder_column else []),
             COUNT_LABELS.get(row.link.step, "") if row.link.step else "", versions_text(row)]
            for row in section.members]
    return data_table(header, rows)


def recorded_values(rows: Sequence[ReportRow]) -> list:
    """The recorded-values table, the absent-column line and the grouped sources."""
    metric_rows = [metric_row(row.tomogram) for row in rows]
    present = [index for index, (definition, _) in enumerate(METRIC_COLUMNS)
               if any(metric.definition == definition for row in rows for metric in row.tomogram.processing.metrics)]
    if not present:
        return [labelled("Recorded values:", "none.")]
    absent = [label.lower() for index, (_, label) in enumerate(METRIC_COLUMNS) if index not in present]
    header = ["Tomogram", *(METRIC_COLUMNS[index][1] for index in present)]
    # ``metric_row.values`` starts with the alignment cell.
    table_rows = [[row.name, *(row.values[index + 1] for index in present)] for row in metric_rows]
    flowables: list = [_subheading("Recorded values"), Spacer(1, 3), data_table(header, table_rows)]
    if absent:
        flowables += [Spacer(1, 3), Paragraph(graphics.html_escape(f"Not recorded: {', '.join(absent)}."), styles.MUTED_STYLE)]
    flowables += [Spacer(1, 6), _subheading("Sources"), *provenance(rows)]
    return flowables


def provenance(rows: Sequence[ReportRow]) -> list:
    """Sources grouped by definition, recorded field and qualification; per row only for ambiguity."""
    groups: dict[tuple[str, str, str], tuple[set[str], set[str]]] = defaultdict(lambda: (set(), set()))
    ambiguous: list[str] = []
    for row in rows:
        metrics = row.tomogram.processing.metrics
        for definition, label in METRIC_COLUMNS:
            candidates = [metric for metric in metrics if metric.definition == definition]
            if len(candidates) > 1:
                values = ", ".join(f"{metric_number(metric)} {metric.unit}" for metric in candidates)
                sources = ", ".join(dict.fromkeys(metric.source for metric in candidates))
                ambiguous.append(f"{row.tomogram.name}: {label} has {len(candidates)} values ({values}), "
                                 f"so the report shows no value. Sources: {sources}.")
            for metric in candidates:
                names, files = groups[(metric.label, metric.field, metric.qualification)]
                names.add(row.key)
                files.add(metric.source)
    qualifications = {qualification for (_, _, qualification) in groups}
    shared = next(iter(qualifications)) if len(qualifications) == 1 else None
    lines = []
    for (label, field, qualification), (names, files) in groups.items():
        text = f"{label}: {field}, {count_phrase(len(names), 'tomogram')}."
        if len(files) == 1:
            text += f" Source: {next(iter(files))}."
        if shared is None and qualification:
            text += f" {qualification}"
        lines.append(text)
    if shared:
        lines.append(shared)
    return [Paragraph(graphics.html_escape(line), styles.MUTED_STYLE) for line in (*lines, *ambiguous)]


def attention(section: TomogramSection, scoped: ScopedTomograms, summary: ProcessingSummary) -> list:
    """The rows the Summary's own predicate selects, with print wording, then folder warnings."""
    labels = folder_labels(scoped)
    stated = dict(summary.input.stated)
    lines = []
    for row in section.members:
        if row.key not in summary.attention_keys:
            continue
        warnings = dict.fromkeys(print_warning(text) for text in (
            *row.link.warnings, *row.tomogram.warnings,
            *tomogram_warnings(row.tomogram, lambda volume: stated.get(volume.id))))
        if warnings:                   # a link state alone is said once, in the table's Note
            lines.append(f"{row.tomogram.name} · {labels[row.tomogram.source_id]} · {' '.join(warnings)}")
    for source in section_sources(section, scoped):
        lines += [f"{source.label} · {warning}" for warning in source.source.warnings]
    if not lines:
        return []
    return [_subheading("Needs attention"), Spacer(1, 2),
            *(Paragraph(graphics.html_escape(line), styles.MUTED_STYLE) for line in lines)]


def exclusion_table(rows: Sequence[ReportRow], scoped: ScopedTomograms) -> Table:
    labels = folder_labels(scoped)
    return data_table(["Tomogram", "Folder", "Cause"],
                      [[row.tomogram.name, labels[row.tomogram.source_id], exclusion_reason(row)] for row in rows])


def tomogram_section(section: TomogramSection, scoped: ScopedTomograms, slices: ReportSlices | None = None) -> list:
    """The Tomograms subsection of one dataset: after the tilt-series table; central slices last."""
    summary = summary_from_input(section_input(section, scoped)) if section.members else None
    notes = tuple({row.key: row for row in section.notes}.values())
    flowables: list = [CondPageBreak(1.3 * inch),
                       graphics.section_heading("Tomograms", anchor=section.anchor,
                                                meta=section_meta(section, scoped, summary))]
    if summary is None:
        return [*flowables, exclusion_table(notes, scoped)]
    coverage = coverage_line(section)
    if coverage is not None:
        flowables += [coverage, Spacer(1, 4)]
    flowables += [facts_row(summary), Spacer(1, styles.CARD_GAP),
                  tomogram_table(section, scoped, folder_column=len(section_sources(section, scoped)) > 1),
                  Spacer(1, 8), *recorded_values(section.members)]
    flagged = attention(section, scoped, summary)
    if flagged:
        flowables += [Spacer(1, 8), *flagged]
    if notes:
        flowables += [Spacer(1, 8), labelled("Not counted:", count_phrase(len(notes), "tomogram") + "."),
                      Spacer(1, 3), exclusion_table(notes, scoped)]
    tags = tags_and_notes(section, scoped)
    if tags:
        flowables += [Spacer(1, 8), *tags]
    if slices is not None:
        flowables += [Spacer(1, 8), *central_slices(section, slices)]
    return flowables


# ------------------------------------------------------------------ tags and notes (Q5-C)

def _tag_cell(content: TagContent | None, name: str) -> str:
    """A count, "Tagged" for a tag without a count, blank for no tag: blank and 0 stay distinct."""
    tag = next((value for value in content.tags if value.name == name), None) if content else None
    if tag is None:
        return ""
    return number_text(tag.count) if tag.count is not None else "Tagged"


def tags_and_notes(section: TomogramSection, scoped: ScopedTomograms) -> list:
    """Tag counts, Counting complete, totals and the reviewer's notes for the members.

    Totals come from ``tag_summary`` over the section's own input, which
    refuses to add counts from different projects. Notes print in full, as
    written. Review status is never printed.
    """
    sources = section_sources(section, scoped)
    if not any(source.tags.project_status is not None for source in sources):
        return []                                   # no member folder has a tag project
    payload = section_input(section, scoped)
    rows = section.members
    contents = tuple(row.summary.tags for row in rows)
    labels = folder_labels(scoped)
    flowables: list = [_subheading("Review tags"), Spacer(1, 2)]
    for source in sources:
        if source.tags.project_status is not None and not source.tags.ready:
            count = sum(row.tomogram.source_id == source.source.id for row in rows)
            flowables.append(labelled("Project not ready:", (
                f"The tags of {source.label} were not available when the report dialog opened. "
                f"The report shows its {count_phrase(count, 'tomogram')} without tags.")))
    tagged = [(row, content) for row, content in zip(rows, contents, strict=True)
              if content is not None and (content.tags or content.counting_complete)]
    if not tagged:
        flowables.append(Paragraph("No review tags.", styles.MUTED_STYLE))
        return [*flowables, *_notes(rows, labels)]
    summary = tag_summary(tuple(row.key for row in rows), contents, folder_projects=payload.folder_projects)
    complete = sum(content.counting_complete for _, content in tagged)
    flowables.append(labelled("Counting complete:", f"{complete} of {len(rows)}"))
    projects = {key for key in payload.folder_projects if key is not None}
    if summary.project is None and len(projects) > 1:
        flowables.append(labelled(f"{len(projects)} tag projects:", (
            f"The tags come from {len(projects)} tag projects. The report does not add counts from different projects.")))
    totals = [f"{row.name} {row.total:,}" for row in summary.rows if row.total is not None]
    if totals:
        flowables.append(labelled("Totals:", " · ".join(totals)))
    names = list(dict.fromkeys([*(row.name for row in summary.rows),
                                *(value.name for _, content in tagged for value in content.tags)]))
    shown, other = names[:TAG_COLUMNS], names[TAG_COLUMNS:]
    folder_column = len(sources) > 1
    header = ["Tomogram", *(["Folder"] if folder_column else []), *shown, "Counting"]
    table_rows = [[row.tomogram.name, *([labels[row.tomogram.source_id]] if folder_column else []),
                   *(_tag_cell(content, name) for name in shown), "Complete" if content.counting_complete else ""]
                  for row, content in tagged]
    flowables += [Spacer(1, 3), data_table(header, table_rows), Spacer(1, 3),
                  Paragraph(graphics.html_escape(TAG_KEY), styles.MUTED_STYLE)]
    if other:
        flowables.append(Paragraph(graphics.html_escape(OTHER_TAGS_KEY), styles.MUTED_STYLE))
    untagged = len(rows) - len(tagged)
    if untagged:
        flowables.append(Paragraph(graphics.html_escape(f"Tomograms without tags: {untagged}."), styles.MUTED_STYLE))
    return [*flowables, *_notes(rows, labels, other)]


def _notes(rows: Sequence[ReportRow], labels: dict[str, str], other: Sequence[str] = ()) -> list:
    """One entry per tomogram: its tags after the table's columns, then its note, both in full (GR-04).

    The other tags are a two-column table whose header names the tomogram and
    folder; the header repeats on each page, so no count is read against
    another tomogram. Table rows and paragraphs split across pages, so no
    number of tags or length of note can overflow a page. Notes print as
    written: escaped, line breaks kept, never shortened.
    """
    entries = []
    for row in rows:
        content = row.summary.tags
        extra = [[name, cell] for name in other if (cell := _tag_cell(content, name))]
        note = content.note if content is not None and content.note.strip() else ""
        if extra or note:
            entries.append((row, extra, note))
    if not entries:
        return []
    heading = " and ".join(part for part, present in (("Other tags", any(e[1] for e in entries)),
                                                      ("notes", any(e[2] for e in entries))) if present)
    flowables: list = [Spacer(1, 6), _subheading(heading[0].upper() + heading[1:]), Spacer(1, 2)]
    for row, extra, note in entries:
        identity = f"{row.tomogram.name} · {labels[row.tomogram.source_id]}"
        if extra:
            flowables.append(data_table([f"{identity} · Other tags", "Count"], extra))
        else:
            flowables.append(Paragraph(f"<b>{graphics.html_escape(row.tomogram.name)}</b> · "
                                       f"{graphics.html_escape(labels[row.tomogram.source_id])}", ENTRY_HEADING_STYLE))
        if note:
            flowables += [*([Spacer(1, 2)] if extra else []),
                          Paragraph(graphics.html_escape(note).replace("\n", "<br/>"), styles.SMALL_STYLE)]
        flowables.append(Spacer(1, 3))
    return flowables


# ------------------------------------------------------------------ cover

def cover_card(scoped: ScopedTomograms, slices: ReportSlices | None = None) -> list:
    """The cover's Linked tomograms card and its diagnostics, each said once."""
    members = scoped.members
    excluded = len(scoped.excluded) + len(scoped.incomplete)
    cards = [graphics.StatCardData("Linked", f"{len(members):,}", [], []),
             graphics.StatCardData("Not counted", f"{excluded:,}", [], [])]
    if slices is not None:
        cards += slice_cover_cards(slices)
    rows: list = [graphics.stat_card_row(cards, total_width=PORTRAIT_USABLE_W - 2 * styles.CARD_PADDING, columns=4)]
    if not members:
        rows.append(Paragraph("0 linked tomograms in the selected datasets.", styles.SMALL_STYLE))
    linked: dict[str, int] = defaultdict(int)
    for row in members:
        linked[row.tomogram.source_id] += 1
    folders = " · ".join(f"{source.label}, {linked[source.source.id]} linked"
                         for source in scoped.sources if linked[source.source.id])
    if scoped.folders_left_out:
        folders = " ".join(part for part in (folders + ("." if folders else ""), (
            f"The report does not include {count_phrase(scoped.folders_left_out, 'other loaded folder')}.")) if part)
    if folders:
        rows.append(labelled("Processing folders:", folders))
    labels = folder_labels(scoped)
    for note in scoped.session_notes:
        for row in note.rows:
            rows.append(labelled(f"{note.session_name}:", (
                f"{row.tomogram.name} ({labels[row.tomogram.source_id]}) has a link to a tilt series of the session, "
                "not of a grid. It is not in a dataset section." if row.state == "unrepresented_owner"
                else f"{row.tomogram.name} ({labels[row.tomogram.source_id]}): {exclusion_reason(row)}")))
    incomplete = incomplete_note(scoped.incomplete, len(scoped.sources))
    if incomplete:
        label, _, text = incomplete.partition(": ")
        rows.append(labelled(label + ":", text))
    # Only folders whose rows the report prints: unrelated unsaved edits do not qualify it.
    contributing = {row.tomogram.source_id for row in members}
    unsaved = [source.label for source in scoped.sources if source.source.id in contributing and source.tags.unsaved]
    if unsaved:
        rows.append(labelled("Tags:", f"{', '.join(unsaved)} had tag changes that the reviewer did not save. "
                                       "The report shows them as they were on the screen."))
    rows.append(Paragraph(graphics.html_escape(SNAPSHOT_NOTE), styles.MUTED_STYLE))
    return [graphics.card(rows, width=PORTRAIT_USABLE_W, title="Linked tomograms")]


def contents_detail(section: TomogramSection | None) -> str:
    """The contents line's tomogram count for one dataset: "12 linked tomograms · 3 not counted"."""
    if section is None:
        return ""
    notes = len({row.key for row in section.notes})
    return " · ".join(part for part in (count_phrase(len(section.members), "linked tomogram"),
                                        f"{notes} not counted" if notes else "") if part)
