"""Choosing a session folder: the folder-only file dialog, and what happens
when a file is chosen instead.

The window's Open session command and the Load sessions window both use it.
Whether a path names a file rather than a folder is
``services.session_intake.names_a_file``, the rule the drop feedback uses too.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import QDialog, QFileDialog, QMessageBox, QWidget

from tomography_session_browser.services.session_intake import names_a_file

DIRECT_DM_FILE_MESSAGE = "Please select the containing folder, not an individual session file."


def normalise_session_folder_for_import(parent: QWidget | None, raw_path: str | Path) -> str | None:
    """The folder to load for ``raw_path``: itself, its parent if the reviewer
    agrees when it names a file, or ``None``."""

    path = Path(raw_path)
    if not names_a_file(path):
        return str(path)
    parent_folder = path.parent
    if parent_folder.exists() and parent_folder.is_dir():
        answer = QMessageBox.question(
            parent,
            "Select session folder",
            f"You selected {path.name}. Would you like to import its parent folder instead?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes,
        )
        if answer == QMessageBox.StandardButton.Yes:
            return str(parent_folder)
    QMessageBox.information(parent, "Select session folder", DIRECT_DM_FILE_MESSAGE)
    return None


def select_session_folder(
    parent: QWidget | None,
    title: str,
    start_path: str,
    accept_label: str,
) -> str:
    """Ask for a folder (folders only); ``""`` when the dialog is cancelled."""

    dialog = QFileDialog(parent, title, start_path)
    dialog.setFileMode(QFileDialog.FileMode.Directory)
    dialog.setOption(QFileDialog.Option.ShowDirsOnly, True)
    dialog.setAcceptMode(QFileDialog.AcceptMode.AcceptOpen)
    dialog.setLabelText(QFileDialog.DialogLabel.Accept, accept_label)
    if dialog.exec() != QDialog.DialogCode.Accepted:
        return ""
    selected = dialog.selectedFiles()
    return selected[0] if selected else ""
