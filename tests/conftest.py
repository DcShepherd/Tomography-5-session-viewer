"""Test-session isolation: from the reviewer's real configuration, and between tests.

Several tests build a ``MainWindow`` and close it or toggle its panels, and
those paths write the application's settings file. Without this redirect every
test run overwrote the real settings under ``%APPDATA%`` (theme, panel
visibility, last tab, recent sessions, and window geometry) with test values.

The config directory is resolved from ``APPDATA`` / ``XDG_CONFIG_HOME`` at call
time (``settings_service._config_dir``), so pointing both at a throwaway folder
before any test runs is enough. Set at import so it precedes collection.
"""

from __future__ import annotations

import atexit
import os
import shutil
import sys
import tempfile

import pytest

TEST_CONFIG_ROOT = tempfile.mkdtemp(prefix="tomoapp-test-config-")
atexit.register(shutil.rmtree, TEST_CONFIG_ROOT, ignore_errors=True)
os.environ["APPDATA"] = TEST_CONFIG_ROOT
os.environ["XDG_CONFIG_HOME"] = TEST_CONFIG_ROOT

# Motion follows the operating system's reduced-motion setting (plan F4). Pin
# it to "allowed" so results do not depend on the machine running the tests;
# tests that exercise reduced motion set the variable or app property.
os.environ["TOMOAPP_REDUCE_MOTION"] = "0"


def _window_addresses() -> dict[int, object]:
    """The process's parentless windows, by C++ address; empty before Qt starts."""

    widgets = sys.modules.get("PySide6.QtWidgets")
    app = widgets.QApplication.instance() if widgets else None
    if app is None:
        return {}
    from shiboken6 import Shiboken

    return {
        Shiboken.getCppPointer(window)[0]: window
        for window in app.topLevelWidgets()
        if window.parentWidget() is None
    }


_THEME_MODULE = "tomography_session_browser.ui.theme"
_ICONS_MODULE = "tomography_session_browser.ui.icons"


def _theme_state() -> tuple:
    """What ``ui.theme.apply_theme`` changes: the application's style sheet and
    font (``None`` before Qt starts, when the default is no sheet), the active
    palette and the default icon colour (``None`` before their modules load)."""

    widgets = sys.modules.get("PySide6.QtWidgets")
    app = widgets.QApplication.instance() if widgets else None
    theme, icons = sys.modules.get(_THEME_MODULE), sys.modules.get(_ICONS_MODULE)
    return (
        app.styleSheet() if app else "",
        app.font() if app else None,
        theme._active_palette if theme else None,
        icons._DEFAULT_COLOR if icons else None,
    )


def _restore_theme_state(state: tuple) -> None:
    sheet, font, palette, colour = state
    widgets = sys.modules.get("PySide6.QtWidgets")
    app = widgets.QApplication.instance() if widgets else None
    if app is not None:
        if app.styleSheet() != sheet:
            app.setStyleSheet(sheet)
        if font is not None and app.font() != font:
            app.setFont(font)
    if palette is not None:
        sys.modules[_THEME_MODULE]._active_palette = palette
    if colour is not None:
        sys.modules[_ICONS_MODULE]._DEFAULT_COLOR = colour


@pytest.fixture(autouse=True)
def _isolate_qt_state_between_tests():
    """Delete the windows a test left behind and undo its theme.

    Most GUI tests build a window or widget and let it go out of scope, but Qt
    owns a parentless widget until it is deleted, so every one stayed alive:
    after a few hundred tests the process held tens of thousands of widgets,
    and each later theme switch, restyle or event-loop turn walked them all.
    A theme a test applied stayed on the application too, and every later
    window was built under the full style sheet, about 2.5 times slower. A
    file that runs in three seconds on its own took over three minutes in the
    suite. Each test now starts from the state it would have run alone in;
    a test that needs a theme applies it, as it must to pass on its own.

    Only windows that appeared during the test are deleted. Module-scoped
    fixtures are set up before this one, so a window they share survives.
    Work the test left queued for a window (background tasks, zero-delay
    timers) runs first, while the window still exists: a timer calling a Qt
    method on a deleted widget crashes the process. Each window is then
    retired on its own (``deleteLater`` then a targeted ``DeferredDelete``),
    never by a global flush that would run other objects' deferred work.
    """

    before = _window_addresses()
    theme_before = _theme_state()
    yield
    left = [window for address, window in _window_addresses().items() if address not in before]
    if left:
        from PySide6.QtCore import QCoreApplication, QEvent, QThreadPool

        QThreadPool.globalInstance().waitForDone(10_000)
        QCoreApplication.processEvents()
        for window in left:
            window.deleteLater()
            QCoreApplication.sendPostedEvents(window, QEvent.Type.DeferredDelete)
    _restore_theme_state(theme_before)
