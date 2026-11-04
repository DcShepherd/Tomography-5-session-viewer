"""Layout report components."""
from __future__ import annotations
from collections import Counter
from typing import Iterable, Sequence
from reportlab.platypus import KeepTogether, Paragraph, Spacer, Table
from tomography_session_browser.domain.markers import ImageMarker
from tomography_session_browser.reports import graphics, styles
from tomography_session_browser.reports.pdf_export import PORTRAIT_USABLE_W
from tomography_session_browser.presentation.dashboard.models import tile_status_word, TILE_STATUS_COMPLETE
from tomography_session_browser.reports.constants import TILE_CHIP_ORDER


def meta_row(items: Sequence[tuple[str, str | None] | tuple[str, str | None, str | None]]) -> Paragraph:
    """The dashboard header's meta row: ``KEY value`` groups on one line.

    Groups wrap as whole units where they can. A third item element names a
    tone for the value (e.g. an unresolved atlas link in the warning tone).
    """

    muted = styles.css_hex(styles.TEXT_MUTED)
    parts: list[str] = []
    for item in items:
        key, value = item[0], item[1]
        tone_name = item[2] if len(item) > 2 else None
        if not value:
            continue
        colour = styles.tone(tone_name).ink_hex if tone_name else styles.css_hex(styles.TEXT)
        parts.append(
            f'<font name="{styles.SANS_SEMIBOLD}" size="{styles.TYPE_CAPTION}" color="{muted}">'
            f"{graphics.html_escape(key.upper())}</font>&nbsp;&nbsp;"
            f'<font color="{colour}">{graphics.html_escape(value)}</font>'
        )
    return Paragraph("&nbsp;&nbsp;&nbsp;&nbsp; ".join(parts), styles.BODY_STYLE)


def spread_widths(natural: Sequence[float], total: float, shares: Sequence[float]) -> list[float]:
    """``natural`` widths plus the room ``total`` leaves, shared out by ``shares``.

    Column widths work like a LaTeX table set to the text width: every column
    keeps its natural width and the room to spare goes into equal gaps between
    neighbours, not to any one column. A share is how much of a gap a column
    owns: a centred column a whole share (half each side), a column aligned to
    one edge half a share (on its open side), and a left-aligned column whose
    neighbour is also left-aligned the whole gap after it.
    """

    unit = (total - sum(natural)) / sum(shares)
    return [width + unit * share for width, share in zip(natural, shares)]


def shrink_widths(widths: Sequence[float], narrowest: Sequence[float], total: float) -> list[float]:
    """``widths`` brought down to ``total``, wrapping before squeezing.

    Each column gives up the same fraction of what wrapping can save it (down
    to its ``narrowest``); only if that is not enough does everything scale.
    """

    widths = list(widths)
    deficit = sum(widths) - total
    reducible = [max(0.0, width - minimum) for width, minimum in zip(widths, narrowest)]
    if deficit > 0 and sum(reducible) > 0:
        fraction = min(1.0, deficit / sum(reducible))
        widths = [width - saving * fraction for width, saving in zip(widths, reducible)]
        deficit -= sum(reducible) * fraction
    if deficit > 1e-6:
        # Nothing left to wrap: scale everything (a table this crowded is rare).
        scale = total / sum(widths)
        widths = [width * scale for width in widths]
    return widths


def longest_word(texts: Iterable[str], font: str, size: float) -> float:
    return max((styles.string_width(word, font, size) for text in texts for word in text.split()), default=0.0)


def stat_card_data(label: str, value: int, statuses: Sequence[str]) -> graphics.StatCardData:
    """A count card from tile statuses, chipped the way ``StatCard`` chips them.

    The complete chip always shows, so "0 complete" reads as a finding; other
    statuses show only when present. A zero count keeps a neutral dot.
    """

    summary = Counter(statuses)
    chips: list[graphics.Chip] = []
    if statuses:
        for status in TILE_CHIP_ORDER:
            count = summary.get(status, 0)
            if count == 0 and status != TILE_STATUS_COMPLETE:
                continue
            dot = styles.TILE_DOT_TONES.get(status, "unknown") if count else "unknown"
            chips.append(graphics.Chip(dot, f"{count:,} {tile_status_word(label, status)}"))
    band = [styles.TILE_BAND_TONES.get(status, "grey") for status in statuses]
    return graphics.StatCardData(label=label, value=f"{value:,}", chips=chips, band=band)


def stat_cards_block(cards: Sequence[tuple[graphics.StatCardData, int]], *, scope_noun: str = "report") -> list:
    """Count cards in one row; zero counts fold into one muted line.

    As on the dashboard, a card saying "0 · nothing here" several times over
    is noise: zero counts are still stated, once, under the row.
    """

    shown = [data for data, value in cards if value]
    empty = [data for data, value in cards if not value]
    flowables: list = []
    if shown:
        flowables.append(graphics.stat_card_row(shown, total_width=PORTRAIT_USABLE_W, columns=max(len(shown), 3)))
    if empty:
        entries = " · ".join(f"{data.label}&nbsp;0" for data in empty)
        if shown:
            flowables.append(Spacer(1, 5))
        flowables.append(Paragraph(f"{entries} — none in this {scope_noun}", styles.MUTED_STYLE))
    return flowables


def headed_block(
    label: str,
    block: list,
    *,
    lead: Sequence = (),
    anchor: str | None = None,
    meta: str | None = None,
) -> list:
    """Keep a section heading (and its key) with its first measured figure."""
    if not block:
        return []
    first = block[0]
    content = first._content if isinstance(first, KeepTogether) else [first]
    heading = graphics.section_heading(label, anchor=anchor, meta=meta)
    return [KeepTogether([heading, *lead, *content]), *block[1:]]


def figure_cell(
    image,
    title: str,
    detail: str | None,
    width: float,
    *,
    extra: Sequence = (),
    inline: bool = False,
) -> Table:
    """An image with its name and a muted detail line under it (or beside it)."""

    if inline and detail:
        muted = styles.css_hex(styles.TEXT_MUTED)
        rows: list = [[image], [Paragraph(
            f'<b>{graphics.html_escape(title)}</b> <font color="{muted}">· {graphics.html_escape(detail)}</font>',
            styles.SMALL_STYLE,
        )]]
        detail = None
    else:
        rows = [[image], [Paragraph(f"<b>{graphics.html_escape(title)}</b>", styles.SMALL_STYLE)]]
    if detail:
        rows.append([Paragraph(detail, styles.MUTED_STYLE)])
    rows.extend([item] for item in extra)
    return Table(
        rows,
        colWidths=[width],
        style=[
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("LEFTPADDING", (0, 0), (-1, -1), 0),
            ("RIGHTPADDING", (0, 0), (-1, -1), 0),
            ("TOPPADDING", (0, 0), (-1, -1), 0),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 1),
            ("BOTTOMPADDING", (0, 0), (-1, 0), 4),
        ],
    )


def figure_row(cells: list, cell_w: float, gutter: float, *, columns: int | None = None) -> Table:
    """Figure cells side by side, padded to ``columns`` so columns line up."""

    columns = columns or len(cells)
    padded = list(cells) + [""] * (columns - len(cells))
    return Table(
        [padded],
        colWidths=[cell_w + gutter] * (columns - 1) + [cell_w],
        hAlign="LEFT",
        style=[
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("LEFTPADDING", (0, 0), (-1, -1), 0),
            ("RIGHTPADDING", (0, 0), (-1, -1), 0),
            ("RIGHTPADDING", (0, 0), (-2, -1), gutter),
            ("TOPPADDING", (0, 0), (-1, -1), 0),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
        ],
    )


def has_figures(block: Sequence) -> bool:
    """Whether a figure section drew any image (missing ones are notes)."""

    return any(not isinstance(item, (Paragraph, Spacer)) for item in block)


def overlay_key(drawn: Sequence[ImageMarker], *, figures: bool = True) -> list:
    """The overlay key under a figure section's heading.

    Built once from every marker the section's figures drew, so an outline
    colour gets one entry naming everything drawn in it across the section.
    """

    if not figures:
        return []
    items = graphics.overlay_key_items(drawn)
    return [graphics.OverlayKey(items, lead="Labels read batch position.exposure area."), Spacer(1, 6)]

