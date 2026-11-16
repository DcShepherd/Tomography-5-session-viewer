from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from PySide6.QtWidgets import QApplication


def run(argv: Sequence[str] | None = None) -> int:
    try:
        from PySide6.QtWidgets import QApplication
    except ModuleNotFoundError as exc:
        raise SystemExit(
            "PySide6 is not installed. Install dependencies with `pip install -r requirements.txt`."
        ) from exc

    # Small modules only (the standard library and QtCore), so the start mark
    # is not delayed. The application modules load in start(), after the mark.
    from tomography_session_browser.diagnostics.crash_logging import install_crash_logging
    from tomography_session_browser.ui.qt_logging import install_qt_message_handler

    install_crash_logging()
    install_qt_message_handler()
    app = QApplication(list(argv or []))
    return start(app)


def start(app: QApplication) -> int:
    """Show the start mark, then import and build the window, then run.

    The mark shows before the application modules are imported. The window
    shows once the mark has had its minimum display interval, and the mark
    closes when the window first paints.
    If the build fails, the mark closes first
    and the error goes on to the crash log, as before the mark.
    """

    from tomography_session_browser.presentation.design_tokens import palette_for
    from tomography_session_browser.services.settings_service import load_settings
    from tomography_session_browser.ui.start_mark import StartMarkHandoff, show_start_mark

    settings = load_settings()
    palette = palette_for(settings.theme)
    handoff = StartMarkHandoff(show_start_mark(palette, settings.window_geometry))
    try:
        from tomography_session_browser.ui.branding import brand_window_icon
        from tomography_session_browser.ui.main_window import MainWindow
        from tomography_session_browser.ui.theme import style_application

        app.setWindowIcon(brand_window_icon())
        style_application(app, palette)
        window = MainWindow(settings=settings)
        handoff.show(window)
        return app.exec()
    finally:
        handoff.close()
