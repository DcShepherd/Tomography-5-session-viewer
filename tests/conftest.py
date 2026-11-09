"""Test-session isolation: from the reviewer's real configuration, and between tests.

Several tests build a ``MainWindow`` and close it or toggle its panels, and
those paths write the application's settings file. Without this redirect every
test run overwrote the real settings under ``%APPDATA%`` (theme, panel
visibility, last tab, recent sessions, and window geometry) with test values.

The config directory is resolved from ``APPDATA`` / ``XDG_CONFIG_HOME`` at call
time (``settings_service._config_dir``), so pointing both at a throwaway folder
before any test runs is enough. Set at import so it precedes collection.
Each independent test then gets its own profile below that root. Windows within
one test still share settings, bindings and review history.
"""

from __future__ import annotations

import atexit
import os
import shutil
import sys
import tempfile
from itertools import count

import pytest

TEST_CONFIG_ROOT = tempfile.mkdtemp(prefix="tomoapp-test-config-")
atexit.register(shutil.rmtree, TEST_CONFIG_ROOT, ignore_errors=True)
os.environ["APPDATA"] = TEST_CONFIG_ROOT
os.environ["XDG_CONFIG_HOME"] = TEST_CONFIG_ROOT
_PROFILE_CASES = count(1)

# Motion follows the operating system's reduced-motion setting (plan F4). Pin
# it to "allowed" so results do not depend on the machine running the tests;
# tests that exercise reduced motion set the variable or app property.
os.environ["TOMOAPP_REDUCE_MOTION"] = "0"

# Many modules set this at import, so a full run is offscreen from collection on.
# Without a default here, a module that does not set it runs natively when run
# alone, and its result then differs from the suite's. Native runs stay available:
# set the variable (for example QT_QPA_PLATFORM=windows) before starting pytest.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


@pytest.fixture(autouse=True)
def _isolate_profile_between_tests():
    """Do not make later history tests scan every earlier test's saved records.

    With one session-wide profile, hundreds of unrelated opened/view/capture
    records accumulated. qWait's event turns stretched those worker scans past
    fixture deadlines, although the same profile completed in the normal Qt
    event loop. Preserve the existing deadline and isolate independent state.
    The short numbered path also avoids extending Windows output filenames.
    """
    keys = ("APPDATA", "XDG_CONFIG_HOME")
    previous = {key: os.environ[key] for key in keys}
    profile = os.path.join(TEST_CONFIG_ROOT, f"case-{next(_PROFILE_CASES):05}")
    for key in keys:
        os.environ[key] = profile
    try:
        yield
    finally:
        for key, value in previous.items():
            os.environ[key] = value


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
    """Put back what ``_theme_state`` recorded.

    A ``None`` means nothing existed to record: the test started Qt or first
    imported the module. Its themed values must still be undone, or every later
    test in the process inherits them (a pytest-xdist worker can begin with such
    a test). So restore the pristine values instead: the platform's general
    font, which is a new application's font, and the dark palette and icon
    colour that ``ui.theme`` and ``ui.icons`` set at import.
    """

    sheet, font, palette, colour = state
    widgets = sys.modules.get("PySide6.QtWidgets")
    app = widgets.QApplication.instance() if widgets else None
    if app is not None:
        if app.styleSheet() != sheet:
            app.setStyleSheet(sheet)
        if font is None:
            from PySide6.QtGui import QFontDatabase

            font = QFontDatabase.systemFont(QFontDatabase.SystemFont.GeneralFont)
        if app.font() != font:
            app.setFont(font)
    theme, icons = sys.modules.get(_THEME_MODULE), sys.modules.get(_ICONS_MODULE)
    if theme is not None or icons is not None:
        from tomography_session_browser.presentation.design_tokens import DARK_PALETTE

        if theme is not None:
            theme._active_palette = palette if palette is not None else DARK_PALETTE
        if icons is not None:
            icons._DEFAULT_COLOR = colour if colour is not None else DARK_PALETTE.icon


def _clear_held_keys() -> None:
    """Forget a modifier key that a simulated input left recorded as held.

    ``QTest`` input updates Qt's record of the held keys, and a click or key press
    with a modifier does not always clear it. A view in ``ExtendedSelection`` reads
    that record, and with Shift held its ``selectRow`` selects nothing, so a test's
    result depended on which test ran before it in the process.
    """

    widgets = sys.modules.get("PySide6.QtWidgets")
    app = widgets.QApplication.instance() if widgets else None
    if app is None:
        return
    from PySide6.QtCore import Qt

    if app.keyboardModifiers() != Qt.KeyboardModifier.NoModifier:
        from PySide6.QtTest import QTest

        QTest.keyRelease(widgets.QWidget(), Qt.Key.Key_Shift)


@pytest.fixture(autouse=True)
def _isolate_qt_state_between_tests(_isolate_profile_between_tests):
    """Delete the windows a test left behind, undo its theme and release its keys.

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

    A finished load freezes the garbage collector's heap (``ui.loading.heap``)
    until the next load or the window's close. A window retired here is never
    closed, so the heap is thawed here too: otherwise a later test's
    ``gc.collect()`` could not free what this test made.
    """

    before = _window_addresses()
    theme_before = _theme_state()
    yield
    left = [window for address, window in _window_addresses().items() if address not in before]
    try:
        if left:
            from PySide6.QtCore import QCoreApplication, QEvent, QThreadPool
            from shiboken6 import Shiboken

            pool = QThreadPool.globalInstance()
            if not pool.waitForDone(10_000):
                pytest.fail("The Qt worker pool did not drain within 10 seconds. "
                            "Test windows were kept alive for the outstanding work.")
            QCoreApplication.processEvents()
            for window in left:
                # The turn above can retire a window itself, such as a pop-up that
                # deletes itself on close.
                if Shiboken.isValid(window):
                    window.deleteLater()
                    QCoreApplication.sendPostedEvents(window, QEvent.Type.DeferredDelete)
    finally:
        from tomography_session_browser.ui.loading import heap

        heap.thaw()
        _restore_theme_state(theme_before)
        _clear_held_keys()
