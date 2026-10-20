"""Canvas chrome: the controls floating over the image, and the Overlays panel.

A fragment of the application's QSS template, filled in with the palette
and tokens by ``ui.stylesheet.build_stylesheet``. Fragments are joined in
the order listed in ``ui.stylesheets.__init__``: QSS precedence depends on
it, so a rule moves only together with the ones it overrides.
"""

TEMPLATE = """\
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
    border: 1px solid transparent;
    border-radius: {radius_control}px;
    color: {canvas_text};
    font-weight: 700;
    min-width: 30px;
    min-height: 30px;
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

QPushButton#viewerZoomButton:focus,
QPushButton#viewerToolButton:focus,
QPushButton#viewerToolButton:checked:focus,
QPushButton#viewerToolButton:checked:hover:focus {{
    border-color: {canvas_accent};
}}

/* Rounded as the shell is, so no square corner of this border shows past the
   curve; the shell's ``CanvasFrame`` paints the visible edge over it, since
   the square viewport covers this border's own arc. */
QGraphicsView#viewerCanvas {{
    border: 1px solid transparent;
    border-radius: {radius_card}px;
}}

QGraphicsView#viewerCanvas:focus {{
    border-color: {canvas_accent};
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

/* A row that sets several layers which do not agree shows a dash, not a tick:
   it is neither on nor off, and the shape says so without colour. */
QWidget#viewerOverlayPanel QCheckBox::indicator:indeterminate {{
    background: {canvas_accent};
    border-color: {canvas_accent};
    image: url("{canvas_dash_icon}");
}}

QWidget#viewerOverlayPanel QWidget#viewerOverlayBody,
QWidget#viewerOverlayPanel QWidget#viewerOverlayRow {{
    background: transparent;
}}

QWidget#viewerOverlayPanel QLabel#viewerOverlaySectionTitle {{
    color: {canvas_text_muted};
    font-size: {type_caption}pt;
    font-weight: 600;
    padding: 6px 0 0 0;
}}

/* The line under a row's label: how the layer behaves, or why it is not there. */
QWidget#viewerOverlayPanel QLabel#viewerOverlayHint {{
    color: {canvas_text_muted};
    font-size: {type_caption}pt;
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

"""
