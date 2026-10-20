"""Explicit widget references and component dependencies for the window."""
from __future__ import annotations
from typing import (
    Callable,
)
from PySide6.QtGui import (
    QAction,
)
from PySide6.QtWidgets import (
    QDockWidget,
    QProgressBar,
    QTabWidget,
    QToolBar,
    QTreeWidget,
    QWidget,
)
from tomography_session_browser.ui.shell.chrome import (
    ShellChrome,
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
from tomography_session_browser.ui.viewer.viewer_tab import ViewerTab
from tomography_session_browser.ui.branding import (
    TitleBarLockup,
)
from tomography_session_browser.ui.widgets.panel_drawer import (
    PanelDrawer,
)
from tomography_session_browser.ui.widgets.context_header import (
    ContextHeader,
)
from tomography_session_browser.ui.widgets.loading_overlay import (
    LoadingOverlay,
)
from tomography_session_browser.ui.widgets.toast import (
    Toast,
)
from tomography_session_browser.ui.dashboard.widget import SessionDashboard
from tomography_session_browser.ui.widgets.session_drop import (
    SessionDropReceipt,
    SessionDropTarget,
    SessionDropOverlay,
)
from tomography_session_browser.ui.context.panel import (
    ContextPanel,
)
from tomography_session_browser.ui.project_tree.panel import (
    ProjectTreePanel,
)

from typing import (
    TYPE_CHECKING,
)
from tomography_session_browser.ui.widgets.metadata_panel import (
    MetadataPanel,
)
from PySide6.QtWidgets import (
    QLineEdit,
)

if TYPE_CHECKING:
    from tomography_session_browser.ui.window_parts.shell import WindowShell
    from tomography_session_browser.ui.window_parts.loading import SessionLoading
    from tomography_session_browser.ui.window_parts.scopes import ScopeQueries
    from tomography_session_browser.ui.window_parts.dashboard import DashboardInteraction
    from tomography_session_browser.ui.window_parts.project import ProjectActions
    from tomography_session_browser.ui.window_parts.history import HistoryController
    from tomography_session_browser.ui.window_parts.context import ContextController
    from tomography_session_browser.ui.window_parts.viewers import ViewerCoordinator
    from tomography_session_browser.ui.window_parts.navigation import MarkerNavigation

class WindowUi:
    context_shell: QWidget
    drop_receipt_wrap: QWidget
    project_drop_target: SessionDropTarget
    theme_actions: list[QAction]
    toolbar_spacer: QWidget
    viewer_tabs: dict[str, ViewerTab]
    back_action: QAction
    brand_label: TitleBarLockup
    compact_action: QAction
    compact_button: QAction
    context_dock: QDockWidget
    context_drawer: PanelDrawer
    project_drawer: PanelDrawer
    context_header: ContextHeader
    context_panel: MetadataPanel
    context_panel_action: QAction
    context_panel_button: QAction
    drop_receipt: SessionDropReceipt
    forward_action: QAction
    loading_overlay: LoadingOverlay
    loading_progress: QProgressBar
    main_toolbar: QToolBar
    open_action: QAction
    open_button: QAction
    project_badge_legend: QWidget
    project_dock: QDockWidget
    project_drop_hint: QWidget
    project_drop_overlay: SessionDropOverlay
    project_filter: QLineEdit
    project_panel_action: QAction
    project_panel_button: QAction
    refresh_action: QAction
    refresh_button: QAction
    report_action: QAction
    report_button: QAction
    session_dashboard: SessionDashboard
    status_runtime: QWidget
    tabs: QTabWidget
    theme_action: QAction
    toast: Toast
    tree: QTreeWidget
    connections: list[Callable[[], None]]

    def __init__(self) -> None:
        self.connections = []
        self.viewer_tabs = {}
        self.theme_actions = []

class WindowParts:
    shell: WindowShell
    loading: SessionLoading
    scopes: ScopeQueries
    dashboard: DashboardInteraction
    project: ProjectActions
    history: HistoryController
    context: ContextController
    viewers: ViewerCoordinator
    navigation: MarkerNavigation
    chrome: ShellChrome
    theme: ThemeSwitch
    report: ReportFlow
    dashboard_host: DashboardHost
    project_panel: ProjectTreePanel
    context_panel: ContextPanel
