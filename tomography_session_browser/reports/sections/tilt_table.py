"""Sections tilt table report components."""
from __future__ import annotations
from dataclasses import dataclass
from typing import Sequence
from reportlab.platypus import Paragraph, Spacer, Table
from tomography_session_browser.domain.models import TiltSeries
from tomography_session_browser.reports import graphics, styles
from tomography_session_browser.reports.pdf_export import PORTRAIT_USABLE_W
from tomography_session_browser.services.item_status import display_status_label
from tomography_session_browser.services.pixel_size import mrc_pixel_size_angstrom
from tomography_session_browser.services.acquisition_metadata import format_target_defocus_values
from tomography_session_browser.services.tilt_series_validation import STATUS_COMPLETE, STATUS_UNKNOWN
from tomography_session_browser.reports.constants import TILT_CELL_PAD, TILT_NAME_MAX_W, TILT_TABLE_HEADERS, TILT_TABLE_UNITS
from tomography_session_browser.reports.context import SessionContext
from tomography_session_browser.reports.layout import shrink_widths, spread_widths


def tilt_unit_text(label: str) -> str | None:
    unit = TILT_TABLE_UNITS.get(label)
    return f"({unit})" if unit else None


@dataclass(frozen=True, slots=True)
class TiltRow:
    name: str
    status_label: str
    tone: str
    values: tuple[str, ...]  # Images, Tilt range, Original pixel, MRC pixel, Target defocus


def tilt_column_widths_bounds(rows: Sequence[TiltRow]) -> tuple[list[float], list[float]]:
    """``(natural, narrowest)`` width of each column, padding included.

    Natural is every heading, unit, value and status mark on one line (a
    name no wider than ``TILT_NAME_MAX_W``). Narrowest lets names and
    headings wrap between words; a value or a status mark never wraps.
    """

    pad = 2 * TILT_CELL_PAD
    cell_size = styles.TABLE_CELL_STYLE.fontSize

    def heading(label: str) -> tuple[float, float]:
        text = label.upper()
        unit = tilt_unit_text(label)
        unit_w = styles.string_width(unit, styles.SANS, styles.TYPE_CAPTION) if unit else 0.0
        whole = styles.string_width(text, styles.SANS_SEMIBOLD, styles.TYPE_CAPTION)
        word = max(styles.string_width(part, styles.SANS_SEMIBOLD, styles.TYPE_CAPTION) for part in text.split())
        return max(whole, unit_w), max(word, unit_w)

    name_whole, name_word = heading(TILT_TABLE_HEADERS[0])
    names = [styles.string_width(row.name, styles.SANS, cell_size) for row in rows]
    name_words = [
        styles.string_width(part, styles.SANS, cell_size)
        for row in rows for part in row.name.split()
    ]
    natural = [min(max(names + [name_whole]), TILT_NAME_MAX_W) + pad]
    narrowest = [min(max(name_words + [name_word]), natural[0] - pad) + pad]

    status_whole, status_word = heading(TILT_TABLE_HEADERS[1])
    marks = [graphics.status_mark(row.status_label, row.tone).wrap(1000, 1000)[0] for row in rows]
    natural.append(max(marks + [status_whole]) + pad)
    narrowest.append(max(marks + [status_word]) + pad)

    for index, label in enumerate(TILT_TABLE_HEADERS[2:]):
        whole, word = heading(label)
        values = [styles.string_width(row.values[index], styles.MONO, styles.TYPE_SMALL) for row in rows]
        natural.append(max(values + [whole]) + pad)
        narrowest.append(max(values + [word]) + pad)
    return natural, narrowest


def compute_tilt_table_widths(rows: Sequence[TiltRow]) -> list[float]:
    """Per-column widths summing to ``PORTRAIT_USABLE_W``, spaced evenly.

    Spare width becomes equal gaps between neighbouring columns. Every
    column but the left-aligned names is centred, so a centred column takes
    a full share (half each side) and the names column half a share (its
    right side), which makes every gap between neighbours the same.
    """

    natural, narrowest = tilt_column_widths_bounds(rows)
    slack = PORTRAIT_USABLE_W - sum(natural)
    # Room to spare goes to unwrapping a long name before it widens the gaps.
    longest_name = max(
        [styles.string_width(row.name, styles.SANS, styles.TABLE_CELL_STYLE.fontSize) for row in rows] + [0.0]
    ) + 2 * TILT_CELL_PAD
    if slack > 0 and longest_name > natural[0]:
        grow = min(slack, longest_name - natural[0])
        natural[0] += grow
        slack -= grow
    if slack >= 0:
        return spread_widths(natural, PORTRAIT_USABLE_W, [0.5] + [1.0] * (len(natural) - 1))

    # Long names wrap first; then long headings, each column giving up the
    # same fraction of what wrapping can save it.
    names = max(narrowest[0], natural[0] + slack)
    return shrink_widths([names, *natural[1:]], [names, *narrowest[1:]], PORTRAIT_USABLE_W)


def readout(text: str) -> Paragraph:
    """A centred monospace table value; an absence ("n/a") is drawn muted."""

    if text == "n/a":
        return Paragraph(text, styles.MUTED_MONO_CENTRE_STYLE)
    return Paragraph(graphics.html_escape(text), styles.MONO_CENTRE_STYLE)


def tilt_table_header(label: str) -> list:
    """A heading: the label, then its bracketed unit on the line below.

    The header row is top-aligned, so every label shares the first line
    whether or not its column has a unit.
    """

    name_column = label == TILT_TABLE_HEADERS[0]
    header = [Paragraph(label, styles.TABLE_HEADER_STYLE if name_column else styles.TABLE_HEADER_CENTRE_STYLE)]
    unit = tilt_unit_text(label)
    if unit:
        header.append(Paragraph(graphics.html_escape(unit), styles.TABLE_UNIT_CENTRE_STYLE))
    return header


def tilt_row(tilt: TiltSeries, ctx: SessionContext) -> TiltRow:
    validation = ctx.validations.get(tilt.id)
    actual = validation.actual_count if validation else len(tilt.sections)
    expected = validation.expected_count if validation else None
    status = validation.status if validation else STATUS_UNKNOWN

    # Degrees are in the column heading.
    tilt_range_text = "n/a"
    if validation and validation.min_tilt is not None and validation.max_tilt is not None:
        tilt_range_text = f"{validation.min_tilt:g} to {validation.max_tilt:g}"
    elif tilt.tilt_range is not None:
        tilt_range_text = f"{tilt.tilt_range[0]:g} to {tilt.tilt_range[1]:g}"

    original_pixel = f"{tilt.original_pixel_size:g}" if tilt.original_pixel_size is not None else "n/a"
    mrc_pixel = mrc_pixel_size_angstrom(tilt.mrc_metadata, [])
    pixel = f"{mrc_pixel:g}" if mrc_pixel is not None else (
        f"{tilt.pixel_size:g}*" if tilt.pixel_size is not None else "n/a"
    )
    # The unit is in the column heading: the same formatter, without it.
    target_def = format_target_defocus_values([tilt.target_defocus], unit="").strip()
    images = f"{actual} / {expected}" if expected is not None else f"{actual} / —"
    return TiltRow(
        name=tilt.name or tilt.id,
        status_label=display_status_label(status),
        tone=styles.VALIDATION_TONES.get(status, "grey"),
        values=(images, tilt_range_text, original_pixel, pixel, target_def),
    )


def tilt_series_table(
    tilt_series: Sequence[TiltSeries],
    ctx: SessionContext,
) -> list:
    """Status-led table with original and stored-stack sampling.

    Status follows the viewer lists: a complete series is V1's glyph, every
    other outcome a labelled pill in the app's words, so a failure stands
    out in a long table.
    """

    flowables: list = []
    header_row = [tilt_table_header(label) for label in TILT_TABLE_HEADERS]
    tilt_rows = [tilt_row(tilt, ctx) for tilt in tilt_series]
    rows = [
        [
            Paragraph(graphics.html_escape(row.name), styles.TABLE_CELL_STYLE),
            graphics.status_mark(row.status_label, row.tone),
            *(readout(value) for value in row.values),
        ]
        for row in tilt_rows
    ]

    table = Table(
        [header_row, *rows],
        colWidths=compute_tilt_table_widths(tilt_rows),
        style=[
            ("BACKGROUND", (0, 0), (-1, 0), styles.SURFACE_ALT),
            ("LINEABOVE", (0, 0), (-1, 0), styles.HAIRLINE, styles.BORDER),
            ("LINEBELOW", (0, 0), (-1, 0), styles.HAIRLINE, styles.BORDER_STRONG),
            ("LINEBELOW", (0, 1), (-1, -1), 0.4, styles.BORDER),
            # The status glyph or pill sits centred under its heading.
            ("ALIGN", (1, 1), (1, -1), "CENTER"),
            # Labels share the header's first line; units sit on the second.
            ("VALIGN", (0, 0), (-1, 0), "TOP"),
            ("VALIGN", (0, 1), (-1, -1), "MIDDLE"),
            ("LEFTPADDING", (0, 0), (-1, -1), TILT_CELL_PAD),
            ("RIGHTPADDING", (0, 0), (-1, -1), TILT_CELL_PAD),
            ("TOPPADDING", (0, 0), (-1, 0), 4.5),
            ("BOTTOMPADDING", (0, 0), (-1, 0), 4),
            ("TOPPADDING", (0, 1), (-1, -1), 2.6),
            ("BOTTOMPADDING", (0, 1), (-1, -1), 2.6),
        ],
        repeatRows=1,
        hAlign="LEFT",
    )
    flowables.append(table)
    flowables.append(Spacer(1, 4))
    flowables.append(graphics.plain_table(
        [[
            graphics.StatusGlyph("complete", styles.VALIDATION_TONES[STATUS_COMPLETE], size=7.5),
            Paragraph("Complete; every other outcome is named. Images are acquired / expected.", styles.MUTED_STYLE),
        ]],
        [12, PORTRAIT_USABLE_W - 12],
    ))
    if any(mrc_pixel_size_angstrom(tilt.mrc_metadata, []) is None and tilt.pixel_size is not None for tilt in tilt_series):
        flowables.append(Paragraph(
            "* MRC sampling unavailable; resolved metadata estimate shown. Original pixel size is the recorded MDOC PixelSpacing.",
            styles.NOTE_STYLE,
        ))
    return flowables

