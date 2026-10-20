"""Compose the session browser; construct every part before connecting signals."""
from __future__ import annotations
import logging
from typing import (
    Callable,
)
from PySide6.QtCore import (
    QEvent,
    QObject,
    QSize,
    QTimer,
    Slot,
)
from PySide6.QtGui import (
    QGuiApplication,
)
from PySide6.QtWidgets import (
    QApplication,
    QMainWindow,
)
from tomography_session_browser.ui.shell.chrome import (
    ChromeActions,
    ShellChrome,
    TAB_LABELS,
)
from tomography_session_browser.ui.shell.theme_switch import (
    ThemeSwitch,
)
from tomography_session_browser.ui.report_flow import (
    ReportFlow,
)
from tomography_session_browser.ui.dashboard_host import (
    DashboardHost,
)
from tomography_session_browser.domain.models import (
    Session,
)
from tomography_session_browser.presentation.workspace import (
    Workspace,
)
from tomography_session_browser.services.settings_service import (
    Settings,
    save_settings,
)
from tomography_session_browser.ui.branding import (
    brand_window_icon,
)
from tomography_session_browser.ui.motion import (
    ThemeRevealOverlay,
    watch_system_reduced_motion,
)
from tomography_session_browser.ui.font_fallback import (
    warm_font_fallback,
)
from tomography_session_browser.ui.native_window import (
    set_title_bar_dark,
)
from tomography_session_browser.ui.theme import (
    current_palette,
)
from tomography_session_browser.ui.widgets.loading_overlay import (
    LoadingOverlay,
)
from tomography_session_browser.ui.widgets.toast import (
    Toast,
)

from tomography_session_browser.ui.window_parts.support import (
    LOADER_PREWARM_DELAY_MS,
    WINDOW_MIN_SIZE,
)
from tomography_session_browser.ui.window_parts.contracts import (
    WindowParts,
    WindowUi,
)
from tomography_session_browser.ui.window_parts.state import (
    WindowState,
)
from tomography_session_browser.ui.window_parts.shell import (
    WindowShell,
)
from tomography_session_browser.ui.window_parts.loading import (
    SessionLoading,
)
from tomography_session_browser.ui.window_parts.scopes import (
    ScopeQueries,
)
from tomography_session_browser.ui.window_parts.dashboard import (
    DashboardInteraction,
)
from tomography_session_browser.ui.window_parts.project import (
    ProjectActions,
)
from tomography_session_browser.ui.window_parts.history import (
    HistoryController,
)
from tomography_session_browser.ui.window_parts.context import (
    ContextController,
)
from tomography_session_browser.ui.window_parts.viewers import (
    ViewerCoordinator,
)
from tomography_session_browser.ui.window_parts.navigation import (
    MarkerNavigation,
)

from tomography_session_browser.ui.shell.chrome import TAB_LABELS
from tomography_session_browser.ui.viewer.preview_scheduler import shutdown_preview_scheduler
from tomography_session_browser.ui.window_parts.support import LOADER_PREWARM_DELAY_MS, WINDOW_MIN_SIZE
LOGGER = logging.getLogger(__name__)

class MainWindow(QMainWindow):
    def __init__(self, *, settings: Settings | None = None, save_settings: Callable[[Settings], None] = save_settings) -> None:
        self._constructed = False
        super().__init__()
        self.state = WindowState(self, settings=settings, save_settings=save_settings)
        self.ui, self.parts = WindowUi(), WindowParts()
        self.parts.shell = WindowShell(self, self.state, self.ui, self.parts)
        self.parts.loading = SessionLoading(self, self.state, self.ui, self.parts)
        self.parts.scopes = ScopeQueries(self, self.state, self.ui, self.parts)
        self.parts.dashboard = DashboardInteraction(self, self.state, self.ui, self.parts)
        self.parts.project = ProjectActions(self, self.state, self.ui, self.parts)
        self.parts.history = HistoryController(self, self.state, self.ui, self.parts)
        self.parts.context = ContextController(self, self.state, self.ui, self.parts)
        self.parts.viewers = ViewerCoordinator(self, self.state, self.ui, self.parts)
        self.parts.navigation = MarkerNavigation(self, self.state, self.ui, self.parts)
        self.setWindowTitle("Tomography Session Browser")
        self.setWindowIcon(brand_window_icon())
        self.setMinimumSize(QSize(*WINDOW_MIN_SIZE))
        self.parts.shell.apply_initial_geometry()
        self.parts.shell.build_toolbar()
        self.parts.shell.build_left_tree()
        self.parts.shell.build_right_panel()
        self.parts.shell.build_tabs()
        self.parts.chrome = ShellChrome(
            self, project_dock=self.ui.project_dock, context_dock=self.ui.context_dock,
            drawer=self.ui.context_drawer, context_shell=self.ui.context_shell,
            project_drawer=self.ui.project_drawer, project_shell=self.parts.project_panel.widget,
            toolbar=self.ui.main_toolbar, brand_label=self.ui.brand_label, toolbar_spacer=self.ui.toolbar_spacer,
            actions=ChromeActions(self.ui.open_action, self.ui.refresh_action, self.ui.report_action,
                self.ui.back_action, self.ui.forward_action, self.ui.project_panel_action,
                self.ui.context_panel_action, self.ui.theme_action, self.ui.theme_actions),
            tabs=self.ui.tabs, context_header=self.ui.context_header,
            settings=self.state.review.settings, save_settings=self.state.review.save_settings,
            loading_active=lambda: self.parts.loading.loading_overlay_active(),
            ride_along=lambda: self.parts.shell.ride_along_with_panel(),
            counts_for_scope=lambda: self.parts.scopes.scope_tab_counts(),
            list_column_rect=lambda: self.parts.viewers.list_column_rect(),
        )
        self.parts.dashboard_host = DashboardHost(
            self.ui.session_dashboard, workspace=lambda: self.state.review.workspace,
            scope_value=lambda: self.parts.scopes.dashboard_scope_value(),
            context_stack=lambda: self.parts.context.current_context_stack(),
            display_model=lambda model, scope: self.parts.dashboard.dashboard_display_model(model, scope),
            loading_active=lambda: self.parts.loading.loading_overlay_active(),
            highlighted_tilt_id=lambda: self.state.dashboard.dashboard_highlighted_tilt_series_id,
            request_render=lambda **kwargs: self.parts.dashboard.render_dashboard_for_scope(**kwargs),
            instrumentation=self.state.loading.instrumentation,
        )
        self.parts.chrome.refresh_branding()
        self.ui.loading_overlay = LoadingOverlay(self)
        self.ui.loading_overlay.setGeometry(self.rect())
        self.parts.theme = ThemeSwitch(
            self, toolbar=self.ui.main_toolbar, theme_action=self.ui.theme_action,
            settings=self.state.review.settings, save_settings=self.state.review.save_settings,
            loading_active=lambda: self.parts.loading.loading_overlay_active(),
            refresh_action_icons=lambda: self.parts.shell.refresh_action_icons(),
            refresh_branding=lambda: self.parts.chrome.refresh_branding(), viewer_tabs=self.ui.viewer_tabs,
            context_panel=self.ui.context_panel, project_tree_panel=self.parts.project_panel,
            session_dashboard=self.ui.session_dashboard, loading_overlay=self.ui.loading_overlay,
        )
        self.parts.report = ReportFlow(
            self, workspace=lambda: self.state.review.workspace, project_groups=lambda: self.parts.scopes.project_groups(),
            default_scope=lambda: self.parts.scopes.report_default_scope_value(), settings=self.state.review.settings,
            save_settings=self.state.review.save_settings, schedule_settings_save=lambda: self.parts.shell.schedule_settings_save(),
            loading_active=lambda: self.state.loading.loading_task_active, thread_pool=self.state.loading.load_thread_pool,
            report_action=self.ui.report_action, loading_progress=self.ui.loading_progress,
            show_toast=lambda *args, **kwargs: self.parts.context.show_toast(*args, **kwargs),
        )
        self.ui.toast = Toast(self)
        self._context_shell = self.ui.context_shell
        self._drop_receipt_wrap = self.ui.drop_receipt_wrap
        self._project_drop_target = self.ui.project_drop_target
        self._theme_actions = self.ui.theme_actions
        self._toolbar_spacer = self.ui.toolbar_spacer
        self._viewer_tabs = self.ui.viewer_tabs
        self.back_action = self.ui.back_action
        self.brand_label = self.ui.brand_label
        self.compact_action = self.ui.compact_action
        self.compact_button = self.ui.compact_button
        self.context_dock = self.ui.context_dock
        self.context_drawer = self.ui.context_drawer
        self.context_header = self.ui.context_header
        self.context_panel = self.ui.context_panel
        self.context_panel_action = self.ui.context_panel_action
        self.context_panel_button = self.ui.context_panel_button
        self.drop_receipt = self.ui.drop_receipt
        self.forward_action = self.ui.forward_action
        self.loading_overlay = self.ui.loading_overlay
        self.loading_progress = self.ui.loading_progress
        self.main_toolbar = self.ui.main_toolbar
        self.open_action = self.ui.open_action
        self.open_button = self.ui.open_button
        self.project_badge_legend = self.ui.project_badge_legend
        self.project_dock = self.ui.project_dock
        self.project_drop_hint = self.ui.project_drop_hint
        self.project_drop_overlay = self.ui.project_drop_overlay
        self.project_filter = self.ui.project_filter
        self.project_panel_action = self.ui.project_panel_action
        self.project_panel_button = self.ui.project_panel_button
        self.refresh_action = self.ui.refresh_action
        self.refresh_button = self.ui.refresh_button
        self.report_action = self.ui.report_action
        self.report_button = self.ui.report_button
        self.session_dashboard = self.ui.session_dashboard
        self.status_runtime = self.ui.status_runtime
        self.tabs = self.ui.tabs
        self.theme_action = self.ui.theme_action
        self.toast = self.ui.toast
        self.tree = self.ui.tree
        self.shell_chrome = self.parts.chrome
        self.theme_switch = self.parts.theme
        self.report_flow = self.parts.report
        self.dashboard_host = self.parts.dashboard_host
        self.project_tree_panel = self.parts.project_panel
        self.workspace_width = self.parts.chrome.workspace_width
        self.open_session = self.parts.loading.open_session
        self.refresh_session = self.parts.loading.refresh_session
        self.load_session = self.parts.loading.load_session
        self.set_loaded_sessions = self.parts.loading.set_loaded_sessions
        self.show_parsed_sessions = self.parts.loading.show_parsed_sessions
        self.motion_held = self.parts.loading.motion_held
        self.navigate_to_tilt_series = self.parts.dashboard.navigate_to_tilt_series
        self.navigate_history_back = self.parts.history.navigate_history_back
        self.navigate_history_forward = self.parts.history.navigate_history_forward
        self.current_context_stack = self.parts.context.current_context_stack
        self.clear_viewer_states = self.parts.viewers.clear_viewer_states
        self.show_toast = self.parts.context.show_toast
        self._constructed = True
        self.parts.chrome.fit_docks()
        self.parts.shell.restore_from_settings()
        self.state.connect_signals(self.parts)
        for connect in self.ui.connections:
            connect()
        self.parts.viewers.scope_preparation.signals.finished.connect(self.scope_prepared)
        self.parts.viewers.scope_preparation.signals.failed.connect(self.scope_preparation_failed)
        self.ui.connections.clear()
        self.ui.loading_overlay.hidden.connect(self.parts.loading.flush_deferred_loading_dashboard)
        self.ui.loading_overlay.hidden.connect(self.parts.theme.schedule_other_theme_icons)
        self.ui.loading_overlay.leaving.connect(self.parts.loading.loading_overlay_leaving)
        self.ui.loading_overlay.hidden.connect(self.parts.loading.show_pending_drop_receipt)
        self.ui.loading_overlay.focus_after = lambda: self.ui.tree
        self.parts.context.refresh_context_header()
        watch_system_reduced_motion(QApplication.instance())

    @Slot(object, object)
    def scope_prepared(self, request, payload) -> None:
        self.parts.viewers.scope_preparation.finished(request, payload)

    @Slot(object, str)
    def scope_preparation_failed(self, request, error: str) -> None:
        self.parts.viewers.scope_preparation.failed(request, error)

    @property
    def loaded_sessions(self) -> tuple[Session, ...]:
        return self.state.review.workspace.sessions

    @property
    def workspace(self) -> Workspace:
        return self.state.review.workspace

    @property
    def theme_reveal(self) -> ThemeRevealOverlay | None:
        return self.parts.theme.active_reveal


    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt signature
        super().resizeEvent(event)
        if not self._constructed:
            return
        self.ui.loading_overlay.setGeometry(self.rect())
        self.parts.chrome.fit_docks()
        self.parts.chrome.update_context_drawer_mode()
        self.parts.chrome.update_responsive_chrome()


    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802 - Qt signature
        if not self._constructed:
            return False
        if watched is self.centralWidget() and event.type() == QEvent.Type.Resize:
            # Dragging either dock changes the central workspace without
            # resizing the main window. A central-widget Resize event already
            # carries the committed geometry, so update in the same event turn
            # and avoid leaving stale chrome until another input arrives.
            self.parts.chrome.note_user_dock_widths(QGuiApplication.mouseButtons())
            self.parts.chrome.update_responsive_chrome()
        return super().eventFilter(watched, event)


    def showEvent(self, event) -> None:  # noqa: N802 - Qt signature
        super().showEvent(event)
        if not self._constructed:
            return
        # Before the first paint, so a narrow window never shows the panel
        # docked first (plan D2).
        self.parts.chrome.update_context_drawer_mode()
        # The native title bar follows the theme; the window needs a native
        # handle for that, which it has once shown.
        set_title_bar_dark(self, current_palette().name == "dark")
        # Load the loader's Qt Quick lamp while the app is idle, so the
        # first session load doesn't pay for it (plan F6.2).
        if not self.state.shell.loader_prewarm_scheduled:
            self.state.shell.loader_prewarm_scheduled = True
            QTimer.singleShot(LOADER_PREWARM_DELAY_MS, self, self.parts.shell.prewarm_loader)
            # At once, on a worker: the first character a font lacks costs a
            # ~400 ms system font search, which a tab's first visit used to pay.
            warm_font_fallback()


    def closeEvent(self, event) -> None:  # noqa: N802 — Qt signature
        self.parts.viewers.scope_preparation.invalidate()
        self.state.review.settings.window_geometry = bytes(self.saveGeometry().toBase64()).decode("ascii")
        if not self.parts.chrome.project_drawer_mode:
            self.state.review.settings.project_panel_visible = self.ui.project_panel_action.isChecked()
        if not self.parts.chrome.drawer_mode:
            # In drawer mode the button says whether the drawer is open, which
            # is never saved; the docked preference already is.
            self.state.review.settings.context_panel_visible = self.ui.context_panel_action.isChecked()
        self.state.review.settings.compact = self.ui.compact_action.isChecked()
        if 0 <= self.ui.tabs.currentIndex() < len(TAB_LABELS):
            self.state.review.settings.last_tab = str(TAB_LABELS[self.ui.tabs.currentIndex()])
        # Write synchronously here so a debounced tab-change save cannot be
        # lost when the window closes before the timer fires.
        self.parts.shell.flush_settings_save()
        # A thumbnail decode may be running; exit is where waiting for it is allowed.
        shutdown_preview_scheduler()
        super().closeEvent(event)
