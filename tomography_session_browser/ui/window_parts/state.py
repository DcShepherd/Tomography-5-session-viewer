"""Typed ownership records for transient window state; no widget or policy logic."""
from __future__ import annotations
import logging
from collections import (
    OrderedDict,
)
from collections.abc import (
    Mapping,
)
from typing import (
    Any,
    Callable,
)
from PySide6.QtCore import (
    QThreadPool,
    QTimer,
)
from PySide6.QtWidgets import (
    QMainWindow,
)
from tomography_session_browser.domain.markers import (
    SelectionState,
)
from tomography_session_browser.domain.models import (
    BatchPosition,
    SearchMap,
    Session,
)
from tomography_session_browser.presentation.workspace import (
    Scope,
    Workspace,
)
from tomography_session_browser.services.tilt_series_validation import (
    TiltSeriesValidation,
)
from tomography_session_browser.services.session_loader import (
    SessionLoader,
)
from tomography_session_browser.services.session_intake import (
    DropReceipt,
    DroppedFolder,
)
from tomography_session_browser.services.settings_service import (
    Settings,
    save_settings,
)
from tomography_session_browser.ui.viewer.models import ViewerViewState
from tomography_session_browser.presentation.navigation_history import (
    NavigationHistory,
)
from tomography_session_browser.presentation.navigation_plan import (
    Jump,
    JumpKey,
)
from tomography_session_browser.ui.loading.instrumentation import (
    LoadInstrumentation,
)
from tomography_session_browser.ui.loading.requests import (
    LoadedSession,
    LoadRequest,
)
from tomography_session_browser.ui.loading.tasks import (
    LoadProgressSignals,
    TaskSignals,
)

LOGGER = logging.getLogger("tomography_session_browser.ui.main_window")


class DashboardState:
    dashboard_filter_reason: str
    dashboard_focus_value: SearchMap | BatchPosition | None
    dashboard_highlighted_tilt_series_id: str | None
    dashboard_point_click_timer: QTimer
    dashboard_scope_prefers_tree: bool
    pending_dashboard_point_click: tuple[str, object] | None
    pending_project_tree_clear_tilt_id: str | None
    project_tree_tilt_click_timer: QTimer
    tilt_viewer_selected_tilt_series_id: str | None


class HistoryState:
    history: NavigationHistory
    suppress_history_record: bool


class LoadingState:
    instrumentation: LoadInstrumentation
    last_loading_status_at: float
    load_progress: LoadProgressSignals
    load_queue: list[LoadRequest]
    load_thread_pool: QThreadPool
    loaded_this_run: list[str]
    loader: SessionLoader
    loading_status_interval_s: float
    loading_status_timer: QTimer
    loading_task_active: bool
    pending_drop_receipt: DropReceipt | None
    pending_drop_skips: tuple[DroppedFolder, ...]
    pending_loading_status: str | None
    pending_ui_integration: LoadedSession | None
    refresh_failures: list[str]
    refresh_previous: dict[str, Session]
    session_load_signals: TaskSignals
    session_ui_prepare_signals: TaskSignals


class ReviewState:
    current_path: str | None
    failed_session_path: str | None
    pending_filter_notice: str | None
    preserve_tree_root_context: bool
    project_display_names: dict[str, str]
    project_tree_activation_guard: int | None
    save_settings: Callable[[Settings], None]
    scope: Scope
    session_context_sync_generation: int
    settings: Settings
    suppress_viewer_context: bool
    workspace: Workspace


class ShellState:
    loader_prewarm_scheduled: bool
    settings_save_timer: QTimer


class ViewerState:
    atlas_collection_visibility: dict[tuple[str, str], bool]
    failed_tilt_ids_cache: frozenset[str] | None
    # The last planned jumps and what they were planned from. Replaced by the
    # next plan and dropped with the workspace (``reset_workspace_views``).
    navigation_plans: dict[str | None, tuple[tuple[Any, ...], Mapping[JumpKey, Jump]]]
    selection_state: SelectionState
    viewer_states: OrderedDict[tuple[str, str], ViewerViewState]
    viewer_tilt_validations: dict[str, TiltSeriesValidation]


from tomography_session_browser.ui.window_parts.contracts import (
    WindowParts,
)

class WindowState:
    def __init__(self, window: QMainWindow, *, settings: Settings | None, save_settings: Callable[[Settings], None]) -> None:
        self.dashboard = DashboardState()
        self.history = HistoryState()
        self.loading = LoadingState()
        self.review = ReviewState()
        self.shell = ShellState()
        self.viewer = ViewerState()
        self.loading.loader = SessionLoader()
        self.review.workspace = Workspace.build([])
        self.review.current_path: str | None = None
        self.review.failed_session_path: str | None = None
        self.review.scope: Scope = None
        self.dashboard.dashboard_focus_value: SearchMap | BatchPosition | None = None
        self.dashboard.dashboard_highlighted_tilt_series_id: str | None = None
        self.dashboard.tilt_viewer_selected_tilt_series_id: str | None = None
        self.dashboard.pending_dashboard_point_click: tuple[str, object] | None = None
        self.dashboard.pending_project_tree_clear_tilt_id: str | None = None
        self.dashboard.dashboard_scope_prefers_tree = False
        self.review.project_display_names: dict[str, str] = {}
        self.loading.refresh_previous: dict[str, Session] = {}
        self.loading.refresh_failures: list[str] = []
        self.viewer.atlas_collection_visibility: dict[tuple[str, str], bool] = {}
        self.review.suppress_viewer_context = False
        self.review.preserve_tree_root_context = False
        self.viewer.selection_state = SelectionState()
        self.review.settings = settings if settings is not None else Settings()
        self.review.save_settings = save_settings
        self.viewer.failed_tilt_ids_cache: frozenset[str] | None = None
        self.viewer.navigation_plans: dict[str | None, tuple[tuple[Any, ...], Mapping[JumpKey, Jump]]] = {}
        self.viewer.viewer_states: OrderedDict[tuple[str, str], ViewerViewState] = OrderedDict()
        self.review.pending_filter_notice: str | None = None
        self.history.history = NavigationHistory()
        self.history.suppress_history_record = False
        self.review.project_tree_activation_guard: int | None = None
        self.dashboard.dashboard_filter_reason = ''
        self.review.session_context_sync_generation = 0
        self.shell.settings_save_timer = QTimer(window)
        self.shell.settings_save_timer.setSingleShot(True)
        self.shell.settings_save_timer.setInterval(400)
        self.viewer.viewer_tilt_validations: dict[str, TiltSeriesValidation] = {}
        self.loading.instrumentation = LoadInstrumentation(LOGGER)
        self.loading.load_queue: list[LoadRequest] = []
        self.loading.loading_task_active = False
        self.loading.load_thread_pool = QThreadPool.globalInstance()
        self.loading.load_progress = LoadProgressSignals(window)
        self.loading.session_load_signals = TaskSignals(window)
        self.loading.session_ui_prepare_signals = TaskSignals(window)
        self.loading.pending_ui_integration: LoadedSession | None = None
        self.loading.loading_status_timer = QTimer(window)
        self.loading.loading_status_timer.setSingleShot(True)
        self.dashboard.dashboard_point_click_timer = QTimer(window)
        self.dashboard.dashboard_point_click_timer.setSingleShot(True)
        self.dashboard.project_tree_tilt_click_timer = QTimer(window)
        self.dashboard.project_tree_tilt_click_timer.setSingleShot(True)
        self.loading.pending_loading_status: str | None = None
        self.loading.last_loading_status_at = 0.0
        self.loading.loading_status_interval_s = 0.06
        self.loading.loaded_this_run: list[str] = []
        self.loading.pending_drop_skips: tuple[DroppedFolder, ...] = ()
        self.loading.pending_drop_receipt: DropReceipt | None = None
        self.shell.loader_prewarm_scheduled = False


    def connect_signals(self, parts: WindowParts) -> None:
        self.shell.settings_save_timer.timeout.connect(parts.shell.flush_settings_save)
        self.loading.load_progress.status.connect(parts.loading.set_loading_status)
        self.loading.load_progress.stage.connect(parts.loading.set_loading_stage)
        self.loading.load_progress.progress.connect(parts.loading.set_loading_progress)
        self.loading.session_load_signals.finished.connect(parts.loading.on_session_loaded)
        self.loading.session_load_signals.failed.connect(parts.loading.on_session_load_task_failed)
        self.loading.session_ui_prepare_signals.finished.connect(parts.loading.on_session_ui_prepared)
        self.loading.session_ui_prepare_signals.failed.connect(parts.loading.on_session_ui_prepare_failed)
        self.loading.loading_status_timer.timeout.connect(parts.loading.flush_pending_loading_status)
        self.dashboard.dashboard_point_click_timer.timeout.connect(parts.dashboard.apply_pending_dashboard_point_click)
        self.dashboard.project_tree_tilt_click_timer.timeout.connect(parts.project.apply_pending_project_tree_highlight_clear)
