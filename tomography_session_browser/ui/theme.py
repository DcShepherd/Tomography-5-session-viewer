"""Application theming.

The app chrome follows the Slate & Sage style guide supplied with the
project: Inter for interface text, JetBrains Mono for identifiers/readouts,
compact 4/8/12 px radii, and a calm sage accent shared across light and dark
mode. ``build_stylesheet`` substitutes the palette into one QSS template so
both themes stay in sync. ``apply_theme`` installs the stylesheet and updates
the icon-loader's default colour so freshly-built actions match the theme.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Final

from PySide6.QtCore import QEvent, QObject, QStandardPaths, Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QApplication, QWidget
import shiboken6


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
    marker_search: str
    marker_overview: str
    marker_batch: str
    marker_tilt: str
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
    marker_exposure="#16a34a",
    marker_focus="#2563eb",
    marker_tracking="#facc15",
    marker_search="#22c55e",
    marker_overview="#60a5fa",
    marker_batch="#22c55e",
    marker_tilt="#2563eb",
    marker_template="#84cc16",
    marker_camera="#38bdf8",
    marker_condition="#f97316",
    marker_link="#7c3aed",
    overlay_label_shadow="#020617",
    overlay_label_halo="#f8fafc",
    overlay_selected="#fbbf24",
    overlay_unresolved="#f87171",
    overlay_camera_fill="#14b8a6",
    overlay_batch_label_exposure="#065f46",
    overlay_batch_label_camera="#1d4ed8",
    overlay_batch_label_default="#166534",
    overlay_status_failed="#f85149",
    overlay_status_warning="#d29922",
    overlay_status_pending="#38bdf8",
    overlay_status_complete="#3fb950",
    overlay_status_unattributed="#9ca3af",
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
    marker_exposure="#16a34a",
    marker_focus="#2563eb",
    marker_tracking="#facc15",
    marker_search="#22c55e",
    marker_overview="#60a5fa",
    marker_batch="#22c55e",
    marker_tilt="#2563eb",
    marker_template="#84cc16",
    marker_camera="#38bdf8",
    marker_condition="#f97316",
    marker_link="#7c3aed",
    overlay_label_shadow="#020617",
    overlay_label_halo="#f8fafc",
    overlay_selected="#fbbf24",
    overlay_unresolved="#f87171",
    overlay_camera_fill="#14b8a6",
    overlay_batch_label_exposure="#065f46",
    overlay_batch_label_camera="#1d4ed8",
    overlay_batch_label_default="#166534",
    overlay_status_failed="#f85149",
    overlay_status_warning="#d29922",
    overlay_status_pending="#38bdf8",
    overlay_status_complete="#3fb950",
    overlay_status_unattributed="#9ca3af",
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


def _status_tone_rules(palette: ThemePalette) -> str:
    rules = []
    for tone, slot in STATUS_TONE_COLOURS.items():
        colour = getattr(palette, slot).lstrip("#")
        red, green, blue = (int(colour[index : index + 2], 16) for index in (0, 2, 4))
        rules.append(
            f'QLabel#viewerChipStatus[tone="{tone}"] {{\n'
            f"    background: rgba({red}, {green}, {blue}, {STATUS_TONE_FILL_ALPHA});\n"
            f"    border-color: #{colour};\n"
            "}\n"
        )
    return "\n".join(rules)


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


def _dashboard_tone_rules(palette: ThemePalette) -> str:
    rules = []
    for tone, slot in DASHBOARD_TONE_COLOURS.items():
        colour = getattr(palette, slot)
        rules.append(
            f'QLabel#statusDot[tone="{tone}"] {{ color: {colour}; }}\n'
            f'QLabel#searchMapSummaryChip[tone="{tone}"] {{ border-color: {colour}; color: {colour}; }}\n'
            f'QLabel#sampleCount[tone="{tone}"] {{ color: {palette.text_strong}; border-color: {colour}; }}\n'
            f'QLabel#warningCount[tone="{tone}"] {{ color: {colour}; border-color: {colour}; }}\n'
        )
    rules.append('QLabel#searchMapSummaryChip[spaced="true"] { margin-top: 4px; }\n')
    return "\n".join(rules)


def _hex_rgb(colour: str) -> tuple[int, int, int]:
    value = colour.lstrip("#")
    return int(value[0:2], 16), int(value[2:4], 16), int(value[4:6], 16)


def _luminance(colour: str) -> float:
    def linear(channel: int) -> float:
        value = channel / 255.0
        return value / 12.92 if value <= 0.04045 else ((value + 0.055) / 1.055) ** 2.4

    red, green, blue = (linear(channel) for channel in _hex_rgb(colour))
    return 0.2126 * red + 0.7152 * green + 0.0722 * blue


def _contrast(foreground: str, background: str) -> float:
    lighter, darker = sorted((_luminance(foreground), _luminance(background)), reverse=True)
    return (lighter + 0.05) / (darker + 0.05)


def readable_tone(tone: str, background: str, toward: str, *, floor: float = 4.5) -> str:
    """``tone``, mixed toward ``toward`` only as far as text needs to read.

    The status colours are chart colours: in the light palette the red and
    amber fall just short of 4.5:1 as small text on the pale panels.
    """

    start, end = _hex_rgb(tone), _hex_rgb(toward)
    for step in range(0, 21):
        amount = step / 20.0
        mixed = "#" + "".join(f"{round(a + (b - a) * amount):02x}" for a, b in zip(start, end))
        if _contrast(mixed, background) >= floor:
            return mixed
    return toward


def _session_intake_rules(palette: ThemePalette) -> str:
    """Tone colours for the Load sessions window and the drop receipt."""

    red, green, blue = _hex_rgb(palette.chart_red)
    good = readable_tone(palette.selection_marker, palette.surface, palette.text_strong)
    bad_on_surface = readable_tone(palette.chart_red, palette.surface, palette.text_strong)
    bad_on_panel = readable_tone(palette.chart_red, palette.panel, palette.text_strong)
    warn = readable_tone(palette.chart_amber, palette.surface_alt, palette.text_strong)
    return (
        f'QFrame#queuedFolderRow[tone="bad"] {{ background: rgba({red}, {green}, {blue}, 12); }}\n'
        f"QLabel#queuedFolderReason {{ color: {bad_on_panel}; }}\n"
        f'QLabel#queueCount[tone="good"] {{ color: {good}; }}\n'
        f'QLabel#queueCount[tone="bad"] {{ color: {bad_on_surface}; }}\n'
        f"QLabel#dropReceiptSkipped {{ color: {warn}; }}\n"
    )


_QSS_TEMPLATE = """
QWidget {{
    color: {text};
    font-family: {sans_font};
    font-size: {type_body}pt;
}}

QMainWindow,
QDialog {{
    background: {background};
}}

QWidget#centralShell,
QWidget#contextShell,
QWidget#projectPanel,
QWidget#viewerListPanel,
QWidget#viewerCanvasShell,
QWidget#viewerTopControls,
QWidget#tabPage,
QWidget#sessionDashboard,
QWidget#dashboardContent,
QWidget#dashboardViewport,
QWidget#metadataPanel,
QWidget#metadataBody,
QWidget#metadataViewport,
QTabWidget#mainTabs {{
    background: {background};
}}

QScrollArea#dashboardScroll,
QScrollArea#metadataScroll {{
    background: {background};
    border: 0;
}}

/* The context panel as a drawer over the page on narrow windows (plan D2):
   the page runs on beneath it, so a strong edge marks where it begins. */
QFrame#contextDrawer {{
    background: {background};
    border: 0;
    border-left: 1px solid {border_strong};
}}

QLabel {{
    background: transparent;
}}

QToolTip {{
    background: {surface};
    color: {text};
    border: 1px solid {border_strong};
    border-radius: {radius_card}px;
    padding: 6px 8px;
}}

/* Menus must be themed explicitly. Without these rules Qt keeps the native
   platform menu background while the global ``QWidget`` rule above still
   forces the app's text colour onto the items — so a light app theme on a
   dark-mode desktop (or the reverse) renders dark-on-dark text. The project
   tree's context menu is the only route to rename/remove a group, so this
   has to work regardless of the desktop theme. */
QMenu {{
    background: {surface};
    color: {text};
    border: 1px solid {border_strong};
    border-radius: {radius_card}px;
    padding: 4px;
}}

QMenu::item {{
    background: transparent;
    color: {text};
    border-radius: {radius_control}px;
    padding: 6px 24px 6px 12px;
}}

QMenu::item:selected {{
    background: {selection};
    color: {selection_text};
}}

QMenu::item:disabled {{
    color: {text_muted};
}}

QMenu::item:disabled:selected {{
    background: transparent;
    color: {text_muted};
}}

QMenu::separator {{
    background: {border};
    height: 1px;
    margin: 4px 8px;
}}

QWidget#projectPanel,
QWidget#viewerListPanel {{
    background: {panel};
}}

QWidget#viewerCanvasShell {{
    background: {canvas};
    border: 1px solid {border};
    border-radius: {radius_card}px;
}}

QWidget#viewerTopControls,
QWidget#viewerFloatingTopRightAnchor,
QWidget#viewerFloatingBottomRightAnchor {{
    background: transparent;
    border: 0;
}}

QMainWindow::separator {{
    background: {border};
    width: 1px;
    height: 1px;
}}

QToolBar#mainToolbar,
QToolBar {{
    background: {surface};
    border: 0;
    border-bottom: 1px solid {border};
    spacing: 4px;
    padding: 3px 12px;
}}

QToolBar QToolButton {{
    background: transparent;
    border: 1px solid transparent;
    border-radius: {radius_control}px;
    color: {text};
    padding: 4px 8px;
    margin: 0 1px;
    font-weight: 500;
}}

QToolBar QToolButton:hover {{
    background: {surface_alt};
    border-color: {border};
}}

QToolBar QToolButton:pressed {{
    background: {accent_soft};
    border-color: {accent_line};
}}

/* A checked toggle (Project, Context) is a soft fill, not a box: it was the
   only bordered action in a borderless toolbar (plan C12). */
QToolBar QToolButton:checked {{
    background: {accent_soft};
    border-color: transparent;
    color: {text_strong};
}}

QToolBar QToolButton:disabled {{
    color: {text_muted};
}}

QToolBar::separator {{
    background: {border};
    width: 1px;
    margin: 6px 6px;
}}

QLabel#appTitle {{
    background: transparent;
    color: {text_strong};
    font-size: {type_title}pt;
    font-weight: 600;
    padding-right: 12px;
}}

QLabel#tabTitle {{
    color: {text_strong};
    font-size: {type_lead}pt;
    font-weight: 600;
}}

QLabel#contextTitle {{
    color: {text_strong};
    font-size: {type_lead}pt;
    font-weight: 600;
    padding: 0 0 8px 0;
    border-bottom: 1px solid {border};
}}

QLabel#metaSection {{
    color: {text_muted};
    font-size: {type_caption}pt;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0;
    padding: 8px 0 4px 0;
}}

QLabel#metaKey {{
    color: {text_muted};
    font-size: {type_caption}pt;
}}

QLabel#metaValue,
QLabel#metaValuePath {{
    color: {text};
    font-size: {type_body}pt;
}}

QLabel#metaPlain {{
    color: {text};
    font-size: {type_body}pt;
}}

QToolButton#metaCopyButton {{
    background: transparent;
    border: 1px solid transparent;
    border-radius: {radius_control}px;
    color: {text_muted};
    font-size: {type_lead}pt;
    padding: 0;
}}

QToolButton#metaCopyButton:hover {{
    background: {surface_alt};
    border-color: {border};
    color: {text};
}}

QPushButton {{
    background: {surface};
    border: 1px solid {control_border};
    border-radius: {radius_control}px;
    color: {text};
    padding: 0 12px;
    min-height: 28px;
    font-weight: 500;
}}

QPushButton:hover {{
    background: {surface_alt};
    border-color: {accent_line};
}}

QPushButton:pressed {{
    background: {accent_soft};
}}

QPushButton:checked {{
    background: {accent_soft};
    border-color: {accent_line};
    color: {text_strong};
}}

QPushButton:disabled {{
    background: {surface};
    border-color: {border};
    color: {text_muted};
}}

QPushButton#primaryButton {{
    background: {primary_fill};
    border-color: {primary_fill};
    color: {primary_text};
    font-weight: 600;
}}

QPushButton#primaryButton:hover {{
    background: {primary_fill_hover};
    border-color: {primary_fill_hover};
    color: {primary_text};
}}

/* A disabled primary action still reads as the primary one, muted, rather
   than as a disabled secondary button (plan C13). */
QPushButton#primaryButton:disabled {{
    background: {accent_soft};
    border-color: {accent_line};
    color: {text_muted};
}}

/* Floating over the canvas, so it shares the floating-control size (32 px):
   22 px of content, 4 px padding and a 1 px border on each side. */
QPushButton#viewerToolButton {{
    background: {surface};
    border: 1px solid {border_strong};
    border-radius: {radius_card}px;
    color: {text};
    min-width: 34px;
    min-height: 22px;
    padding: 4px 10px;
    font-weight: 600;
}}

/* Icon-only Overlays in a narrow canvas: a 32 px square, 30 px of content
   and a 1 px border on each side. */
QPushButton#viewerToolButton[compact="true"] {{
    min-width: 30px;
    padding: 4px 0;
}}

QPushButton#viewerToolButton:hover {{
    background: {surface_alt};
    border-color: {accent_line};
    color: {text_strong};
}}

QPushButton#viewerToolButton:pressed {{
    background: {accent_soft};
    border-color: {accent_line};
}}

QPushButton#viewerToolButton:checked {{
    background: {primary_fill};
    border-color: {primary_fill};
    color: {primary_text};
}}

QPushButton#viewerToolButton:checked:hover {{
    background: {primary_fill_hover};
    border-color: {primary_fill_hover};
    color: {primary_text};
}}

QPushButton#viewerZoomButton {{
    background: transparent;
    border: 0;
    border-radius: {radius_control}px;
    color: {text};
    font-weight: 700;
    min-width: 32px;
    min-height: 32px;
    padding: 0;
    text-align: center;
}}

QFrame#viewerControlDivider {{
    background: {border};
    border: 0;
}}

QPushButton#viewerZoomButton:hover {{
    background: {surface_alt};
    color: {text_strong};
}}

QPushButton#viewerZoomButton:pressed {{
    background: {accent_soft};
    color: {accent};
}}

QPushButton#viewerNavButton {{
    min-width: 76px;
    padding: 5px 10px;
    font-weight: 600;
}}

QWidget#viewerOverlayPanel,
QWidget#viewerZoomPanel,
QLabel#viewerImageBadge {{
    background: {surface};
    border: 1px solid {border_strong};
    border-radius: {radius_card}px;
}}

QLabel#imageExportPreview {{
    background: {canvas};
    border: 1px solid {border_strong};
    border-radius: {radius_card}px;
    padding: 6px;
}}

QWidget#viewerOverlayPanel {{
    padding: 2px;
}}

/* The Data collections disclosure in the Overlays panel. With no rule, the
   native Windows 11 style drew it, and painted the checked (open) state in
   the system accent blue in both themes. Every state names a fill and a
   border, so the sheet always draws it: a rule without them hands the button
   back to the native style (offscreen tests cannot see that). It reads as a
   row of the panel, like the check boxes beside it, until hovered or open.
   The arrow takes the text colour. */
QToolButton#viewerOverlaySectionButton {{
    background: {surface};
    border: 1px solid {surface};
    border-radius: {radius_control}px;
    color: {text_muted};
    padding: 2px 8px 2px 0;
}}

QToolButton#viewerOverlaySectionButton:hover {{
    background: {surface_alt};
    border-color: {border};
    color: {text};
}}

QToolButton#viewerOverlaySectionButton:checked {{
    background: {surface_alt};
    border-color: {border};
    color: {text_strong};
}}

QToolButton#viewerOverlaySectionButton:checked:hover {{
    background: {surface_hi};
    border-color: {border_strong};
    color: {text_strong};
}}

/* Last and as specific as the states above, so keyboard focus always shows:
   the id rule outranks the app-wide ``QToolButton:focus`` border. */
QToolButton#viewerOverlaySectionButton:focus,
QToolButton#viewerOverlaySectionButton:checked:focus {{
    border-color: {accent};
}}

QLabel#viewerImageBadge {{
    color: {text};
    padding: 6px 10px;
    font-family: {mono_font};
    font-size: {type_caption}pt;
}}

/* No min-width here: the viewer fixes this label's size in code, and a width
   rule would undo that on every polish, letting each zoom step re-lay out the
   whole window (plan F5.1). */
QLabel#viewerZoomLabel {{
    color: {text_muted};
    font-family: {mono_font};
    font-size: {type_caption}pt;
    padding: 0;
}}

QCheckBox {{
    color: {text_muted};
    spacing: 6px;
}}

QCheckBox:hover {{
    color: {text};
}}

QCheckBox::indicator {{
    width: 14px;
    height: 14px;
    border: 1px solid {border_strong};
    border-radius: {radius_control}px;
    background: {surface};
}}

/* Checked state carries a tick, not just a fill. Colour alone left "checked"
   and "unchecked" differing only in lightness, which is indistinguishable in
   grayscale and unreliable for colour-vision deficiency. */
QCheckBox::indicator:checked {{
    background: {accent};
    border-color: {accent};
    image: url("{check_icon}");
}}

QCheckBox::indicator:disabled {{
    background: {surface_alt};
    border-color: {border};
}}

QTabWidget::pane {{
    border: 1px solid {border};
    border-top: 0;
    background: {background};
}}

QTabBar::tab {{
    background: {panel};
    color: {text_muted};
    border: 1px solid {border};
    border-bottom: 0;
    padding: 8px 14px;
    margin-right: 2px;
}}

QTabBar::tab:selected {{
    background: {background};
    color: {text_strong};
    border-top: 2px solid {accent};
}}

QTabBar::tab:hover:!selected {{
    background: {surface_alt};
    color: {text};
}}

QDockWidget {{
    titlebar-close-icon: none;
    titlebar-normal-icon: none;
    color: {text_muted};
    font-weight: 600;
}}

QDockWidget::title {{
    background: {surface};
    border: 1px solid {border};
    padding: 7px 8px;
    text-align: left;
}}

QTreeWidget,
QPlainTextEdit,
QLineEdit {{
    background: {surface};
    border: 1px solid {border};
    border-radius: {radius_card}px;
    color: {text};
    selection-background-color: {selection};
    selection-color: {selection_text};
}}

/* An input is a control, not a panel. */
QLineEdit {{
    border-radius: {radius_control}px;
}}

QTreeWidget {{
    alternate-background-color: {surface_alt};
    outline: 0;
    padding: 3px;
}}

QTreeWidget::item {{
    min-height: 24px;
    border-radius: {radius_control}px;
    padding: 3px 5px;
}}

QTreeWidget::item:hover {{
    background: {surface_alt};
}}

/* The selection fill alone is a very low-contrast cue in both palettes
   (~1.2:1 against the panel). An accent marker carries the real signal at
   >=3:1, but it cannot live here: ``::item`` rules apply per column, so a
   ``border-left`` paints one arc per column instead of one bar per row.
   ``ui.list_decorations.paint_selection_marker`` draws it from the item
   delegates, which can see the column index. */
QTreeWidget::item:selected {{
    background: {selection};
    color: {selection_text};
}}

/* Table and tree headers were never styled, so the report-scope dialog and
   the search-map detail table rendered a platform-grey header bar against
   themed chrome. */
QHeaderView {{
    background: {surface_alt};
    border: 0;
}}

QHeaderView::section {{
    background: {surface_alt};
    color: {text_muted};
    border: 0;
    border-bottom: 1px solid {border};
    border-right: 1px solid {border};
    padding: 6px 8px;
    font-weight: 600;
}}

QHeaderView::section:last {{
    border-right: 0;
}}

QHeaderView::section:hover {{
    color: {text};
    background: {surface_hi};
}}

/* Keyboard focus had no visible indicator anywhere except the two search
   fields, so tabbing through the app was invisible. */
QPushButton:focus,
QToolButton:focus,
QCheckBox:focus,
QLineEdit:focus,
QSlider:focus,
QGraphicsView:focus,
QTreeWidget:focus,
QTableView:focus,
QPlainTextEdit:focus {{
    border: 1px solid {accent};
    outline: 0;
}}

QTabBar::tab:focus {{
    background: {accent_soft};
    color: {text_strong};
}}

QScrollArea {{
    background: transparent;
    border: 0;
}}

QScrollBar:vertical {{
    background: {panel};
    border: 0;
    width: 11px;
    margin: 0;
}}

QScrollBar:horizontal {{
    background: {panel};
    border: 0;
    height: 11px;
    margin: 0;
}}

QScrollBar::handle:vertical,
QScrollBar::handle:horizontal {{
    background: {scroll_thumb};
    border-radius: 5px; /* pill */
    min-height: 28px;
    min-width: 28px;
}}

QScrollBar::handle:vertical:hover,
QScrollBar::handle:horizontal:hover {{
    background: {text_muted};
}}

QScrollBar::add-line:vertical,
QScrollBar::sub-line:vertical,
QScrollBar::add-line:horizontal,
QScrollBar::sub-line:horizontal {{
    width: 0;
    height: 0;
    border: 0;
    background: transparent;
}}

QScrollBar::add-page:vertical,
QScrollBar::sub-page:vertical,
QScrollBar::add-page:horizontal,
QScrollBar::sub-page:horizontal {{
    background: transparent;
}}

QPlainTextEdit {{
    font-family: {mono_font};
    font-size: {type_body}pt;
    line-height: 140%;
    padding: 8px;
}}

QGraphicsView {{
    background: {surface};
    border: 1px solid {border};
    border-radius: {radius_card}px;
}}

QLabel#viewerStatus {{
    color: {text_muted};
    background: {surface};
    border: 1px solid {border};
    border-radius: {radius_card}px;
    padding: 6px 8px;
}}

QLabel#frameLabel {{
    color: {text};
    background: {surface};
    border: 1px solid {border};
    border-radius: {radius_card}px;
    padding: 5px 8px;
}}

QFrame#dashboardCard {{
    background: {surface};
    border: 1px solid {border};
    border-radius: {radius_card}px;
}}

QLabel#cardTitle {{
    color: {text_muted};
    font-size: {type_caption}pt;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0;
}}

QLabel#doseCardHeading {{
    color: {text_muted};
    font-size: {type_caption}pt;
    font-weight: 600;
    letter-spacing: 0;
}}

QLabel#cardValue {{
    color: {text_strong};
    font-size: {type_display}pt;
    font-weight: 600;
}}

QLabel#cardSubvalue {{
    color: {text_muted};
    font-size: {type_caption}pt;
}}

QSplitter::handle {{
    background: transparent;
}}

QSplitter::handle:horizontal {{
    width: 5px;
    border-left: 2px solid transparent;
    border-right: 2px solid transparent;
    background: {border_strong};
}}

QSplitter::handle:vertical {{
    height: 5px;
    border-top: 2px solid transparent;
    border-bottom: 2px solid transparent;
    background: {border_strong};
}}

QSplitter::handle:hover {{
    background: {accent};
}}

QProgressBar {{
    background: {surface};
    border: 1px solid {border_strong};
    border-radius: 5px; /* pill */
    color: {text_muted};
    min-height: 8px;
    max-height: 10px;
    text-align: center;
}}

QProgressBar::chunk {{
    background: {accent};
    border-radius: 4px; /* pill */
}}

QSlider::groove:horizontal {{
    height: 6px;
    background: {border};
    border-radius: 3px; /* pill */
}}

QSlider::sub-page:horizontal {{
    background: {accent};
    border-radius: 3px; /* pill */
}}

QSlider::add-page:horizontal {{
    background: {border};
    border-radius: 3px; /* pill */
}}

QSlider::handle:horizontal {{
    background: {text_strong};
    border: 1px solid {accent};
    width: 16px;
    height: 16px;
    margin: -6px 0;
    border-radius: 8px; /* pill */
}}

QSlider#frameScrubber::sub-page:horizontal {{
    background: {border};
    border-radius: 3px; /* pill */
}}

QStatusBar {{
    background: {panel};
    border-top: 1px solid {border};
    color: {text_muted};
}}

QWidget#brandLockup {{
    background: transparent;
    border: 0;
    padding: 0;
}}

QFrame#sessionPill,
QLabel#statusSegment,
QLabel#smallChip,
QLabel#viewerChip,
QLabel#viewerChipStatus,
QLabel#tabCountChip {{
    background: {surface};
    border: 1px solid {border};
    border-radius: {radius_control}px;
    color: {text_muted};
    padding: 3px 8px;
    font-size: {type_caption}pt;
}}

QLabel#smallChip,
QLabel#viewerChip,
QLabel#tabCountChip {{
    background: transparent;
    color: {text_muted};
    border-color: {border};
}}

QLabel#viewerChipStatus {{
    background: {accent_soft};
    border-color: {accent_line};
    color: {text_strong};
    font-weight: 700;
}}

QFrame#sessionPill {{
    border-radius: 15px; /* pill */
    min-height: 26px;
}}

QLabel#sessionPillText,
QLabel#sessionPillIcon {{
    background: transparent;
    border: 0;
    color: {text};
    padding: 0;
}}

QLabel#sessionPillText {{
    font-size: {type_caption}pt;
}}

QLabel#screenEyebrow,
QLabel#panelHeader,
QLabel#viewerPanelHeader {{
    color: {text_muted};
    font-size: {type_caption}pt;
    font-weight: 700;
    letter-spacing: 0;
    text-transform: uppercase;
}}

/* ---------------------------------------------------------------------
   Three visual tiers (V1).

   1. Page landmark  — where am I: the context header's scope, an empty
      state's heading.
   2. Primary scientific content — the thing under review: card values,
      row names, detail text.
   3. Supporting metadata — everything that qualifies it: card titles,
      counts, evidence, the context header's notes row.

   Expressed as type weight and colour only. The plan forbids gradients,
   oversized cards and added whitespace; density is the point of this app.

   These are **dynamic properties**, not object names. An earlier version used
   ``QLabel#tierLandmark``, which asked every widget to give up the semantic
   object name its own rule already needed — so no widget ever carried a tier
   and the whole block was unreachable. A property sits alongside the object
   name, so a widget can say both what it is and which tier it belongs to.

   A widget's own ``#objectName`` rule has higher specificity and may refine
   the size within its tier where a surface genuinely needs a different step
   (a 22pt card value and a 9.5pt crumb are both primary content). The tier is
   the declared classification and the default; the id rule is the exception.
   Use ``apply_tier()`` rather than setting the property by hand.
   --------------------------------------------------------------------- */

QLabel[tier="landmark"] {{
    color: {text_strong};
    font-size: {type_lead}pt;
    font-weight: 600;
}}

QLabel[tier="primary"] {{
    color: {text};
    font-size: {type_body}pt;
    font-weight: 500;
}}

QLabel[tier="supporting"] {{
    color: {text_muted};
    font-size: {type_caption}pt;
}}

/* Dashboard row typography. Previously set with inline style sheets, which
   put type decisions in a fourth place and baked the palette in at
   construction so a theme switch left them stale. */
QLabel#statusDot {{
    font-size: {type_lead}pt;
}}

QLabel#statusDot[compact="true"] {{
    font-size: {type_body}pt;
}}

QLabel#progressRowName {{
    font-size: {type_body}pt;
    font-weight: 600;
}}

QLabel#progressRowName[compact="true"] {{
    font-size: {type_caption}pt;
}}

QLabel#warningGroupLabel {{
    font-weight: 600;
}}

QLabel#statStatusText {{
    color: {text};
    font-size: {type_caption}pt;
}}

/* One visually primary action per screen; related context is subordinate.
   The pair reads as a hierarchy rather than two equal buttons. */
QPushButton#primaryAction {{
    background: {accent};
    color: {primary_text};
    border: 1px solid {accent};
    border-radius: {radius_control}px;
    padding: 5px 14px;
    font-weight: 600;
}}

QPushButton#primaryAction:hover {{
    background: {accent_strong};
    border-color: {accent_strong};
}}

QPushButton#primaryAction:pressed {{
    background: {accent_line};
    border-color: {accent_line};
}}

QPushButton#primaryAction:disabled {{
    background: {accent_soft};
    color: {text_muted};
    border-color: {accent_line};
}}

QPushButton#secondaryAction {{
    background: transparent;
    color: {accent};
    border: none;
    padding: 5px 8px;
    font-weight: 500;
}}

QPushButton#secondaryAction:hover {{
    color: {accent_strong};
    text-decoration: underline;
}}

/* Session drop: the empty project panel's invitation, and what a drop
   skipped (widgets/session_drop.py). Tone colours: _session_intake_rules. */
QWidget#projectDropHint {{
    background: transparent;
}}

QLabel#dropHintTitle {{
    color: {text};
    font-size: {type_lead}pt;
    font-weight: 600;
}}

QLabel#dropHintDetail {{
    color: {text_muted};
    font-size: {type_caption}pt;
}}

QLabel#dropHintShortcut {{
    color: {text_muted};
    border: 1px solid {border};
    border-radius: {radius_control}px;
    padding: 1px 6px;
    font-size: {type_caption}pt;
}}

QFrame#dropReceipt {{
    background: {surface_alt};
    border: 1px solid {border_strong};
    border-radius: {radius_card}px;
}}

QFrame#dropReceiptSection {{
    background: transparent;
    border: none;
    border-top: 1px solid {border};
}}

QLabel#dropReceiptTitle {{
    color: {text_strong};
    font-weight: 600;
}}

QLabel#dropReceiptLines {{
    color: {text_muted};
    font-size: {type_caption}pt;
}}

/* The Load sessions window (ui/load_sessions_dialog.py). */
QDialog#loadSessionsDialog,
QWidget#loadSessionsBody {{
    background: {surface};
}}

QLabel#loadSessionsEmblem {{
    background: {accent_soft};
    border: 1px solid {accent_line};
    border-radius: {radius_card}px;
}}

QLabel#loadSessionsTitle {{
    color: {text_strong};
    font-size: {type_heading}pt;
    font-weight: 600;
}}

QLabel#loadSessionsSubtitle,
QLabel#loadSessionsFooterNote {{
    color: {text_muted};
}}

QFrame#loadSessionsDropWell {{
    background: {panel};
    border: none;
    border-radius: {radius_card}px;
    min-height: 112px;
}}

QLabel#dropWellTitle {{
    color: {text_strong};
    font-size: {type_lead}pt;
    font-weight: 600;
}}

QLabel#dropWellHint,
QLabel#queuedFolderPath,
QLabel#queuedFolderKind {{
    color: {text_muted};
    font-size: {type_caption}pt;
}}

QLabel#loadSessionsQueueHeading {{
    color: {text_muted};
    font-size: {type_caption}pt;
    font-weight: 700;
}}

QLabel#queueCount {{
    font-size: {type_caption}pt;
}}

QScrollArea#queuedFolderScroll {{
    background: transparent;
}}

QFrame#queuedFolderList {{
    background: {panel};
    border: 1px solid {border};
    border-radius: {radius_card}px;
}}

QFrame#queuedFolderRow {{
    background: transparent;
    border: none;
    border-bottom: 1px solid {border};
}}

QFrame#queuedFolderRow[last="true"] {{
    border-bottom: none;
}}

QLabel#queuedFolderBadge {{
    background: {surface_hi};
    border: 1px solid {border_strong};
    border-radius: {radius_control}px;
    color: {text};
    font-size: {type_caption}pt;
    font-weight: 700;
}}

QLabel#queuedFolderBadge[empty="true"] {{
    background: transparent;
    border: 1px dashed {unknown};
    color: {text_muted};
}}

QLabel#queuedFolderName {{
    color: {text_strong};
}}

QLabel#queuedFolderReason {{
    font-size: {type_caption}pt;
}}

QFrame#loadSessionsFooter {{
    background: {panel};
    border: none;
    border-top: 1px solid {border};
}}

/* Empty-state panel. Restrained: no illustration, no oversized chrome. */
QWidget#emptyStatePanel {{
    background: transparent;
}}

QLabel#emptyStateTitle {{
    color: {text_strong};
    font-size: {type_lead}pt;
    font-weight: 600;
}}

QLabel#emptyStateDetail {{
    color: {text};
    font-size: {type_body}pt;
}}

QLabel#emptyStateEvidence {{
    color: {text_muted};
    font-size: {type_caption}pt;
}}

/* Status chips. Completing available/queued/pending/selected so a chip's
   state is never carried by colour alone — each keeps a readable border and
   its own text. */
QLabel[chipState="available"] {{
    color: {overlay_status_complete};
    border: 1px solid {overlay_status_complete};
    border-radius: {radius_control}px;
    padding: 0 5px;
    font-size: {type_caption}pt;
}}

QLabel[chipState="queued"], QLabel[chipState="pending"] {{
    color: {overlay_status_pending};
    border: 1px solid {overlay_status_pending};
    border-radius: {radius_control}px;
    padding: 0 5px;
    font-size: {type_caption}pt;
}}

QLabel[chipState="selected"] {{
    color: {primary_text};
    background: {accent};
    border: 1px solid {accent};
    border-radius: {radius_control}px;
    padding: 0 5px;
    font-size: {type_caption}pt;
    font-weight: 600;
}}

/* Interactive cards get a restrained, visible response. A card with nothing
   in it is inert and must not react — see StatCard's cardEmpty property. */
QFrame#dashboardCard[cardEmpty="false"]:hover {{
    border-color: {accent_line};
}}

QFrame#dashboardCard[cardEmpty="true"] {{
    color: {text_muted};
}}

QLabel#projectBadgeLegend {{
    color: {text_muted};
    font-size: {type_caption}pt;
}}

QWidget#contextHeader {{
    background: {surface};
    border-bottom: 1px solid {border};
}}

QLabel#contextScope {{
    color: {text_strong};
    font-size: {type_body}pt;
    font-weight: 600;
}}

QLabel#contextCrumbs {{
    color: {text};
    font-size: {type_body}pt;
}}

QLabel#contextNotes {{
    color: {text_muted};
    font-size: {type_caption}pt;
}}

QPushButton#contextClearFilter {{
    color: {accent};
    font-size: {type_caption}pt;
    font-weight: 600;
    border: none;
    padding: 0 4px;
}}

QPushButton#contextClearFilter:hover {{
    color: {accent_strong};
    text-decoration: underline;
}}

QLabel#screenTitle {{
    color: {text_strong};
    font-size: {type_display}pt;
    font-weight: 600;
}}

QLabel#viewerTitle {{
    color: {text_strong};
    font-size: {type_heading}pt;
    font-weight: 600;
}}

QLabel#panelCount {{
    background: {surface};
    border: 1px solid {border};
    border-radius: 9px; /* pill */
    color: {text_muted};
    padding: 2px 7px;
    font-size: {type_caption}pt;
}}

QLabel#sampleCount {{
    color: {text_muted};
    font-size: {type_caption}pt;
}}

QLabel#sampleBadge {{
    background: {surface_alt};
    border: 1px solid {border};
    border-radius: 11px; /* pill */
    color: {text};
    padding: 0px 8px;
    font-size: {type_caption}pt;
    min-height: 22px;
}}

QLabel#healthValue {{
    color: {text_strong};
    font-family: {mono_font};
    font-size: {type_title}pt;
    font-weight: 700;
}}

QToolButton#dashboardFilterButton {{
    background: {surface_alt};
    border: 1px solid {border};
    border-radius: {radius_control}px;
    color: {text};
    padding: 4px 9px;
    font-weight: 600;
}}

QToolButton#dashboardFilterButton:hover {{
    border-color: {accent};
    background: {surface_hi};
}}

QToolButton#dashboardFilterButton:checked {{
    border-color: {accent};
    color: {text_strong};
}}

QToolButton#dashboardFilterButton:disabled {{
    color: {text_muted};
    border-color: {border};
    background: {surface};
}}

QLabel#searchMapSummaryChip {{
    background: {surface_alt};
    border: 1px solid {border};
    border-radius: {radius_control}px;
    padding: 3px 8px;
    font-size: {type_caption}pt;
    font-weight: 600;
}}

QTableView#dashboardSearchMapTable {{
    background: {surface};
    alternate-background-color: {surface_alt};
    border: 1px solid {border};
    border-radius: {radius_card}px;
    color: {text};
    gridline-color: {border};
    selection-background-color: {accent_soft};
    selection-color: {text_strong};
}}

QTableView#dashboardSearchMapTable::item {{
    padding: 5px 8px;
}}

QLineEdit#treeSearch,
QLineEdit#viewerFilter {{
    background: {surface};
    border: 1px solid {border};
    border-radius: {radius_control}px;
    color: {text};
    padding: 6px 10px;
    min-height: 24px;
}}

QLineEdit#treeSearch:focus,
QLineEdit#viewerFilter:focus {{
    border-color: {accent};
}}

QTreeWidget#projectTree,
QTreeWidget#viewerList {{
    background: {panel};
    border: 0;
    alternate-background-color: transparent;
    padding: 4px 6px;
}}

QTreeWidget#projectTree::item,
QTreeWidget#viewerList::item {{
    min-height: 30px;
    border-radius: {radius_control}px;
    padding: 5px 8px;
}}

QTreeWidget#viewerList::item {{
    min-height: 44px;
}}

/* Hover on these two views is painted by their delegates, faded by
   ``ui.motion.ItemHoverAnimator``. The generic :hover rule would otherwise
   paint a second, snapping layer per column and over the branch area. */
QTreeWidget#projectTree::item:hover,
QTreeWidget#viewerList::item:hover {{
    background: transparent;
}}

/* The main project tree paints its selection as one row-wide fill in its
   delegate (plan C9). Per-column ``::item:selected`` fills drew the name and
   the badge as two rounded cells with a seam between them, and
   ``show-decoration-selected`` added a third block over the expander. The
   property keeps the report dialog's tree, which shares the object name but
   has no such delegate, on the sheet's own selection. */
QTreeWidget#projectTree[paintsRowSelection="true"] {{
    show-decoration-selected: 0;
}}

QTreeWidget#projectTree[paintsRowSelection="true"]::item:selected {{
    background: transparent;
    color: {selection_text};
}}

QTabWidget::pane {{
    border: 0;
    background: {background};
}}

QTabBar::tab {{
    background: {surface};
    color: {text_muted};
    border: 0;
    border-bottom: 2px solid transparent;
    padding: 10px 14px;
    margin-right: 2px;
    font-weight: 600;
}}

QTabBar::tab:selected {{
    background: {background};
    color: {text_strong};
    border-bottom: 2px solid {accent};
}}

/* The main tab bar (#mainTabBar) paints its own tab fills and a sliding
   underline on springs, and takes only each tab's label from these rules
   (plan F5.3, ui/widgets/sliding_tab_bar.py). The fills and underline above
   still style every other tab bar, such as the report dialog's. */

QTabBar QToolButton {{
    background: {surface_alt};
    color: {text};
    border: 1px solid {control_border};
    border-radius: {radius_control}px;
    margin: 2px;
    padding: 0;
}}

QTabBar QToolButton:hover {{
    background: {accent_soft};
    border-color: {accent};
}}

QLabel#mutedLabel {{
    color: {text_muted};
}}

QPushButton#dashboardFilterButton {{
    background: {surface_alt};
    color: {text};
    border: 1px solid {control_border};
    border-radius: {radius_control}px;
    min-height: 28px;
    padding: 0 10px;
}}

QPushButton#dashboardFilterButton:hover {{
    background: {surface_hi};
    border-color: {accent};
}}

QFrame#dashboardCard:focus,
QWidget#dashboardInteractiveRow:focus,
QWidget#statusTileGrid:focus,
QWidget#acquisitionTimeline:focus {{
    border: 1px solid {accent};
}}

QFrame#dashboardCard {{
    background: {surface};
    border: 1px solid {border};
    border-radius: {radius_card}px;
}}

QFrame#warningGroup {{
    background: {surface_alt};
    border: 1px solid {border};
    border-radius: {radius_card}px;
}}

QLabel#warningCount {{
    background: transparent;
    border: 1px solid {border};
    border-radius: 11px; /* pill */
    padding: 2px 8px;
    font-size: {type_caption}pt;
    font-weight: 700;
    min-height: 18px;
}}

QGraphicsView {{
    background: {canvas};
    border: 0;
    border-radius: {radius_card}px;
}}

QWidget#centralShell,
QWidget#contextShell,
QWidget#projectPanel,
QWidget#viewerListPanel,
QWidget#viewerCanvasShell,
QWidget#tabPage,
QWidget#sessionDashboard,
QWidget#dashboardContent,
QWidget#dashboardViewport,
QWidget#metadataPanel,
QWidget#metadataBody,
QWidget#metadataViewport,
QTabWidget#mainTabs,
QTabWidget#mainTabs::pane,
QScrollArea#dashboardScroll,
QScrollArea#metadataScroll {{
    background: {background};
}}
"""


def build_stylesheet(palette: ThemePalette) -> str:
    sheet = _QSS_TEMPLATE.format(
        **palette.__dict__,
        sans_font=SANS_FONT_FAMILY,
        mono_font=MONO_FONT_FAMILY,
        check_icon=_check_indicator_path(palette),
        radius_control=RADIUS_CONTROL,
        radius_card=RADIUS_CARD,
        radius_popover=RADIUS_POPOVER,
        type_caption=TYPE_CAPTION,
        type_body=TYPE_BODY,
        type_lead=TYPE_LEAD,
        type_title=TYPE_TITLE,
        type_heading=TYPE_HEADING,
        type_display=TYPE_DISPLAY,
    )
    # After the base ``viewerChipStatus`` rule they refine.
    return (
        sheet
        + "\n"
        + _status_tone_rules(palette)
        + "\n"
        + _dashboard_tone_rules(palette)
        + "\n"
        + _session_intake_rules(palette)
    )


def _check_indicator_path(palette: ThemePalette) -> str:
    """Absolute POSIX path to a tick recoloured for ``palette``.

    Written once per palette into the user's cache directory: Qt style sheets
    can only reference an image by URL, so this one icon has to exist on disk
    rather than being recoloured in memory like every other icon.

    Deliberately self-contained. ``APP_QSS`` is built at import time, so
    reaching into ``ui.icons`` here — which imports this module — produced a
    circular import depending on which was loaded first.
    """

    source = Path(__file__).resolve().parent.parent / "assets" / "icons" / "check.svg"
    colour = palette.primary_text
    try:
        cache_root = QStandardPaths.writableLocation(
            QStandardPaths.StandardLocation.CacheLocation
        )
        cache_dir = Path(cache_root) if cache_root else source.parent
        cache_dir.mkdir(parents=True, exist_ok=True)
        target = cache_dir / f"check-{colour.lstrip('#').lower()}.svg"
        if not target.exists():
            target.write_text(
                source.read_text(encoding="utf-8").replace("currentColor", colour),
                encoding="utf-8",
            )
        return target.as_posix()
    except OSError:
        # A read-only cache location must never break theming; the uncoloured
        # tick still reads as a tick.
        return source.as_posix()


# Backwards-compat: ``APP_QSS`` was a module-level constant in the previous
# revision. Some external tools and the existing ``app.py`` reference
# ``apply_theme`` without arguments. Keep the module-level dark stylesheet so
# nothing breaks on import; downstream callers should prefer ``apply_theme``.
APP_QSS = build_stylesheet(DARK_PALETTE)


_active_palette: ThemePalette = DARK_PALETTE

# The three visual tiers from V1, as dynamic-property values. Import these
# rather than spelling the strings at call sites, so a widget cannot silently
# claim a tier that has no rule behind it.
TIER_LANDMARK = "landmark"
TIER_PRIMARY = "primary"
TIER_SUPPORTING = "supporting"
VISUAL_TIERS: tuple[str, ...] = (TIER_LANDMARK, TIER_PRIMARY, TIER_SUPPORTING)


def apply_tier(widget: QWidget, tier: str) -> QWidget:
    """Classify ``widget`` into one of the three visual tiers.

    Returns the widget so it can be used inline where one is being built.
    Setting the property after the stylesheet is installed needs an explicit
    repolish, which is done here so callers cannot forget it.
    """

    if tier not in VISUAL_TIERS:
        raise ValueError(f"Unknown visual tier: {tier!r}")
    widget.setProperty("tier", tier)
    style = widget.style()
    if style is not None:
        style.unpolish(widget)
        style.polish(widget)
    return widget


def current_palette() -> ThemePalette:
    """Return the palette that ``apply_theme`` last applied.

    Custom-painted widgets (donut chart, sparklines, timeline strip) read
    this when they need a colour the QSS can't reach (e.g. centre-text on a
    paint event). Defaults to ``DARK_PALETTE`` until a theme is applied.
    """

    return _active_palette


_DEFERRED_POLISH_PROPERTY: Final[str] = "_tomo_polish_deferred"
_STALE_REGION_PROPERTY: Final[str] = "_tomo_theme_region_stale"


class _ThemeRegions(QObject):
    """Parts of the window restyled when shown, not at every theme switch.

    Restyling the application re-polishes every polished widget, visible or
    not: about 270 ms on a loaded session, two thirds of it for widgets on
    tab pages nobody is looking at. Qt's application-wide re-polish passes
    over widgets not marked polished, so just before a switch the widgets of
    hidden regions lose that mark; each is polished once with the new style
    when its region is next shown, before it paints. Themes differ only in
    colour, so nothing moves when a region catches up.

    The widgets are also flagged as having a style of their own for the
    switch only, which keeps them out of its style-change round too. Both
    flags are Qt's own and are given back at once (after the switch, or when
    shown): this relies on how ``QApplication::setStyle`` picks widgets, so
    re-check it on Qt upgrades (``tests/test_theme_regions.py`` does, in a
    fresh process; if Qt changes, a switch is only slower, or that test sees
    a stale colour).

    (Giving a region a style sheet of its own does *not* work: Qt then
    re-applies that sheet to each of its widgets at every switch, by the slow
    path, and a switch took 1.8 s instead of 0.3 s.)
    """

    def __init__(self) -> None:
        super().__init__()
        self._regions: list[QWidget] = []
        self._borrowed_set_style: list[QWidget] = []

    def add(self, widget: QWidget) -> None:
        widget.installEventFilter(self)
        self._regions.append(widget)

    def before_switch(self) -> None:
        self._regions = [region for region in self._regions if shiboken6.isValid(region)]
        for region in self._regions:
            if region.isVisible() or region.property(_STALE_REGION_PROPERTY):
                continue
            for widget in (region, *region.findChildren(QWidget)):
                if widget.testAttribute(Qt.WidgetAttribute.WA_WState_Polished):
                    widget.setAttribute(Qt.WidgetAttribute.WA_WState_Polished, False)
                    widget.setProperty(_DEFERRED_POLISH_PROPERTY, True)
                # Also out of the switch's style-change round, which makes
                # each widget recompute its sizes against the new sheet.
                if not widget.testAttribute(Qt.WidgetAttribute.WA_SetStyle):
                    widget.setAttribute(Qt.WidgetAttribute.WA_SetStyle, True)
                    self._borrowed_set_style.append(widget)
            region.setProperty(_STALE_REGION_PROPERTY, True)

    def after_switch(self) -> None:
        for widget in self._borrowed_set_style:
            if shiboken6.isValid(widget):
                widget.setAttribute(Qt.WidgetAttribute.WA_SetStyle, False)
        self._borrowed_set_style = []

    def stale(self, widget: QWidget) -> bool:
        return bool(widget.property(_STALE_REGION_PROPERTY))

    def refresh(self, region: QWidget) -> None:
        """Polish what the switch passed over in ``region``, with the current style."""

        if not self.stale(region):
            return
        region.setProperty(_STALE_REGION_PROPERTY, None)
        deferred = [
            widget
            for widget in (region, *region.findChildren(QWidget))
            if widget.property(_DEFERRED_POLISH_PROPERTY)
        ]
        for widget in deferred:
            widget.setProperty(_DEFERRED_POLISH_PROPERTY, None)
            widget.style().polish(widget)
            widget.setAttribute(Qt.WidgetAttribute.WA_WState_Polished, True)
        change = QEvent(QEvent.Type.StyleChange)
        for widget in deferred:
            QApplication.sendEvent(widget, change)
            widget.update()

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802 - Qt API
        if event.type() == QEvent.Type.Show and isinstance(watched, QWidget) and self.stale(watched):
            self.refresh(watched)
        return False


_THEME_REGIONS = _ThemeRegions()


def mark_theme_region(widget: QWidget) -> None:
    """Restyle ``widget`` (and everything in it) only while or when it shows.

    For large parts of the window that are often hidden, such as tab pages.
    """

    _THEME_REGIONS.add(widget)


def apply_theme(app: QApplication, palette: ThemePalette | None = None) -> ThemePalette:
    """Install the stylesheet for ``palette`` on ``app`` and update icon colour.

    Returns the palette that was applied so callers can mirror their internal
    state without re-resolving the name.
    """

    global _active_palette  # noqa: PLW0603 — module-level state mirrors Qt
    chosen = palette or DARK_PALETTE
    _active_palette = chosen
    app.setFont(QFont(SANS_FONT_NAME, TYPE_BODY))
    sheet = build_stylesheet(chosen)
    current = app.styleSheet()
    if current != sheet:
        # Hidden theme regions are left out, and caught up when shown.
        _THEME_REGIONS.before_switch()
        try:
            if current:
                # Replacing one application style sheet with another
                # re-polishes every widget once per ancestor as well (Qt's
                # style-sheet style recurses into the children of every
                # widget it already lists): 1.2–1.5 s on a loaded session
                # (plan F5.8a). Clearing it first takes the path that polishes
                # each widget once, ~0.35 s for both steps. Nothing paints in
                # between.
                app.setStyleSheet("")
            app.setStyleSheet(sheet)
        finally:
            _THEME_REGIONS.after_switch()
    # Late import keeps ``theme`` free of UI-only dependencies at import time.
    from tomography_session_browser.ui.icons import set_default_icon_color

    set_default_icon_color(chosen.icon)
    return chosen
