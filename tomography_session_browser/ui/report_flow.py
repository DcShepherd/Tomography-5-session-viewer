"""Scoped PDF export dialogs and background-task lifecycle."""
from __future__ import annotations
from collections.abc import Callable, Sequence
import logging
from pathlib import Path
from PySide6.QtCore import QObject, QThreadPool, QUrl, Slot
from PySide6.QtGui import QAction, QDesktopServices
from PySide6.QtWidgets import QDialog, QFileDialog, QMainWindow, QMessageBox, QProgressBar
from tomography_session_browser.presentation.project_tree import ProjectTreeGroup
from tomography_session_browser.presentation.workspace import Scope, Workspace
from tomography_session_browser.presentation.report_scope import ReportScope, normalise_report_scope
from tomography_session_browser.reports import build_session_report
from tomography_session_browser.services.settings_service import Settings
from tomography_session_browser.ui.loading.tasks import BackgroundTask, TaskSignals
from tomography_session_browser.ui.report_scope_dialog import ReportScopeDialog
from tomography_session_browser.ui.widgets.toast import TONE_ERROR, TONE_SUCCESS
LOGGER = logging.getLogger("tomography_session_browser.ui.main_window")

def report_task(scope: ReportScope, output_path: Path, signals: TaskSignals) -> BackgroundTask:
    """Write ``scope``'s PDF to ``output_path`` on the thread pool."""

    sessions, groups = list(scope.sessions), list(scope.project_groups)
    return BackgroundTask(
        lambda: build_session_report(
            sessions,
            output_path,
            project_title=scope.project_title,
            project_groups=groups,
            atlas_context_sessions=scope.atlas_context_sessions,
            include_exposure_thumbnails=scope.include_exposure_thumbnails,
        ),
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
    ) -> None:
        super().__init__(window)
        self.window, self.workspace = window, workspace
        self.project_groups, self.default_scope = project_groups, default_scope
        self.settings, self.save_settings = settings, save_settings
        self.schedule_settings_save, self.loading_active = schedule_settings_save, loading_active
        self.thread_pool, self.report_action = thread_pool, report_action
        self.loading_progress, self.show_toast = loading_progress, show_toast
        self.active = False
        self.signals = TaskSignals(self)
        self.signals.finished.connect(self.on_report_generated)
        self.signals.failed.connect(self.on_report_failed)

    def on_generate_report(self) -> None:
        if not self.workspace().sessions:
            QMessageBox.information(
                self.window,
                "No session loaded",
                "No sessions loaded. Load a session before generating a report.",
            )
            return

        groups = self.project_groups()
        dialog = ReportScopeDialog(
            groups,
            current_scope=self.default_scope(),
            # Passed so the dialog can preview the *normalised* scope, which is
            # what the generator will actually produce.
            sessions=self.workspace().sessions,
            include_exposure_thumbnails=self.settings.report_exposure_thumbnails,
            parent=self.window,
        )
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        request = dialog.selected_request()
        # Remembered for the next report.
        if self.settings.report_exposure_thumbnails != request.include_exposure_thumbnails:
            self.settings.report_exposure_thumbnails = request.include_exposure_thumbnails
            self.schedule_settings_save()
        try:
            scope = normalise_report_scope(self.workspace().sessions, groups, request)
        except ValueError as exc:
            QMessageBox.warning(self.window, "No report scope selected", str(exc))
            return

        primary = scope.sessions[0]
        default_dir = self.settings.last_report_directory or str(primary.path.parent)
        default_name = scope.default_filename
        default_path = str(Path(default_dir) / default_name)

        target, _ = QFileDialog.getSaveFileName(
            self.window,
            "Save session report",
            default_path,
            "PDF (*.pdf)",
        )
        if not target:
            return
        if not target.lower().endswith(".pdf"):
            target += ".pdf"

        output_path = Path(target)
        self.active = True
        self.report_action.setEnabled(False)
        self.loading_progress.setVisible(True)
        self.window.statusBar().showMessage(f"Generating {output_path.name}…")
        self.thread_pool.start(report_task(scope, output_path, self.signals))

    @Slot(object, object)
    def on_report_generated(self, output_value: object, result: object) -> None:
        output_path = Path(output_value)
        self.finish_report_task()
        self.settings.last_report_directory = str(output_path.parent)
        self.save_settings(self.settings)
        page_count = int(getattr(result, "page_count", 0))
        warnings = list(getattr(result, "warnings", ()))
        self.window.statusBar().clearMessage()  # "Generating …" is over
        self.show_toast(f"Saved {output_path.name} ({page_count} pages)", tone=TONE_SUCCESS)

        box = QMessageBox(self.window)
        box.setIcon(QMessageBox.Icon.Information)
        box.setWindowTitle("Report saved")
        box.setText(f"Saved to {output_path}")
        if warnings:
            box.setInformativeText(
                f"Saved with {len(warnings)} warning{'s' if len(warnings) != 1 else ''}. "
                "Open details to review them."
            )
            box.setDetailedText("\n".join(warnings))
        open_report_button = box.addButton("Open report", QMessageBox.ButtonRole.AcceptRole)
        open_folder_button = box.addButton("Show in folder", QMessageBox.ButtonRole.ActionRole)
        box.addButton(QMessageBox.StandardButton.Close)
        box.exec()
        if box.clickedButton() is open_report_button:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(output_path)))
        elif box.clickedButton() is open_folder_button:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(output_path.parent)))

    @Slot(object, str)
    def on_report_failed(self, output_value: object, message: str) -> None:
        output_path = Path(output_value)
        self.finish_report_task()
        QMessageBox.critical(
            self.window,
            "Report failed",
            f"Could not generate {output_path.name}:\n\n{message}",
        )
        self.window.statusBar().clearMessage()
        self.show_toast("Report generation failed", tone=TONE_ERROR)

    def finish_report_task(self) -> None:
        self.active = False
        if not self.loading_active():
            self.loading_progress.setVisible(False)
        self.report_action.setEnabled(
            bool(self.workspace().sessions) and not self.active and not self.loading_active()
        )

