"""Scoped PDF export dialogs and background-task lifecycle."""
from __future__ import annotations
from collections.abc import Callable, Sequence
import logging
import threading
from pathlib import Path
from PySide6.QtCore import QObject, QStandardPaths, QThreadPool, QUrl, Qt, Signal, Slot
from PySide6.QtGui import QAction, QDesktopServices
from tomography_session_browser.ui.file_actions import show_in_folder
from PySide6.QtWidgets import QDialog, QFileDialog, QMainWindow, QProgressBar, QProgressDialog
from tomography_session_browser.domain.display_names import count_phrase
from tomography_session_browser.presentation.messages import ActionPrompt, MessageAction, MessageContent
from tomography_session_browser.ui.widgets import message_sheet
from tomography_session_browser.presentation.project_folders import UserGroup
from tomography_session_browser.presentation.project_tree import ProjectTreeGroup
from tomography_session_browser.presentation.workspace import Scope, Workspace
from tomography_session_browser.presentation.report_scope import ReportScope, ReportScopeRequest, preset_folders
from tomography_session_browser.presentation.report_tomograms import ReportTomograms
from tomography_session_browser.reports.report_generator import SLICES, ReportProgress, build_report
from tomography_session_browser.reports.tomogram_slices import CANCELLED
from tomography_session_browser.services.output_boundary import check_output_path
from tomography_session_browser.services.settings_service import Settings
from tomography_session_browser.ui.loading.tasks import BackgroundTask, TaskSignals
from tomography_session_browser.ui.report_scope_dialog import ReportScopeDialog
from tomography_session_browser.ui.report_tomogram_options import FREEZE_FAILED
from tomography_session_browser.ui.widgets.toast import TONE_ERROR, TONE_SUCCESS
LOGGER = logging.getLogger("tomography_session_browser.ui.main_window")

SLICES_STARTING = "Central slices: starting"
WRITING_PDF = "Writing the PDF"
CANCELLING = "Cancelling the report…"

class ReportProgressSignals(QObject):
    """progress(stage, done, total) from the report worker (D4 §5.5)."""
    progress = Signal(str, int, int)


def report_task(scope: ReportScope, output_path: Path, signals: TaskSignals,
                protected_roots: tuple[Path, ...] = (), *,
                cancelled: Callable[[], bool] = lambda: False,
                progress: ReportProgress = lambda stage, done, total: None) -> BackgroundTask:
    """Write ``scope``'s PDF to ``output_path`` on the thread pool."""

    return BackgroundTask(
        lambda: build_report(scope, output_path, protected_roots, cancelled=cancelled, progress=progress),
        context=output_path,
        signals=signals,
        logger=LOGGER,
        failure_log="Failed to generate PDF report",
    )


class ReportFlow(QObject):
    """Own one export task and its dialogs; normalisation remains canonical."""

    def __init__(
        self, window: QMainWindow, *, workspace: Callable[[], Workspace],
        project_groups: Callable[[], Sequence[ProjectTreeGroup]], default_scope: Callable[[], Scope],
        settings: Settings, save_settings: Callable[[Settings], None],
        schedule_settings_save: Callable[[], None], loading_active: Callable[[], bool],
        thread_pool: QThreadPool, report_action: QAction, loading_progress: QProgressBar,
        show_toast: Callable[..., None],
        review_scope: Callable[[], object] = lambda: None,
        freeze_tomograms: Callable[[Workspace], ReportTomograms] | None = None,
        user_groups: Callable[[], Sequence[UserGroup]] = lambda: (),
    ) -> None:
        super().__init__(window)
        self.user_groups = user_groups
        self.window, self.workspace = window, workspace
        # The raw review scope carries a folder's id for the folder preset;
        # ``default_scope`` has already turned it into its acquisition scope.
        self.review_scope = review_scope
        self.freeze_tomograms = freeze_tomograms
        self.project_groups, self.default_scope = project_groups, default_scope
        self.settings, self.save_settings = settings, save_settings
        self.schedule_settings_save, self.loading_active = schedule_settings_save, loading_active
        self.thread_pool, self.report_action = thread_pool, report_action
        self.loading_progress, self.show_toast = loading_progress, show_toast
        self.active = False
        self.signals = TaskSignals(self)
        self.signals.finished.connect(self.on_report_generated)
        self.signals.failed.connect(self.on_report_failed)
        # Central slices: counted progress with Cancel, then "Writing the PDF".
        self.cancel_event = threading.Event()
        self.progress_signals = ReportProgressSignals(self)
        self.progress_signals.progress.connect(self.on_report_progress)
        self.progress_dialog: QProgressDialog | None = None

    def available(self) -> bool:
        """The one rule for the report action (D4 F28): sessions or processing
        folders loaded, no report running and no loading task. A processing
        scope opens the dialog with its folder ticked (a folder report)."""
        workspace = self.workspace()
        return (bool(workspace.sessions or workspace.processing_sources)
                and not self.active and not self.loading_active())

    def refresh_action(self) -> None:
        self.report_action.setEnabled(self.available())

    def snapshot(self, workspace: Workspace) -> tuple[ReportTomograms | None, str | None]:
        """The tomogram snapshot when the dialog opens, or why there is none.

        Only the freeze is guarded: it reads tag and review state, so it can
        fail where an acquisition-only report still works.
        """
        if self.freeze_tomograms is None or not workspace.processing_sources:
            return None, None
        try:
            return self.freeze_tomograms(workspace), None
        except Exception:
            LOGGER.exception("Could not capture tomograms for the report")
            return None, FREEZE_FAILED

    def remember_options(self, request: ReportScopeRequest) -> None:
        """Remember the export options; only an explicit checkbox change alters them."""
        chosen = {"report_exposure_thumbnails": request.include_exposure_thumbnails,
                  "report_tomograms": request.include_tomograms,
                  "report_tomogram_slices": request.include_slices}
        changed = False
        for name, value in chosen.items():
            if getattr(self.settings, name) != value:
                setattr(self.settings, name, value)
                changed = True
        if changed:
            self.schedule_settings_save()

    def on_generate_report(self) -> None:
        current = self.default_scope()
        workspace = self.workspace()
        if not workspace.sessions and not workspace.processing_sources:
            message_sheet.choose_action(self.window, ActionPrompt(MessageContent("No session loaded", "Load a session before generating a report."),
                (MessageAction("close", "Close"),), "close", "close"))
            return

        snapshot, issue = self.snapshot(workspace)
        dialog = ReportScopeDialog(
            self.project_groups(),
            current_scope=current,
            # Passed so the dialog can preview the *normalised* scope, which is
            # what the generator will actually produce.
            sessions=workspace.sessions,
            include_exposure_thumbnails=self.settings.report_exposure_thumbnails,
            tomograms=snapshot,
            tomogram_issue=issue,
            include_tomograms=self.settings.report_tomograms,
            include_slices=self.settings.report_tomogram_slices,
            folder_preset=preset_folders(self.review_scope(), current, snapshot),
            user_groups=self.user_groups(),
            parent=self.window,
        )
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        # The exact scope of the final preview; no second normalisation, so a
        # workspace replaced while either dialog is open cannot change it.
        scope = dialog.accepted_scope()
        self.remember_options(dialog.selected_request())
        if scope is None:
            return

        default_dir = allowed_report_directory((self.settings.last_report_directory,
            str(scope.default_directory or ""), QStandardPaths.writableLocation(QStandardPaths.StandardLocation.DocumentsLocation)),
            self.workspace().source_roots)
        default_name = scope.default_filename
        default_path = str(default_dir / default_name) if default_dir is not None else default_name

        while True:
            target, _ = QFileDialog.getSaveFileName(self.window, "Save session report", default_path, "PDF (*.pdf)")
            if not target:
                return
            if not target.lower().endswith(".pdf"):
                target += ".pdf"
            output_path = Path(target)
            # Read the roots after the Save dialog: a folder loaded while it
            # was open is protected too (F36).
            roots = self.workspace().source_roots
            try:
                check_output_path(output_path, roots)
            except (OSError, ValueError, RuntimeError) as exc:
                message_sheet.choose_action(self.window, ActionPrompt(MessageContent(
                    "Choose a folder outside the loaded sources", "No report was written. Choose another destination.",
                    (str(output_path),), str(exc)), (MessageAction("choose", "Choose folder"),), "choose", "choose"))
                continue
            break
        self.active = True
        self.report_action.setEnabled(False)
        self.loading_progress.setVisible(True)
        self.window.statusBar().showMessage(f"Generating {output_path.name}…")
        self.cancel_event = event = threading.Event()
        if scope.prints_slices:
            self.show_slice_progress()
        self.thread_pool.start(report_task(scope, output_path, self.signals, roots, cancelled=event.is_set,
                                           progress=self.progress_signals.progress.emit))

    def show_slice_progress(self) -> None:
        dialog = QProgressDialog(SLICES_STARTING, "Cancel", 0, 0, self.window)
        dialog.setWindowTitle("Generating the report")
        dialog.setWindowModality(Qt.WindowModality.NonModal)
        dialog.setAutoClose(False)
        dialog.setAutoReset(False)
        dialog.setMinimumDuration(0)
        dialog.canceled.connect(self.cancel_report)
        dialog.show()
        self.progress_dialog = dialog

    def cancel_report(self) -> None:
        self.cancel_event.set()
        if self.progress_dialog is not None:
            self.progress_dialog.setLabelText(CANCELLING)
        self.window.statusBar().showMessage(CANCELLING)

    @Slot(str, int, int)
    def on_report_progress(self, stage: str, done: int, total: int) -> None:
        dialog = self.progress_dialog
        if dialog is None or self.cancel_event.is_set():
            return
        if stage == SLICES:
            dialog.setMaximum(max(total, 1))
            dialog.setValue(done)
            dialog.setLabelText(f"Central slices: {done} of {total}")
            return
        # ReportLab's build cannot stop, so Cancel is no longer offered.
        dialog.setCancelButton(None)
        dialog.setRange(0, 0)
        dialog.setLabelText(WRITING_PDF)
        self.window.statusBar().showMessage(WRITING_PDF)

    def close_slice_progress(self) -> None:
        if self.progress_dialog is not None:
            self.progress_dialog.canceled.disconnect(self.cancel_report)
            self.progress_dialog.close()
            self.progress_dialog.deleteLater()
            self.progress_dialog = None

    @Slot(object, object)
    def on_report_generated(self, output_value: object, result: object) -> None:
        output_path = Path(output_value)
        self.finish_report_task()
        try:
            check_output_path(output_path.parent, self.workspace().source_roots)
        except (OSError, ValueError, RuntimeError):
            pass  # A source may have been loaded since dispatch; do not remember it.
        else:
            self.settings.last_report_directory = str(output_path.parent)
        self.save_settings(self.settings)
        page_count = int(getattr(result, "page_count", 0))
        warnings = list(getattr(result, "warnings", ()))
        self.window.statusBar().clearMessage()  # "Generating …" is over
        self.show_toast(f"Saved {output_path.name} ({page_count} pages)", tone=TONE_SUCCESS)

        consequence = "The report is ready."
        if warnings:
            consequence += f" Saved with {count_phrase(len(warnings), 'warning')}; review Details."
        answer = message_sheet.choose_action(self.window, ActionPrompt(
            MessageContent("Report saved", consequence, (str(output_path),), "\n".join(warnings)),
            (MessageAction("close", "Close"), MessageAction("open", "Open report"), MessageAction("folder", "Show in folder")), "close", "close"))
        if answer == "open":
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(output_path)))
        elif answer == "folder":
            show_in_folder(output_path)

    @Slot(object, str)
    def on_report_failed(self, output_value: object, message: str) -> None:
        output_path = Path(output_value)
        self.finish_report_task()
        if self.cancel_event.is_set():
            # A cancel writes no file: say so once, without a failure sheet.
            self.window.statusBar().clearMessage()
            self.show_toast(CANCELLED)
            return
        message_sheet.choose_action(self.window, ActionPrompt(
            MessageContent("The report was not saved", "Your review is still available. Try again or choose another destination.", (str(output_path),), message),
            (MessageAction("close", "Close"),), "close", "close"))
        self.window.statusBar().clearMessage()
        self.show_toast("Report generation failed", tone=TONE_ERROR)

    def finish_report_task(self) -> None:
        self.close_slice_progress()
        self.active = False
        if not self.loading_active():
            self.loading_progress.setVisible(False)
        self.refresh_action()


def allowed_report_directory(candidates: Sequence[str], roots: tuple[Path, ...]) -> Path | None:
    """First allowed preference; no assumed-safe Documents fallback."""
    for candidate in candidates:
        if candidate:
            try:
                return check_output_path(Path(candidate), roots)
            except (OSError, ValueError, RuntimeError):
                continue
    return None

