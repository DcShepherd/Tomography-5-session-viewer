"""Palette, fonts and paragraph styles for the session PDF report.

The report is the app's light theme in print. The Slate & Sage palette, the
type tiers (uppercase caption titles, body values, monospace readouts) and the
status tones are the ones the dashboard draws with, so a printed page and the
Session tab read as the same product.

The palette is the app's own ``LIGHT_PALETTE`` from
``presentation/design_tokens.py``, which is Qt-free, so a report still builds
in a worker or a test without a display.

Status colours are tones, as in the app (``STATUS_TONE_COLOURS``): each
has a ``fill`` for marks (dots, bands, rings), an ``ink`` mixed toward the
text colour only as far as small print needs to read at 4.5:1, and a pale
``tint`` behind a pill. Overlay colours drawn over microscope images are not
tones; they live with the overlay code in ``graphics.py`` and are unchanged.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT
from reportlab.lib.styles import ParagraphStyle
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

from tomography_session_browser.presentation.design_tokens import (
    LIGHT_PALETTE,
    STATUS_TONE_FILL_ALPHA,
    mix,
    readable_tone,
)

LOGGER = logging.getLogger(__name__)


# =============================================================================
# Palette: presentation/design_tokens.LIGHT_PALETTE
# =============================================================================

TEXT_STRONG = colors.HexColor(LIGHT_PALETTE.text_strong)
TEXT = colors.HexColor(LIGHT_PALETTE.text)
TEXT_MUTED = colors.HexColor(LIGHT_PALETTE.text_muted)
BORDER = colors.HexColor(LIGHT_PALETTE.border)
BORDER_STRONG = colors.HexColor(LIGHT_PALETTE.border_strong)
SURFACE = colors.HexColor(LIGHT_PALETTE.surface)
SURFACE_ALT = colors.HexColor(LIGHT_PALETTE.surface_alt)
PANEL = colors.HexColor(LIGHT_PALETTE.panel)
SURFACE_HI = colors.HexColor(LIGHT_PALETTE.surface_hi)
ACCENT = colors.HexColor(LIGHT_PALETTE.accent)
ACCENT_STRONG = colors.HexColor(LIGHT_PALETTE.accent_strong)


# =============================================================================
# Tones
# =============================================================================


@dataclass(frozen=True, slots=True)
class Tone:
    name: str
    fill: colors.Color  # dots, bands, rings, pill outlines
    ink: colors.Color  # text in the tone, readable on the page and on SURFACE_ALT
    tint: colors.Color  # pale fill behind a pill's text

    @property
    def ink_hex(self) -> str:
        return css_hex(self.ink)


def _tone(name: str, fill_hex: str) -> Tone:
    ink = readable_tone(fill_hex, LIGHT_PALETTE.surface_alt, LIGHT_PALETTE.text_strong)
    tint = mix(LIGHT_PALETTE.surface, fill_hex, STATUS_TONE_FILL_ALPHA / 255.0)
    return Tone(name, colors.HexColor(fill_hex), colors.HexColor(ink), colors.HexColor(tint))


# The app's tones (design_tokens.STATUS_TONE_COLOURS / DASHBOARD_TONE_COLOURS).
TONES: dict[str, Tone] = {
    "good": _tone("good", LIGHT_PALETTE.chart_green),
    "warn": _tone("warn", LIGHT_PALETTE.chart_amber),
    "bad": _tone("bad", LIGHT_PALETTE.chart_red),
    "info": _tone("info", LIGHT_PALETTE.chart_blue),
    "grey": _tone("grey", LIGHT_PALETTE.chart_grey),
    "unknown": _tone("unknown", LIGHT_PALETTE.unknown),
}

#: Tilt-series validation status -> tone (the dashboard donut and filters).
VALIDATION_TONES: dict[str, str] = {
    "complete": "good",
    "incomplete": "warn",
    "failed": "bad",
    "unknown": "grey",
}

#: Stat-card tile status -> tone for the band (``StatusTileGrid``).
TILE_BAND_TONES: dict[str, str] = {
    "complete": "good",
    "warning": "warn",
    "failed": "bad",
    "missing": "bad",
    "neutral": "grey",
}

#: Stat-card tile status -> tone for its chip dot (``StatCard``): neutral is
#: the palette's "unknown", as on the dashboard.
TILE_DOT_TONES: dict[str, str] = {**TILE_BAND_TONES, "neutral": "unknown"}

#: Warning severity -> tone. The dashboard gives "info" its grey fallback.
SEVERITY_TONES: dict[str, str] = {"error": "bad", "warning": "warn", "info": "grey"}


def tone(name: str | None) -> Tone:
    """Resolve a tone name; anything unknown is drawn grey rather than raising."""

    return TONES.get((name or "").lower(), TONES["grey"])


def css_hex(color: colors.Color) -> str:
    """``#rrggbb`` for a ReportLab colour, as Paragraph markup expects."""

    r = int(round(color.red * 255))
    g = int(round(color.green * 255))
    b = int(round(color.blue * 255))
    return f"#{r:02x}{g:02x}{b:02x}"


# =============================================================================
# Fonts
# =============================================================================
#
# The app sets Segoe UI for interface text and Cascadia Mono for readouts.
# The report embeds the same families when the host has them, then Consolas
# for the mono face, then the PDF base-14 faces, so a report always builds.
# Registration is lazy: the reports package is imported at app start-up and
# reading the TTFs there would cost start-up time for every session.

SANS = "ReportSans"
SANS_SEMIBOLD = "ReportSans-Semibold"
SANS_BOLD = "ReportSans-Bold"
SANS_ITALIC = "ReportSans-Italic"
MONO = "ReportMono"
MONO_BOLD = "ReportMono-Bold"

_FONT_DIRS = (
    Path("C:/Windows/Fonts"),
    Path.home() / "AppData/Local/Microsoft/Windows/Fonts",
    Path("/Library/Fonts"),
    Path.home() / "Library/Fonts",
    Path("/usr/share/fonts/truetype"),
    Path.home() / ".fonts",
)

# (face name, candidate font files in preference order, base-14 fallback)
_FACES: tuple[tuple[str, tuple[str, ...], str], ...] = (
    (SANS, ("segoeui.ttf",), "Helvetica"),
    (SANS_SEMIBOLD, ("seguisb.ttf", "segoeuib.ttf"), "Helvetica-Bold"),
    (SANS_BOLD, ("segoeuib.ttf",), "Helvetica-Bold"),
    (SANS_ITALIC, ("segoeuii.ttf",), "Helvetica-Oblique"),
    (MONO, ("CascadiaMono-Regular.ttf", "CascadiaMono.ttf", "consola.ttf"), "Courier"),
    (MONO_BOLD, ("CascadiaMono-Bold.ttf", "consolab.ttf"), "Courier-Bold"),
)

_font_lock = threading.Lock()
_font_sources: dict[str, str] = {}


def _register_families() -> None:
    # ``<b>`` and ``<i>`` in markup resolve through the family, and building a
    # Paragraph already needs the mapping. Bold is the semibold face: the
    # app's emphasis weight is 600, not 700.
    pdfmetrics.registerFontFamily(
        SANS, normal=SANS, bold=SANS_SEMIBOLD, italic=SANS_ITALIC, boldItalic=SANS_SEMIBOLD
    )
    pdfmetrics.registerFontFamily(MONO, normal=MONO, bold=MONO_BOLD, italic=MONO, boldItalic=MONO_BOLD)


# Name mappings only, no font files: cheap enough for import time, so a
# Paragraph can be built before a report starts. The faces load lazily.
_register_families()


def _find_font_file(names: tuple[str, ...]) -> Path | None:
    for name in names:
        for directory in _FONT_DIRS:
            candidate = directory / name
            try:
                if candidate.is_file():
                    return candidate
            except OSError:
                continue
    return None


def ensure_fonts() -> dict[str, str]:
    """Register the report faces once; return ``{face: source}``.

    A face whose TrueType file is missing or cannot be embedded falls back to
    a base-14 face under the same name, so every style keeps working.
    """

    with _font_lock:
        if _font_sources:
            return dict(_font_sources)
        for face, files, fallback in _FACES:
            path = _find_font_file(files)
            source = fallback
            if path is not None:
                try:
                    pdfmetrics.registerFont(TTFont(face, str(path)))
                    source = path.name
                except Exception as exc:  # restricted embedding, unreadable file
                    LOGGER.info("Report font %s unavailable (%s); using %s", path, exc, fallback)
                    path = None
            if path is None:
                pdfmetrics.registerFont(pdfmetrics.Font(face, fallback, "WinAnsiEncoding"))
            _font_sources[face] = source
        _register_families()
        return dict(_font_sources)


def string_width(text: str, font: str, size: float) -> float:
    ensure_fonts()
    return pdfmetrics.stringWidth(text, font, size)


def has_unicode_fonts() -> bool:
    """Whether the sans face is a TrueType font (arrows and minus signs render)."""

    return not ensure_fonts().get(SANS, "Helvetica").startswith("Helvetica")


# =============================================================================
# Type scale (points)
# =============================================================================
#
# The app's six steps (9/10/11/13/16/22 pt on screen) at print size. Captions
# are the uppercase card titles, keys, table headers and chips.

TYPE_CAPTION = 6.6
TYPE_SMALL = 7.2
TYPE_BODY = 8.0
TYPE_LEAD = 9.6
TYPE_TITLE = 11.5
TYPE_HEADING = 15.0
TYPE_DISPLAY = 20.0
TYPE_VALUE = 16.0  # stat-card values

#: Letter spacing of uppercase captions, in points.
CAPTION_TRACKING = 0.35


def _style(name: str, **kwargs) -> ParagraphStyle:
    defaults = dict(fontName=SANS, fontSize=TYPE_BODY, leading=TYPE_BODY * 1.32, textColor=TEXT)
    defaults.update(kwargs)
    return ParagraphStyle(name=name, **defaults)


EYEBROW_STYLE = _style(
    "ReportEyebrow",
    fontName=SANS_SEMIBOLD,
    fontSize=TYPE_CAPTION,
    leading=TYPE_CAPTION * 1.4,
    textColor=ACCENT_STRONG,
    textTransform="uppercase",
)

TITLE_STYLE = _style(
    "ReportTitle",
    fontName=SANS_SEMIBOLD,
    fontSize=TYPE_DISPLAY,
    leading=TYPE_DISPLAY * 1.18,
    textColor=TEXT_STRONG,
    spaceAfter=3,
)

SECTION_TITLE_STYLE = _style(
    "ReportSectionTitle",
    fontName=SANS_SEMIBOLD,
    fontSize=TYPE_HEADING,
    leading=TYPE_HEADING * 1.2,
    textColor=TEXT_STRONG,
    spaceAfter=2,
)

# Subsection heading (Atlas, Overviews, Search maps, Tilt series). Kept with
# the flowable that follows so a heading never ends a page alone.
H2_STYLE = _style(
    "ReportH2",
    fontName=SANS_SEMIBOLD,
    fontSize=TYPE_LEAD,
    leading=TYPE_LEAD * 1.3,
    textColor=TEXT_STRONG,
    spaceBefore=10,
    spaceAfter=7,
    keepWithNext=1,
)

CARD_TITLE_STYLE = _style(
    "ReportCardTitle",
    fontName=SANS_SEMIBOLD,
    fontSize=TYPE_CAPTION,
    leading=TYPE_CAPTION * 1.35,
    textColor=TEXT_MUTED,
    textTransform="uppercase",
)

CARD_TITLE_RIGHT_STYLE = _style(
    "ReportCardTitleRight",
    fontName=SANS_SEMIBOLD,
    fontSize=TYPE_CAPTION,
    leading=TYPE_CAPTION * 1.35,
    textColor=TEXT_MUTED,
    textTransform="uppercase",
    alignment=TA_RIGHT,
)

CARD_TITLE_CENTRE_STYLE = _style(
    "ReportCardTitleCentre",
    fontName=SANS_SEMIBOLD,
    fontSize=TYPE_CAPTION,
    leading=TYPE_CAPTION * 1.35,
    textColor=TEXT_MUTED,
    textTransform="uppercase",
    alignment=TA_CENTER,
)

CARD_BADGE_STYLE = _style(
    "ReportCardBadge",
    fontSize=TYPE_CAPTION,
    leading=TYPE_CAPTION * 1.35,
    textColor=TEXT_MUTED,
    alignment=TA_RIGHT,
)

KEY_STYLE = CARD_TITLE_STYLE

BODY_STYLE = _style("ReportBody")

BODY_STRONG_STYLE = _style("ReportBodyStrong", fontName=SANS_SEMIBOLD, textColor=TEXT_STRONG)

SMALL_STYLE = _style(
    "ReportSmall",
    fontSize=TYPE_SMALL,
    leading=TYPE_SMALL * 1.33,
    textColor=TEXT,
)

MUTED_STYLE = _style(
    "ReportMuted",
    fontSize=TYPE_SMALL,
    leading=TYPE_SMALL * 1.33,
    textColor=TEXT_MUTED,
)

MUTED_RIGHT_STYLE = _style(
    "ReportMutedRight",
    fontSize=TYPE_SMALL,
    leading=TYPE_SMALL * 1.33,
    textColor=TEXT_MUTED,
    alignment=TA_RIGHT,
)

CAPTION_STYLE = _style(
    "ReportCaption",
    fontSize=TYPE_SMALL,
    leading=TYPE_SMALL * 1.33,
    textColor=TEXT_MUTED,
    spaceBefore=2,
    spaceAfter=4,
    alignment=TA_LEFT,
)

NOTE_STYLE = _style(
    "ReportNote",
    fontSize=TYPE_SMALL,
    leading=TYPE_SMALL * 1.35,
    textColor=TEXT_MUTED,
)

WARNING_STYLE = _style(
    "ReportWarning",
    fontSize=TYPE_SMALL,
    leading=TYPE_SMALL * 1.35,
    textColor=TONES["warn"].ink,
)

# Monospace readouts: values with units, identifiers, counts that align.
VALUE_STYLE = _style(
    "ReportValue",
    fontName=MONO,
    fontSize=TYPE_BODY - 0.3,
    leading=(TYPE_BODY - 0.3) * 1.3,
    textColor=TEXT_STRONG,
)

# A readout that is an absence ("n/a"): the same face, in the muted colour.
VALUE_MUTED_STYLE = _style(
    "ReportValueMuted",
    fontName=MONO,
    fontSize=TYPE_BODY - 0.3,
    leading=(TYPE_BODY - 0.3) * 1.3,
    textColor=TEXT_MUTED,
)

MONO_STYLE = _style(
    "ReportMono",
    fontName=MONO,
    fontSize=TYPE_SMALL,
    leading=TYPE_SMALL * 1.33,
    textColor=TEXT,
)

MONO_RIGHT_STYLE = _style(
    "ReportMonoRight",
    fontName=MONO,
    fontSize=TYPE_SMALL,
    leading=TYPE_SMALL * 1.33,
    textColor=TEXT,
    alignment=TA_RIGHT,
)

TABLE_HEADER_STYLE = _style(
    "ReportTableHeader",
    fontName=SANS_SEMIBOLD,
    fontSize=TYPE_CAPTION,
    leading=TYPE_CAPTION * 1.35,
    textColor=TEXT_MUTED,
    textTransform="uppercase",
)

TABLE_HEADER_RIGHT_STYLE = _style(
    "ReportTableHeaderRight",
    fontName=SANS_SEMIBOLD,
    fontSize=TYPE_CAPTION,
    leading=TYPE_CAPTION * 1.35,
    textColor=TEXT_MUTED,
    textTransform="uppercase",
    alignment=TA_RIGHT,
)

TABLE_HEADER_CENTRE_STYLE = _style(
    "ReportTableHeaderCentre",
    fontName=SANS_SEMIBOLD,
    fontSize=TYPE_CAPTION,
    leading=TYPE_CAPTION * 1.35,
    textColor=TEXT_MUTED,
    textTransform="uppercase",
    alignment=TA_CENTER,
)

TABLE_UNIT_CENTRE_STYLE = _style(
    "ReportTableUnitCentre",
    fontSize=TYPE_CAPTION,
    leading=TYPE_CAPTION * 1.35,
    textColor=TEXT_MUTED,
    alignment=TA_CENTER,
)

MONO_CENTRE_STYLE = _style(
    "ReportMonoCentre",
    fontName=MONO,
    fontSize=TYPE_SMALL,
    leading=TYPE_SMALL * 1.33,
    textColor=TEXT,
    alignment=TA_CENTER,
)

MUTED_MONO_CENTRE_STYLE = _style(
    "ReportMutedMonoCentre",
    fontName=MONO,
    fontSize=TYPE_SMALL,
    leading=TYPE_SMALL * 1.33,
    textColor=TEXT_MUTED,
    alignment=TA_CENTER,
)

TABLE_CELL_STYLE = _style(
    "ReportTableCell",
    fontSize=TYPE_SMALL + 0.2,
    leading=(TYPE_SMALL + 0.2) * 1.3,
    textColor=TEXT_STRONG,
)


# =============================================================================
# Card geometry
# =============================================================================

#: Corner radius of cards and framed rows (``design_tokens.RADIUS_CARD`` at print size).
CARD_RADIUS = 5.0
#: Corner radius of chips and badges (``design_tokens.RADIUS_CONTROL``).
CONTROL_RADIUS = 2.5
CARD_PADDING = 9.0
CARD_GAP = 8.0
HAIRLINE = 0.6


__all__ = [
    "ACCENT",
    "ACCENT_STRONG",
    "BODY_STRONG_STYLE",
    "BODY_STYLE",
    "BORDER",
    "BORDER_STRONG",
    "CAPTION_STYLE",
    "CARD_BADGE_STYLE",
    "CARD_GAP",
    "CARD_PADDING",
    "CARD_RADIUS",
    "CARD_TITLE_RIGHT_STYLE",
    "CARD_TITLE_STYLE",
    "CONTROL_RADIUS",
    "EYEBROW_STYLE",
    "H2_STYLE",
    "HAIRLINE",
    "KEY_STYLE",
    "LIGHT_PALETTE_SLOTS",
    "MONO",
    "MONO_BOLD",
    "MONO_CENTRE_STYLE",
    "MONO_RIGHT_STYLE",
    "MUTED_MONO_CENTRE_STYLE",
    "MONO_STYLE",
    "MUTED_RIGHT_STYLE",
    "MUTED_STYLE",
    "NOTE_STYLE",
    "SANS",
    "SANS_BOLD",
    "SANS_ITALIC",
    "SANS_SEMIBOLD",
    "SECTION_TITLE_STYLE",
    "SEVERITY_TONES",
    "SMALL_STYLE",
    "SURFACE",
    "SURFACE_ALT",
    "SURFACE_HI",
    "TABLE_CELL_STYLE",
    "TABLE_HEADER_CENTRE_STYLE",
    "TABLE_HEADER_RIGHT_STYLE",
    "TABLE_HEADER_STYLE",
    "TABLE_UNIT_CENTRE_STYLE",
    "TEXT",
    "TEXT_MUTED",
    "TEXT_STRONG",
    "TILE_BAND_TONES",
    "TILE_DOT_TONES",
    "TITLE_STYLE",
    "TONES",
    "Tone",
    "VALIDATION_TONES",
    "VALUE_MUTED_STYLE",
    "VALUE_STYLE",
    "WARNING_STYLE",
    "contrast",
    "css_hex",
    "ensure_fonts",
    "has_unicode_fonts",
    "readable_tone",
    "string_width",
    "tone",
]
