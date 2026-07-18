from __future__ import annotations

from collections.abc import Sequence


def run(argv: Sequence[str] | None = None) -> int:
    try:
        from PySide6.QtWidgets import QApplication
    except ModuleNotFoundError as exc:
        raise SystemExit(
            "PySide6 is not installed. Install dependencies with `pip install -r requirements.txt`."
        ) from exc

    from tomography_session_browser.diagnostics import install_crash_logging
    from tomography_session_browser.services.settings_service import load_settings
    from tomography_session_browser.ui.branding import brand_window_icon
    from tomography_session_browser.ui.main_window import MainWindow
    from tomography_session_browser.ui.theme import apply_theme, palette_for

    install_crash_logging()
    app = QApplication(list(argv or []))
    app.setWindowIcon(brand_window_icon())
    settings = load_settings()
    apply_theme(app, palette_for(settings.theme))
    window = MainWindow(settings=settings)
    window.show()
    return app.exec()
