"""The dashboard card and the viewer's chips (status pill, readout, count badge).

A fragment of the application's QSS template, filled in with the palette
and tokens by ``ui.stylesheet.build_stylesheet``. Fragments are joined in
the order listed in ``ui.stylesheets.__init__``: QSS precedence depends on
it, so a rule moves only together with the ones it overrides.
"""

TEMPLATE = """\
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

/* The sample summary's atlas card: the atlas's name beside its picture. */
QLabel#cardName {{
    color: {text_strong};
    font-weight: 600;
}}

/* The atlas session's contact sheet (B3.4): a tile per atlas. A tile that a
   collection sample uses takes the accent line; its sample's name sits over the
   picture, in the canvas chrome both themes share. */
QFrame#atlasTile {{
    background: {surface};
    border: 1px solid {border};
    border-radius: {radius_card}px;
}}

QFrame#atlasTile[linked="true"] {{
    border-color: {accent_line};
}}

QFrame#atlasTile:hover {{
    border-color: {border_strong};
}}

QFrame#atlasTile:focus {{
    border-color: {accent};
}}

QLabel#tileLink {{
    background: {canvas_surface};
    border: 1px solid {canvas_border};
    border-radius: {radius_control}px;
    color: {canvas_text};
    font-size: {type_caption}pt;
    padding: 1px 7px;
}}

QLabel#tileCount {{
    color: {text_strong};
    font-size: {type_caption}pt;
    font-weight: 600;
}}

/* A state worth a word on a tile or a row: Missing, Re-acquired. Its colour is
   a tone rule (ui/stylesheet.py), so a theme switch recolours it in place. */
QLabel#statePill {{
    border: 1px solid {border_strong};
    border-radius: 9px; /* pill */
    color: {text};
    font-size: {type_caption}pt;
    padding: 1px 8px;
    min-height: 16px;
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

QSplitter#viewerSplitter::handle:horizontal {{
    width: 5px;
    border: 0;
    background: {border};
}}

QSplitter#viewerSplitter::handle:horizontal:hover {{
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

/* The collection summary (design review B3.2). A section on the page, not a
   card title: the Lead tier names it; these rules style what sits under it. */
QLabel#outcomeTotal {{
    color: {text_strong};
    font-size: {type_display}pt;
    font-weight: 600;
}}

QLabel#outcomeCount {{
    color: {text_strong};
    font-weight: 600;
}}

/* A count in a tone: failed in red, incomplete in amber. The colour is a
   tone rule below, so a theme switch recolours it in place. */
QLabel#toneText {{
    font-weight: 600;
}}

QLabel#tableHead {{
    color: {text_muted};
    font-size: {type_caption}pt;
    font-weight: 600;
    text-transform: uppercase;
}}

QToolButton#tableLink {{
    background: transparent;
    border: 0;
    color: {link_text};
    font-weight: 600;
    padding: 0;
}}

QToolButton#tableLink:hover {{
    color: {link_text_hover};
    text-decoration: underline;
}}

QLabel#scopeBadge {{
    border: 1px solid {border_strong};
    border-radius: 4px;
    color: {text_muted};
    font-size: {type_caption}pt;
    font-weight: 600;
    padding: 0px 6px;
}}

"""
