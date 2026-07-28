"""Application theming.

The app chrome follows the Slate & Sage style guide supplied with the
project: Inter for interface text, JetBrains Mono for identifiers/readouts,
compact 6/8/10 px radii, and a calm sage accent shared across light and dark
mode. ``build_stylesheet`` substitutes the palette into one QSS template so
both themes stay in sync. ``apply_theme`` installs the stylesheet and updates
the icon-loader's default colour so freshly-built actions match the theme.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from PySide6.QtGui import QFont
from PySide6.QtWidgets import QApplication


# Qt stylesheets do not handle CSS-style fallback stacks reliably on Windows.
# Keep the guide's sans/mono split, but use single installed families so text
# never renders as placeholder squares when Inter / JetBrains Mono are absent.
SANS_FONT_NAME: Final[str] = "Segoe UI"
MONO_FONT_NAME: Final[str] = "Cascadia Mono"
SANS_FONT_FAMILY: Final[str] = f'"{SANS_FONT_NAME}"'
MONO_FONT_FAMILY: Final[str] = f'"{MONO_FONT_NAME}"'


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


_QSS_TEMPLATE = """
QWidget {{
    color: {text};
    font-family: {sans_font};
    font-size: 10pt;
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

QLabel {{
    background: transparent;
}}

QToolTip {{
    background: {surface};
    color: {text};
    border: 1px solid {border_strong};
    border-radius: 6px;
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
    border-radius: 8px;
    padding: 4px;
}}

QMenu::item {{
    background: transparent;
    color: {text};
    border-radius: 6px;
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
    border-radius: 8px;
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
    border-radius: 6px;
    color: {text};
    padding: 5px 9px;
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

QToolBar QToolButton:checked {{
    background: {accent_soft};
    border-color: {accent_line};
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
    font-size: 12pt;
    font-weight: 600;
    padding-right: 12px;
}}

QLabel#tabTitle {{
    color: {text_strong};
    font-size: 11pt;
    font-weight: 600;
}}

QLabel#contextTitle {{
    color: {text_strong};
    font-size: 11pt;
    font-weight: 600;
    padding: 1px 0 7px 0;
    border-bottom: 1px solid {border};
}}

QLabel#metaSection {{
    color: {text_muted};
    font-size: 8.5pt;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0;
    padding: 6px 0 2px 0;
}}

QLabel#metaKey {{
    color: {text_muted};
    font-size: 9pt;
}}

QLabel#metaValue,
QLabel#metaValuePath {{
    color: {text};
    font-size: 9.5pt;
}}

QLabel#metaPlain {{
    color: {text};
    font-size: 9.5pt;
}}

QToolButton#metaCopyButton {{
    background: transparent;
    border: 1px solid transparent;
    border-radius: 5px;
    color: {text_muted};
    font-size: 11pt;
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
    border-radius: 6px;
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

QPushButton#primaryButton:disabled {{
    background: {surface};
    border-color: {border};
    color: {text_muted};
}}

QPushButton#viewerToolButton {{
    background: {surface};
    border: 1px solid {border_strong};
    border-radius: 8px;
    color: {text};
    min-width: 34px;
    min-height: 28px;
    padding: 4px 10px;
    font-weight: 600;
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
    border-radius: 6px;
    color: {text};
    font-weight: 700;
    min-width: 30px;
    min-height: 28px;
    padding: 0;
    text-align: center;
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
    border-radius: 8px;
}}

QLabel#imageExportPreview {{
    background: {canvas};
    border: 1px solid {border_strong};
    border-radius: 8px;
    padding: 6px;
}}

QWidget#viewerOverlayPanel {{
    padding: 2px;
}}

QLabel#viewerImageBadge {{
    color: {text};
    padding: 6px 10px;
    font-family: {mono_font};
    font-size: 9pt;
}}

QLabel#viewerZoomLabel {{
    color: {text_muted};
    font-family: {mono_font};
    font-size: 8pt;
    min-width: 0;
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
    border-radius: 4px;
    background: {surface};
}}

QCheckBox::indicator:checked {{
    background: {accent};
    border-color: {accent};
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
    border-radius: 8px;
    color: {text};
    selection-background-color: {selection};
    selection-color: {selection_text};
}}

QTreeWidget {{
    alternate-background-color: {surface_alt};
    outline: 0;
    padding: 3px;
}}

QTreeWidget::item {{
    min-height: 24px;
    border-radius: 6px;
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
    border-radius: 5px;
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
    font-size: 9.5pt;
    line-height: 140%;
    padding: 8px;
}}

QGraphicsView {{
    background: {surface};
    border: 1px solid {border};
    border-radius: 6px;
}}

QLabel#viewerStatus {{
    color: {text_muted};
    background: {surface};
    border: 1px solid {border};
    border-radius: 8px;
    padding: 6px 8px;
}}

QLabel#frameLabel {{
    color: {text};
    background: {surface};
    border: 1px solid {border};
    border-radius: 8px;
    padding: 5px 8px;
}}

QFrame#dashboardCard {{
    background: {surface};
    border: 1px solid {border};
    border-radius: 8px;
}}

QLabel#cardTitle {{
    color: {text_muted};
    font-size: 8.5pt;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0;
}}

QLabel#doseCardHeading {{
    color: {text_muted};
    font-size: 8.5pt;
    font-weight: 600;
    letter-spacing: 0;
}}

QLabel#cardValue {{
    color: {text_strong};
    font-size: 22pt;
    font-weight: 600;
}}

QLabel#cardSubvalue {{
    color: {text_muted};
    font-size: 9.5pt;
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
    border-radius: 5px;
    color: {text_muted};
    min-height: 8px;
    max-height: 10px;
    text-align: center;
}}

QProgressBar::chunk {{
    background: {accent};
    border-radius: 4px;
}}

QSlider::groove:horizontal {{
    height: 6px;
    background: {border};
    border-radius: 3px;
}}

QSlider::sub-page:horizontal {{
    background: {accent};
    border-radius: 3px;
}}

QSlider::add-page:horizontal {{
    background: {border};
    border-radius: 3px;
}}

QSlider::handle:horizontal {{
    background: {text_strong};
    border: 1px solid {accent};
    width: 16px;
    height: 16px;
    margin: -6px 0;
    border-radius: 8px;
}}

QSlider#frameScrubber::sub-page:horizontal {{
    background: {border};
    border-radius: 3px;
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

QLabel#sessionPill,
QLabel#statusSegment,
QLabel#smallChip,
QLabel#viewerChip,
QLabel#viewerChipStatus,
QLabel#tabCountChip {{
    background: {surface};
    border: 1px solid {border};
    border-radius: 10px;
    color: {text_muted};
    padding: 3px 8px;
    font-family: {mono_font};
    font-size: 9pt;
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

QLabel#sessionPill {{
    color: {text};
    border-radius: 15px;
    min-height: 26px;
    padding: 2px 13px;
}}

QLabel#screenEyebrow,
QLabel#panelHeader,
QLabel#viewerPanelHeader {{
    color: {text_muted};
    font-size: 8.5pt;
    font-weight: 700;
    letter-spacing: 0;
    text-transform: uppercase;
}}

QLabel#screenTitle {{
    color: {text_strong};
    font-size: 21pt;
    font-weight: 600;
}}

QLabel#viewerTitle {{
    color: {text_strong};
    font-size: 15pt;
    font-weight: 600;
}}

QLabel#panelCount {{
    background: {surface};
    border: 1px solid {border};
    border-radius: 9px;
    color: {text_muted};
    padding: 2px 7px;
    font-family: {mono_font};
    font-size: 8.5pt;
}}

QLabel#sampleCount {{
    color: {text_muted};
    font-family: {mono_font};
    font-size: 9pt;
}}

QLabel#sampleBadge {{
    background: {surface_alt};
    border: 1px solid {border};
    border-radius: 11px;
    color: {text};
    padding: 0px 8px;
    font-family: {mono_font};
    font-size: 8.5pt;
    min-height: 22px;
}}

QLabel#healthValue {{
    color: {text_strong};
    font-family: {mono_font};
    font-size: 14pt;
    font-weight: 700;
}}

QToolButton#dashboardFilterButton {{
    background: {surface_alt};
    border: 1px solid {border};
    border-radius: 8px;
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
    border-radius: 8px;
    padding: 3px 8px;
    font-family: {mono_font};
    font-size: 8.5pt;
    font-weight: 600;
}}

QTableView#dashboardSearchMapTable {{
    background: {surface};
    alternate-background-color: {surface_alt};
    border: 1px solid {border};
    border-radius: 8px;
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
    border-radius: 8px;
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
    border-radius: 6px;
    padding: 5px 8px;
}}

QTreeWidget#viewerList::item {{
    min-height: 44px;
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

QTabBar QToolButton {{
    background: {surface_alt};
    color: {text};
    border: 1px solid {control_border};
    border-radius: 4px;
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
    border-radius: 6px;
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
    border-radius: 8px;
}}

QFrame#warningGroup {{
    background: {surface_alt};
    border: 1px solid {border};
    border-radius: 7px;
}}

QLabel#warningCount {{
    background: transparent;
    border: 1px solid {border};
    border-radius: 11px;
    padding: 2px 8px;
    font-family: {mono_font};
    font-size: 8.5pt;
    font-weight: 700;
    min-height: 18px;
}}

QLabel#cardValue {{
    font-family: {mono_font};
    font-size: 26pt;
    font-weight: 600;
}}

QGraphicsView {{
    background: {canvas};
    border: 0;
    border-radius: 8px;
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
    return _QSS_TEMPLATE.format(
        **palette.__dict__,
        sans_font=SANS_FONT_FAMILY,
        mono_font=MONO_FONT_FAMILY,
    )


# Backwards-compat: ``APP_QSS`` was a module-level constant in the previous
# revision. Some external tools and the existing ``app.py`` reference
# ``apply_theme`` without arguments. Keep the module-level dark stylesheet so
# nothing breaks on import; downstream callers should prefer ``apply_theme``.
APP_QSS = build_stylesheet(DARK_PALETTE)


_active_palette: ThemePalette = DARK_PALETTE


def current_palette() -> ThemePalette:
    """Return the palette that ``apply_theme`` last applied.

    Custom-painted widgets (donut chart, sparklines, timeline strip) read
    this when they need a colour the QSS can't reach (e.g. centre-text on a
    paint event). Defaults to ``DARK_PALETTE`` until a theme is applied.
    """

    return _active_palette


def apply_theme(app: QApplication, palette: ThemePalette | None = None) -> ThemePalette:
    """Install the stylesheet for ``palette`` on ``app`` and update icon colour.

    Returns the palette that was applied so callers can mirror their internal
    state without re-resolving the name.
    """

    global _active_palette  # noqa: PLW0603 — module-level state mirrors Qt
    chosen = palette or DARK_PALETTE
    _active_palette = chosen
    app.setFont(QFont(SANS_FONT_NAME, 10))
    app.setStyleSheet(build_stylesheet(chosen))
    # Late import keeps ``theme`` free of UI-only dependencies at import time.
    from tomography_session_browser.ui.icons import set_default_icon_color

    set_default_icon_color(chosen.icon)
    return chosen
