"""The app's design tokens: palettes, tones, type, spacing and radii.

Qt-free, so the window (through ``ui/theme.py`` and ``ui/stylesheet.py``)
and the PDF (``reports/styles.py``) draw from one definition: the report is
the light palette in print, not a copy of it. The colour maths that keeps a
tone readable as small text lives here for the same reason.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from tomography_session_browser.presentation import marker_palette


# Qt stylesheets do not handle CSS-style fallback stacks reliably on Windows.
# Keep the guide's sans/mono split, but use single installed families so text
# never renders as placeholder squares when Inter / JetBrains Mono are absent.
SANS_FONT_NAME: Final[str] = "Segoe UI"
MONO_FONT_NAME: Final[str] = "Cascadia Mono"
SANS_FONT_FAMILY: Final[str] = f'"{SANS_FONT_NAME}"'
MONO_FONT_FAMILY: Final[str] = f'"{MONO_FONT_NAME}"'

# Corner radii. The chrome had drifted into ten values (3–15 px); these three
# steps replace them:
#   RADIUS_CONTROL: buttons, inputs, chips, menu items, list and tree rows;
#   RADIUS_CARD:    cards, panels, menus, tooltips, floating viewer panels;
#   RADIUS_POPOVER: large floating surfaces such as the loading card.
# A menu uses the card radius on purpose: with 4 px of padding, its 4 px items
# sit concentric inside its 8 px corner.
#
# Pills are the one exception. Qt draws a *square* corner when the radius is
# more than half the widget's height, so a pill's radius belongs to its own
# height and stays just under half of it. Mark such a line ``/* pill */``; the
# style-guide test rejects any other radius that is not one of these tokens.
RADIUS_CONTROL: Final[int] = 4
RADIUS_CARD: Final[int] = 8
RADIUS_POPOVER: Final[int] = 12
RADIUS_TOKENS: Final[tuple[int, ...]] = (RADIUS_CONTROL, RADIUS_CARD, RADIUS_POPOVER)

# Spacing on a 4 px grid. The chrome had 23 margin tuples and 13 spacing
# values; layouts adopt these as each surface is migrated (plan F2).
#   SPACE_XS: a label and its value, an icon and its label, rows in a
#             dense list (the context panel, card row lists);
#   SPACE_S:  items in a row, a key column and its values, blocks in a stack;
#   SPACE_M:  cards in a grid, the padding inside a card, an indent;
#   SPACE_L:  groups in a row, sections on a page;
#   SPACE_XL / SPACE_XXL: wide separation (side-by-side readouts).
# Values are chosen by role, not by rounding the old number: a dense list
# stays dense.
SPACE_XS: Final[int] = 4
SPACE_S: Final[int] = 8
SPACE_M: Final[int] = 12
SPACE_L: Final[int] = 16
SPACE_XL: Final[int] = 24
SPACE_XXL: Final[int] = 32
SPACE_SCALE: Final[tuple[int, ...]] = (SPACE_XS, SPACE_S, SPACE_M, SPACE_L, SPACE_XL, SPACE_XXL)

# Type scale in points. Thirteen sizes (8–26 pt) had accumulated; these six
# steps replace them. Body text is the application font.
#   TYPE_CAPTION: card titles, section headings, keys, chips, counts, notes;
#   TYPE_BODY:    values, row names, detail text;
#   TYPE_LEAD:    panel titles and the landmark tier;
#   TYPE_TITLE:   health readouts;
#   TYPE_HEADING: the viewer title;
#   TYPE_DISPLAY: the dashboard title and card values.
# Monospace is kept for readouts (numbers with units, raw metadata), not for
# chrome. Counts use Segoe UI: its Regular digits are already tabular. Its
# Semibold digits are not, so a bold number that must align in a column needs
# ``QFont.setFeature(QFont.Tag("tnum"), 1)``.
TYPE_CAPTION: Final[int] = 9
TYPE_BODY: Final[int] = 10
TYPE_LEAD: Final[int] = 11
TYPE_TITLE: Final[int] = 13
TYPE_HEADING: Final[int] = 16
TYPE_DISPLAY: Final[int] = 22
TYPE_SCALE: Final[tuple[int, ...]] = (
    TYPE_CAPTION,
    TYPE_BODY,
    TYPE_LEAD,
    TYPE_TITLE,
    TYPE_HEADING,
    TYPE_DISPLAY,
)


@dataclass(frozen=True)
class ThemePalette:
    """Colours used to render the application chrome.

    ``chart_*`` slots expose status colours for the app chrome and dashboard
    widgets. ``marker_*`` slots are deliberately brighter: they draw directly
    over microscope images and must stay high-contrast even while the broader
    UI follows the softer Slate & Sage palette.
    """

    name: str
    background: str
    surface: str
    surface_alt: str
    panel: str
    border: str
    border_strong: str
    text: str
    text_muted: str
    text_strong: str
    accent: str
    accent_strong: str
    accent_soft: str
    accent_line: str
    # Primary-button fill/label. Kept as their own tokens because the right
    # combination inverts between palettes: dark mode wants the light accent
    # under dark text, light mode a dark teal under light text. Reusing
    # ``accent`` for both left the light theme's button label at 3.3:1.
    primary_fill: str
    primary_fill_hover: str
    primary_text: str
    # Text of a link-style action (Clear filter, an empty state's secondary
    # actions). ``accent`` read at 3.6:1 on the light theme's white panels,
    # under the 4.5:1 that text this size needs.
    link_text: str
    link_text_hover: str
    selection: str
    selection_text: str
    selection_marker: str
    control_border: str
    scroll_thumb: str
    canvas: str
    safe_text_dark: str
    safe_text_light: str
    chart_blue: str
    chart_green: str
    chart_amber: str
    chart_red: str
    chart_violet: str
    chart_grey: str
    surface_hi: str
    unknown: str
    marker_exposure: str
    marker_focus: str
    marker_tracking: str
    marker_template: str
    marker_camera: str
    marker_condition: str
    marker_link: str
    overlay_label_shadow: str
    overlay_label_halo: str
    overlay_selected: str
    overlay_unresolved: str
    overlay_camera_fill: str
    overlay_batch_label_exposure: str
    overlay_batch_label_camera: str
    overlay_batch_label_default: str
    overlay_status_failed: str
    overlay_status_warning: str
    overlay_status_pending: str
    overlay_status_complete: str
    overlay_status_unattributed: str
    overlay_marker_halo: str
    atlas_marker_collected: str
    atlas_marker_partial: str
    atlas_marker_queued: str
    atlas_marker_failed: str
    atlas_marker_unattributed: str
    atlas_marker_ink: str
    atlas_marker_selected: str
    atlas_marker_label_text: str
    icon: str  # default monochrome icon stroke colour


DARK_PALETTE: Final[ThemePalette] = ThemePalette(
    name="dark",
    background="#0c1116",
    surface="#131922",
    surface_alt="#171e29",
    panel="#0f151c",
    border="#1e2632",
    border_strong="#283142",
    text="#d6dde6",
    text_muted="#8d96a4",
    text_strong="#f0f3f7",
    accent="#6fb3a8",
    accent_strong="#4f9388",
    accent_soft="rgba(111, 179, 168, 0.10)",
    accent_line="rgba(111, 179, 168, 0.28)",
    primary_fill="#6fb3a8",
    primary_fill_hover="#4f9388",
    primary_text="#0c1116",
    link_text="#6fb3a8",
    link_text_hover="#8fc6bd",
    selection="#223c44",
    selection_text="#f0f3f7",
    selection_marker="#6fb3a8",
    control_border="#5c6c7e",
    scroll_thumb="#657386",
    canvas="#04070b",
    safe_text_dark="#0c1116",
    safe_text_light="#f0f3f7",
    chart_blue="#8997c2",
    chart_green="#7eb287",
    chart_amber="#d2a865",
    chart_red="#cf7a7a",
    # Distinct from chart_blue. These two were the same hex, so the fifth and
    # second series of a plot rendered identically — invisible with four
    # samples, a silent data error with five. Pushed toward magenta rather
    # than merely lightened so the separation is one of hue, not brightness.
    chart_violet="#c58ac0",
    chart_grey="#6f7888",
    surface_hi="#1c2532",
    unknown="#4a525d",
    # On-image colours: one set for both themes and the PDF
    # (``presentation.marker_palette``).
    marker_exposure=marker_palette.MARKER_EXPOSURE,
    marker_focus=marker_palette.MARKER_FOCUS,
    marker_tracking=marker_palette.MARKER_TRACKING,
    marker_template=marker_palette.MARKER_TEMPLATE,
    marker_camera=marker_palette.MARKER_CAMERA,
    marker_condition=marker_palette.MARKER_CONDITION,
    marker_link=marker_palette.MARKER_LINK,
    overlay_label_shadow="#020617",
    overlay_label_halo="#f8fafc",
    overlay_selected=marker_palette.SELECTED,
    overlay_unresolved=marker_palette.UNRESOLVED,
    overlay_camera_fill=marker_palette.CAMERA_FILL,
    overlay_batch_label_exposure="#065f46",
    overlay_batch_label_camera="#1d4ed8",
    overlay_batch_label_default="#166534",
    overlay_status_failed=marker_palette.STATUS_FAILED,
    overlay_status_warning=marker_palette.STATUS_WARNING,
    overlay_status_pending=marker_palette.STATUS_PENDING,
    overlay_status_complete=marker_palette.STATUS_COMPLETE,
    overlay_status_unattributed=marker_palette.STATUS_UNATTRIBUTED,
    overlay_marker_halo="#020617",
    atlas_marker_collected="#48B06C",
    atlas_marker_partial="#C78B09",
    atlas_marker_queued="#599FD8",
    atlas_marker_failed="#FD7468",
    atlas_marker_unattributed="#8F9AA4",
    atlas_marker_ink="#090E12",
    atlas_marker_selected="#EED059",
    atlas_marker_label_text="#D6DDE6",
    icon="#d6dde6",
)


LIGHT_PALETTE: Final[ThemePalette] = ThemePalette(
    name="light",
    background="#f3f5f7",
    surface="#ffffff",
    surface_alt="#f6f8fa",
    panel="#eaeef2",
    border="#dde2e8",
    border_strong="#c7cdd4",
    text="#2a323d",
    text_muted="#5f6b76",
    text_strong="#0f1620",
    accent="#4f9388",
    accent_strong="#3f7a72",
    accent_soft="rgba(79, 147, 136, 0.12)",
    accent_line="rgba(79, 147, 136, 0.30)",
    primary_fill="#33665f",
    primary_fill_hover="#295450",
    primary_text="#f0f3f7",
    link_text="#3f7a72",
    link_text_hover="#295450",
    selection="#cee3db",
    selection_text="#0f1620",
    selection_marker="#3f7a72",
    control_border="#8793a1",
    scroll_thumb="#8d98a4",
    canvas="#04070b",
    safe_text_dark="#0c1116",
    safe_text_light="#f0f3f7",
    chart_blue="#6e7da9",
    chart_green="#4f8a5b",
    chart_amber="#96702f",
    chart_red="#b85d5d",
    # See the dark palette: chart_violet must not duplicate chart_blue.
    chart_violet="#94519a",
    chart_grey="#79818b",
    surface_hi="#e6ebf0",
    unknown="#a8aeb6",
    # On-image colours: one set for both themes and the PDF
    # (``presentation.marker_palette``).
    marker_exposure=marker_palette.MARKER_EXPOSURE,
    marker_focus=marker_palette.MARKER_FOCUS,
    marker_tracking=marker_palette.MARKER_TRACKING,
    marker_template=marker_palette.MARKER_TEMPLATE,
    marker_camera=marker_palette.MARKER_CAMERA,
    marker_condition=marker_palette.MARKER_CONDITION,
    marker_link=marker_palette.MARKER_LINK,
    overlay_label_shadow="#020617",
    overlay_label_halo="#f8fafc",
    overlay_selected=marker_palette.SELECTED,
    overlay_unresolved=marker_palette.UNRESOLVED,
    overlay_camera_fill=marker_palette.CAMERA_FILL,
    overlay_batch_label_exposure="#065f46",
    overlay_batch_label_camera="#1d4ed8",
    overlay_batch_label_default="#166534",
    overlay_status_failed=marker_palette.STATUS_FAILED,
    overlay_status_warning=marker_palette.STATUS_WARNING,
    overlay_status_pending=marker_palette.STATUS_PENDING,
    overlay_status_complete=marker_palette.STATUS_COMPLETE,
    overlay_status_unattributed=marker_palette.STATUS_UNATTRIBUTED,
    overlay_marker_halo="#020617",
    atlas_marker_collected="#137D41",
    atlas_marker_partial="#8E5E00",
    atlas_marker_queued="#036EAE",
    atlas_marker_failed="#C53732",
    atlas_marker_unattributed="#606A74",
    atlas_marker_ink="#090E12",
    atlas_marker_selected="#B98A00",
    atlas_marker_label_text="#D6DDE6",
    icon="#2a323d",
)


def palette_for(name: str | None) -> ThemePalette:
    """Resolve a stored theme name to a palette, defaulting to dark."""

    if name == "light":
        return LIGHT_PALETTE
    return DARK_PALETTE


#: Chrome drawn over the image canvas: the zoom stack, Overlays and its panel,
#: the image badge, the Atlas marker legend and the cluster popup. The canvas
#: is near-black in both themes (``canvas``), so these keep the dark palette in
#: both: white controls on a black canvas were the loudest thing on a light
#: theme's viewer, and the painted legend and popup were already dark.
CANVAS_CHROME: Final[ThemePalette] = DARK_PALETTE


# Status tones, as the palette slot each one is drawn in. The viewer list's
# badges paint them; the header status chip gets them from the stylesheet
# through its ``tone`` property, so a theme switch recolours it. It used to
# carry the colours in its own inline style, which kept the old palette: after
# switching to light, "Acquired" and "Failed" were white on a pale fill.
STATUS_TONE_COLOURS: Final[dict[str, str]] = {
    "good": "chart_green",
    "warn": "chart_amber",
    "bad": "chart_red",
    "info": "chart_blue",
    "neutral": "chart_grey",
}
# Alpha (of 255) of a status tone's fill behind its text.
STATUS_TONE_FILL_ALPHA: Final[int] = 40

#: Normal states use a disc/ring rather than an exception pill, on screen and paper.
NORMAL_STATUS_GLYPHS: Final[dict[str, str]] = {
    "complete": "disc", "available": "disc", "pending": "ring",
    "with acquisitions": "disc", "no acquisitions": "ring",
}


#: The dashboard's status tones: the colour a status gives its dot, chip or
#: count. They carried inline style sheets that baked the palette in, so a
#: theme switch rebuilt the whole dashboard to recolour them (~150 ms with its
#: clean-up), and Qt re-applied each of those sheets at every restyle. Now a
#: widget carries only its ``tone`` and these rules colour it, so a switch
#: recolours it in place. (The dashboard's "unknown" is not the chart grey.)
DASHBOARD_TONE_COLOURS: Final[dict[str, str]] = {
    "good": "chart_green",
    "warn": "chart_amber",
    "bad": "chart_red",
    "unknown": "unknown",
    "grey": "chart_grey",
}


def hex_rgb(colour: str) -> tuple[int, int, int]:
    value = colour.lstrip("#")
    return int(value[0:2], 16), int(value[2:4], 16), int(value[4:6], 16)


def _luminance(colour: str) -> float:
    def linear(channel: int) -> float:
        value = channel / 255.0
        return value / 12.92 if value <= 0.04045 else ((value + 0.055) / 1.055) ** 2.4

    red, green, blue = (linear(channel) for channel in hex_rgb(colour))
    return 0.2126 * red + 0.7152 * green + 0.0722 * blue


def contrast(foreground: str, background: str) -> float:
    lighter, darker = sorted((_luminance(foreground), _luminance(background)), reverse=True)
    return (lighter + 0.05) / (darker + 0.05)


def mix(start: str, end: str, amount: float) -> str:
    """The colour ``amount`` (0 to 1) of the way from ``start`` to ``end``."""

    a, b = hex_rgb(start), hex_rgb(end)
    return "#" + "".join(f"{round(x + (y - x) * amount):02x}" for x, y in zip(a, b))


def readable_tone(tone: str, background: str, toward: str, *, floor: float = 4.5) -> str:
    """``tone``, mixed toward ``toward`` only as far as text needs to read.

    The status colours are chart colours: in the light palette the red and
    amber fall just short of 4.5:1 as small text on the pale panels.
    """

    for step in range(0, 21):
        mixed = mix(tone, toward, step / 20.0)
        if contrast(mixed, background) >= floor:
            return mixed
    return toward
