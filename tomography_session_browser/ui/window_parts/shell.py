"""WindowShell owns its window interaction responsibilities."""
from __future__ import annotations
from tomography_session_browser import __version__
from tomography_session_browser.presentation.help_content import HelpPage
import sys
from PySide6.QtCore import (
    QByteArray,
    QObject,
    QPoint,
    QSize,
    Qt,
)
from PySide6.QtGui import (
    QAction,
    QGuiApplication,
    QKeySequence,
)
from PySide6.QtWidgets import (
    QDockWidget,
    QApplication,
    QLabel,
    QMainWindow,
    QProgressBar,
    QSizePolicy,
    QTabWidget,
    QToolBar,
    QTreeWidget,
    QVBoxLayout,
    QWidget,
)
from tomography_session_browser.ui.shell.chrome import (
    CONTEXT_DOCK_MIN_WIDTH_PX,
    TAB_LABELS,
)
from tomography_session_browser.ui.viewer.list_rows import status_chip_tone
from tomography_session_browser.ui.tab_registry import TAB_SPECS
from tomography_session_browser.ui.tomograms.tab import TomogramTab
from tomography_session_browser.ui.shell.side_column import SideColumn
from tomography_session_browser.ui.shell.tomogram_focus import TomogramFocus
from tomography_session_browser.ui.viewer.viewer_tab import ViewerTab
from tomography_session_browser.ui.branding import (
    TitleBarLockup,
)
from tomography_session_browser.ui.icons import (
    themed_icon,
)
from tomography_session_browser.ui.navigation_icons import (
    TAB_ICONS,
)
from tomography_session_browser.presentation.context_stack import (
    ENTITY_DISPLAY_LABELS,
)
from tomography_session_browser.presentation.design_tokens import (
    SPACE_M,
    SPACE_S,
)
from tomography_session_browser.ui.theme import (
    mark_theme_region,
)
from tomography_session_browser.ui.widgets.panel_drawer import (
    PanelDrawer,
)
from tomography_session_browser.ui.widgets.context_header import (
    ContextHeader,
)
from tomography_session_browser.ui.dashboard.common import severity_tone
from tomography_session_browser.ui.widgets.load_receipt import LoadReceipt
from tomography_session_browser.ui.widgets.session_drop import (
    SessionDropOverlay,
    SessionDropTarget,
)
from tomography_session_browser.ui.load_sessions_dialog import (
    LOAD_SESSIONS_TITLE,
)
from tomography_session_browser.ui.widgets.sliding_tab_bar import (
    SlidingTabBar,
)
from tomography_session_browser.ui.context.panel import (
    ContextPanel,
)
from tomography_session_browser.ui.project_tree.panel import (
    ProjectTreePanel,
)
from tomography_session_browser.presentation.warnings import (
    warning_severity,
)

from tomography_session_browser.ui.window_parts.contracts import (
    WindowParts,
    WindowUi,
)
from tomography_session_browser.ui.window_parts.state import (
    WindowState,
)
from tomography_session_browser.ui.window_parts.support import first_run_window_rect, panel_header, FIRST_RUN_MAX_SIZE, SIDE_PANEL_INSET_PX
from tomography_session_browser.presentation.tab_keys import TabKey

class WindowShell(QObject):
    def __init__(self, window: QMainWindow, state: WindowState, ui: WindowUi, parts: WindowParts) -> None:
        super().__init__(window)
        self.window, self.state, self.ui, self.parts = window, state, ui, parts
        self.tomogram_focus = TomogramFocus(window, ui, parts)

    def toast_clearance(self) -> int:
        """Reserve the visible tomogram plane band in window coordinates."""
        tab = self.ui.tomogram_tab
        index = self.ui.tabs.currentIndex()
        if not 0 <= index < len(TAB_LABELS) or TAB_LABELS[index] != TabKey.TOMOGRAMS or not tab.plane_band.isVisible():
            return 0
        central = self.window.centralWidget()
        bottom = central.mapTo(self.window, QPoint(0, central.height())).y()
        top = tab.plane_band.mapTo(self.window, QPoint()).y()
        return max(0, bottom - top)

    def build_toolbar(self) -> None:
        # Qt's default right-click menu hides toolbars and docks, and nothing here shows them again.
        self.window.setContextMenuPolicy(Qt.ContextMenuPolicy.NoContextMenu)
        toolbar = QToolBar("Main")
        toolbar.setObjectName("mainToolbar")
        toolbar.setMovable(False)
        toolbar.setIconSize(QSize(18, 18))
        # Set once. Each button's own style is then fitted per step
        # (``_fit_toolbar``); changing the toolbar's style would reset them all.
        toolbar.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        toolbar.setFixedHeight(50)
        self.ui.main_toolbar = toolbar
        # (what the widths depend on) → width each toolbar step needs.
        # (texts of every step, font, icon size) → width each step needs.
        self.window.addToolBar(Qt.ToolBarArea.TopToolBarArea, toolbar)

        self.ui.brand_label = TitleBarLockup(self.window)
        self.ui.brand_label.setToolTip("Tomography Session Browser")
        # Target the asset-sheet 50 px row, but let the toolbar grow if a
        # platform's fallback font produces a taller lockup (e.g. macOS SF
        # Pro or some Linux desktops). The +8 accounts for the toolbar's
        # 3 px QSS padding above and below the widget plus the 1 px bottom
        # border. Without this backstop the tagline can clip when widget
        # height > toolbar content area.
        toolbar.setFixedHeight(max(50, self.ui.brand_label.height() + 8))
        toolbar.addWidget(self.ui.brand_label)

        toolbar.addSeparator()

        # Primary file action -------------------------------------------------
        # One action: Open and Import both added to the project, so they were
        # one command under two names. Folders can also be dropped onto the
        # project panel.
        self.ui.open_action = self.parts.shell.action(
            "folder-plus",
            LOAD_SESSIONS_TITLE + "…",
            "Load acquisition sessions or Warp, IMOD or AreTomo3 processing folders. "
            "You can load several or drop them onto Project.",
            QKeySequence.StandardKey.Open,
            identity="shell.open",
        )
        self.ui.connections.append(lambda signal=self.ui.open_action.triggered: signal.connect(self.parts.loading.open_session))
        toolbar.addAction(self.ui.open_action)

        self.ui.refresh_action = self.parts.shell.action(
            "refresh", "Refresh", "Reload the currently opened session folders.", QKeySequence.StandardKey.Refresh,
            identity="shell.refresh",
        )
        self.ui.connections.append(lambda signal=self.ui.refresh_action.triggered: signal.connect(self.parts.loading.refresh_session))
        self.ui.refresh_action.setEnabled(False)
        toolbar.addAction(self.ui.refresh_action)
        self.ui.connections.append(lambda: self.parts.context_panel.sources.refresh_requested.connect(self.parts.project.refresh_processing_source))
        self.ui.connections.append(lambda: self.parts.context_panel.sources.remove_requested.connect(self.parts.project.remove_processing_source))
        self.ui.connections.append(lambda: self.parts.context_panel.tomogram.parent_requested.connect(self.parts.viewers.open_tomogram_parent))

        toolbar.addSeparator()

        # Back / Forward ------------------------------------------------------
        # Only explicit jumps are recorded, so Back returns to somewhere the
        # reviewer chose to be rather than replaying passive selections.
        self.ui.back_action = self.parts.shell.action(
            "chevron-left",
            "Back",
            "Return to the previous navigation step.",
            "Alt+Left",
            identity="shell.back",
        )
        self.ui.connections.append(lambda signal=self.ui.back_action.triggered: signal.connect(self.parts.history.navigate_history_back))
        self.ui.back_action.setEnabled(False)
        toolbar.addAction(self.ui.back_action)

        self.ui.forward_action = self.parts.shell.action(
            "chevron-right",
            "Forward",
            "Move forward again after going back.",
            "Alt+Right",
            identity="shell.forward",
        )
        self.ui.connections.append(lambda signal=self.ui.forward_action.triggered: signal.connect(self.parts.history.navigate_history_forward))
        self.ui.forward_action.setEnabled(False)
        toolbar.addAction(self.ui.forward_action)

        toolbar.addSeparator()

        # Report action -------------------------------------------------------
        self.ui.report_action = self.parts.shell.action(
            "file-text", "Report", "Export a PDF report for the loaded session(s).", QKeySequence.StandardKey.Print,
            identity="shell.report",
        )
        self.ui.report_action.setEnabled(False)
        self.ui.connections.append(lambda signal=self.ui.report_action.triggered: signal.connect(self.parts.report.on_generate_report))
        toolbar.addAction(self.ui.report_action)

        # Spacer pushes the remaining group to the right end of the toolbar.
        spacer = QWidget(self.window)
        spacer.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        toolbar.addWidget(spacer)
        self.ui.toolbar_spacer = spacer

        # View / theme actions ------------------------------------------------
        self.ui.compact_action = self.parts.shell.action(
            "layout-compact", "Compact", "Dense layout mode is planned, but not available in this build.",
            identity="shell.compact",
        )
        self.ui.compact_action.setCheckable(True)
        self.ui.compact_action.setEnabled(False)
        self.ui.compact_action.setVisible(False)
        self.ui.compact_action.setStatusTip("Dense layout mode is not available yet.")
        toolbar.addAction(self.ui.compact_action)

        self.ui.project_panel_action = self.parts.shell.action(
            "panel-left", "Project", "Show or hide the project / session tree.", "Ctrl+1",
            identity="shell.project_panel",
        )
        self.ui.project_panel_action.setCheckable(True)
        self.ui.project_panel_action.setChecked(True)
        self.ui.connections.append(lambda signal=self.ui.project_panel_action.toggled: signal.connect(self.parts.chrome.toggle_project_panel))
        toolbar.addAction(self.ui.project_panel_action)

        self.ui.context_panel_action = self.parts.shell.action(
            "panel-right", "Context", "Show or hide the context / metadata panel.", "Ctrl+2",
            identity="shell.context_panel",
        )
        self.ui.context_panel_action.setCheckable(True)
        self.ui.context_panel_action.setChecked(True)
        self.ui.connections.append(lambda signal=self.ui.context_panel_action.toggled: signal.connect(self.parts.chrome.toggle_context_panel))
        toolbar.addAction(self.ui.context_panel_action)

        self.ui.theme_action = self.parts.shell.action(
            "moon", "Theme", "Switch between dark and light themes.", "Ctrl+Shift+T",
            identity="shell.theme",
        )
        self.ui.theme_action.setCheckable(True)
        self.ui.connections.append(lambda signal=self.ui.theme_action.toggled: signal.connect(self.parts.theme.on_theme_toggled))
        toolbar.addAction(self.ui.theme_action)

        self.help_action = self.action("help", "Help", "Show keyboard shortcuts and controls.", "F1",
            identity="shell.help",
        )
        self.help_action.setObjectName("shortcutHelpAction")
        self.help_action.setShortcutContext(Qt.ShortcutContext.WindowShortcut)
        self.help_action.triggered.connect(lambda: self.open_help())
        toolbar.addAction(self.help_action)

        # Loading indicator -------------------------------------------------
        # Lives in the status bar so it never fights the toolbar for space.
        self.ui.loading_progress = QProgressBar()
        self.ui.loading_progress.setRange(0, 0)
        self.ui.loading_progress.setMaximumWidth(180)
        self.ui.loading_progress.setTextVisible(False)
        self.ui.loading_progress.setVisible(False)
        # The status bar carries messages: where a jump went, why one could
        # not, and work in progress. The scope is in the context header and
        # the counts are on the tabs, so it no longer repeats either.
        self.ui.status_runtime = QLabel(f"v{__version__}")
        self.ui.status_runtime.setObjectName("statusSegment")
        self.ui.status_runtime.setToolTip(
            f"Tomography Session Browser v{__version__} · Python "
            f"{sys.version_info.major}.{sys.version_info.minor}"
        )
        self.window.statusBar().addPermanentWidget(self.ui.loading_progress)
        self.window.statusBar().addPermanentWidget(self.ui.status_runtime)

        # ---- Backwards-compat aliases used by older tests -------------------
        # The old toolbar exposed ``QPushButton`` attributes; tests inspect them
        # through ``isEnabled()`` / ``isChecked()`` / ``click()``. ``QAction``
        # supports the same surface, so we alias the new actions back to the
        # historical attribute names.
        self.ui.open_button = self.ui.open_action
        self.ui.refresh_button = self.ui.refresh_action
        self.ui.report_button = self.ui.report_action
        self.ui.compact_button = self.ui.compact_action
        self.ui.project_panel_button = self.ui.project_panel_action
        self.ui.context_panel_button = self.ui.context_panel_action
        # Responsive chrome is fitted after its widgets have been built.


    def open_help(self, page: HelpPage = HelpPage.KEYBOARD) -> None:
        from tomography_session_browser.ui.shortcut_help import show_help
        show_help(self.window, tabs={TabKey.SESSION: self.ui.session_dashboard, **self.ui.review_tabs},
            current=TAB_LABELS[self.ui.tabs.currentIndex()], page=page, load_action=self.ui.open_action)


    def action(
        self,
        icon_name: str,
        label: str,
        tooltip: str,
        shortcut: QKeySequence | QKeySequence.StandardKey | str | None = None,
        *,
        identity: str,
    ) -> QAction:
        """Create a QAction with a themed icon and remember it for theme changes."""

        action = QAction(themed_icon(icon_name), label, self.window)
        # Toolbar buttons show ``iconText``; menus, tooltips and ``text()``
        # keep the plain label. Qt's text-beside-icon layout hard-codes a
        # 4 px gap that style sheets cannot widen, so the icon butted against
        # its label (plan C12). One no-break space restores the gap.
        action.setIconText(f" {label}")
        action.setProperty("helpBindingId", identity)
        if shortcut is not None:
            action.setShortcut(shortcut)
            # Qt only appends the shortcut to menu text, never to a toolbar
            # tooltip, so spell it out — otherwise the bindings are
            # undiscoverable.
            hint = action.shortcut().toString(QKeySequence.SequenceFormat.NativeText)
            tooltip = f"{tooltip}  ({hint})" if hint else tooltip
        action.setToolTip(tooltip)
        action.setData(icon_name)  # remember the icon source for refresh
        self.ui.theme_actions.append(action)
        return action



    def refresh_action_icons(self) -> None:
        """Re-render all toolbar action icons with the active theme's stroke colour."""

        for action in self.ui.theme_actions:
            name = action.data()
            if action is self.ui.theme_action:
                name = "sun" if self.ui.theme_action.isChecked() else "moon"
            if isinstance(name, str):
                action.setIcon(themed_icon(name))
        self.parts.shell.refresh_tab_icons()


    def refresh_tab_icons(self) -> None:
        """Re-render entity tab icons after the active palette changes."""

        for index, label in enumerate(TAB_LABELS):
            self.ui.tabs.setTabIcon(index, themed_icon(TAB_ICONS[label], size=16))


    def schedule_settings_save(self) -> None:
        """Queue a settings write, coalescing bursts into one disk round-trip."""

        self.state.shell.settings_save_timer.start()


    def flush_settings_save(self) -> None:
        self.state.shell.settings_save_timer.stop()
        self.state.review.save_settings(self.state.review.settings)


    def prewarm_loader(self) -> None:
        if self.window.isVisible():
            self.ui.loading_overlay.prewarm()
        self.parts.theme.schedule_other_theme_icons()


    def restore_from_settings(self) -> None:
        settings = self.state.review.settings
        # Theme: app.py applies the theme before the window is built, so here
        # we only mirror the toggle state of the action.
        self.ui.theme_action.blockSignals(True)
        self.ui.theme_action.setChecked(settings.theme == "light")
        self.ui.theme_action.blockSignals(False)
        self.parts.shell.refresh_action_icons()
        # Panel visibility — the toggled-callback also writes settings, so
        # block signals while we restore to avoid an immediate re-save.
        self.ui.project_panel_action.blockSignals(True)
        self.ui.project_panel_action.setChecked(settings.project_panel_visible)
        self.ui.project_panel_action.blockSignals(False)
        # Avoid calling setVisible(True) on dock widgets before the main
        # window is shown. On Windows, native dock widgets can briefly appear
        # as tiny top-level windows during startup if explicitly shown early.
        if not settings.project_panel_visible:
            self.ui.project_dock.hide()
        self.ui.context_panel_action.blockSignals(True)
        self.ui.context_panel_action.setChecked(settings.context_panel_visible)
        self.ui.context_panel_action.blockSignals(False)
        if not settings.context_panel_visible:
            self.ui.context_dock.hide()
        # Compact mode is wired in a later milestone; sync the toggle anyway.
        self.ui.compact_action.blockSignals(True)
        self.ui.compact_action.setChecked(settings.compact)
        self.ui.compact_action.blockSignals(False)
        # Last-active tab.
        if settings.last_tab in TAB_LABELS:
            self.ui.tabs.setCurrentIndex(TAB_LABELS.index(settings.last_tab))
        # Tab changes need to be persisted.
        self.ui.connections.append(lambda signal=self.ui.tabs.currentChanged: signal.connect(self.parts.context.on_tab_changed))
        # The tab being looked at keeps its name when the bar is short of room. (An open
        # The chrome owner's geometry watcher follows the current list column.
        self.ui.connections.append(lambda signal=self.ui.tabs.currentChanged: signal.connect(lambda _index: self.parts.chrome.refit_tabs_for_current()))


    def apply_initial_geometry(self) -> None:
        """Restore the last window geometry, or fit a first-run size to the screen."""

        saved = self.state.review.settings.window_geometry
        if saved:
            # restoreGeometry() also brings a window saved on a monitor that
            # is no longer attached back onto a visible screen.
            if self.window.restoreGeometry(QByteArray.fromBase64(saved.encode("ascii", errors="ignore"))):
                return
        screen = self.window.screen() or QGuiApplication.primaryScreen()
        if screen is None:
            self.window.resize(*FIRST_RUN_MAX_SIZE)
            return
        self.window.setGeometry(first_run_window_rect(screen.availableGeometry()))


    def build_left_tree(self) -> None:
        self.ui.project_dock = QDockWidget("", self.window)
        self.ui.project_dock.setObjectName("projectDock")
        self.ui.project_dock.setAllowedAreas(Qt.DockWidgetArea.LeftDockWidgetArea)
        self.ui.project_dock.setFeatures(QDockWidget.DockWidgetFeature.NoDockWidgetFeatures)
        self.ui.project_dock.setTitleBarWidget(QWidget(self.ui.project_dock))
        self.ui.project_dock.setMinimumWidth(220)

        self.parts.project_panel = ProjectTreePanel(
            self.ui.project_dock,
            header=panel_header,
            inset=SIDE_PANEL_INSET_PX,
            has_sessions=lambda: self.state.review.workspace.has_sources,
            open_help=lambda: self.open_help(HelpPage.READING),
        )
        panel, layout = self.parts.project_panel.widget, self.parts.project_panel.layout
        self.ui.project_filter = self.parts.project_panel.filter
        panel.setFocusProxy(self.ui.project_filter)
        self.ui.project_badge_legend = self.parts.project_panel.badge_legend
        self.ui.project_drop_hint = self.parts.project_panel.drop_hint
        self.ui.tree = self.parts.project_panel.tree
        self.ui.connections.append(lambda signal=self.ui.tree.customContextMenuRequested: signal.connect(self.parts.project.on_project_tree_context_menu))
        self.ui.connections.append(lambda signal=self.ui.tree.currentItemChanged: signal.connect(self.parts.project.tree_selection_changed))
        self.ui.connections.append(lambda signal=self.ui.tree.itemPressed: signal.connect(self.parts.project.project_tree_item_pressed))
        self.ui.connections.append(lambda signal=self.ui.tree.itemClicked: signal.connect(self.parts.project.project_tree_item_clicked))
        self.ui.connections.append(lambda signal=self.ui.tree.itemDoubleClicked: signal.connect(self.parts.project.project_tree_item_double_clicked))
        # Enter previously did nothing on any tree row, for any entity type.
        self.ui.connections.append(lambda signal=self.ui.tree.itemActivated: signal.connect(self.parts.project.project_tree_item_activated))
        # What a drop skipped, kept until dismissed.
        receipt_wrap = QWidget(panel)
        receipt_layout = QVBoxLayout(receipt_wrap)
        receipt_layout.setContentsMargins(SPACE_M, SPACE_S, SPACE_M, SPACE_M)
        self.ui.drop_receipt = LoadReceipt(receipt_wrap)
        receipt_layout.addWidget(self.ui.drop_receipt)
        self.ui.connections.append(lambda signal=self.ui.drop_receipt.retry_requested:
                                   signal.connect(self.parts.loading.retry_failed_loads))
        receipt_wrap.setVisible(False)
        self.ui.connections.append(lambda signal=self.ui.drop_receipt.dismissed: signal.connect(lambda: receipt_wrap.setVisible(False)))
        self.ui.drop_receipt_wrap = receipt_wrap
        layout.addWidget(receipt_wrap)
        # Session folders dropped onto the panel load like the Load sessions
        # window's; the overlay says, while dragging, which of them can.
        self.ui.project_drop_overlay = SessionDropOverlay(panel)
        self.ui.project_drop_target = SessionDropTarget(
            panel,
            self.ui.project_drop_overlay,
            assess=self.parts.loading.assess_project_drop,
            refused=self.parts.loading.project_drops_refused,
            load=self.parts.loading.load_dropped_sessions,
        )
        self.parts.project_panel.sync_empty_state()
        self.ui.project_dock.setWidget(panel)
        self.window.addDockWidget(Qt.DockWidgetArea.LeftDockWidgetArea, self.ui.project_dock)
        self.ui.project_drawer = PanelDrawer(self.window, name="Project", from_left=True)
        self.ui.connections.append(lambda signal=self.ui.project_drawer.escape_pressed: signal.connect(self.parts.chrome.close_project_drawer))
        self.ui.project_drawer.stay_under = lambda: self.ui.loading_overlay
        self.ui.project_drawer.toggle_control = lambda: self.ui.main_toolbar.widgetForAction(self.ui.project_panel_action)


    def build_right_panel(self) -> None:
        self.ui.context_dock = QDockWidget("", self.window)
        self.ui.context_dock.setObjectName("contextDock")
        self.ui.context_dock.setAllowedAreas(Qt.DockWidgetArea.RightDockWidgetArea)
        self.ui.context_dock.setFeatures(QDockWidget.DockWidgetFeature.NoDockWidgetFeatures)
        self.ui.context_dock.setTitleBarWidget(QWidget(self.ui.context_dock))
        self.ui.context_dock.setMinimumWidth(CONTEXT_DOCK_MIN_WIDTH_PX)
        self.parts.context_panel = ContextPanel(
            self.ui.context_dock,
            header=panel_header,
            inset=SIDE_PANEL_INSET_PX,
            status_tone=status_chip_tone,
            warning_tone=lambda warning: severity_tone(warning_severity(warning)),
        )
        panel = self.parts.context_panel.widget
        self.ui.context_panel = self.parts.context_panel.metadata
        self.ui.connections.append(lambda signal=self.ui.context_panel.title_changed: signal.connect(lambda _title: self.parts.context.sync_context_heading()))
        self.ui.side_column = SideColumn(panel, self.ui.context_dock)
        self.ui.connections.append(lambda: self.ui.side_column.image_dismissed.connect(
            lambda: self.ui.tomogram_tab.image_button.setChecked(False)))
        self.ui.context_dock.setWidget(self.ui.side_column)
        self.window.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, self.ui.context_dock)
        # On a narrow window the same panel moves into a drawer over the page
        # (plan D2): ``_update_context_drawer_mode``.
        self.ui.context_shell = self.ui.side_column
        self.ui.context_drawer = PanelDrawer(self.window)
        self.ui.connections.append(lambda signal=self.ui.context_drawer.escape_pressed: signal.connect(self.parts.chrome.close_context_drawer_from_keyboard))
        self.ui.context_drawer.stay_under = lambda: self.ui.loading_overlay
        self.ui.context_drawer.toggle_control = lambda: self.ui.main_toolbar.widgetForAction(self.ui.context_panel_action)


    def build_tabs(self) -> None:
        self.ui.tabs = QTabWidget()
        self.ui.connections.append(lambda: self.ui.session_dashboard.empty_state_action_requested.connect(
            self.parts.context.on_empty_state_action))
        self.ui.tabs.setObjectName("mainTabs")
        # Before any tab is added: the bar paints the sliding underline (F5.3).
        tab_bar = SlidingTabBar(self.ui.tabs)
        tab_bar.focus_fallback = lambda: self.parts.chrome.give_focus_to_page()
        self.ui.tabs.setTabBar(tab_bar)
        self.ui.tabs.setIconSize(QSize(16, 16))
        for spec in TAB_SPECS:
            label = spec.key
            tab = QWidget(self.ui.tabs)
            tab.setObjectName("tabPage")
            layout = QVBoxLayout(tab)
            layout.setContentsMargins(0, 0, 0, 0)
            layout.setSpacing(0)
            content = spec.factory(label, self.ui, self.parts)
            if isinstance(content, (ViewerTab, TomogramTab)):
                self.ui.review_tabs[label] = content
            if isinstance(content, ViewerTab):
                viewer_tab = content
                self.ui.viewer_tabs[label] = viewer_tab
                self.ui.connections.append(lambda signal=viewer_tab.header_title_changed: signal.connect(lambda _title: self.parts.context.sync_context_heading()))
                # Keep the header's filter summary and Clear action in step
                # with what the reviewer types.
                self.ui.connections.append(lambda signal=viewer_tab.query_changed: signal.connect(lambda _query: self.parts.context.refresh_context_header()))
                self.ui.connections.append(lambda signal=viewer_tab.empty_state_action_requested: signal.connect(self.parts.context.on_empty_state_action))
            layout.addWidget(content, stretch=1)
            # Focus given to the page belongs to what it shows: Tomograms
            # commands act only within their tab (ShellChrome.hand_focus_to_page).
            tab.setFocusProxy(content)
            index = self.ui.tabs.addTab(tab, spec.label)
            self.ui.tabs.setTabIcon(index, themed_icon(TAB_ICONS[label], size=16))
            # Restyled at a theme switch only if on screen, else when next
            # shown: most of the window's widgets are on hidden pages.
            mark_theme_region(tab)
        container = QWidget(self.window)
        container.setObjectName("centralShell")
        outer = QVBoxLayout(container)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        # One header above every page, so scope, section, selection, filters
        # and borrowed Atlas context are stated on all of them rather than
        # only on the dashboard.
        self.ui.context_header = ContextHeader(container)
        outer.addWidget(self.ui.context_header)
        outer.addWidget(self.ui.tabs, stretch=1)
        self.window.setCentralWidget(container)
        container.installEventFilter(self.window)
        # Context is refreshed once all components are constructed.


    def sync_image_settings(self) -> None:
        tab = self.ui.tomogram_tab
        visible = TAB_LABELS[self.ui.tabs.currentIndex()] == TabKey.TOMOGRAMS and tab.image_button.isChecked()
        focus = QApplication.focusWidget()
        return_focus = focus is tab.image_settings or (focus is not None and tab.image_settings.isAncestorOf(focus))
        previous = self.parts.chrome.set_image_panel(tab.image_settings if visible else None)
        if previous is not None:
            previous.setParent(tab)
            previous.hide()
            if return_focus and TAB_LABELS[self.ui.tabs.currentIndex()] == TabKey.TOMOGRAMS:
                tab.image_button.setFocus(Qt.FocusReason.OtherFocusReason)


    def ride_along_with_panel(self) -> None:
        """The current viewer eases its fitted image to the width a panel leaves."""

        viewer_tab = self.ui.viewer_tabs.get(TAB_LABELS[self.ui.tabs.currentIndex()])
        if viewer_tab is not None and viewer_tab.isVisible():
            viewer_tab.viewer.ease_next_refit()

