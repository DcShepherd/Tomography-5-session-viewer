"""Session drop, the Load sessions window and empty states.

A fragment of the application's QSS template, filled in with the palette
and tokens by ``ui.stylesheet.build_stylesheet``. Fragments are joined in
the order listed in ``ui.stylesheets.__init__``: QSS precedence depends on
it, so a rule moves only together with the ones it overrides.
"""

TEMPLATE = """\
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

QLabel#emptyStatePath {{
    color: {canvas_text_muted};
    font-family: {mono_font};
    font-size: {type_caption}pt;
}}

QToolButton#emptyStateCopy {{
    background: transparent;
    border: 1px solid transparent;
    border-radius: {radius_control}px;
    padding: 2px;
}}

QToolButton#emptyStateCopy:hover {{
    border-color: {canvas_text_muted};
}}

QToolButton#emptyStateCopy:focus {{
    border-color: {accent};
}}

"""
