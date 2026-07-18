from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QDialog, QFileDialog, QLabel, QMessageBox

import tomography_session_browser.ui.main_window as main_window
from tomography_session_browser.domain.enums import SessionKind
from tomography_session_browser.ui.main_window import (
    DIRECT_DM_FILE_MESSAGE,
    LINKED_IMPORT_HELP_TEXT,
    SESSION_FOLDER_HELP_TEXT,
    OpenSessionsDialog,
    _normalise_session_folder_for_import,
    _select_session_folder,
)


def _app() -> QApplication:
    app = QApplication.instance()
    return app or QApplication([])


def test_main_toolbar_actions_use_sentence_case_commands() -> None:
    _app()
    window = main_window.MainWindow()

    assert window.open_action.text() == "Open folder"
    assert window.import_action.text() == "Import folder"
    assert window.report_action.text() == "Report"


class _FakeLoader:
    def classify(self, path: str | Path) -> SessionKind:
        name = Path(path).name
        if name == "atlas":
            return SessionKind.ATLAS_SCREENING
        if name == "collection":
            return SessionKind.COLLECTION
        return SessionKind.UNKNOWN


def test_select_session_folder_uses_folder_only_dialog(monkeypatch, tmp_path: Path) -> None:
    _app()
    real_qfile_dialog = QFileDialog

    class FakeFileDialog:
        FileMode = real_qfile_dialog.FileMode
        Option = real_qfile_dialog.Option
        AcceptMode = real_qfile_dialog.AcceptMode
        DialogLabel = real_qfile_dialog.DialogLabel
        last: "FakeFileDialog | None" = None

        def __init__(self, parent, title: str, start_path: str) -> None:
            self.parent = parent
            self.title = title
            self.start_path = start_path
            self.file_mode = None
            self.options: dict[object, bool] = {}
            self.accept_mode = None
            self.labels: dict[object, str] = {}
            FakeFileDialog.last = self

        def setFileMode(self, mode) -> None:  # noqa: N802 - Qt API shape
            self.file_mode = mode

        def setOption(self, option, on: bool = True) -> None:  # noqa: N802 - Qt API shape
            self.options[option] = on

        def setAcceptMode(self, mode) -> None:  # noqa: N802 - Qt API shape
            self.accept_mode = mode

        def setLabelText(self, label, text: str) -> None:  # noqa: N802 - Qt API shape
            self.labels[label] = text

        def exec(self):
            return QDialog.DialogCode.Accepted

        def selectedFiles(self) -> list[str]:  # noqa: N802 - Qt API shape
            return [str(tmp_path)]

    monkeypatch.setattr(main_window, "QFileDialog", FakeFileDialog)

    selected = _select_session_folder(None, "Select session folder", str(tmp_path), "Open folder")

    assert selected == str(tmp_path)
    assert FakeFileDialog.last is not None
    assert FakeFileDialog.last.title == "Select session folder"
    assert FakeFileDialog.last.file_mode == QFileDialog.FileMode.Directory
    assert FakeFileDialog.last.options[QFileDialog.Option.ShowDirsOnly] is True
    assert FakeFileDialog.last.accept_mode == QFileDialog.AcceptMode.AcceptOpen
    assert FakeFileDialog.last.labels[QFileDialog.DialogLabel.Accept] == "Open folder"


def test_open_sessions_dialog_explains_folder_selection_and_parent_recovery(tmp_path: Path) -> None:
    _app()
    collection = tmp_path / "collection"
    collection.mkdir()
    session_dm = collection / "session.dm"
    session_dm.write_text("<Session />", encoding="utf-8")

    dialog = OpenSessionsDialog(_FakeLoader(), str(tmp_path))

    assert dialog.windowTitle() == "Select session folder"
    assert dialog.import_button.text() == "Open folders"
    label_texts = {label.text() for label in dialog.findChildren(QLabel)}
    assert SESSION_FOLDER_HELP_TEXT in label_texts
    assert LINKED_IMPORT_HELP_TEXT in label_texts
    assert "Atlas session" in label_texts
    assert "Data collection session" in label_texts
    assert "Do not select" not in SESSION_FOLDER_HELP_TEXT
    assert dialog.message.text() == LINKED_IMPORT_HELP_TEXT

    dialog.collection_path.setText(str(session_dm))

    assert DIRECT_DM_FILE_MESSAGE in dialog.message.text()
    assert "Use its parent folder" in dialog.message.text()
    assert not dialog.import_button.isEnabled()
    parent_button = dialog._parent_buttons[dialog.collection_path]  # noqa: SLF001 - targeted dialog contract test
    assert not parent_button.isHidden()

    dialog._use_parent_folder(dialog.collection_path)  # noqa: SLF001 - targeted dialog contract test

    assert dialog.collection_path.text() == str(collection)
    assert dialog.import_button.isEnabled()


def test_normalise_session_dm_path_prompts_for_parent(monkeypatch, tmp_path: Path) -> None:
    session_folder = tmp_path / "collection"
    session_folder.mkdir()
    session_dm = session_folder / "session.dm"
    session_dm.write_text("<Session />", encoding="utf-8")
    prompts: list[str] = []

    def fake_question(parent, title: str, text: str, buttons, default_button):
        del parent, buttons, default_button
        prompts.append(f"{title}|{text}")
        return QMessageBox.StandardButton.Yes

    monkeypatch.setattr(QMessageBox, "question", fake_question)

    assert _normalise_session_folder_for_import(None, session_dm) == str(session_folder)
    assert prompts == ["Select session folder|You selected session.dm. Would you like to import its parent folder instead?"]


def test_normalise_single_file_path_prompts_for_parent(monkeypatch, tmp_path: Path) -> None:
    session_folder = tmp_path / "collection"
    session_folder.mkdir()
    preview = session_folder / "preview.jpg"
    preview.write_bytes(b"not an actual jpg")
    prompts: list[str] = []

    def fake_question(parent, title: str, text: str, buttons, default_button):
        del parent, buttons, default_button
        prompts.append(f"{title}|{text}")
        return QMessageBox.StandardButton.Yes

    monkeypatch.setattr(QMessageBox, "question", fake_question)

    assert _normalise_session_folder_for_import(None, preview) == str(session_folder)
    assert prompts == ["Select session folder|You selected preview.jpg. Would you like to import its parent folder instead?"]


def test_normalise_session_dm_path_shows_clear_message_when_declined(monkeypatch, tmp_path: Path) -> None:
    session_folder = tmp_path / "collection"
    session_folder.mkdir()
    session_dm = session_folder / "Atlas.dm"
    session_dm.write_text("<Atlas />", encoding="utf-8")
    messages: list[str] = []

    monkeypatch.setattr(QMessageBox, "question", lambda *args, **kwargs: QMessageBox.StandardButton.No)

    def fake_information(parent, title: str, text: str):
        del parent
        messages.append(f"{title}|{text}")
        return QMessageBox.StandardButton.Ok

    monkeypatch.setattr(QMessageBox, "information", fake_information)

    assert _normalise_session_folder_for_import(None, session_dm) is None
    assert messages == [f"Select session folder|{DIRECT_DM_FILE_MESSAGE}"]
