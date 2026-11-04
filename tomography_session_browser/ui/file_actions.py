"""File-location and clipboard actions shared by row menus and exports."""
from pathlib import Path
from collections.abc import Callable

from tomography_session_browser.domain.tomograms import ProcessingSource

from PySide6.QtCore import QUrl
from PySide6.QtGui import QDesktopServices, QGuiApplication
from PySide6.QtWidgets import QMenu


def show_in_folder(path: Path) -> bool:
    return QDesktopServices.openUrl(QUrl.fromLocalFile(str(path if path.is_dir() else path.parent)))


def copy_text(text: str) -> None:
    clipboard = QGuiApplication.clipboard()
    if clipboard is not None:
        clipboard.setText(text)


def add_path_actions(menu: QMenu, path: Path, name: str) -> None:
    menu.addAction('Show in folder', lambda: show_in_folder(path))
    menu.addAction('Copy path', lambda: copy_text(str(path)))
    menu.addAction('Copy name', lambda: copy_text(name))


def add_source_actions(menu: QMenu, source: ProcessingSource,
                       refresh: Callable[[str], None], remove: Callable[[str], None]) -> None:
    """Every action closes over exactly one inventory source."""
    menu.addAction('Show in folder', lambda: show_in_folder(source.path))
    menu.addAction('Copy folder path', lambda: copy_text(str(source.path)))
    menu.addAction('Refresh', lambda: refresh(source.id))
    menu.addAction('Remove', lambda: remove(source.id))
