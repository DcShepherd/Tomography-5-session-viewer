"""The Linked strip, toggles and ticks, inputs, headers, keyboard focus and the viewer's provenance line.

A fragment of the application's QSS template, filled in with the palette
and tokens by ``ui.stylesheet.build_stylesheet``. Fragments are joined in
the order listed in ``ui.stylesheets.__init__``: QSS precedence depends on
it, so a rule moves only together with the ones it overrides.
"""

TEMPLATE = """\
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

QTabBar::tab:disabled {{
    color: {text_muted};
    background: {background};
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

"""
