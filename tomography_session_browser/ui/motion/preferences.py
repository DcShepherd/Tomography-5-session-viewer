"""Preferences components for the motion."""
from __future__ import annotations
import logging
import os
import subprocess
import sys
import threading
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QWidget


LOGGER = logging.getLogger("tomography_session_browser.ui.motion")


ENV_VAR = "TOMOAPP_REDUCE_MOTION"


TRUTHY = {"1", "true", "yes", "on"}


FALSY = {"0", "false", "no", "off"}


system_reduced_motion_cache: bool | None = None


def query_system_reduced_motion() -> bool:
    """Ask the operating system whether the user turned animations off."""

    try:
        if sys.platform == "win32":
            import ctypes

            spi_get_client_area_animation = 0x1042
            enabled = ctypes.c_int(1)
            ok = ctypes.windll.user32.SystemParametersInfoW(
                spi_get_client_area_animation, 0, ctypes.byref(enabled), 0
            )
            return bool(ok) and not bool(enabled.value)
        if sys.platform == "darwin":
            result = subprocess.run(
                ["defaults", "read", "com.apple.universalaccess", "reduceMotion"],
                capture_output=True,
                text=True,
                timeout=1.0,
                check=False,
            )
            return result.stdout.strip() == "1"
        result = subprocess.run(
            ["gsettings", "get", "org.gnome.desktop.interface", "enable-animations"],
            capture_output=True,
            text=True,
            timeout=1.0,
            check=False,
        )
        return result.stdout.strip() == "false"
    except (OSError, ValueError, subprocess.SubprocessError, AttributeError):
        LOGGER.debug("Could not read the system reduced-motion setting", exc_info=True)
        return False


def system_prefers_reduced_motion(*, refresh: bool = False) -> bool:
    """The OS reduced-motion preference, cached; ``watch_system_reduced_motion``
    refreshes it."""

    global system_reduced_motion_cache  # noqa: PLW0603 - process-wide cache
    if refresh or system_reduced_motion_cache is None:
        system_reduced_motion_cache = query_system_reduced_motion()
    return system_reduced_motion_cache


WATCH_PROPERTY = "_tomo_reduced_motion_watch"


def watch_system_reduced_motion(app: QApplication) -> None:
    """Re-read the OS setting whenever the application becomes active again.

    It was read once per run, so turning Windows' "Animation effects" off
    (or on) took a restart. Changing it means going to another application,
    so coming back to this one is when it may have changed. On Windows the
    read is one system call; elsewhere it runs a small command, so it is
    read on a worker thread and applies from the next animation on.
    Idempotent.
    """

    if app.property(WATCH_PROPERTY):
        return
    app.setProperty(WATCH_PROPERTY, True)
    app.applicationStateChanged.connect(application_state_changed)


def application_state_changed(state: Qt.ApplicationState) -> None:
    if state != Qt.ApplicationState.ApplicationActive:
        return
    if sys.platform == "win32":
        system_prefers_reduced_motion(refresh=True)
        return
    threading.Thread(
        target=system_prefers_reduced_motion,
        kwargs={"refresh": True},
        name="reduced-motion-read",
        daemon=True,
    ).start()


def motion_enabled(widget: QWidget | None = None) -> bool:
    """Whether optional interface motion should run.

    Precedence: a widget's ``_tomo_disable_animations`` opt-out, then the
    ``tomo_reduce_motion`` application property, then ``TOMOAPP_REDUCE_MOTION``
    (truthy reduces; an explicit ``0``/``false`` allows motion regardless of
    the OS, which keeps tests deterministic), then the OS setting.
    """

    app = QApplication.instance()
    if app is None:
        return False
    if widget is not None and widget.property("_tomo_disable_animations"):
        return False
    reduced = app.property("tomo_reduce_motion")
    if reduced is not None:
        return not bool(reduced)
    env_value = os.environ.get(ENV_VAR, "").strip().lower()
    if env_value in TRUTHY:
        return False
    if env_value in FALSY:
        return True
    return not system_prefers_reduced_motion()

