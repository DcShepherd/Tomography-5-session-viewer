"""One pressed recipe and shared disclosure/link states.

Specificity matches each family's normal states. Canvas controls retain their
fixed dark palette, using the same semantic accent recipe in both themes.
"""
_BUTTONS = (
    "QPushButton", "QToolButton", "QToolBar QToolButton",
    *("QPushButton#" + name for name in (
        "primaryButton", "tomogramQuiet", "secondaryAction", "contextClearFilter", "dashboardFilterButton",
        "movieStepButton", "movieOutlineButton", "movieHoldHere")),
    *("QToolButton#" + name for name in (
        "segmentButton", "inlineLink", "tableLink", "sectionHeader", "emptyStateCopy",
        "tomogramInsetClose", "tomogramKeyHint", "tomogramFeedbackLink", "dashboardFilterButton",
        "viewerListToggle", "viewerShowList", "viewerCompactItem", "exposureChip", "metaCopyButton",
        "movieShowChip", "movieSwap")),
)
_CANVAS_BUTTONS = (
    "QWidget#viewerOverlayPanel QPushButton",
    "QWidget#viewerOverlayPanel QToolButton#sectionHeader",
    "QWidget#tomogramCanvasToolbar QPushButton",
    "QWidget#tomogramCanvasToolbar QFrame#tomogramToolGroup QPushButton",
    *("QPushButton#" + name for name in (
        "viewerToolButton", "viewerZoomButton", "tomogramIconButton", "tomogramZoomValue", "tomogramRate", "tomogramFilm", "tomogramPlay")),
    "QToolButton#tomogramCornerCell",
)


def _pressed(selectors: tuple[str, ...], prefix: str = "") -> str:
    states = ",\n".join(selector + ":pressed" + state for selector in selectors for state in (":hover", ":checked", ""))
    return (states + " {{\n    background: {" + prefix + "accent_soft};\n"
            "    border-color: {" + prefix + "accent};\n    color: {" + prefix + "text_strong};\n}}\n")


TEMPLATE = """
QWidget#routeFilterChip {{ background: {accent_soft}; border-radius: {radius_control}px; }}
QToolButton#sectionHeader {{
    background: transparent;
    border: 1px solid transparent;
    color: {text_muted};
    font-size: {type_caption}pt;
    font-weight: 600;
    padding: 4px 0;
    text-align: left;
}}
QToolButton#sectionHeader:hover {{ color: {text}; }}
QToolButton#sectionHeader:focus {{ border-color: {accent}; }}
QWidget#viewerOverlayPanel QToolButton#sectionHeader {{
    background: {canvas_surface};
    border: 1px solid {canvas_surface};
    color: {canvas_text_muted};
}}
QWidget#viewerOverlayPanel QToolButton#sectionHeader:hover,
QWidget#viewerOverlayPanel QToolButton#sectionHeader:checked {{
    background: {canvas_surface_alt};
    border-color: {canvas_border};
    color: {canvas_text_strong};
}}
QWidget#viewerOverlayPanel QToolButton#sectionHeader:focus,
QWidget#viewerOverlayPanel QToolButton#sectionHeader:checked:focus {{ border-color: {canvas_accent}; }}
""" + _pressed(_BUTTONS) + _pressed(_CANVAS_BUTTONS, "canvas_") + """
QCheckBox:pressed, QRadioButton:pressed {{ background: {accent_soft}; }}
QToolButton#inlineLink:disabled, QToolButton#tableLink:disabled,
QToolButton#inlineLink[tone]:disabled, QToolButton#tableLink[tone]:disabled {{
    color: {text_muted};
    text-decoration: none;
}}
"""
