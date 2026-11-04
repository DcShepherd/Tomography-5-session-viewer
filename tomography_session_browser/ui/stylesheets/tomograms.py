"""The Tomograms tab: its title and version rows, stage frame, list and Image settings.

A fragment of the application's QSS template, filled in with the palette
and tokens by ``ui.stylesheet.build_stylesheet``. Fragments are joined in
the order listed in ``ui.stylesheets.__init__``: QSS precedence depends on
it, so a rule moves only together with the ones it overrides.

The title, version and feedback rows, the list and Image settings follow the
theme. The stage frame (canvas toolbar, panes and plane band) is canvas
chrome, near-black in both themes, so it takes the ``canvas_*`` colours.
"""

TEMPLATE = """\
QSplitter#tomogramListColumn::handle {{ background: {panel}; }}
QSplitter#tomogramListColumn::handle:hover {{ background: {border}; }}
QFrame#tomogramTagsPanel {{ background: {panel}; border-top: 1px solid {border}; }}
QFrame#tomogramTagsPopup, QFrame#tomogramTagFilterChoices {{ background: {panel}; border: 1px solid {border_strong}; border-radius: {radius_control}px; }}
QLabel#tomogramTagEntryError {{ color: {chart_red}; font-size: {type_caption}pt; }}
QPushButton#tomogramTagCount {{ background: transparent; border: 1px solid transparent; padding: 0 4px; text-align: right; }}
QPushButton#tomogramTagCount:hover {{ color: {link_text}; background: {surface_hi}; }}
QPushButton#tomogramTagCount:focus {{ border-color: {accent}; }}
QFrame#captureRow {{ background: {panel}; border-bottom: 1px solid {border}; }}
QFrame#captureRow:hover {{ background: {surface_hi}; }}
QFrame#captureRow QPushButton {{ background: transparent; border: none; padding: 0; text-align: left; color: {text}; }}
QFrame#captureRow QPushButton:hover {{ color: {link_text}; }}
QFrame#captureRow QPushButton:focus {{ border: 1px solid {accent}; }}
QFrame#captureRow QLabel {{ color: {text_muted}; font-size: {type_caption}pt; }}
QFrame#captureRow QToolButton {{ background: transparent; border: none; padding: 2px; }}
QFrame#captureRow QToolButton:hover {{ background: {surface_hi}; }}
QFrame#captureRow QToolButton:focus {{ border: 1px solid {accent}; }}
QFrame#captureRecovery {{ background: {surface}; border-left: 2px solid {chart_amber}; }}
QFrame#captureRecovery QPushButton {{ background: transparent; border: none; color: {link_text}; }}
QFrame#captureRecovery QPushButton:focus {{ border: 1px solid {accent}; }}
QComboBox#tomogramSort {{
    background: {surface};
    color: {text};
    border: 1px solid {border};
    border-radius: {radius_control}px;
    padding: 6px 10px;
    min-height: 24px;
}}
QComboBox#tomogramSort:focus {{ border-color: {accent}; }}
QComboBox#tomogramSort::drop-down {{ border: none; background: transparent; width: 20px; }}
/* ---------------------------------------------------------------------
   The title and Linked strip share viewer styles. At compact widths the
   selector replaces the title; Snapshot lives in Image settings.
   --------------------------------------------------------------------- */
QComboBox#tomogramTitleSelector {{
    background: transparent;
    border: 1px solid transparent;
    color: {text_strong};
    font-size: {type_heading}pt;
    font-weight: 600;
    padding-left: 0;
}}
QComboBox#tomogramTitleSelector:focus {{
    border-color: {accent};
}}
/* Viewer and list part at a hairline; the handle keeps a 7 px drag area. */
QSplitter#tomogramSplitter::handle:horizontal {{
    width: 1px;
    border-left: 3px solid transparent;
    border-right: 3px solid transparent;
    background: {border};
    background-clip: padding;
}}
QSplitter#tomogramSplitter::handle:horizontal:hover {{
    background: {accent};
}}
/* Three named filters must fit together in the normal review column. */
QWidget#tomogramList QToolButton#segmentButton {{
    padding: 2px 4px;
}}

/* ---------------------------------------------------------------------
   The stage frame: one rounded canvas-chrome block holding the toolbar,
   the panes and the plane band, so nothing floats over the tomogram.
   --------------------------------------------------------------------- */
QFrame#tomogramStageFrame {{
    background: {canvas_background};
    border: 1px solid {canvas_border};
    border-radius: {radius_card}px;
}}
QWidget#tomogramCorner {{
    background: {canvas_background};
}}
QWidget#tomogramCorner QLabel {{ color: {canvas_text_muted}; }}
QWidget#tomogramCorner QSpinBox {{
    background: {canvas_panel};
    color: {canvas_text_strong};
    border: 1px solid {canvas_border_strong};
}}
QWidget#tomogramCorner QSpinBox:focus {{ border-color: {canvas_accent}; }}
QWidget#tomogramStage QSplitter::handle {{
    background: {canvas_border};
}}
QWidget#tomogramCanvasToolbar {{
    background: {canvas_background};
    border-bottom: 1px solid {canvas_border};
    border-top-left-radius: {radius_card}px;
    border-top-right-radius: {radius_card}px;
}}
QWidget#tomogramCanvasToolbar QPushButton {{
    background: transparent;
    color: {canvas_text_muted};
    border: 1px solid transparent;
    border-radius: {radius_control}px;
    padding: 4px 8px;
    min-width: 0;
    min-height: 20px;
}}
QWidget#tomogramCanvasToolbar QPushButton:hover {{
    background: {canvas_surface_alt};
    color: {canvas_text_strong};
}}
QWidget#tomogramCanvasToolbar QPushButton:checked {{
    background: {canvas_accent_soft};
    border-color: {canvas_accent_line};
    color: {canvas_text_strong};
}}
QWidget#tomogramCanvasToolbar QPushButton:focus {{
    border-color: {canvas_accent};
    color: {canvas_text_strong};
}}
QWidget#tomogramCanvasToolbar QPushButton#tomogramZoomValue {{
    color: {canvas_text_strong};
}}
QPushButton#tomogramZoomValue:focus {{
    color: {canvas_text_strong};
    border-color: {canvas_accent};
}}
QWidget#tomogramCanvasToolbar QPushButton:disabled {{
    color: {canvas_text_muted};
}}
QPushButton#tomogramIconButton {{
    min-width: 20px;
    padding: 4px 4px;
}}
QPushButton#tomogramIconButton:focus {{
    border-color: {canvas_accent};
}}
QLabel#tomogramToolbarLabel {{
    color: {canvas_text_muted};
    font-size: {type_caption}pt;
    padding: 0 2px 0 6px;
}}
QWidget#tomogramCanvasToolbar QComboBox,
QWidget#tomogramCanvasToolbar QSpinBox,
QWidget#tomogramPlaneBand QComboBox,
QWidget#tomogramPlaneBand QSpinBox {{
    background: {canvas_panel};
    color: {canvas_text_strong};
    border: 1px solid {canvas_border_strong};
    border-radius: {radius_control}px;
    padding: 2px 6px;
    min-width: 0;
}}
QWidget#tomogramCanvasToolbar QComboBox:focus,
QWidget#tomogramCanvasToolbar QSpinBox:focus,
QWidget#tomogramPlaneBand QComboBox:focus,
QWidget#tomogramPlaneBand QSpinBox:focus {{
    border-color: {canvas_accent};
}}
/* Every toolbar control is a 24 px cell in a bordered 28 px group, so the
   Slices and zoom steppers match. Hairlines part cells; groups need no
   dividers. Two-ID selectors outrank the toolbar-wide control rules. */
QFrame#tomogramToolGroup {{
    background: transparent;
    border: 1px solid {canvas_border_strong};
    border-radius: {radius_control}px;
}}
QFrame#tomogramToolHair {{
    background: {canvas_border};
    border: 0;
}}
/* QSS sizes replace sizes set in code, so the 24 px cell is set here:
   22 px of content inside a 1 px border. */
QWidget#tomogramCanvasToolbar QFrame#tomogramToolGroup QPushButton {{
    padding: 0 6px;
    min-height: 22px;
    max-height: 22px;
}}
QWidget#tomogramCanvasToolbar QFrame#tomogramToolGroup QPushButton#tomogramIconButton {{
    padding: 0;
    min-width: 22px;
    max-width: 22px;
}}
QWidget#tomogramCanvasToolbar QFrame#tomogramToolGroup QPushButton:checked {{
    border-color: transparent;
}}
QWidget#tomogramCanvasToolbar QFrame#tomogramToolGroup QComboBox,
QWidget#tomogramCanvasToolbar QFrame#tomogramToolGroup QSpinBox {{
    background: transparent;
    border: 1px solid transparent;
    padding: 0 4px;
}}
QWidget#tomogramCanvasToolbar QFrame#tomogramToolGroup QPushButton:focus,
QWidget#tomogramCanvasToolbar QFrame#tomogramToolGroup QComboBox:focus,
QWidget#tomogramCanvasToolbar QFrame#tomogramToolGroup QSpinBox:focus {{
    border-color: {canvas_accent};
}}
QWidget#tomogramPlaneBand {{
    background: {canvas_background};
    border-top: 1px solid {canvas_border};
    border-bottom-left-radius: {radius_card}px;
    border-bottom-right-radius: {radius_card}px;
}}
QWidget#tomogramPlaneBand QLabel {{
    color: {canvas_text};
}}
/* The plane slider sits on canvas chrome in both themes. */
QWidget#tomogramPlaneBand QSlider::groove:horizontal,
QWidget#tomogramPlaneBand QSlider::add-page:horizontal {{
    background: {canvas_border_strong};
}}
QWidget#tomogramPlaneBand QSlider::sub-page:horizontal {{
    background: {canvas_accent};
}}
QWidget#tomogramPlaneBand QSlider::handle:horizontal {{
    background: {canvas_text_strong};
    border-color: {canvas_accent};
}}
/* Words in the UI face; the coordinates stay monospaced so they do not jitter. */
QLabel#tomogramReadout {{
    color: {canvas_text};
}}
QLabel#tomogramCursor {{
    color: {text_muted};
    font-family: {mono_font};
}}
QWidget#tomogramPlaneBand QLabel#tomogramCursor {{
    color: {canvas_text_muted};
}}
/* Rate and end behaviour: one menu button on the band, never a default white face. */
QPushButton#tomogramRate {{
    background: {canvas_panel};
    color: {canvas_text};
    border: 1px solid {canvas_border_strong};
    border-radius: {radius_control}px;
    padding: 2px 8px;
    min-width: 0;
}}
QPushButton#tomogramRate:hover {{
    background: {canvas_surface_alt};
}}
QPushButton#tomogramRate:focus {{
    border-color: {canvas_accent};
}}
QPushButton#tomogramPlay {{
    background: transparent;
    color: {canvas_text};
    border: 1px solid {canvas_border_strong};
    border-radius: 14px; /* pill */
    min-width: 26px;
    max-width: 26px;
    min-height: 26px;
    max-height: 26px;
    padding: 0;
}}
QPushButton#tomogramPlay:hover {{
    background: {canvas_surface_alt};
}}
QPushButton#tomogramPlay[playing="true"] {{
    border-color: {canvas_accent};
}}
QPushButton#tomogramPlay:focus {{
    border-color: {canvas_accent};
}}
/* The pixel-size prompt: canvas chrome with an amber edge, above the band. */
QFrame#tomogramPixelPrompt {{
    background: {canvas_surface};
    border-top: 1px solid {canvas_chart_amber};
    border-bottom: 1px solid {canvas_border};
}}
QFrame#tomogramPixelPrompt QLabel {{
    color: {canvas_text};
}}
QLabel#tomogramPromptTitle {{
    color: {canvas_chart_amber};
    font-weight: 600;
}}
QFrame#tomogramPixelPrompt QLabel#tomogramPromptNote {{
    color: {canvas_text_muted};
}}
QFrame#tomogramPixelPrompt QDoubleSpinBox {{
    background: {canvas_panel};
    color: {canvas_text};
    border: 1px solid {canvas_border_strong};
    border-radius: {radius_control}px;
    padding: 2px 6px;
}}
QFrame#tomogramPixelPrompt QPushButton {{
    background: transparent;
    color: {canvas_text};
    border: 1px solid {canvas_border_strong};
    border-radius: {radius_control}px;
    padding: 2px 10px;
}}
QFrame#tomogramPixelPrompt QPushButton#tomogramPromptSuggestion {{
    border-style: dashed;
    border-color: {canvas_accent_line};
    color: {canvas_accent};
}}
QFrame#tomogramPixelPrompt QPushButton#tomogramPromptPrimary {{
    background: {canvas_accent};
    border-color: {canvas_accent};
    color: {canvas_background};
    font-weight: 600;
}}
QFrame#tomogramPixelPrompt QPushButton:focus,
QFrame#tomogramPixelPrompt QPushButton#tomogramPromptSuggestion:focus,
QFrame#tomogramPixelPrompt QPushButton#tomogramPromptPrimary:focus,
QFrame#tomogramPixelPrompt QDoubleSpinBox:focus {{
    border: 1px solid {canvas_text_strong};
}}
QToolButton#tomogramInsetClose {{
    background: {canvas_surface};
    color: {canvas_text};
    border: 1px solid {canvas_border_strong};
    border-radius: {radius_control}px;
    padding: 0;
}}
QToolButton#tomogramInsetClose:focus {{
    border: 2px solid {canvas_accent};
}}
QToolButton#tomogramInsetClose:hover {{
    background: {canvas_accent_soft};
}}

/* The feedback row under the stage: one quiet line and Captures. */
QPushButton#tomogramQuiet {{
    background: transparent;
    border: 1px solid transparent;
}}
QPushButton#tomogramQuiet:hover {{
    background: {surface_hi};
}}
QPushButton#tomogramQuiet:focus {{
    border-color: {accent};
}}
QLabel#tomogramFeedback {{
    color: {text_muted};
    font-size: {type_caption}pt;
}}

/* ---------------------------------------------------------------------
   Image settings: uppercase section headings, label | field | unit |
   switch rows, folding sections that say their choice while folded.
   --------------------------------------------------------------------- */
QScrollArea#tomogramImageScroll, QWidget#tomogramImageContent {{
    background: {background};
    border: 0;
}}
QLabel#tomogramPanelTitle,
QLabel#imageSectionTitle {{
    color: {text_muted};
    font-size: {type_caption}pt;
    font-weight: 600;
}}
QLabel#tomogramSectionSummary,
QLabel#tomogramUnit,
QLabel#tomogramNote {{
    color: {text_muted};
    font-size: {type_caption}pt;
}}
QFrame#tomogramSectionRule {{
    background: {border};
    border: 0;
}}
QLabel#tomogramSliderValue {{
    color: {text_strong};
}}
/* A slider or field whose switch is off reads as off: with the accent kept,
   the lowpass slider looked live while the filter was off. */
QWidget#tomogramImageContent QSlider::sub-page:horizontal:disabled {{
    background: {border_strong};
}}
QWidget#tomogramImageContent QSlider::handle:horizontal:disabled {{
    background: {text_muted};
    border-color: {border_strong};
}}
QWidget#tomogramImageContent QAbstractSpinBox:disabled,
QWidget#tomogramImageContent QLineEdit:disabled {{
    color: {text_muted};
}}
/* The scale bar's 2 × 2 corner picker: dashed cells, the chosen one filled. */
QWidget#tomogramCornerPicker {{
    border: 1px solid {border_strong};
    border-radius: {radius_control}px;
}}
QToolButton#tomogramCornerCell {{
    background: transparent;
    border: 1px dashed {border_strong};
    border-radius: {radius_control}px;
}}
QToolButton#tomogramCornerCell:checked {{
    background: {accent_soft};
    border: 1px solid {accent};
}}
QToolButton#tomogramCornerCell:focus {{
    border: 2px solid {accent};
}}
QLabel#tomogramCaptureName {{
    color: {text_strong};
}}
/* The G key cap is a button: it turns the filters off and on, as G does. */
QToolButton#tomogramKeyHint {{
    background: transparent;
    color: {text_muted};
    border: 1px solid {border_strong};
    border-bottom-width: 2px;
    border-radius: {radius_control}px;
    padding: 0 5px;
    font-size: {type_caption}pt;
}}
QToolButton#tomogramKeyHint:hover {{
    color: {text};
    border-color: {control_border};
}}
QToolButton#tomogramKeyHint:focus {{
    border-color: {accent};
}}
/* Show in folder after a snapshot: a link no taller than the status text, so
   showing it never changes the row height or moves the canvas. */
QToolButton#tomogramFeedbackLink {{
    background: transparent;
    border: 1px solid transparent;
    color: {link_text};
    font-size: {type_caption}pt;
    padding: 0 2px;
}}
QToolButton#tomogramFeedbackLink:hover {{
    color: {link_text_hover};
    text-decoration: underline;
}}
QToolButton#tomogramFeedbackLink:focus {{
    border-color: {link_text};
}}
QFrame#tomogramKeyboardTip {{
    background: {surface_hi};
    color: {text};
    border: 1px solid {border_strong};
    border-radius: {radius_card}px;
}}
"""
