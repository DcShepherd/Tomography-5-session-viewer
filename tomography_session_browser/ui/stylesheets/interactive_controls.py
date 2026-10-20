"""Interactive cards, filter buttons, segmented control, tree and tab bar, and context callouts.

A fragment of the application's QSS template, filled in with the palette
and tokens by ``ui.stylesheet.build_stylesheet``. Fragments are joined in
the order listed in ``ui.stylesheets.__init__``: QSS precedence depends on
it, so a rule moves only together with the ones it overrides.
"""

TEMPLATE = """\
/* Interactive cards get a restrained, visible response. A card with nothing
   in it is inert and must not react: a card sets cardEmpty, and a hover is for the others. */
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

QLabel#contextNotes,
QLabel#viewerLinkedReason {{
    color: {text_muted};
    font-size: {type_caption}pt;
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

/* A list of one, collapsed to a selector above the canvas (B5.2). */
QToolButton#viewerListToggle,
QToolButton#viewerShowList {{
    background: transparent;
    border: 1px solid transparent;
    border-radius: {radius_control}px;
    color: {text_muted};
    padding: 2px 8px;
}}

QToolButton#viewerListToggle:hover,
QToolButton#viewerShowList:hover {{
    background: {surface_hi};
    color: {text};
}}

QToolButton#viewerListToggle:focus,
QToolButton#viewerShowList:focus {{
    border-color: {accent};
}}

QToolButton#viewerCompactItem {{
    background: {surface};
    border: 1px solid {border_strong};
    border-radius: {radius_control}px;
    color: {text_strong};
    font-weight: 600;
    padding: 3px 24px 3px 10px;
}}

QToolButton#viewerCompactItem:hover {{
    background: {surface_hi};
}}

QToolButton#viewerCompactItem:focus {{
    border-color: {accent};
}}

QToolButton#viewerCompactItem::menu-indicator {{
    subcontrol-position: right center;
    subcontrol-origin: padding;
    right: 4px;
}}

/* The exposure-area chooser (B5.1): numbered chips on a quiet panel, chosen as a segment is. */
QFrame#viewerExposureChooser {{
    background: {surface_alt};
    border: 1px solid {border};
    border-radius: {radius_card}px;
}}

QToolButton#exposureChip {{
    background: {surface};
    border: 1px solid {border_strong};
    border-radius: {radius_control}px;
    color: {text};
    min-width: 30px;
    min-height: 26px;
}}

QToolButton#exposureChip:hover {{
    background: {surface_hi};
}}

QToolButton#exposureChip[offImage="true"] {{
    color: {text_muted};
}}

QLabel#viewerExposureImageNote {{
    color: {text_muted};
}}

QToolButton#exposureChip:checked {{
    background: {selection};
    border-color: {accent};
    color: {selection_text};
    font-weight: 600;
}}

QToolButton#exposureChip:focus {{
    border-color: {accent};
}}

/* A segment with nothing behind it (a list's "Failed 0") stays readable, as a
   plain word: it says there are none, and cannot be chosen. */
QWidget#viewerFilterBar QToolButton#segmentButton:enabled:!checked {{
    color: {text};
}}

QToolButton#segmentButton:disabled {{
    background: transparent;
    color: {text_muted};
    font-weight: 400;
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
    border: 1px solid transparent;
    alternate-background-color: transparent;
    padding: 4px 6px;
}}

QTreeWidget#projectTree::item,
QTreeWidget#viewerList::item {{
    min-height: 30px;
    border-radius: {radius_control}px;
    padding: 5px 8px;
}}

/* Match the ID rules above; the generic focus selector cannot override them.
   Borders are reserved at rest, so focus changes colour, never geometry. */
QTreeWidget#projectTree:focus,
QTreeWidget#viewerList:focus,
QPushButton#viewerNavButton:focus,
QPushButton#contextClearFilter:focus,
QPushButton#secondaryAction:focus,
QToolButton#inlineLink:focus,
QToolButton#tableLink:focus,
QToolButton#metaCopyButton:focus,
QPushButton#dashboardFilterButton:focus,
QToolButton#dashboardFilterButton:focus {{
    border-color: {link_text};
}}

QPushButton#contextClearFilter,
QPushButton#secondaryAction,
QToolButton#inlineLink,
QToolButton#tableLink {{
    border: 1px solid transparent;
}}

/* These base rules follow the colour-only focus rules to reserve the border;
   a pseudo-state still has greater specificity than its base selector. */
QPushButton#primaryButton:focus {{
    border-color: {primary_text};
}}

QTreeWidget#viewerList::item {{
    min-height: 44px;
}}

/* The panel owns the common filter/list gutter; keep the scrollbar inside it. */
QTreeWidget#viewerList {{
    padding: 0;
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
    border-radius: 0;
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
