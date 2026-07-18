"""Reportlab paragraph and table styles for the session PDF report.

Centralised so the generator and the custom Flowables in ``graphics.py``
reach for the same colour palette and typography. The palette uses the
same status semantics as the GUI dashboard so a printed report and the
on-screen view read the same way:

* green  — complete / success
* amber  — incomplete / warning
* red    — failed / error
* grey   — unknown / missing
* blue   — neutral session information

Status colours are also exported as RGB tuples so the custom Flowables
can render them on a canvas without going through reportlab's
``HexColor`` adapter on every paint.
"""

from __future__ import annotations

from reportlab.lib import colors
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.platypus import TableStyle


_BASE = getSampleStyleSheet()


# --------------------------------------------------------------- semantic colour
#
# Each entry pairs the hex string used by reportlab with a slightly tinted
# background suitable for chips/badges. ``text`` is what reads well *on*
# the chip background. The values are deliberately a bit more saturated
# than the GUI palette so they survive a black-and-white print.

class StatusColor:
    def __init__(self, label: str, fill: str, bg: str, text: str) -> None:
        self.label = label
        self.fill = colors.HexColor(fill)
        self.bg = colors.HexColor(bg)
        self.text = colors.HexColor(text)


COMPLETE = StatusColor("Complete", "#10b981", "#d1fae5", "#065f46")
INCOMPLETE = StatusColor("Incomplete", "#f59e0b", "#fef3c7", "#92400e")
FAILED = StatusColor("Failed", "#ef4444", "#fee2e2", "#991b1b")
UNKNOWN = StatusColor("Unknown", "#6b7280", "#e5e7eb", "#1f2937")
SESSION = StatusColor("Session", "#3b82f6", "#dbeafe", "#1e3a8a")
NEUTRAL = StatusColor("Neutral", "#1a2733", "#eef2f6", "#1a2733")

STATUS_COLORS: dict[str, StatusColor] = {
    "complete": COMPLETE,
    "incomplete": INCOMPLETE,
    "failed": FAILED,
    "unknown": UNKNOWN,
    "session": SESSION,
    "neutral": NEUTRAL,
    # Severity aliases the warning summariser uses.
    "error": FAILED,
    "warning": INCOMPLETE,
    "info": SESSION,
}


def status_color(name: str) -> StatusColor:
    """Resolve a status / severity name to a ``StatusColor``.

    Falls back to ``NEUTRAL`` so a typo doesn't crash the renderer mid-
    report — graphics will still draw, just in the muted neutral tone.
    """

    return STATUS_COLORS.get(name.lower(), NEUTRAL)


def css_hex(color: colors.Color) -> str:
    """Return ``#RRGGBB`` for a reportlab Color.

    ``HexColor.hexval()`` produces ``0xRRGGBB`` which the Paragraph
    HTML parser doesn't accept as a colour attribute. The cover's
    severity chips and the tilt-table cell paragraphs need real CSS
    hex strings — this helper does the conversion in one place.
    """

    r = int(round(color.red * 255))
    g = int(round(color.green * 255))
    b = int(round(color.blue * 255))
    return f"#{r:02x}{g:02x}{b:02x}"


# --------------------------------------------------------------- typography
#
# Keep the size hierarchy compact. Reports embed many rows; oversized type
# wastes vertical space and drives unnecessary page breaks.

TITLE_STYLE = ParagraphStyle(
    name="ReportTitle",
    parent=_BASE["Title"],
    fontSize=22,
    leading=26,
    spaceAfter=2,
    textColor=colors.HexColor("#0f172a"),
)

SUBTITLE_STYLE = ParagraphStyle(
    name="ReportSubtitle",
    parent=_BASE["Normal"],
    fontSize=10,
    leading=13,
    textColor=colors.HexColor("#475569"),
    spaceAfter=12,
)

H1_STYLE = ParagraphStyle(
    name="ReportH1",
    parent=_BASE["Heading1"],
    fontSize=15,
    leading=19,
    textColor=colors.HexColor("#0f172a"),
    spaceBefore=14,
    spaceAfter=4,
    # Headings must stay glued to the next flowable so the layout
    # engine doesn't leave "Search maps" alone at the bottom of one
    # page and the first image on the next — see
    # report_generator's section builders.
    keepWithNext=1,
)

H2_STYLE = ParagraphStyle(
    name="ReportH2",
    parent=_BASE["Heading2"],
    fontSize=12,
    leading=15,
    textColor=colors.HexColor("#1e293b"),
    spaceBefore=10,
    spaceAfter=3,
    keepWithNext=1,
)

H3_STYLE = ParagraphStyle(
    name="ReportH3",
    parent=_BASE["Heading3"],
    fontSize=10,
    leading=13,
    textColor=colors.HexColor("#334155"),
    spaceBefore=6,
    spaceAfter=2,
    fontName="Helvetica-Bold",
    keepWithNext=1,
)

BODY_STYLE = ParagraphStyle(
    name="ReportBody",
    parent=_BASE["Normal"],
    fontSize=9,
    leading=12,
    textColor=colors.HexColor("#1f2937"),
)

SMALL_STYLE = ParagraphStyle(
    name="ReportSmall",
    parent=BODY_STYLE,
    fontSize=8,
    leading=11,
    textColor=colors.HexColor("#475569"),
)

MUTED_STYLE = ParagraphStyle(
    name="ReportMuted",
    parent=BODY_STYLE,
    fontSize=8,
    leading=11,
    textColor=colors.HexColor("#64748b"),
)

CAPTION_STYLE = ParagraphStyle(
    name="ReportCaption",
    parent=_BASE["Normal"],
    fontName="Helvetica-Oblique",
    fontSize=8,
    leading=10,
    textColor=colors.HexColor("#475569"),
    spaceBefore=2,
    spaceAfter=8,
    alignment=0,  # left
)

WARNING_STYLE = ParagraphStyle(
    name="ReportWarning",
    parent=BODY_STYLE,
    fontSize=8.5,
    leading=11,
    textColor=colors.HexColor("#7c2d12"),
)

NOTE_STYLE = ParagraphStyle(
    name="ReportNote",
    parent=BODY_STYLE,
    fontSize=8,
    leading=11,
    textColor=colors.HexColor("#475569"),
    leftIndent=8,
)


# ----------------------------------------------------------------------- tables

KEY_VALUE_TABLE_STYLE = TableStyle(
    [
        ("FONT", (0, 0), (-1, -1), "Helvetica", 9),
        ("TEXTCOLOR", (0, 0), (0, -1), colors.HexColor("#475569")),
        ("TEXTCOLOR", (1, 0), (-1, -1), colors.HexColor("#1f2937")),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
        ("TOPPADDING", (0, 0), (-1, -1), 1),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 1),
    ]
)

DATA_TABLE_STYLE = TableStyle(
    [
        ("FONT", (0, 0), (-1, 0), "Helvetica-Bold", 9),
        ("FONT", (0, 1), (-1, -1), "Helvetica", 8.5),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.HexColor("#0f172a")),
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#eef2f6")),
        ("LINEBELOW", (0, 0), (-1, 0), 0.5, colors.HexColor("#94a3b8")),
        ("LINEABOVE", (0, 0), (-1, 0), 0.5, colors.HexColor("#94a3b8")),
        ("LINEBELOW", (0, -1), (-1, -1), 0.5, colors.HexColor("#94a3b8")),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 4),
        ("RIGHTPADDING", (0, 0), (-1, -1), 4),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f8fafc")]),
    ]
)

# Compact variant used inside cards and graphical-summary panels where
# the surrounding card already provides padding and a background.
COMPACT_TABLE_STYLE = TableStyle(
    [
        ("FONT", (0, 0), (-1, -1), "Helvetica", 8.5),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 2),
        ("RIGHTPADDING", (0, 0), (-1, -1), 4),
        ("TOPPADDING", (0, 0), (-1, -1), 1),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 1),
    ]
)


__all__ = [
    "BODY_STYLE",
    "CAPTION_STYLE",
    "COMPACT_TABLE_STYLE",
    "COMPLETE",
    "DATA_TABLE_STYLE",
    "FAILED",
    "H1_STYLE",
    "H2_STYLE",
    "H3_STYLE",
    "INCOMPLETE",
    "KEY_VALUE_TABLE_STYLE",
    "MUTED_STYLE",
    "NEUTRAL",
    "NOTE_STYLE",
    "SESSION",
    "SMALL_STYLE",
    "STATUS_COLORS",
    "SUBTITLE_STYLE",
    "StatusColor",
    "TITLE_STYLE",
    "UNKNOWN",
    "WARNING_STYLE",
    "css_hex",
    "status_color",
]
