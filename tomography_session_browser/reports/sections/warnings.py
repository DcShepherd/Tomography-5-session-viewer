"""Sections warnings report components."""
from __future__ import annotations
from typing import Mapping
from reportlab.platypus import Paragraph, Table
from tomography_session_browser.domain.display_names import count_phrase
from tomography_session_browser.reports import graphics, styles
from tomography_session_browser.reports.pdf_export import PORTRAIT_USABLE_W
from tomography_session_browser.services.warning_classification import WarningSummary, severity_counts


def warning_summary_table(
    rows: list[WarningSummary],
    *,
    severities: Mapping[str, int] | None = None,
    width: float = PORTRAIT_USABLE_W,
) -> Table:
    """The grouped warnings card, one framed row per warning category.

    Counts are affected objects, never raw warning strings, so the card and
    the dashboard agree. Each row names its severity in words as well as its
    colour, so a greyscale print loses nothing.
    """

    inner = width - 2 * styles.CARD_PADDING
    if not rows:
        return graphics.card([Paragraph("No warnings.", styles.MUTED_STYLE)], width=width, title="Warnings")

    severities = dict(severities if severities is not None else severity_counts(rows))
    chips = graphics.Chips(
        [
            graphics.Chip(
                styles.SEVERITY_TONES[severity] if severities.get(severity, 0) else "unknown",
                f"{severities.get(severity, 0):,} {noun}",
            )
            for severity, noun in (("error", "errors"), ("warning", "warnings"), ("info", "info"))
        ],
        style="outline",
        gap=5.0,
    )
    framed: list = [chips]
    count_w = 64.0
    for row in rows:
        tone = styles.tone(styles.SEVERITY_TONES.get(row.severity, "grey"))
        category = Paragraph(
            f"<b>{graphics.html_escape(row.category)}</b>"
            + (f'<br/><font color="{styles.css_hex(styles.TEXT_MUTED)}">{graphics.html_escape(row.explanation)}</font>' if row.explanation else ""),
            styles.SMALL_STYLE,
        )
        affected = Paragraph(
            f'<font name="{styles.MONO}">{row.affected_items:,}</font> item{"s" if row.affected_items != 1 else ""}'
            f'<br/><font name="{styles.SANS_SEMIBOLD}" size="{styles.TYPE_CAPTION}" color="{tone.ink_hex}">{row.severity.upper()}</font>',
            styles.MUTED_RIGHT_STYLE,
        )
        framed.append(Table(
            [[graphics.Dot(styles.SEVERITY_TONES.get(row.severity, "grey"), size=6), category, affected]],
            colWidths=[14, inner - 14 - count_w, count_w],
            hAlign="LEFT",
            style=[
                ("BACKGROUND", (0, 0), (-1, -1), styles.SURFACE_ALT),
                ("BOX", (0, 0), (-1, -1), styles.HAIRLINE, styles.BORDER),
                ("ROUNDEDCORNERS", [styles.CARD_RADIUS - 1] * 4),
                ("LEFTPADDING", (0, 0), (-1, -1), 7),
                ("LEFTPADDING", (1, 0), (-1, -1), 2),
                ("RIGHTPADDING", (0, 0), (-1, -1), 7),
                ("TOPPADDING", (0, 0), (-1, -1), 5),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
                ("TOPPADDING", (0, 0), (0, 0), 7),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ],
        ))
    badge = count_phrase(len(rows), "issue")
    return graphics.card(framed, width=width, title="Warnings", badge=badge, gap=4)

