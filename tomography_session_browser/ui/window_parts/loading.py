"""SessionLoading owns its window interaction responsibilities."""
from __future__ import annotations
import logging
import time
from collections.abc import (
    Iterable,
)
from dataclasses import (
    replace,
)
from typing import (
    Callable,
)
from pathlib import (
    Path,
)
from PySide6.QtCore import (
    QObject,
    QTimer,
)
from PySide6.QtWidgets import (
    QDialog,
    QMainWindow,
    QMessageBox,
)
from tomography_session_browser.ui.shell.chrome import (
    TAB_LABELS,
)
from tomography_session_browser.domain.identity import (
    path_identity_key,
)
from tomography_session_browser.domain.models import (
    Session,
)
from tomography_session_browser.presentation.workspace import (
    Scope,
    Workspace,
)
from tomography_session_browser.diagnostics.profiling import (
    NULL_PROFILE,
    LoadingProfiler,
)
from tomography_session_browser.services.session_intake import (
    DRAG_CHECK_BUDGET_S,
    DropReceipt,
    SessionIntake,
    assess_paths,
    recheck_unchecked,
)
from tomography_session_browser.services.settings_service import (
    add_recent_session,
)
from tomography_session_browser.ui.viewer.preview_cache import clear_preview_cache
from tomography_session_browser.presentation.empty_states import (
    load_failed_state,
)
from tomography_session_browser.ui.folder_picker import (
    normalise_session_folder_for_import,
)
from tomography_session_browser.ui.widgets.toast import (
    TONE_ERROR,
    TONE_SUCCESS,
)
from tomography_session_browser.ui.load_sessions_dialog import (
    LoadSessionsDialog,
)
from tomography_session_browser.presentation.describe.context import (
    describe_project_group,
)
from tomography_session_browser.presentation.prepared_session import (
    PreparedSessionUi,
)
from tomography_session_browser.ui.loading.instrumentation import (
    LoadInstrumentation,
)
from tomography_session_browser.ui.loading.requests import (
    LoadedSession,
    LoadJob,
    LoadRequest,
)
from tomography_session_browser.ui.loading.steps import (
    StagedSteps,
)
from tomography_session_browser.ui.loading.tasks import (
    session_load_task,
    ui_prepare_task,
)
from tomography_session_browser.presentation.describe.entities import (
    describe_object,
)

LOGGER = logging.getLogger("tomography_session_browser.ui.main_window")
from tomography_session_browser.ui.window_parts.contracts import (
    WindowParts,
    WindowUi,
)
from tomography_session_browser.ui.window_parts.state import (
    WindowState,
)
from tomography_session_browser.ui.window_parts.support import (
    LOGGER,
)
from tomography_session_browser.presentation.tab_keys import TabKey

class SessionLoading(QObject):
    def __init__(self, window: QMainWindow, state: WindowState, ui: WindowUi, parts: WindowParts) -> None:
        super().__init__(window)
        self.window, self.state, self.ui, self.parts = window, state, ui, parts

    def open_session(self) -> None:
        """The Load sessions window; what it queues joins the project."""

        dialog = LoadSessionsDialog(
            self.state.loading.loader,
            self.state.review.current_path or "",
            self.window,
            loaded_paths=[session.path for session in self.state.review.workspace.sessions],
        )
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        self.parts.loading.load_session_paths(dialog.selected_paths())


    def load_session_paths(self, paths: list[str]) -> None:
        """Load already-checked session folders, adding them to the project.

        Each is named in the finished toast (none is ``quiet``): the folders
        were checked first, so none can be a duplicate the toast would repeat.
        """

        if not paths:
            return
        replacing_empty_project = not self.state.review.workspace.sessions
        self.parts.loading.queue_session_loads(
            [
                (path, replacing_empty_project and index == 0, False, index == len(paths) - 1)
                for index, path in enumerate(paths)
            ]
        )


    def loaded_path_keys(self) -> frozenset[str]:
        return frozenset(path_identity_key(session.path) for session in self.state.review.workspace.sessions)


    def project_drops_refused(self) -> bool:
        """Drops wait for no load: while one runs (hand-off included) they are refused."""

        overlay = self.ui.loading_overlay
        return bool(self.state.loading.loading_task_active or self.state.loading.load_queue or (overlay is not None and overlay.isVisible()))


    def assess_project_drop(self, paths: list[str]) -> SessionIntake | None:
        if self.parts.loading.project_drops_refused():
            return None
        return assess_paths(
            paths,
            self.state.loading.loader.classify,
            loaded_keys=self.parts.loading.loaded_path_keys(),
            budget_s=DRAG_CHECK_BUDGET_S,
        )


    def load_dropped_sessions(self, intake: SessionIntake) -> None:
        if self.parts.loading.project_drops_refused():
            return
        intake = recheck_unchecked(intake, self.state.loading.loader.classify, loaded_keys=self.parts.loading.loaded_path_keys())
        paths = list(intake.ready_paths)
        if not paths:
            return
        self.ui.drop_receipt.dismiss()
        self.state.loading.pending_drop_skips = intake.problems
        self.parts.loading.load_session_paths(paths)


    def show_pending_drop_receipt(self) -> None:
        receipt, self.state.loading.pending_drop_receipt = self.state.loading.pending_drop_receipt, None
        if receipt is None:
            return
        self.ui.drop_receipt_wrap.setVisible(True)
        self.ui.drop_receipt.show_receipt(receipt)


    def refresh_session(self) -> None:
        if self.state.review.current_path:
            paths = [str(session.path) for session in self.state.review.workspace.sessions] or [self.state.review.current_path]
            # Kept until each folder reloads: one that fails keeps its previous
            # load instead of taking the rest of the project with it.
            self.state.loading.refresh_previous = {path_identity_key(session.path): session for session in self.state.review.workspace.sessions}
            self.state.loading.refresh_failures = []
            self.parts.loading.apply_workspace(Workspace.build([], self.state.review.project_display_names), None)
            self.parts.loading.queue_session_loads(
                [
                    (path, index == 0, True, index == len(paths) - 1)
                    for index, path in enumerate(paths)
                ]
            )


    def load_session(
        self,
        path: str,
        replace: bool = True,
        quiet: bool = False,
        *,
        animate: bool = True,
    ) -> None:
        self.parts.loading.queue_session_loads([LoadRequest(path, replace, quiet, animate)])


    def set_loaded_sessions(self, sessions: Iterable[Session], *, scope: Scope = None) -> None:
        """Install the complete project before invalidating its derived views."""
        self.parts.loading.apply_workspace(Workspace.build(list(sessions), self.state.review.project_display_names), scope)
        self.parts.loading.reset_workspace_views()


    def apply_workspace(self, workspace: Workspace, scope: Scope) -> None:
        self.state.review.workspace, self.state.review.scope = workspace, scope


    def reset_workspace_views(self) -> None:
        clear_preview_cache()
        self.state.viewer.failed_tilt_ids_cache = None
        self.state.viewer.navigation_plans.clear()
        self.parts.viewers.clear_viewer_states()
        self.parts.history.prune_history()
        self.state.viewer.viewer_tilt_validations.clear()
        self.parts.dashboard_host.invalidate()


    def queue_session_loads(self, loads: Iterable[tuple[str, bool, bool, bool]]) -> None:
        """Queue ``loads`` (each a ``LoadRequest`` or its tuple) and start the
        first unless a load is already running."""

        loads = [LoadRequest(*load) for load in loads]
        if not loads:
            return
        normalised_loads: list[LoadRequest] = []
        for request in loads:
            folder_path = normalise_session_folder_for_import(self.window, request.path)
            if folder_path is None:
                continue
            normalised_loads.append(request._replace(path=folder_path))
        if not normalised_loads:
            return
        self.state.loading.load_queue.extend(normalised_loads)
        if not self.state.loading.loading_task_active:
            self.parts.loading.begin_loading("Loading session...")
            self.parts.loading.start_next_session_load()


    def start_next_session_load(self) -> None:
        if not self.state.loading.load_queue:
            self.state.loading.loading_task_active = False
            self.parts.loading.finish_loading_after_repaint("Loading complete")
            return
        request = self.state.loading.load_queue.pop(0)
        path = request.path
        self.state.loading.loading_task_active = True
        self.parts.loading.set_loading_stage("discover")
        self.ui.loading_overlay.update_loading_message("Discovering files...", detail=Path(path).name)
        self.parts.loading.set_loading_status("Discovering files...", force=True)
        # Build the profile on the main thread so the stall monitor can
        # share it with the worker's parsing run. The worker calls
        # ``record_stall`` indirectly via the monitor; the worker itself
        # owns the phase/counter additions. Binding the profile on the
        # main thread too lets context-panel updates and other main-
        # thread aggregate phases land in the same report.
        profile = LoadingProfiler()
        self.state.loading.instrumentation.bind(profile)
        self.state.loading.instrumentation.attach_stall_monitor(profile)
        job = LoadJob(request, profile)
        self.state.loading.load_thread_pool.start(session_load_task(job, self.state.loading.session_load_signals, self.state.loading.load_progress, LOGGER))


    def on_session_loaded(self, job: LoadJob, session: object) -> None:
        request = job.request
        if not isinstance(session, Session):
            self.parts.loading.on_session_load_failed(request.path, "Loader returned an unexpected session object.")
            return
        self.state.review.failed_session_path = None
        if not request.replace and self.parts.scopes.session_for_path(session.path) is not None:
            if not request.quiet:
                self.parts.context.show_toast(f"{session.name} is already loaded.")
            self.parts.loading.after_loaded_session_integrated()
            return
        self.parts.loading.integrate_loaded_session_cooperatively(LoadedSession(job, session))


    def on_session_load_task_failed(self, job: LoadJob, error: str) -> None:
        self.parts.loading.on_session_load_failed(job.request.path, error)


    def on_session_load_failed(self, path: str, error: str) -> None:
        previous = self.state.loading.refresh_previous.pop(path_identity_key(path), None)
        if previous is not None:
            self.parts.loading.keep_previous_session_after_failed_reload(path, previous, error)
            return
        self.state.loading.refresh_previous = {}
        self.state.loading.refresh_failures = []
        self.state.loading.load_queue.clear()
        self.state.loading.pending_ui_integration = None
        self.state.loading.loading_task_active = False
        self.state.loading.instrumentation.detach_stall_monitor()
        self.state.loading.instrumentation.unbind()
        self.parts.loading.finish_loading("Loading failed", fade=False, success=False)
        self.state.review.failed_session_path = path
        if not self.state.review.workspace.sessions:
            state = load_failed_state(path=path, error=error)
            for viewer_tab in self.ui.viewer_tabs.values():
                viewer_tab.show_empty_state(state)
            self.ui.tabs.setCurrentIndex(TAB_LABELS.index(TabKey.OVERVIEW))
        QMessageBox.critical(self.window, "Could not load session", f"{error}\n\nPath: {path}")


    def keep_previous_session_after_failed_reload(self, path: str, previous: Session, error: str) -> None:
        """A refreshed folder failed to reload: keep its last load and go on."""

        key = path_identity_key(path)
        # Only the list for now: the next load appends to it and installs the
        # project. Installing it here would prune history against a project
        # that is still half reloaded.
        kept = [session for session in self.state.review.workspace.sessions if path_identity_key(session.path) != key]
        self.parts.loading.apply_workspace(Workspace.build([*kept, previous], self.state.review.project_display_names), None)
        self.state.loading.refresh_failures.append(f"{previous.name or Path(path).name}: {error}")
        LOGGER.warning("Could not reload %s; keeping the previous load: %s", path, error)
        self.state.loading.pending_ui_integration = None
        self.state.loading.instrumentation.detach_stall_monitor()
        self.state.loading.instrumentation.unbind()
        if self.state.loading.load_queue:
            self.parts.loading.start_next_session_load()
            return
        # The last folder failed, so no later load will render the project.
        self.state.loading.loading_task_active = False
        self.parts.project.refresh_project_state_after_edit(self.state.review.workspace.sessions)
        self.parts.loading.finish_loading("Some sessions could not be reloaded", fade=False, success=False)
        self.parts.loading.report_refresh_failures()


    def report_refresh_failures(self) -> None:
        failures, self.state.loading.refresh_failures = self.state.loading.refresh_failures, []
        self.state.loading.refresh_previous = {}
        if not failures:
            return
        box = QMessageBox(self.window)
        box.setIcon(QMessageBox.Icon.Warning)
        box.setWindowTitle("Some sessions could not be reloaded")
        box.setText(
            f"{len(failures)} session{'s' if len(failures) != 1 else ''} could not be reloaded "
            "and still show what was loaded before the refresh."
        )
        box.setDetailedText("\n".join(failures))
        box.setStandardButtons(QMessageBox.StandardButton.Close)
        box.exec()


    def on_session_ui_prepare_failed(self, _context: object, error: str) -> None:
        pending = self.state.loading.pending_ui_integration
        self.state.loading.pending_ui_integration = None
        if pending is None:
            return
        self.parts.loading.on_session_load_failed(pending.job.request.path, error)


    def on_session_ui_prepared(self, _context: object, prepared: object) -> None:
        pending = self.state.loading.pending_ui_integration
        self.state.loading.pending_ui_integration = None
        if pending is None:
            return
        if not isinstance(prepared, PreparedSessionUi):
            self.parts.loading.on_session_load_failed(pending.job.request.path, "UI preparation returned an unexpected result.")
            return
        self.parts.loading.apply_prepared_session_ui_cooperatively(prepared, pending)


    def apply_prepared_session_ui_cooperatively(self, prepared: PreparedSessionUi, loaded: LoadedSession) -> None:
        request, profile, session = loaded.job.request, loaded.job.profile, loaded.session
        tab_labels = [TabKey.ATLAS, TabKey.OVERVIEW, TabKey.SEARCH_MAP, TabKey.SEARCH, TabKey.BATCH_POSITION, TabKey.TILT_SERIES]
        steps: list[tuple[str, Callable[[], None]]] = [
            (
                "Building session dashboard...",
                lambda: self.parts.dashboard.render_session_summary(animate_dashboard=request.animate, prepared=prepared),
            ),
            *[
                (
                    f"Building {label.lower()} browser...",
                    lambda label=label: self.parts.viewers.render_viewer_tabs(prepared=prepared, labels=[label]),
                )
                for label in tab_labels
            ],
            (
                "Finalising interface...",
                lambda: self.parts.loading.finalise_loaded_session(
                    session, request.path, replace=request.replace, quiet=request.quiet, animate=request.animate
                ),
            ),
        ]

        def show_step(index: int, message: str) -> None:
            if index < len(steps) - 1:
                self.parts.loading.set_loading_stage("build")
                self.parts.loading.set_loading_progress("Building", index + 1, len(steps) - 1)
            else:
                self.parts.loading.set_loading_stage("finish")
            self.parts.loading.set_loading_status(message, force=True)

        def fail(exc: Exception) -> None:
            LOGGER.exception("Could not render prepared tomography session UI path=%s", request.path)
            self.parts.loading.on_session_load_failed(request.path, str(exc))

        StagedSteps(
            steps,
            on_step=show_step,
            on_error=fail,
            on_done=lambda: self.parts.loading.after_loaded_session_integrated(profile=profile, profile_path=request.path),
            profile=profile,
        ).start()


    def integrate_loaded_session_cooperatively(self, loaded: LoadedSession) -> None:
        """Integrate a parsed session without treating parsing as UI readiness."""

        request, profile, session = loaded.job.request, loaded.job.profile, loaded.session
        path = request.path

        def prepare_tree() -> None:
            self.parts.loading.set_loading_stage("link")
            self.parts.loading.set_loading_status("Building project tree...", force=True)
            try:
                started = time.perf_counter()
                self.parts.loading.prepare_loaded_session(session, path, replace=request.replace)
                profile.add_phase("populate_project_tree", time.perf_counter() - started, ui_updates=1)
                profile.record_ui_update()
            except Exception as exc:  # noqa: BLE001 - show a clear UI error and keep diagnostics
                LOGGER.exception("Could not integrate loaded tomography session path=%s", path)
                self.parts.loading.on_session_load_failed(path, str(exc))
                return
            self.state.loading.pending_ui_integration = loaded
            self.parts.loading.set_loading_status("Building data model...", force=True)
            self.state.loading.load_thread_pool.start(
                ui_prepare_task(self.state.review.workspace, self.state.review.scope, self.state.loading.session_ui_prepare_signals, LOGGER, profile)
            )

        QTimer.singleShot(0, prepare_tree)


    def show_parsed_sessions(self, sessions: Iterable[Session]) -> None:
        """Show sessions that are already parsed as the project, at once.

        The staged loader's steps run in one call: no worker thread, loading
        overlay, animation or toast. The app loads through ``load_session``;
        this is for tools and tests that drive the window with sessions they
        built or parsed themselves.
        """

        for index, session in enumerate(sessions):
            path, replace = str(session.path), index == 0
            self.parts.loading.prepare_loaded_session(session, path, replace=replace)
            self.parts.dashboard.render_session_summary(animate_dashboard=False)
            self.parts.viewers.render_viewer_tabs()
            self.parts.loading.finalise_loaded_session(session, path, replace=replace, quiet=True, animate=False)


    def prepare_loaded_session(
        self,
        session: Session,
        path: str,
        *,
        replace: bool,
    ) -> None:
        if replace:
            clear_preview_cache()
        sessions = [session] if replace else [*self.state.review.workspace.sessions, session]
        workspace = Workspace.build(sessions, self.state.review.project_display_names)
        scope = next((group for group in workspace.project_groups if any(item is session for item in group.sessions)), session)
        self.parts.loading.apply_workspace(workspace, scope)
        self.parts.loading.reset_workspace_views()
        self.state.review.current_path = str(session.path)
        # The load's scope is its project group, known once the sessions are in place.
        add_recent_session(self.state.review.settings, str(session.path))
        self.state.review.save_settings(self.state.review.settings)
        # Mid-load this stays off: ``_finish_loading`` turns it on again.
        self.ui.refresh_button.setEnabled(not self.state.loading.loading_task_active)
        self.parts.scopes.populate_tree()


    def finalise_loaded_session(
        self,
        session: Session,
        path: str,
        *,
        replace: bool,
        quiet: bool,
        animate: bool,
    ) -> None:
        active_group = self.parts.scopes.project_group_for_session(session)
        if active_group is not None:
            self.state.review.scope = active_group
            self.state.review.preserve_tree_root_context = True
            self.ui.context_panel.set_title(active_group.display_name)
            self.ui.context_panel.set_text(describe_project_group(active_group))
        else:
            self.ui.context_panel.set_title("Session")
            self.ui.context_panel.set_text(describe_object(session))
        self.parts.chrome.update_tab_counts()
        if not quiet:
            action = "Loaded" if replace else "Imported"
            # Said once, by the toast when the whole load finishes (plan F5.9).
            self.state.loading.loaded_this_run.append(f"{action} {session.name}")
        LOGGER.debug("Integrated tomography session path=%s replace=%s animate=%s", path, replace, animate)


    def after_loaded_session_integrated(
        self,
        *,
        profile: LoadingProfiler = NULL_PROFILE,
        profile_path: str = "",
    ) -> None:
        if self.state.loading.load_queue:
            self.state.loading.instrumentation.fold_into(profile)
            profile.emit_perf_report(LOGGER, path=profile_path)
            self.parts.loading.start_next_session_load()
            return
        self.state.loading.loading_task_active = False
        self.parts.loading.record_loading_animation_profile(profile)
        self.parts.loading.finish_loading_after_repaint("Loading complete", fade=True)
        self.state.loading.instrumentation.detach_stall_monitor()
        self.state.loading.instrumentation.unbind()
        self.state.loading.instrumentation.fold_into(profile)
        profile.emit_perf_report(LOGGER, path=profile_path)
        if self.state.loading.refresh_failures:
            # After the loader has gone, so the dialog is not under it.
            QTimer.singleShot(0, self.parts.loading.report_refresh_failures)
        else:
            self.state.loading.refresh_previous = {}


    def begin_loading(self, message: str) -> None:
        self.state.loading.loaded_this_run = []
        # While the overlay shows, the status bar says nothing (plan §3.2).
        self.window.statusBar().clearMessage()
        self.state.loading.pending_loading_status = None
        self.state.loading.loading_status_timer.stop()
        self.state.loading.last_loading_status_at = 0.0
        self.parts.dashboard_host.reset_pending()
        # Reset perf counters for this load so the report attributes
        # rebuild count and widget build cost to *this* load only.
        self.state.loading.instrumentation.reset_counters()
        # A new monitor starts with the load's profile.
        self.state.loading.instrumentation.detach_stall_monitor()
        # Drop any prior main-thread profile binding so a new load's
        # aggregate-phase recordings (context panel, etc.) don't leak
        # into a stale profile.
        self.state.loading.instrumentation.unbind()
        self.ui.loading_progress.setVisible(True)
        self.ui.loading_overlay.show_loading(message)
        self.parts.loading.set_loading_status(message, force=True)
        self.ui.open_button.setEnabled(False)
        self.ui.refresh_button.setEnabled(False)
        # A report started now would read the project while it changes.
        self.ui.report_action.setEnabled(False)
        # A drop is refused while this load runs; what an earlier drop
        # skipped is no longer news.
        self.ui.project_drop_overlay.clear()
        self.ui.drop_receipt.dismiss()
        self.state.loading.pending_drop_receipt = None


    def set_loading_status(self, message: str, *, force: bool = False) -> None:
        if not message:
            return
        now = time.monotonic()
        elapsed = now - self.state.loading.last_loading_status_at
        if not force and self.state.loading.last_loading_status_at and elapsed < self.state.loading.loading_status_interval_s:
            self.state.loading.pending_loading_status = message
            remaining_ms = max(1, int((self.state.loading.loading_status_interval_s - elapsed) * 1000))
            if not self.state.loading.loading_status_timer.isActive():
                self.state.loading.loading_status_timer.start(remaining_ms)
            return
        self.parts.loading.apply_loading_status(message)


    def flush_pending_loading_status(self) -> None:
        if self.state.loading.pending_loading_status is None:
            return
        message = self.state.loading.pending_loading_status
        self.state.loading.pending_loading_status = None
        self.parts.loading.apply_loading_status(message)


    def apply_loading_status(self, message: str) -> None:
        self.state.loading.last_loading_status_at = time.monotonic()
        if self.ui.loading_overlay.isVisible():
            # The overlay says it; the status bar used to repeat it (plan §3.2).
            self.ui.loading_overlay.update_loading_message(message)
            return
        self.window.statusBar().showMessage(message)


    def set_loading_stage(self, stage: str) -> None:
        self.ui.loading_overlay.set_stage(stage)


    def set_loading_progress(self, label: str, current: int, total: int) -> None:
        self.ui.loading_overlay.set_progress(label, current, total)


    def finish_loading_after_repaint(self, message: str, *, fade: bool = True) -> None:
        self.parts.loading.set_loading_stage("finish")
        self.parts.loading.set_loading_status("Finalising interface...", force=True)
        self.window.update()
        self.ui.loading_overlay.update()

        def finish_after_repaint() -> None:
            QTimer.singleShot(0, lambda: self.parts.loading.finish_loading(message, fade=fade))

        QTimer.singleShot(0, finish_after_repaint)


    def finish_loading(self, message: str, *, fade: bool = True, success: bool = True) -> None:
        self.state.loading.pending_loading_status = None
        self.state.loading.loading_status_timer.stop()
        self.ui.loading_progress.setVisible(self.parts.report.active)
        self.parts.loading.set_loading_status(message, force=True)
        # A completion message is news, not state: it used to stay in the
        # status bar indefinitely ("Loading complete" minutes later). It is a
        # toast now (plan F5.9), naming what loaded when it can.
        self.window.statusBar().clearMessage()
        loaded, self.state.loading.loaded_this_run = self.state.loading.loaded_this_run, []
        skipped, self.state.loading.pending_drop_skips = self.state.loading.pending_drop_skips, ()
        if success and len(loaded) == 1:
            message = loaded[0]
        elif success and loaded:
            message = f"Loaded {len(loaded)} sessions"
        if success and loaded and skipped:
            # A drop that skipped folders is confirmed by the receipt, which
            # stays until dismissed, once the loading screen has handed over
            # (``_show_pending_drop_receipt``): a toast would say half of it.
            self.state.loading.pending_drop_receipt = DropReceipt(loaded_count=len(loaded), skipped=skipped)
        else:
            self.parts.context.show_toast(message, tone=TONE_SUCCESS if success else TONE_ERROR)
        self.ui.loading_overlay.hide_loading(fade=fade)
        if not self.ui.loading_overlay.isVisible():
            self.parts.loading.show_pending_drop_receipt()
        self.ui.open_button.setEnabled(True)
        self.ui.refresh_button.setEnabled(bool(self.state.review.workspace.sessions))
        self.ui.report_action.setEnabled(bool(self.state.review.workspace.sessions) and not self.parts.report.active)


    def loading_overlay_active(self) -> bool:
        # A leaving overlay no longer stands for a load: the hand-off must
        # never hold anything back (plan §3.4).
        return bool(
            self.state.loading.loading_task_active
            or self.ui.loading_overlay.blocking
        )


    def motion_held(self) -> bool:
        """Whether small transitions should wait (``motion.veil`` asks).

        While the loader is up, hand-off included: it pictures the page, and
        a veil half-lifted in that picture would pop when the hand-off ends.
        """

        return self.parts.loading.loading_overlay_active() or (
            self.ui.loading_overlay.isVisible()
        )


    def record_loading_animation_profile(self, profile: LoadingProfiler) -> None:
        if not profile.enabled:
            return
        LoadInstrumentation.record_loading_animation(profile, self.ui.loading_overlay.performance_snapshot())


    def loading_overlay_leaving(self) -> None:
        self.parts.loading.flush_deferred_loading_dashboard(now=True)


    def flush_deferred_loading_dashboard(self, *, now: bool = False) -> None:
        self.parts.dashboard_host.flush(now=now)

