"""The application style sheet: one QSS template, filled from a palette.

``build_stylesheet`` substitutes a ``ThemePalette`` and the type, radius and
font tokens (``presentation/design_tokens.py``) into the template, then adds
the tone rules, so both themes stay in sync. ``ui/theme.py`` installs it.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QStandardPaths

from tomography_session_browser.presentation.design_tokens import (
    CANVAS_CHROME,
    DASHBOARD_TONE_COLOURS,
    MONO_FONT_FAMILY,
    RADIUS_CARD,
    RADIUS_CONTROL,
    RADIUS_POPOVER,
    SANS_FONT_FAMILY,
    STATUS_TONE_COLOURS,
    STATUS_TONE_FILL_ALPHA,
    TYPE_BODY,
    TYPE_CAPTION,
    TYPE_DISPLAY,
    TYPE_HEADING,
    TYPE_LEAD,
    TYPE_TITLE,
    ThemePalette,
    hex_rgb,
    readable_tone,
)


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


def _dashboard_tone_rules(palette: ThemePalette) -> str:
    rules = []
    for tone, slot in DASHBOARD_TONE_COLOURS.items():
        colour = getattr(palette, slot)
        red, green, blue = hex_rgb(colour)
        rules.append(
            f'QFrame#metaCallout[tone="{tone}"] {{ background: rgba({red}, {green}, {blue}, 20); '
            f"border-color: rgba({red}, {green}, {blue}, 115); }}\n"
        )
        rules.append(
            f'QLabel#statusDot[tone="{tone}"] {{ color: {colour}; }}\n'
            f'QLabel#searchMapSummaryChip[tone="{tone}"] {{ border-color: {colour}; color: {colour}; }}\n'
            f'QLabel#sampleCount[tone="{tone}"] {{ color: {palette.text_strong}; border-color: {colour}; }}\n'
            f'QLabel#warningCount[tone="{tone}"] {{ color: {colour}; border-color: {colour}; }}\n'
        )
    rules.append('QLabel#searchMapSummaryChip[spaced="true"] { margin-top: 4px; }\n')
    return "\n".join(rules)


def _viewer_status_rules(palette: ThemePalette) -> str:
    """Tones for the viewer's provenance line, readable on the page."""

    warn = readable_tone(palette.chart_amber, palette.background, palette.text_strong)
    bad = readable_tone(palette.chart_red, palette.background, palette.text_strong)
    return (
        f'QLabel#viewerStatus[tone="warn"] {{ color: {warn}; }}\n'
        f'QLabel#viewerStatus[tone="bad"] {{ color: {bad}; font-weight: 600; }}\n'
    )


def _session_intake_rules(palette: ThemePalette) -> str:
    """Tone colours for the Load sessions window and the drop receipt."""

    red, green, blue = hex_rgb(palette.chart_red)
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

/* The page surfaces' background is set once, at the end of the sheet, so it
   outranks every rule above it. */

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

QWidget#viewerCanvasShell {{
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

/* ---------------------------------------------------------------------
   Canvas chrome: the controls floating over the image. The canvas is
   near-black in both themes, so these take the ``canvas_*`` colours
   (``CANVAS_CHROME``, the dark palette) in both, like the painted Atlas
   legend and cluster popup beside them.
   --------------------------------------------------------------------- */

/* Floating over the canvas, so it shares the floating-control size (32 px):
   22 px of content, 4 px padding and a 1 px border on each side. */
QPushButton#viewerToolButton {{
    background: {canvas_surface};
    border: 1px solid {canvas_border_strong};
    border-radius: {radius_card}px;
    color: {canvas_text};
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
    background: {canvas_surface_alt};
    border-color: {canvas_accent_line};
    color: {canvas_text_strong};
}}

QPushButton#viewerToolButton:pressed {{
    background: {canvas_accent_soft};
    border-color: {canvas_accent_line};
}}

QPushButton#viewerToolButton:checked {{
    background: {canvas_primary_fill};
    border-color: {canvas_primary_fill};
    color: {canvas_primary_text};
}}

QPushButton#viewerToolButton:checked:hover {{
    background: {canvas_primary_fill_hover};
    border-color: {canvas_primary_fill_hover};
    color: {canvas_primary_text};
}}

QPushButton#viewerZoomButton {{
    background: transparent;
    border: 0;
    border-radius: {radius_control}px;
    color: {canvas_text};
    font-weight: 700;
    min-width: 32px;
    min-height: 32px;
    padding: 0;
    text-align: center;
}}

QFrame#viewerControlDivider {{
    background: {canvas_border};
    border: 0;
}}

QPushButton#viewerZoomButton:hover {{
    background: {canvas_surface_alt};
    color: {canvas_text_strong};
}}

QPushButton#viewerZoomButton:pressed {{
    background: {canvas_accent_soft};
    color: {canvas_accent};
}}

QWidget#viewerOverlayPanel,
QWidget#viewerZoomPanel,
QLabel#viewerImageBadge {{
    background: {canvas_surface};
    border: 1px solid {canvas_border_strong};
    border-radius: {radius_card}px;
}}

QWidget#viewerOverlayPanel {{
    padding: 2px;
}}

/* The Overlays panel's own controls, on the canvas surface. */
QWidget#viewerOverlayPanel QCheckBox {{
    color: {canvas_text_muted};
}}

QWidget#viewerOverlayPanel QCheckBox:hover {{
    color: {canvas_text};
}}

QWidget#viewerOverlayPanel QCheckBox::indicator {{
    border: 1px solid {canvas_border_strong};
    background: {canvas_surface};
}}

QWidget#viewerOverlayPanel QCheckBox::indicator:checked {{
    background: {canvas_accent};
    border-color: {canvas_accent};
    image: url("{canvas_check_icon}");
}}

QWidget#viewerOverlayPanel QCheckBox::indicator:disabled {{
    background: {canvas_surface_alt};
    border-color: {canvas_border};
}}

QWidget#viewerOverlayPanel QPushButton {{
    background: {canvas_surface};
    border: 1px solid {canvas_control_border};
    color: {canvas_text};
}}

QWidget#viewerOverlayPanel QPushButton:hover {{
    background: {canvas_surface_alt};
    border-color: {canvas_accent_line};
}}

QWidget#viewerOverlayPanel QPushButton:pressed {{
    background: {canvas_accent_soft};
}}

QWidget#viewerOverlayPanel QScrollBar:vertical {{
    background: {canvas_panel};
}}

QWidget#viewerOverlayPanel QScrollBar::handle:vertical {{
    background: {canvas_scroll_thumb};
}}

/* The Data collections disclosure in the Overlays panel. With no rule, the
   native Windows 11 style drew it, and painted the checked (open) state in
   the system accent blue in both themes. Every state names a fill and a
   border, so the sheet always draws it: a rule without them hands the button
   back to the native style (offscreen tests cannot see that). It reads as a
   row of the panel, like the check boxes beside it, until hovered or open.
   The arrow takes the text colour. */
QToolButton#viewerOverlaySectionButton {{
    background: {canvas_surface};
    border: 1px solid {canvas_surface};
    border-radius: {radius_control}px;
    color: {canvas_text_muted};
    padding: 2px 8px 2px 0;
}}

QToolButton#viewerOverlaySectionButton:hover {{
    background: {canvas_surface_alt};
    border-color: {canvas_border};
    color: {canvas_text};
}}

QToolButton#viewerOverlaySectionButton:checked {{
    background: {canvas_surface_alt};
    border-color: {canvas_border};
    color: {canvas_text_strong};
}}

QToolButton#viewerOverlaySectionButton:checked:hover {{
    background: {canvas_surface_hi};
    border-color: {canvas_border_strong};
    color: {canvas_text_strong};
}}

/* Last and as specific as the states above, so keyboard focus always shows:
   the id rule outranks the app-wide ``QToolButton:focus`` border. */
QToolButton#viewerOverlaySectionButton:focus,
QToolButton#viewerOverlaySectionButton:checked:focus {{
    border-color: {canvas_accent};
}}

QLabel#viewerImageBadge {{
    color: {canvas_text};
    padding: 6px 10px;
    font-family: {mono_font};
    font-size: {type_caption}pt;
}}

/* No min-width here: the viewer fixes this label's size in code, and a width
   rule would undo that on every polish, letting each zoom step re-lay out the
   whole window (plan F5.1). */
QLabel#viewerZoomLabel {{
    color: {canvas_text_muted};
    font-family: {mono_font};
    font-size: {type_caption}pt;
    padding: 0;
}}

/* End of canvas chrome. */

/* The Linked strip (harmonisation B1): jumps as compact links on their own
   row, the item shown in its place. An unavailable jump keeps its place,
   dashed and muted; the context header says why. */
QPushButton#viewerNavButton {{
    min-height: 24px;
    padding: 0 10px;
    font-weight: 500;
}}

QPushButton#viewerNavButton:disabled {{
    background: transparent;
    border-style: dashed;
    border-color: {border_strong};
    color: {text_muted};
}}

QLabel#viewerLinkedHere {{
    background: {accent_soft};
    border-radius: {radius_control}px;
    color: {text_strong};
    padding: 3px 10px;
    font-weight: 600;
}}

QLabel#viewerLinkedSeparator {{
    color: {text_muted};
    padding: 0 2px;
}}

QLabel#imageExportPreview {{
    background: {canvas};
    border: 1px solid {border_strong};
    border-radius: {radius_card}px;
    padding: 6px;
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

/* The provenance line under the canvas (harmonisation A3): a caption, not
   a box. A fallback, an unreadable MRC or parse warnings take a tone; the
   tone rules are ``_viewer_status_rules``. */
QLabel#viewerStatus {{
    color: {text_muted};
    background: transparent;
    border: 0;
    padding: 2px 4px;
    font-size: {type_caption}pt;
}}

/* The frame readout beside the scrubber. */
QLabel#frameLabel {{
    color: {text};
    background: transparent;
    border: 0;
    padding: 0 4px;
    font-family: {mono_font};
    font-size: {type_caption}pt;
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

/* Three chips (harmonisation D3): the status pill, a count badge and an
   unboxed readout. Only things that can be pressed have a box beside them. */
QLabel#statusSegment,
QLabel#viewerChipStatus {{
    background: {surface};
    border: 1px solid {border};
    border-radius: {radius_control}px;
    color: {text_muted};
    padding: 3px 8px;
    font-size: {type_caption}pt;
}}

/* The status pill, in the header and in the context panel alike; its tone
   rules follow at the end of the sheet. */
QLabel#viewerChipStatus {{
    background: {accent_soft};
    border-color: {accent_line};
    color: {text_strong};
    font-weight: 600;
}}

/* A readout: a number with its unit, or an identifier. It was boxed like the
   jump buttons beside it, so readouts read as controls (B2). */
QLabel#viewerChip {{
    background: transparent;
    border: 0;
    border-left: 1px solid transparent;
    color: {text_muted};
    padding: 3px 0 3px 0;
    font-family: {mono_font};
    font-size: {type_caption}pt;
}}

/* A thin rule between consecutive readouts. */
QLabel#viewerChip[separated="true"] {{
    border-left-color: {border_strong};
    padding-left: 10px;
}}

/* Count badge: a number that qualifies a heading or a row. */
QLabel#panelCount,
QLabel#warningCount,
QLabel#viewerChipMore {{
    background: transparent;
    border: 1px solid {border};
    border-radius: 9px; /* pill */
    color: {text_muted};
    padding: 1px 8px;
    font-size: {type_caption}pt;
    font-weight: 600;
    min-height: 16px;
}}

QLabel#screenEyebrow,
QLabel#panelHeader,
QLabel#viewerPanelHeader,
QLabel#viewerLinkedLabel {{
    color: {text_muted};
    font-size: {type_caption}pt;
    font-weight: 600;
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

/* A link-style action, subordinate to the screen's one #primaryButton. The
   empty state's primary action used to have a recipe of its own on the plain
   accent, which read at 3.2:1 under its label in the light theme. */
QPushButton#secondaryAction {{
    background: transparent;
    color: {link_text};
    border: none;
    padding: 5px 8px;
    font-weight: 500;
}}

QPushButton#secondaryAction:hover {{
    color: {link_text_hover};
    text-decoration: underline;
}}

/* Dashboard warning actions are QToolButtons. Style both text and surface so
   a dark Linux desktop palette cannot supply a black native button. */
QToolButton#inlineLink {{
    background: transparent;
    border: 0;
    color: {link_text};
    padding: 4px 8px;
}}

QToolButton#inlineLink:hover {{
    color: {link_text_hover};
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
    font-weight: 600;
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

/* Empty states sit over the near-black image canvas in both themes. */
QWidget#emptyStatePanel {{
    background: transparent;
}}

QLabel#emptyStateTitle {{
    color: {canvas_text_strong};
    font-size: {type_lead}pt;
    font-weight: 600;
}}

QLabel#emptyStateDetail {{
    color: {canvas_text};
    font-size: {type_body}pt;
}}

QLabel#emptyStateEvidence {{
    color: {canvas_text_muted};
    font-size: {type_caption}pt;
}}

/* Interactive cards get a restrained, visible response. A card with nothing
   in it is inert and must not react — see StatCard's cardEmpty property. */
QFrame#dashboardCard[cardEmpty="false"]:hover {{
    border-color: {accent_line};
}}

QFrame#dashboardCard[cardEmpty="true"] {{
    color: {text_muted};
}}

QWidget#projectBadgeLegend {{
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
    color: {link_text};
    font-size: {type_caption}pt;
    font-weight: 600;
    border: none;
    padding: 0 4px;
}}

QPushButton#contextClearFilter:hover {{
    color: {link_text_hover};
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

/* The dashboard's and the report dialog's secondary buttons: one rule for
   both classes (they were styled twice, with different borders and padding). */
QToolButton#dashboardFilterButton,
QPushButton#dashboardFilterButton {{
    background: {surface_alt};
    border: 1px solid {border};
    border-radius: {radius_control}px;
    color: {text};
    padding: 4px 10px;
    font-weight: 600;
}}

QToolButton#dashboardFilterButton:hover,
QPushButton#dashboardFilterButton:hover {{
    border-color: {accent};
    background: {surface_hi};
}}

QToolButton#dashboardFilterButton:checked,
QPushButton#dashboardFilterButton:checked {{
    border-color: {accent};
    color: {text_strong};
}}

QToolButton#dashboardFilterButton:disabled,
QPushButton#dashboardFilterButton:disabled {{
    color: {text_muted};
    border-color: {border};
    background: {surface};
}}

/* Segmented control (D2): one track; the chosen segment takes the selection
   fill, as a checked toolbar toggle takes the soft accent. */
QFrame#segmentedControl {{
    background: {panel};
    border: 1px solid {border_strong};
    border-radius: {radius_control}px;
}}

QToolButton#segmentButton {{
    background: transparent;
    border: 1px solid transparent;
    border-radius: {radius_control}px;
    margin: 1px;
    color: {text_muted};
    padding: 2px 12px;
    font-weight: 500;
}}

QToolButton#segmentButton:hover {{
    background: {surface_hi};
    color: {text};
}}

QToolButton#segmentButton:checked {{
    background: {selection};
    color: {selection_text};
    font-weight: 600;
}}

QToolButton#segmentButton:focus {{
    border-color: {accent};
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
   (plan F5.3, ui/widgets/sliding_tab_bar.py). The fills and underline are
   for any other tab bar. */

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

QFrame#dashboardCard:focus,
QWidget#dashboardInteractiveRow:focus,
QWidget#statusTileGrid:focus,
QWidget#acquisitionTimeline:focus {{
    border: 1px solid {accent};
}}

/* A warning in the context panel: the dashboard's tone, as a callout (C1).
   Tone rules follow at the end of the sheet. */
QFrame#metaCallout {{
    background: {surface_alt};
    border: 1px solid {border};
    border-radius: {radius_card}px;
}}

QLabel#metaCalloutTitle {{
    color: {text_strong};
    font-weight: 600;
}}

QLabel#metaCalloutDetail,
QLabel#metaValueDetail {{
    color: {text_muted};
    font-size: {type_caption}pt;
}}

QFrame#warningGroup {{
    background: {surface_alt};
    border: 1px solid {border};
    border-radius: {radius_card}px;
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
        **{f"canvas_{slot}": value for slot, value in CANVAS_CHROME.__dict__.items()},
        sans_font=SANS_FONT_FAMILY,
        mono_font=MONO_FONT_FAMILY,
        check_icon=_check_indicator_path(palette),
        canvas_check_icon=_check_indicator_path(CANVAS_CHROME),
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
        + "\n"
        + _viewer_status_rules(palette)
    )


def _check_indicator_path(palette: ThemePalette) -> str:
    """Absolute POSIX path to a tick recoloured for ``palette``.

    Written once per palette into the user's cache directory: Qt style sheets
    can only reference an image by URL, so this one icon has to exist on disk
    rather than being recoloured in memory like every other icon.

    Deliberately self-contained: ``ui.icons`` loads the theme, so reaching
    into it from here invites a circular import.
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
