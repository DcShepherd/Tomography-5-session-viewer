"""Session loading coordinates one batch and the existing staged publication."""
from __future__ import annotations
from collections.abc import Callable, Iterable
from dataclasses import replace
from pathlib import Path
import time

from PySide6.QtCore import QObject, QTimer
from PySide6.QtWidgets import QDialog, QMainWindow
from tomography_session_browser.domain.identity import path_identity_key
from tomography_session_browser.domain.models import Sample, Session
from tomography_session_browser.domain.tomograms import ProcessingSource
from tomography_session_browser.diagnostics.profiling import NULL_PROFILE, LoadingProfiler
from tomography_session_browser.presentation.describe.context import describe_project_group
from tomography_session_browser.presentation.load_results import (
    ResultSurface, load_result_content, load_result_state, result_surface,
)
from tomography_session_browser.presentation.prepared_session import PreparedSessionUi
from tomography_session_browser.presentation.project_tree import folded_session_for_sample
from tomography_session_browser.presentation.scope_identity import scope_key
from tomography_session_browser.presentation.tab_keys import TabKey
from tomography_session_browser.presentation.tomogram_selection import AllTomograms, FolderTomograms, loaded_scope
from tomography_session_browser.presentation.workspace import Scope, Workspace
from tomography_session_browser.services.load_results import (
    LoadRequest, LoadIntent, LoadStage, LoadFailure, LoadOutcome, OutcomeKind, classify_failure,
)
from tomography_session_browser.services.session_intake import (
    DRAG_CHECK_BUDGET_S, DroppedFolder, SessionIntake, assess_paths, recheck_unchecked,
)
from tomography_session_browser.services.settings_service import add_recent_session
from tomography_session_browser.ui.folder_picker import normalise_session_folder_for_import
from tomography_session_browser.ui.load_sessions_dialog import LoadSessionsDialog
from tomography_session_browser.ui.loading import heap
from tomography_session_browser.ui.loading.batch import LoadBatch
from tomography_session_browser.ui.loading.instrumentation import LoadInstrumentation
from tomography_session_browser.ui.loading.publication import CommittedLoadView
from tomography_session_browser.ui.loading.requests import LoadedSession, LoadJob, LoadCandidate, PreparedLoad
from tomography_session_browser.ui.loading.source_candidate import source_candidate
from tomography_session_browser.ui.loading.steps import StagedSteps
from tomography_session_browser.ui.loading.tasks import session_load_task, ui_prepare_task
from tomography_session_browser.ui.viewer.preview_cache import clear_preview_cache
from tomography_session_browser.ui.widgets.toast import TONE_SUCCESS
from tomography_session_browser.ui.window_parts.contracts import WindowParts, WindowUi
from tomography_session_browser.ui.window_parts.state import WindowState
from tomography_session_browser.ui.window_parts.support import LOGGER


class SessionLoading(QObject):
    def __init__(self, window: QMainWindow, state: WindowState, ui: WindowUi, parts: WindowParts) -> None:
        super().__init__(window)
        self.window, self.state, self.ui, self.parts = window, state, ui, parts
        self.candidate: LoadCandidate | None = None
        self.rollback_view: CommittedLoadView | None = None
        self.closed = False
        self.batch: LoadBatch | None = None
        self.pending_receipt_batch: str | None = None

    def retire_candidate(self) -> None:
        if self.candidate is not None:
            self.candidate.cancelled.set()
        self.candidate = None

    def retire_run(self) -> None:
        """Retire work and restore any partially published view once."""
        self.retire_candidate()
        if self.rollback_view is not None:
            previous, self.rollback_view = self.rollback_view, None
            previous.restore(self.state, self.ui, self.parts)
        if self.batch is not None:
            self.batch.cancel()
        self.state.loading.loading_task_active = False
        self.state.loading.instrumentation.detach_stall_monitor()
        self.state.loading.instrumentation.unbind()

    def cancel_pending(self) -> None:
        """A closing window cannot accept parse, preparation or staged updates."""
        self.closed = True
        heap.thaw()
        self.retire_run()
        self.ui.loading_overlay.hide_loading(fade=False)

    def open_session(self) -> None:
        """The Load sessions window; what it queues joins the project."""

        dialog = LoadSessionsDialog(
            self.state.loading.loader,
            self.state.review.current_path or "",
            self.window,
            loaded_paths=[source.path for source in (*self.state.review.workspace.sessions, *self.state.review.workspace.processing_sources)],
            recent_paths=self.state.review.settings.recent_sessions,
        )
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        self.parts.loading.load_session_paths(dialog.selected_paths())


    def load_session_paths(self, paths: list[str], *, admission_skips: tuple[DroppedFolder, ...] = ()) -> None:
        """Load already-checked session folders, adding them to the project.

        Each is named in the finished toast (none is ``quiet``): the folders
        were checked first, so none can be a duplicate the toast would repeat.
        """

        if not paths and not admission_skips:
            return
        self.parts.loading.queue_session_loads(
            [LoadRequest(path, animate=index == len(paths) - 1) for index, path in enumerate(paths)],
            admission_skips=admission_skips,
        )


    def loaded_path_keys(self) -> frozenset[str]:
        return frozenset(path_identity_key(session.path) for session in (*self.state.review.workspace.sessions, *self.state.review.workspace.processing_sources))


    def project_drops_refused(self) -> bool:
        """Drops wait for no load: while one runs (hand-off included) they are refused."""

        overlay = self.ui.loading_overlay
        return bool(self.state.loading.loading_task_active or (self.batch is not None and self.batch.running)
                    or (overlay is not None and overlay.isVisible()))


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
        self.ui.drop_receipt.dismiss()
        self.parts.loading.load_session_paths(paths, admission_skips=intake.problems)


    def show_pending_load_result(self) -> None:
        batch = self.batch
        if (self.closed or batch is None or self.pending_receipt_batch != batch.id
                or batch.running or self.ui.loading_overlay.isVisible()
                or self.parts.chrome.transient_focus):
            return
        self.pending_receipt_batch = None
        result = batch.result
        surface = result_surface(result, has_sources=self.state.review.workspace.has_sources)
        if surface == ResultSurface.CANVAS:
            self.parts.chrome.close_project_drawer()
            self.ui.session_dashboard.show_empty_state(load_result_state(result))
            self.parts.chrome.apply_tab_availability({})
        elif surface == ResultSurface.RECEIPT:
            self.ui.drop_receipt_wrap.setVisible(True)
            self.ui.drop_receipt.show_receipt(load_result_content(result))
            chrome = self.parts.chrome
            if not self.ui.project_panel_action.isChecked():
                chrome.show_project_action_checked(True)
                chrome.toggle_project_panel()
        elif surface == ResultSurface.TOAST:
            content = load_result_content(result)
            if content.toast:
                self.parts.context.show_toast(content.toast, tone=TONE_SUCCESS)


    def refresh_session(self) -> None:
        if self.state.review.current_path:
            paths = [str(session.path) for session in (*self.state.review.workspace.sessions, *self.state.review.workspace.processing_sources)] or [self.state.review.current_path]
            self.parts.loading.queue_session_loads(
                [LoadRequest(path, LoadIntent.RELOAD, quiet=True, animate=index == len(paths) - 1)
                 for index, path in enumerate(paths)]
            )


    def load_session(
        self,
        path: str,
        replace: bool = True,
        quiet: bool = False,
        *,
        animate: bool = True,
    ) -> None:
        intent = LoadIntent.REPLACE if replace else LoadIntent.IMPORT
        self.parts.loading.queue_session_loads([LoadRequest(path, intent, quiet, animate)])

    def retry_failed_loads(self) -> None:
        if self.ui.loading_overlay.isVisible():
            return
        batch = self.batch
        if batch is not None and batch.retry():
            self.ui.drop_receipt.set_running()
            self.parts.loading.begin_loading("Retrying folders...")
            self.parts.loading.start_next_session_load()


    def set_loaded_sessions(self, sessions: Iterable[Session], *, scope: Scope = None,
                            processing_sources: Iterable[ProcessingSource] | None = None) -> None:
        """Install the complete project before invalidating its derived views."""
        self.retire_run()
        self.batch = None
        self.pending_receipt_batch = None
        self.ui.drop_receipt.dismiss()
        self.ui.loading_overlay.hide_loading(fade=False)
        self.parts.loading.apply_workspace(Workspace.build(list(sessions), self.state.review.project_display_names,
            processing_sources=list(processing_sources) if processing_sources is not None else self.state.review.workspace.processing_sources), scope)
        self.parts.loading.reset_workspace_views()


    def apply_workspace(self, workspace: Workspace, scope: Scope) -> None:
        from datetime import datetime
        records = self.parts.link_review.snapshot.records
        if workspace.link_confirmations != records:
            workspace = workspace.with_link_confirmations(records)
        self.state.review.workspace, self.state.review.scope = workspace, workspace.rebind_scope(scope)
        self.state.review.tomogram_review.retain_sources(workspace.processing_sources)
        self.state.review.source_reads = self.state.review.source_reads.admitted(workspace.processing_sources, datetime.now().astimezone())
        QTimer.singleShot(0, self.parts.link_review, self.parts.link_review.reload)


    def reset_workspace_views(self) -> None:
        clear_preview_cache()
        self.state.viewer.failed_tilt_ids_cache = None
        self.state.viewer.navigation_plans.clear()
        self.parts.viewers.clear_viewer_states()
        self.parts.history.prune_history()
        self.parts.dashboard_host.invalidate()


    def queue_session_loads(self, loads: Iterable[LoadRequest], *, admission_skips: tuple[DroppedFolder, ...] = ()) -> None:
        """An independent user load owns a fresh batch and supersedes old work."""
        if self.closed:
            return
        normalised_loads: list[LoadRequest] = []
        for request in loads:
            folder_path = normalise_session_folder_for_import(self.window, request.path)
            if folder_path is None:
                continue
            normalised_loads.append(replace(request, path=folder_path))
        if not normalised_loads and not admission_skips:
            return
        self.retire_run()
        self.batch = LoadBatch(normalised_loads, has_sources=self.state.review.workspace.has_sources,
                               admission_skips=admission_skips)
        if not normalised_loads:
            self.parts.loading.finish_loading("No folders loaded", fade=False)
            return
        self.parts.loading.begin_loading("Loading session...")
        self.parts.loading.start_next_session_load()


    def start_next_session_load(self) -> None:
        batch = self.batch
        attempt = batch.next_attempt() if batch is not None else None
        if attempt is None or batch is None:
            self.state.loading.loading_task_active = False
            self.parts.loading.finish_loading_after_repaint("Loading complete")
            return
        request = attempt.request
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
        previous = [source for source in (*self.state.review.workspace.sessions, *self.state.review.workspace.processing_sources)
                    if path_identity_key(source.path) == request.key] if request.intent == LoadIntent.RELOAD else []
        job = LoadJob(attempt, profile, batch.replaces_project(attempt), previous[0] if len(previous) == 1 else None)
        self.state.loading.load_thread_pool.start(session_load_task(job, self.state.loading.session_load_signals, self.state.loading.load_progress, LOGGER))


    def on_session_loaded(self, job: LoadJob, session: object) -> None:
        batch = self.batch
        if batch is None or not self.accepts_job(job):
            return
        request = job.attempt.request
        if not isinstance(session, (Session, ProcessingSource)):
            self.fail_job(job, ValueError("Loader returned an unexpected session object."), LoadStage.PARSE)
            return
        if (not job.replace and request.intent != LoadIntent.RELOAD
                and any(path_identity_key(item.path) == path_identity_key(session.path)
                        for item in (*self.state.review.workspace.sessions, *self.state.review.workspace.processing_sources))):
            batch.record(LoadOutcome(job.attempt, OutcomeKind.SKIPPED, session.name))
            self.advance_load(job)
            return
        self.parts.loading.integrate_loaded_session_cooperatively(LoadedSession(job, session))


    def accepts_job(self, job: object) -> bool:
        return (not self.closed and isinstance(job, LoadJob) and self.batch is not None
                and self.batch.accepts(job.attempt))

    def load_status(self, job: object, message: str) -> None:
        if self.accepts_job(job):
            self.set_loading_status(message)

    def load_stage(self, job: object, stage: str) -> None:
        if self.accepts_job(job):
            self.set_loading_stage(stage)

    def load_progress(self, job: object, label: str, current: int, total: int) -> None:
        if self.accepts_job(job):
            self.set_loading_progress(label, current, total)

    def fail_job(self, job: LoadJob, error: Exception, stage: LoadStage) -> None:
        self.on_session_load_failed(job, classify_failure(job.attempt, stage, error))


    def on_session_load_failed(self, job: LoadJob, failure: LoadFailure) -> None:
        batch = self.batch
        if batch is None or not self.accepts_job(job) or failure.attempt != job.attempt:
            return
        self.retire_candidate()
        previous = job.previous
        kind = OutcomeKind.RETAINED if previous is not None else OutcomeKind.FAILED
        batch.record(LoadOutcome(job.attempt, kind,
            previous.name if previous is not None else Path(job.attempt.request.path).name, failure))
        self.advance_load(job)





    def on_session_ui_prepare_failed(self, context: object, failure: LoadFailure) -> None:
        if context is self.candidate and isinstance(context, LoadCandidate):
            self.on_session_load_failed(context.loaded.job, failure)


    def on_session_ui_prepared(self, context: object, prepared: object) -> None:
        if (context is not self.candidate or not isinstance(context, LoadCandidate)
                or context.cancelled.is_set() or not self.accepts_job(context.loaded.job)):
            return
        pending = context.loaded
        if isinstance(prepared, PreparedLoad):
            if (prepared.candidate.cancelled is not context.cancelled
                    or prepared.candidate.loaded is not context.loaded
                    or scope_key(prepared.candidate.scope) != scope_key(loaded_scope(prepared.candidate.workspace, context.scope))):
                self.fail_job(pending.job, ValueError('Mismatched acquisition preparation result.'), LoadStage.PREPARATION)
                return
            self.candidate = prepared.candidate
            prepared = prepared.payload
        if not isinstance(prepared, PreparedSessionUi):
            self.fail_job(pending.job, ValueError("UI preparation returned an unexpected result."), LoadStage.PREPARATION)
            return
        self.parts.loading.apply_prepared_session_ui_cooperatively(prepared, pending)


    def apply_prepared_session_ui_cooperatively(self, prepared: PreparedSessionUi, loaded: LoadedSession) -> None:
        request, profile, session = loaded.job.attempt.request, loaded.job.profile, loaded.session
        candidate, batch = self.candidate, self.batch
        if candidate is None or batch is None or candidate.loaded is not loaded or not self.accepts_job(loaded.job):
            return
        previous = CommittedLoadView.capture(self.state, self.ui, self.parts)
        self.rollback_view = previous
        def install() -> None:
            self.parts.loading.apply_workspace(candidate.workspace, candidate.scope)
            self.state.review.preserve_tree_root_context = True
            self.parts.loading.reset_workspace_views()
            self.parts.scopes.populate_tree()
        tab_labels = [TabKey.ATLAS, TabKey.OVERVIEW, TabKey.SEARCH_MAP, TabKey.SEARCH, TabKey.BATCH_POSITION, TabKey.TILT_SERIES, TabKey.TOMOGRAMS]
        steps: list[tuple[str, Callable[[], None]]] = [
            ("Building project tree...", install),
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
                "Finishing session dashboard...",
                lambda: self.parts.dashboard_host.flush(now=True, raise_on_error=True),
            ),
            (
                "Finalising interface...",
                lambda: self.parts.loading.finalise_loaded_session(
                    session, request.path, replace=loaded.job.replace, quiet=request.quiet, animate=request.animate
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
            previous.restore(self.state, self.ui, self.parts)
            self.rollback_view = None
            self.fail_job(loaded.job, exc, LoadStage.PUBLICATION)

        def done() -> None:
            self.candidate = None
            self.rollback_view = None
            batch.record(LoadOutcome(loaded.job.attempt,
                OutcomeKind.RELOADED if request.intent == LoadIntent.RELOAD else OutcomeKind.LOADED,
                session.name, replaced_project=loaded.job.replace))
            self.advance_load(loaded.job)

        StagedSteps(
            steps,
            on_step=show_step,
            on_error=fail,
            on_done=done,
            profile=profile,
            is_current=lambda: self.accepts_job(loaded.job) and self.candidate is candidate,
        ).start()


    def integrate_loaded_session_cooperatively(self, loaded: LoadedSession) -> None:
        """Integrate a parsed session without treating parsing as UI readiness."""

        request, profile, session = loaded.job.attempt.request, loaded.job.profile, loaded.session
        if not self.accepts_job(loaded.job):
            return
        try:
            workspace, scope = source_candidate(self.state.review.workspace, session, loaded.job.replace,
                                                self.state.review.project_display_names, self.state.review.scope)
        except Exception as exc:
            LOGGER.exception("Could not prepare candidate workspace")
            self.fail_job(loaded.job, exc, LoadStage.PREPARATION)
            return
        candidate = LoadCandidate(loaded, workspace, scope)
        self.retire_candidate()
        self.candidate = candidate

        self.parts.loading.set_loading_status("Building data model...", force=True)
        self.state.loading.load_thread_pool.start(
            ui_prepare_task(workspace, scope, self.state.loading.session_ui_prepare_signals, LOGGER, profile, context=candidate)
        )


    def show_parsed_sessions(self, sessions: Iterable[Session | ProcessingSource]) -> None:
        """Show sessions that are already parsed as the project, at once.

        The staged loader's steps run in one call: no worker thread, loading
        overlay, animation or toast. The app loads through ``load_session``;
        this is for tools and tests that drive the window with sessions they
        built or parsed themselves.
        """

        self.retire_run()
        self.batch = None
        self.pending_receipt_batch = None
        self.ui.drop_receipt.dismiss()
        for index, session in enumerate(sessions):
            path, replace = str(session.path), index == 0
            self.parts.loading.prepare_loaded_session(session, path, replace=replace)
            self.parts.dashboard.render_session_summary(animate_dashboard=False)
            self.parts.viewers.render_viewer_tabs()
            self.parts.loading.finalise_loaded_session(session, path, replace=replace, quiet=True, animate=False)


    def prepare_loaded_session(
        self,
        session: Session | ProcessingSource,
        path: str,
        *,
        replace: bool,
    ) -> None:
        if replace:
            clear_preview_cache()
        workspace, scope = source_candidate(self.state.review.workspace, session, replace,
                                            self.state.review.project_display_names, self.state.review.scope)
        self.parts.loading.apply_workspace(workspace, scope)
        self.state.review.preserve_tree_root_context = True
        self.parts.loading.reset_workspace_views()
        self.state.review.current_path = str(session.path)
        # The load's scope is its project group, known once the sessions are in place.
        # Mid-load this stays off: ``_finish_loading`` turns it on again.
        self.ui.refresh_button.setEnabled(not self.state.loading.loading_task_active)
        self.parts.scopes.populate_tree()


    def finalise_loaded_session(
        self,
        session: Session | ProcessingSource,
        path: str,
        *,
        replace: bool,
        quiet: bool,
        animate: bool,
    ) -> None:
        active_group = next((group for group in self.state.review.workspace.project_groups
                             if group.processing_source is session or any(item is session for item in group.sessions)), None)
        folded_scope = self.state.review.scope
        if isinstance(folded_scope, AllTomograms | FolderTomograms):
            self.state.review.preserve_tree_root_context = True
            self.parts.context.sync_context_panel_to_active_scope()
        elif isinstance(folded_scope, Sample) and folded_session_for_sample(folded_scope, self.state.review.workspace) is session:
            self.state.review.preserve_tree_root_context = True
            self.parts.context.show_scope_description(
                f'folded:{session.id}', session.name,
                lambda: self.parts.context.context_description(folded_scope))
        elif active_group is not None:
            self.state.review.scope = active_group
            self.state.review.preserve_tree_root_context = True
            self.parts.context.show_scope_description(
                f"{type(active_group).__name__}:{id(active_group)}",
                active_group.display_name,
                lambda: describe_project_group(active_group, self.state.review.workspace),
            )
        else:
            self.parts.context.show_scope_description(
                f"{type(session).__name__}:{id(session)}",
                "Session",
                lambda: self.parts.context.context_description(session),
            )
        self.parts.chrome.update_tab_counts()
        self.state.review.current_path = str(session.path)
        add_recent_session(self.state.review.settings, str(session.path))
        self.state.review.save_settings(self.state.review.settings)
        LOGGER.debug("Integrated tomography session path=%s replace=%s animate=%s", path, replace, animate)


    def advance_load(self, job: LoadJob) -> None:
        batch = self.batch
        if self.closed or batch is None or batch.id != job.attempt.batch_id:
            return
        profile = job.profile
        self.state.loading.instrumentation.detach_stall_monitor()
        self.state.loading.instrumentation.unbind()
        self.state.loading.instrumentation.fold_into(profile)
        profile.emit_perf_report(LOGGER, path=job.attempt.request.path)
        if batch.running:
            self.parts.loading.start_next_session_load()
            return
        self.state.loading.loading_task_active = False
        self.parts.loading.record_loading_animation_profile(profile)
        self.parts.loading.finish_loading_after_repaint("Loading complete", fade=True)


    def begin_loading(self, message: str) -> None:
        # Every load begins here, a retry included (``ui.loading.heap``).
        heap.thaw()
        # While the overlay shows, the status bar says nothing (plan §3.2).
        self.window.statusBar().clearMessage()
        self.ui.toast.dismiss()
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
        self.ui.loading_progress.setVisible(False)
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
        self.pending_receipt_batch = None


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

        # The window is each timer's context: a window closed before they fire
        # (a test, or a quick quit) drops them instead of finishing a dead load.
        batch = self.batch
        def finish_after_repaint() -> None:
            if self.closed or self.batch is not batch:
                return
            QTimer.singleShot(0, self.window, lambda: self.parts.loading.finish_loading(message, fade=fade) if not self.closed and self.batch is batch else None)

        QTimer.singleShot(0, self.window, finish_after_repaint)


    def finish_loading(self, message: str, *, fade: bool = True) -> None:
        self.state.loading.pending_loading_status = None
        self.state.loading.loading_status_timer.stop()
        self.ui.loading_progress.setVisible(self.parts.report.active)
        self.parts.loading.set_loading_status(message, force=True)
        self.window.statusBar().clearMessage()
        batch = self.batch
        self.pending_receipt_batch = batch.id if batch is not None else None
        self.ui.loading_overlay.hide_loading(fade=fade)
        self.parts.loading.show_pending_load_result()
        self.ui.open_button.setEnabled(True)
        self.ui.refresh_button.setEnabled(self.state.review.workspace.has_sources)
        self.parts.report.refresh_action()
        heap.settle()


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

