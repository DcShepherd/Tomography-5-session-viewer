"""The shared desktop message grammar, using the application's theme tokens."""
TEMPLATE = """\
QDialog#messageSheet {{ background: {panel}; }}
QLabel#messageSheetTitle {{ color: {text_strong}; font-size: {type_lead}pt; font-weight: 600; }}
QPushButton#messageDestructive {{ color: {chart_red}; border: 1px solid {chart_red}; background: {panel}; }}
QPushButton#messageDestructive:hover {{ background: {surface_hi}; }}
QPushButton#messageDestructive:focus {{ border: 2px solid {accent}; }}
QFrame#messageDivider {{ color: {border}; }}
QFrame#messageProjectRow, QFrame#inlineNotice {{ background: {surface_hi}; border: 1px solid {border}; border-radius: {radius_control}px; }}
QLabel#outputPathError {{ color: {chart_red}; }}
QLabel#inlineNoticeTitle {{
    color: {text_strong};
    font-weight: 600;
}}
QFrame#mergeObservation {{ background: {panel}; border: 1px solid {border}; border-radius: {radius_control}px; }}
QFrame#mergeObservation[selected="true"] {{ background: {accent_soft}; border: 1px solid {accent}; }}
QRadioButton#mergeCount {{ font-size: {type_lead}pt; font-weight: 600; }}
QLabel#mergeNotice {{
    color: {chart_amber};
}}
"""
