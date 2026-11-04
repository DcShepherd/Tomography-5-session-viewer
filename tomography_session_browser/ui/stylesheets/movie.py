"""Movie export: the film button joined to the rate button, the export dialog and the progress window."""

TEMPLATE = """\
/* The rate and film buttons are one bordered group with a hairline between
   them: the movie starts with this rate. The cells drop their own borders. */
QFrame#tomogramRateGroup {{
    background: {canvas_panel};
    border: 1px solid {canvas_border_strong};
    border-radius: {radius_control}px;
}}
QFrame#tomogramRateGroup QPushButton#tomogramRate {{
    background: transparent;
    border: 1px solid transparent;
}}
QPushButton#tomogramFilm {{
    background: transparent;
    color: {canvas_text};
    border: 1px solid transparent;
    border-radius: {radius_control}px;
    padding: 0;
    min-width: 0;
}}
QFrame#tomogramRateGroup QPushButton#tomogramRate:hover,
QPushButton#tomogramFilm:hover {{
    background: {canvas_surface_alt};
}}
QFrame#tomogramRateGroup QPushButton#tomogramRate:focus,
QPushButton#tomogramFilm:focus {{
    border-color: {canvas_accent};
}}

/* The export dialog of board 3: the page background, a panel-coloured footer bar. */
QDialog#movieDialog,
QScrollArea#movieSettingsScroll,
QWidget#movieSettings {{
    background: {background};
    border: 0;
}}
QDialog#movieProgress {{
    background: {panel};
    border: 0;
}}
QFrame#movieFooter {{
    background: {panel};
    border: 0;
    border-top: 1px solid {border};
}}
/* Board 3b: a section is a card with one line; the open one has the stronger outline. One fill for both,
   so that nothing but the fold changes as a section opens. The whole line is a button. */
QWidget#movieSection {{
    background: {surface};
    border: 1px solid {border};
    border-radius: {radius_card}px;
}}
QWidget#movieSection[open="true"] {{
    border-color: {border_strong};
}}
QAbstractButton#movieSectionLine {{
    background: transparent;
    border: 1px solid transparent;
    border-radius: {radius_card}px;
}}
QAbstractButton#movieSectionLine:hover {{
    background: {surface_hi};
}}
QAbstractButton#movieSectionLine:focus {{
    border-color: {accent};
}}
QLabel#movieSectionText {{
    color: {text_strong};
}}
QLabel#movieResultName {{
    color: {text_strong};
    font-weight: 600;
}}
QLabel#movieReadout {{
    color: {text};
}}
QLabel#movieHint {{
    color: {text_muted};
}}
QLabel#movieNote {{
    color: {text_muted};
}}
QLabel#movieNote[kind="error"] {{
    color: {chart_red};
}}
QLabel#movieNote[kind="warning"] {{
    color: {chart_amber};
}}
/* Typed values and choices: one height and one outline down the settings. */
QWidget#movieSettings QAbstractSpinBox,
QWidget#movieSettings QLineEdit,
QWidget#movieSettings QComboBox {{
    background: {panel};
    border: 1px solid {border_strong};
    border-radius: {radius_control}px;
    color: {text_strong};
    padding: 0 6px;
}}
QWidget#movieSettings QComboBox QLineEdit {{
    border: 0;
    padding: 0;
    background: transparent;
}}
QWidget#movieSettings QAbstractSpinBox:focus,
QWidget#movieSettings QLineEdit:focus,
QWidget#movieSettings QComboBox:focus {{
    border-color: {accent};
}}
QWidget#movieSettings QAbstractSpinBox:disabled,
QWidget#movieSettings QLineEdit:disabled {{
    background: transparent;
    border-color: {border};
    color: {text_muted};
}}
/* Board 3's segments: as wide as their text, the height of a field. */
QWidget#movieSettings QToolButton#segmentButton {{
    padding: 0 6px;
}}
/* Outlined buttons: First, Middle, Last, Change and Swap; Hold here has the accent outline. */
QPushButton#movieStepButton,
QPushButton#movieOutlineButton,
QPushButton#movieHoldHere,
QToolButton#movieSwap {{
    background: transparent;
    border: 1px solid {border_strong};
    border-radius: {radius_control}px;
    color: {text};
    padding: 0 10px;
    min-height: 24px;
}}
/* The settings' Change button is as tall as a field; the timeline's buttons are 2 px lower. */
QPushButton#movieOutlineButton {{
    min-height: 26px;
}}
QToolButton#movieSwap {{
    padding: 0;
}}
QPushButton#movieHoldHere {{
    border-color: {accent_line};
    color: {text_strong};
}}
QPushButton#movieStepButton:hover,
QPushButton#movieOutlineButton:hover,
QPushButton#movieHoldHere:hover,
QToolButton#movieSwap:hover {{
    background: {surface_hi};
}}
QPushButton#movieStepButton:focus,
QPushButton#movieOutlineButton:focus,
QPushButton#movieHoldHere:focus,
QToolButton#movieSwap:focus {{
    border-color: {accent};
}}
QPushButton#movieStepButton:disabled,
QPushButton#movieOutlineButton:disabled,
QPushButton#movieHoldHere:disabled {{
    background: transparent;
    border-color: {border};
    color: {text_muted};
}}
/* What the picture shows: on has the accent outline and a check mark. */
QToolButton#movieShowChip {{
    background: transparent;
    border: 1px solid {border_strong};
    border-radius: {radius_control}px;
    color: {text_muted};
    padding: 0 10px;
}}
QToolButton#movieShowChip:hover {{
    background: {surface_hi};
    color: {text};
}}
QToolButton#movieShowChip:checked {{
    background: {accent_soft};
    border-color: {accent_line};
    color: {text_strong};
}}
QToolButton#movieShowChip:focus {{
    border-color: {accent};
}}
QToolButton#movieShowChip:disabled {{
    background: transparent;
    border-color: {border};
    color: {text_muted};
}}
/* A hold at a chosen frame; dashed while the movie does not use it. */
QFrame#movieHoldChip {{
    background: {surface};
    border: 1px solid {border_strong};
    border-radius: {radius_control}px;
}}
QWidget#movieSettings QFrame#movieHoldChip QAbstractSpinBox {{
    padding: 0 4px;
}}
QFrame#movieHoldChip[unused="true"] {{
    border-style: dashed;
}}
QToolButton#movieChipRemove,
QToolButton#movieQuickRates {{
    background: transparent;
    border: 1px solid transparent;
    border-radius: {radius_control}px;
    color: {text_muted};
    padding: 0 4px;
}}
QToolButton#movieChipRemove:hover,
QToolButton#movieQuickRates:hover {{
    color: {text_strong};
    border-color: {border_strong};
}}
QToolButton#movieChipRemove:focus,
QToolButton#movieQuickRates:focus {{
    border-color: {accent};
}}
QToolButton#movieQuickRates::menu-indicator {{
    image: none;
    width: 0;
}}
"""
