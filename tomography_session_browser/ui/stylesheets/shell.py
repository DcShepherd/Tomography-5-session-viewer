"""Window, menus, toolbars, buttons and the other controls the whole shell shares.

A fragment of the application's QSS template, filled in with the palette
and tokens by ``ui.stylesheet.build_stylesheet``. Fragments are joined in
the order listed in ``ui.stylesheets.__init__``: QSS precedence depends on
it, so a rule moves only together with the ones it overrides.
"""

TEMPLATE = """
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

QFrame#projectDrawer {{
    background: {background};
    border: 0;
    border-right: 1px solid {border_strong};
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

/* A Colour by mode with no data: greyed, but it takes the focus so that its
   reason (the tooltip and accessible description) can be read. It takes no
   fill, under the pointer or the focus, so it does not look selectable; the
   keyboard focus shows as an outline only. The transparent border keeps the
   row from moving when the focus arrives. */
QWidget#positionsColourUnavailable {{
    background: transparent;
    border: 1px solid transparent;
    border-radius: {radius_control}px;
}}

QWidget#positionsColourUnavailable:focus {{
    border-color: {accent};
}}

QWidget#positionsColourUnavailable QRadioButton:disabled {{
    color: {text_muted};
}}

/* A Colour by mode that can be selected takes the selection fill under the
   pointer, as other menu items do. Each row spans the menu's width. */
QMenu#positionsColourMenu QRadioButton#positionsColourChoice {{
    border-radius: {radius_control}px;
}}

QMenu#positionsColourMenu QRadioButton#positionsColourChoice:enabled:hover {{
    background: {selection};
    color: {selection_text};
}}

/* The Colour by radios. Without these rules Fusion draws the ring from the
   window colour, which nearly disappears on the dark menu. ``control_border``
   clears 3:1 on ``surface`` in both themes; the selected mode is an accent
   ring and dot; a greyed mode is quieter. */
QMenu#positionsColourMenu QRadioButton::indicator {{
    width: 10px;
    height: 10px;
    border: 1px solid {control_border};
    border-radius: 6px; /* pill */
    background: {surface};
}}

QMenu#positionsColourMenu QRadioButton::indicator:checked {{
    border-color: {accent};
    image: url("{radio_dot_icon}");
}}

QMenu#positionsColourMenu QRadioButton::indicator:disabled {{
    border-color: {border_strong};
    background: {surface_alt};
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

/* The 2 px border shows the card at rest (the fill stops inside it), so the
   focus ring can go round the fill and change colour, never size. A ring on
   the fill itself could not reach 3:1 in both themes (Batch 4c, Q2). */
QPushButton#primaryButton {{
    background: {primary_fill};
    background-clip: padding;
    border: 2px solid transparent;
    color: {primary_text};
    font-weight: 600;
}}

QPushButton#primaryButton:hover {{
    background: {primary_fill_hover};
    border-color: transparent;
    color: {primary_text};
}}

/* A disabled primary action still reads as the primary one, muted, rather
   than as a disabled secondary button (plan C13). */
QPushButton#primaryButton:disabled {{
    background: {accent_soft};
    border-color: {accent_line};
    color: {text_muted};
}}

"""
