"""Cards report components."""
from __future__ import annotations
import re
from dataclasses import dataclass
from typing import Iterable, Sequence
from reportlab.lib import colors
from reportlab.lib.styles import ParagraphStyle
from reportlab.platypus import Flowable, Paragraph, Table
from tomography_session_browser.reports import styles
from tomography_session_browser.reports.graphics.overlays import MARK_LINES
from tomography_session_browser.reports.graphics.status import Chip, Chips, draw_status_band
from tomography_session_browser.reports.graphics.text import draw_caption, fit_text, html_escape


@dataclass(slots=True)
class StatCardData:
    label: str
    value: str
    chips: list[Chip]
    #: One tone per item, in item order; empty draws the empty band; None draws no band.
    band: list[str] | None
    #: A muted sentence in place of the chips; it wraps and prints in full.
    note: str | None = None
    #: Smaller, muted text after the value: "of 3" in "2 of 3".
    suffix: str = ""


#: The card's note: the chips' size, wrapped.
NOTE_STYLE = ParagraphStyle("stat-card-note", parent=styles.MUTED_STYLE, fontSize=styles.TYPE_SMALL - 0.5,
                            leading=(styles.TYPE_SMALL - 0.5) * 1.3, spaceBefore=0, spaceAfter=0)


class StatCard(Flowable):
    """The dashboard's count card, for paper.

    Title (uppercase caption), the count, a dot-chip strip and one status
    band. The row sets every card in it to the tallest card's height so the
    bands line up. A card with a note shows the note, in full, in place of the
    chips; a card with no band (``band=None``) has no band and no space for one.
    """

    PAD = 8.0
    BAND_HEIGHT = 4.0

    def __init__(self, data: StatCardData, *, width: float) -> None:
        super().__init__()
        self.data = data
        self.width = width
        self.height = 0.0
        self._chips = Chips(data.chips, size=styles.TYPE_SMALL - 0.5, gap=7.0)

    def _note(self) -> Paragraph | None:
        if not self.data.note or self.data.chips:
            return None
        return Paragraph(html_escape(self.data.note), NOTE_STYLE)

    def natural_height(self) -> float:
        inner = self.width - 2 * self.PAD
        note = self._note()
        _w, below_h = note.wrap(inner, 1000) if note is not None else self._chips.wrap(inner, 1000)
        band_h = 5.0 + self.BAND_HEIGHT if self.data.band is not None else 0.0
        return self.PAD + 9.0 + styles.TYPE_VALUE * 1.15 + 3.0 + below_h + band_h + self.PAD

    def wrap(self, _avail_w, _avail_h):  # noqa: N802
        if not self.height:
            self.height = self.natural_height()
        return self.width, self.height

    def draw(self) -> None:
        c = self.canv
        w, h = self.width, self.height
        c.saveState()
        c.setFillColor(styles.SURFACE)
        c.setStrokeColor(styles.BORDER)
        c.setLineWidth(styles.HAIRLINE)
        c.roundRect(0.3, 0.3, w - 0.6, h - 0.6, styles.CARD_RADIUS, stroke=1, fill=1)

        inner = w - 2 * self.PAD
        title_y = h - self.PAD - styles.TYPE_CAPTION * 0.75
        draw_caption(c, self.PAD, title_y, fit_text(self.data.label.upper(), styles.SANS_SEMIBOLD, styles.TYPE_CAPTION, inner))

        value_baseline = title_y - 4.0 - styles.TYPE_VALUE * 0.78
        c.setFillColor(styles.TEXT_STRONG)
        c.setFont(styles.SANS_SEMIBOLD, styles.TYPE_VALUE)
        c.drawString(self.PAD - 0.6, value_baseline, self.data.value)
        if self.data.suffix:
            after = self.PAD - 0.6 + c.stringWidth(self.data.value + " ", styles.SANS_SEMIBOLD, styles.TYPE_VALUE)
            c.setFillColor(styles.TEXT_MUTED)
            c.setFont(styles.SANS, styles.TYPE_VALUE * 0.6)
            c.drawString(after, value_baseline, self.data.suffix)

        chips_top = value_baseline - styles.TYPE_VALUE * 0.3 - 3.0
        note = self._note()
        if note is not None:
            _nw, note_h = note.wrap(inner, 1000)
            note.drawOn(c, self.PAD, chips_top - note_h)
        else:
            _cw, chips_h = self._chips.wrap(inner, 1000)
            self._chips.drawOn(c, self.PAD, chips_top - chips_h)
        if self.data.band is not None:
            draw_status_band(c, self.PAD, self.PAD, inner, self.BAND_HEIGHT, self.data.band)
        c.restoreState()


def stat_card_row(cards: Sequence[StatCardData], *, total_width: float, gap: float = 7.0, columns: int | None = None) -> Table:
    """Cards evenly across ``total_width``, all as tall as the tallest."""

    count = max(columns or len(cards), 1)
    each = (total_width - gap * (count - 1)) / count
    flowables = [StatCard(card, width=each) for card in cards]
    height = max((card.natural_height() for card in flowables), default=0.0)
    for card in flowables:
        card.height = height
    cells: list = list(flowables) + [""] * (count - len(flowables))
    return Table(
        [cells],
        colWidths=[each + gap] * (count - 1) + [each],
        hAlign="LEFT",
        style=[
            ("LEFTPADDING", (0, 0), (-1, -1), 0),
            ("RIGHTPADDING", (0, 0), (-1, -1), 0),
            ("TOPPADDING", (0, 0), (-1, -1), 0),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ],
    )


@dataclass(frozen=True, slots=True)
class FactCardData:
    label: str
    value: str
    detail: str = ""


#: Digits and number punctuation only ("1156 × 1628 × 282"): mono. Words: sans.
_NUMBER_TEXT = re.compile(r"[\d\s×.,:/–-]*\d[\d\s×.,:/–-]*")


def fact_value_style(value: str) -> ParagraphStyle:
    """Mono for a number, sans for words (``REPORTS.md``: mono is for numbers)."""

    return styles.VALUE_STYLE if _NUMBER_TEXT.fullmatch(value) else styles.VALUE_TEXT_STYLE


class FactCard(Flowable):
    """A fact card for paper: caption title, the full value, then a muted detail line.

    ``fact_card_row`` gives every card in a row one height and one top for
    the values and for the detail lines, so a value that wraps never moves
    its detail line out of line with the others. The value is never shortened.
    """

    def __init__(self, data: FactCardData, *, width: float, padding: float = styles.CARD_PADDING,
                 gap: float = 5.0) -> None:
        super().__init__()
        self.data = data
        self.width = width
        self.padding = padding
        self.gap = gap
        inner = width - 2 * padding
        self.title = Paragraph(html_escape(data.label), styles.CARD_TITLE_STYLE)
        self.value = Paragraph(html_escape(data.value), fact_value_style(data.value))
        self.detail = Paragraph(html_escape(data.detail), styles.MUTED_STYLE) if data.detail else None
        self.title_h = self.title.wrap(inner, 10_000)[1]
        self.value_h = self.value.wrap(inner, 10_000)[1]
        self.detail_h = self.detail.wrap(inner, 10_000)[1] if self.detail is not None else 0.0
        # Distances from the card's top edge, as ``card`` spaces a title and its rows.
        self.value_top = padding - 1 + self.title_h + gap + 1
        self.detail_top = self.value_top + self.value_h + gap
        self.height = self.natural_height()

    def natural_height(self) -> float:
        if self.detail is None:
            return self.value_top + self.value_h + self.padding
        return self.detail_top + self.detail_h + self.padding

    def wrap(self, _avail_w, _avail_h):  # noqa: N802
        return self.width, self.height

    def draw(self) -> None:
        c = self.canv
        w, h, pad = self.width, self.height, self.padding
        c.saveState()
        c.setFillColor(styles.SURFACE)
        c.setStrokeColor(styles.BORDER)
        c.setLineWidth(styles.HAIRLINE)
        c.roundRect(0.3, 0.3, w - 0.6, h - 0.6, styles.CARD_RADIUS, stroke=1, fill=1)
        c.restoreState()
        self.title.drawOn(c, pad, h - (pad - 1) - self.title_h)
        self.value.drawOn(c, pad, h - self.value_top - self.value_h)
        if self.detail is not None:
            self.detail.drawOn(c, pad, h - self.detail_top - self.detail_h)


def fact_card_row(cards: Sequence[FactCardData], *, total_width: float, gap: float = styles.CARD_GAP) -> Table:
    """Fact cards evenly across ``total_width``: one height, values level, detail lines level."""

    each = (total_width - gap * (len(cards) - 1)) / max(len(cards), 1)
    flowables = [FactCard(card, width=each) for card in cards]
    value_top = max((card.value_top for card in flowables), default=0.0)
    detail_top = max((value_top + card.value_h + card.gap for card in flowables), default=0.0)
    for card in flowables:
        card.value_top, card.detail_top = value_top, detail_top
    height = max((card.natural_height() for card in flowables), default=0.0)
    for card in flowables:
        card.height = height
    return side_by_side(flowables, [each] * len(flowables), gap=gap)


def draw_brand_mark(canvas, x: float, y: float, size: float) -> None:
    """Draw the app mark in a ``size`` square whose bottom-left is ``(x, y)``."""

    scale = size / 22.0
    canvas.saveState()
    canvas.setLineCap(1)
    for x1, x2, line_y, opacity, centre in MARK_LINES:
        canvas.setStrokeColor(styles.TEXT_STRONG if centre else styles.ACCENT)
        canvas.setStrokeAlpha(opacity)
        canvas.setLineWidth((1.6 if centre else 1.4) * scale)
        py = y + size - (line_y - 0.5) * scale
        canvas.line(x + x1 * scale, py, x + x2 * scale, py)
    canvas.restoreState()


def card_header(title: str, badge: str | None, width: float) -> Table | Paragraph:
    """A card's caption title, with an optional muted count at the right."""

    title_para = Paragraph(html_escape(title), styles.CARD_TITLE_STYLE)
    if not badge:
        return title_para
    return Table(
        [[title_para, Paragraph(html_escape(badge), styles.CARD_BADGE_STYLE)]],
        colWidths=[width * 0.62, width * 0.38],
        style=[
            ("LEFTPADDING", (0, 0), (-1, -1), 0),
            ("RIGHTPADDING", (0, 0), (-1, -1), 0),
            ("TOPPADDING", (0, 0), (-1, -1), 0),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
            ("VALIGN", (0, 0), (-1, -1), "BOTTOM"),
        ],
    )


def card(
    rows: Sequence,
    *,
    width: float,
    title: str | None = None,
    badge: str | None = None,
    padding: float = styles.CARD_PADDING,
    gap: float = 5.0,
    fill: colors.Color = styles.SURFACE,
) -> Table:
    """A dashboard card: a rounded, hairline-bordered panel of rows.

    Each row is its own table row, so a long card (a warnings list) can
    break across pages between rows instead of overflowing one.
    """

    inner = width - 2 * padding
    data: list[list] = []
    if title:
        data.append([card_header(title, badge, inner)])
    data.extend([row] for row in rows)
    if not data:
        data.append([""])
    style = [
        ("BACKGROUND", (0, 0), (-1, -1), fill),
        ("BOX", (0, 0), (-1, -1), styles.HAIRLINE, styles.BORDER),
        ("ROUNDEDCORNERS", [styles.CARD_RADIUS] * 4),
        ("LEFTPADDING", (0, 0), (-1, -1), padding),
        ("RIGHTPADDING", (0, 0), (-1, -1), padding),
        ("TOPPADDING", (0, 0), (-1, -1), 0),
        ("BOTTOMPADDING", (0, 0), (-1, -1), gap),
        ("TOPPADDING", (0, 0), (-1, 0), padding - 1),
        ("BOTTOMPADDING", (0, -1), (-1, -1), padding),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
    ]
    if title:
        style.append(("BOTTOMPADDING", (0, 0), (-1, 0), gap + 1))
    return Table(data, colWidths=[width], style=style, hAlign="LEFT")


def side_by_side(cells: Sequence, widths: Sequence[float], *, gap: float = styles.CARD_GAP) -> Table:
    """Place cards (or any flowables) in one row with ``gap`` between them."""

    col_widths = [width + (gap if index < len(widths) - 1 else 0) for index, width in enumerate(widths)]
    return Table(
        [list(cells)],
        colWidths=col_widths,
        hAlign="LEFT",
        style=[
            ("LEFTPADDING", (0, 0), (-1, -1), 0),
            ("RIGHTPADDING", (0, 0), (-1, -1), 0),
            ("RIGHTPADDING", (0, 0), (-2, -1), gap),
            ("TOPPADDING", (0, 0), (-1, -1), 0),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ],
    )


def plain_table(rows: Sequence[Sequence], col_widths: Sequence[float], *, extra_style: Iterable = (), valign: str = "MIDDLE") -> Table:
    """A layout table with no padding; ``extra_style`` adds to it."""

    return Table(
        [list(row) for row in rows],
        colWidths=list(col_widths),
        hAlign="LEFT",
        style=[
            ("LEFTPADDING", (0, 0), (-1, -1), 0),
            ("RIGHTPADDING", (0, 0), (-1, -1), 0),
            ("TOPPADDING", (0, 0), (-1, -1), 0),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
            ("VALIGN", (0, 0), (-1, -1), valign),
            *extra_style,
        ],
    )

