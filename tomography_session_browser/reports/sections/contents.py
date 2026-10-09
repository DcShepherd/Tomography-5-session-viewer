"""Sections contents report components."""
from __future__ import annotations
from dataclasses import dataclass
from typing import Sequence
from reportlab.platypus import Paragraph, Table
from tomography_session_browser.domain.display_names import count_phrase
from tomography_session_browser.reports import graphics, styles
from tomography_session_browser.reports.constants import CONTENTS_BAND_GAP, CONTENTS_BAND_W, CONTENTS_CELL_PAD, TEXT_FIT
from tomography_session_browser.reports.context import ReportSection
from tomography_session_browser.reports.formatting import tilt_outcome_sub_label
from tomography_session_browser.reports.layout import longest_word, shrink_widths, spread_widths
from tomography_session_browser.reports.sections.atlas_overview import resolved_sample_atlases
from tomography_session_browser.reports.sections.dataset import sample_validation_summary


@dataclass(frozen=True, slots=True)
class ContentsEntry:
    section: ReportSection
    name: str
    detail: str
    tones: tuple[str, ...] = ()
    outcome: str | None = None  # None for an atlas session (no tilt series column)


def contents_entry(section: ReportSection) -> ContentsEntry:
    ctx = section.ctx
    if section.kind == "atlas":
        atlases = len(resolved_sample_atlases(ctx.session))
        return ContentsEntry(section, ctx.session.name, f"Atlas session · {count_phrase(atlases, 'atlas', 'atlases')}")
    sample = section.sample
    tones = tuple(
        styles.VALIDATION_TONES.get(validation.status, "grey")
        for tilt in sample.tilt_series
        if (validation := ctx.validations.get(tilt.id)) is not None
    )
    detail_bits = [ctx.session.name] if ctx.session.name != sample.name else []
    detail_bits.append(count_phrase(len(sample.tilt_series), "tilt series", "tilt series"))
    summary = sample_validation_summary(sample, ctx)
    outcome = tilt_outcome_sub_label(summary) if sample.tilt_series else "none"
    return ContentsEntry(section, sample.name, " · ".join(reversed(detail_bits)), tones, outcome)


def contents_outcome_width(entries: Sequence[ContentsEntry]) -> float:
    """The band and the widest outcome after it: the block the heading centres over."""

    widest = max(
        (styles.string_width(entry.outcome, styles.SANS, styles.TYPE_SMALL) for entry in entries if entry.outcome),
        default=0.0,
    )
    return CONTENTS_BAND_W + CONTENTS_BAND_GAP + widest + TEXT_FIT


def compute_contents_widths(entries: Sequence[ContentsEntry], inner: float) -> list[float]:
    """Section, tilt series (if any dataset) and page widths summing to ``inner``.

    The tilt table's rule: every column keeps its natural width and the room
    to spare becomes equal gaps. Section names sit at the card's left edge
    and page numbers at its right, as in any contents list, so each owns half
    a gap; the centred tilt-series block owns a whole one. Too little room
    wraps the section names (their detail line is cut short instead).
    """

    pad = CONTENTS_CELL_PAD

    def caption(text: str) -> float:
        return styles.string_width(text.upper(), styles.SANS_SEMIBOLD, styles.TYPE_CAPTION)

    names = [entry.name for entry in entries]
    section = max(
        [caption("Section")]
        + [styles.string_width(name, styles.SANS_SEMIBOLD, styles.TYPE_SMALL) for name in names]
        + [styles.string_width(entry.detail, styles.SANS, styles.TYPE_SMALL) for entry in entries]
    ) + TEXT_FIT
    section_min = max(caption("Section"), longest_word(names, styles.SANS_SEMIBOLD, styles.TYPE_SMALL)) + TEXT_FIT
    # Page numbers are filled in after layout; three digits is plenty.
    page = max(caption("Page"), styles.string_width("999", styles.MONO, styles.TYPE_SMALL)) + pad
    natural, narrowest, shares = [section + pad], [section_min + pad], [0.5]
    if any(entry.outcome is not None for entry in entries):
        outcomes = max(caption("Tilt series"), contents_outcome_width(entries)) + 2 * pad
        natural.append(outcomes)
        narrowest.append(outcomes)
        shares.append(1.0)
    natural.append(page)
    narrowest.append(page)
    shares.append(0.5)
    if sum(natural) <= inner:
        return spread_widths(natural, inner, shares)
    return shrink_widths(natural, narrowest, inner)


def contents_card(sections: Sequence[ReportSection], *, width: float) -> Table | None:
    """The sections after the cover with the page each starts on."""

    if not sections:
        return None
    inner = width - 2 * styles.CARD_PADDING
    pad = CONTENTS_CELL_PAD
    entries = [contents_entry(section) for section in sections]
    widths = compute_contents_widths(entries, inner)
    has_datasets = len(widths) == 3
    section_w, page_w = widths[0], widths[-1]
    style: list = [
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (0, -1), pad),
        ("LEFTPADDING", (-1, 0), (-1, -1), pad),
        ("TOPPADDING", (0, 0), (-1, -1), 3.5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3.5),
        ("TOPPADDING", (0, 0), (-1, 0), 0),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LINEBELOW", (0, 0), (-1, -2), styles.HAIRLINE * 0.7, styles.BORDER),
    ]
    header: list = [Paragraph("Section", styles.CARD_TITLE_STYLE)]
    col_widths = [section_w]
    if has_datasets:
        # The band and its count are one block, centred in the column under
        # a heading spanning both; bands start together, counts follow them.
        lead = (widths[1] - contents_outcome_width(entries)) / 2
        band_w = lead + CONTENTS_BAND_W + CONTENTS_BAND_GAP
        col_widths += [band_w, widths[1] - band_w]
        header += [Paragraph("Tilt series", styles.CARD_TITLE_CENTRE_STYLE), ""]
        style += [("SPAN", (1, 0), (2, 0)), ("LEFTPADDING", (1, 1), (1, -1), lead)]
    col_widths.append(page_w)
    header.append(Paragraph("Page", styles.CARD_TITLE_RIGHT_STYLE))

    rows: list = [header]
    for entry in entries:
        fitted = graphics.fit_text(entry.detail, styles.SANS, styles.TYPE_SMALL, section_w - pad)
        cells: list = [[
            Paragraph(f"<b>{graphics.html_escape(entry.name)}</b>", styles.SMALL_STYLE),
            Paragraph(graphics.html_escape(fitted), styles.MUTED_STYLE),
        ]]
        if has_datasets:
            cells.append(graphics.Band(list(entry.tones), width=CONTENTS_BAND_W) if entry.tones else "")
            cells.append(Paragraph(graphics.html_escape(entry.outcome), styles.MUTED_STYLE) if entry.outcome else "")
        # PageRef draws its number right-aligned at its width: the card's edge.
        cells.append(graphics.PageRef(entry.section.key, width=page_w - pad))
        rows.append(cells)
    table = Table(rows, colWidths=col_widths, hAlign="LEFT", repeatRows=1, style=style)
    return graphics.card([table], width=width, title="Contents", badge=count_phrase(len(sections), "section"))

