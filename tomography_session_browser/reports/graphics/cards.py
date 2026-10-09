"""Cards report components."""
from __future__ import annotations
from dataclasses import dataclass
from typing import Iterable, Sequence
from reportlab.lib import colors
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
    band: list[str]  # one tone per item, in item order
    note: str | None = None  # muted line used instead of chips


class StatCard(Flowable):
    """The dashboard's count card, for paper.

    Title (uppercase caption), the count, a dot-chip strip and one status
    band. The row sets every card in it to the tallest card's height so the
    bands line up.
    """

    PAD = 8.0
    BAND_HEIGHT = 4.0

    def __init__(self, data: StatCardData, *, width: float) -> None:
        super().__init__()
        self.data = data
        self.width = width
        self.height = 0.0
        self._chips = Chips(data.chips, size=styles.TYPE_SMALL - 0.5, gap=7.0)

    def natural_height(self) -> float:
        inner = self.width - 2 * self.PAD
        if self.data.note and not self.data.chips:
            chips_h = styles.TYPE_SMALL * 1.5
        else:
            _w, chips_h = self._chips.wrap(inner, 1000)
        return self.PAD + 9.0 + styles.TYPE_VALUE * 1.15 + 3.0 + chips_h + 5.0 + self.BAND_HEIGHT + self.PAD

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

        band_y = self.PAD
        chips_top = value_baseline - styles.TYPE_VALUE * 0.3 - 3.0
        if self.data.note and not self.data.chips:
            c.setFillColor(styles.TEXT_MUTED)
            c.setFont(styles.SANS, styles.TYPE_SMALL - 0.5)
            c.drawString(self.PAD, chips_top - (styles.TYPE_SMALL - 0.5), fit_text(self.data.note, styles.SANS, styles.TYPE_SMALL - 0.5, inner))
        else:
            _cw, chips_h = self._chips.wrap(inner, 1000)
            self._chips.drawOn(c, self.PAD, chips_top - chips_h)
        draw_status_band(c, self.PAD, band_y, inner, self.BAND_HEIGHT, self.data.band)
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

