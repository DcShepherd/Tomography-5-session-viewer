"""Explicit capture selection and bounded PDF work, separate from acquisition reports."""
from __future__ import annotations

import logging
from threading import Event
from pathlib import Path
from typing import TYPE_CHECKING

from PySide6.QtWidgets import QDialog, QFileDialog

from tomography_session_browser.presentation.tomogram_report_scope import normalise_contact_sheet
from tomography_session_browser.reports.tomogram_contact_sheet import build_contact_sheet
from tomography_session_browser.services.volume.snapshot import SnapshotRequest
from tomography_session_browser.ui.loading.tasks import BackgroundTask, TaskSignals
from tomography_session_browser.ui.tomograms.history_dialog import CaptureHistoryDialog

if TYPE_CHECKING:
    from tomography_session_browser.ui.tomograms.tab import TomogramTab


class ContactSheetDialog(CaptureHistoryDialog):
    def __init__(self, candidates: tuple[SnapshotRequest, ...], allowed_keys: frozenset[str], parent=None, *, history=None):
        super().__init__(history, allowed_keys=allowed_keys, candidates=candidates, parent=parent)


class ContactSheetExport:
    def __init__(self, tab: TomogramTab):
        self.tab = tab
        self.pending: Path | None = None
        self.cancelled = Event()
        self.signals = TaskSignals()
        self.signals.finished.connect(tab._contact_sheet_finished)
        self.signals.failed.connect(tab._contact_sheet_failed)

    def candidates(self) -> tuple[SnapshotRequest, ...]:
        allowed = frozenset(self.tab.review_list.ordered_keys)
        return tuple(entry.record.request for entry in self.tab.history.entries if entry.record.request.row_key in allowed)

    def choose(self) -> None:
        self.tab.playback.stop()
        if self.pending is not None:
            self.tab.status.message.setText("A contact sheet is already being exported.")
            return
        allowed = frozenset(self.tab.review_list.ordered_keys)
        if not allowed:
            self.tab.status.message.setText("No tomograms in the current filtered list.")
            return
        generation = self.tab.history.generation
        if self.tab.history.contact_dialog is None:
            self.tab.history.contact_dialog = ContactSheetDialog((), allowed, self.tab, history=self.tab.history)
        else:
            self.tab.history.contact_dialog.prepare_selection(allowed)
        dialog = self.tab.history.contact_dialog
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        if dialog.invalidated or generation != self.tab.history.generation:
            self.tab.status.message.setText("The review scope changed. Reopen contact-sheet selection.")
            return
        scope = dialog.selected_scope()
        filename, _ = QFileDialog.getSaveFileName(self.tab, "Save tomogram contact sheet",
            str(Path(self.tab.settings.tomogram_snapshot_folder or "") / "tomogram-contact-sheet.pdf"), "PDF files (*.pdf)",
            options=QFileDialog.Option.DontConfirmOverwrite)
        if not filename:
            return
        output = Path(filename)
        if not output.suffix:
            output = output.with_suffix(".pdf")
        try:
            # The nested file dialog may process scope changes. Never broaden.
            if generation != self.tab.history.generation:
                raise ValueError("The review scope changed. Reopen contact-sheet selection.")
            scope = normalise_contact_sheet(scope.captures, frozenset(self.tab.review_list.ordered_keys),
                                            frozenset(range(len(scope.captures))))
        except ValueError as exc:
            self.tab.status.message.setText(str(exc))
            return
        self.pending = output
        self.cancelled = Event()
        self.tab.update_commands()
        cancelled = self.cancelled
        roots = self.tab.protected_roots()
        self.tab.status.message.setText(f"Exporting {len(scope.captures)} captures to contact sheet…")
        self.tab.captures.pool.start(BackgroundTask(lambda: build_contact_sheet(scope, output, roots, cancelled=cancelled.is_set),
            context=output, signals=self.signals, logger=logging.getLogger(__name__),
            failure_log="Tomogram contact sheet failed"))

    def cancel(self) -> None:
        if self.pending is not None:
            self.cancelled.set()
            self.tab.status.message.setText("Cancelling contact sheet…")
            self.tab.update_commands()

    def finished(self, output: Path, result: Path) -> None:
        if self.pending == output:
            self.pending = None
            self.tab.update_commands()
            self.tab.status.message.setText(f"Saved contact sheet: {result.name}")
            self.tab.status.message.setToolTip(str(result))

    def failed(self, output: Path, message: str) -> None:
        if self.pending == output:
            self.pending = None
            self.tab.update_commands()
            self.tab.status.message.setText("Contact sheet cancelled; no PDF was published." if self.cancelled.is_set()
                                     else f"Contact sheet failed: {message}")
