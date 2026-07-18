"""Crash and exception logging.

Installs three layers of diagnostics on application startup so a silent crash
becomes a tractable bug:

* Python's ``sys.excepthook`` and ``threading.excepthook`` route uncaught
  exceptions to a rotating log file under the user's config directory.
* ``faulthandler`` writes a native-stack traceback if the interpreter dies
  from a segfault or fatal Qt error.
* A Qt ``qInstallMessageHandler`` forwards Qt warnings / criticals into the
  same log so paint-event exceptions and OpenGL warnings are captured.

Use:

    from tomography_session_browser.diagnostics import install_crash_logging
    install_crash_logging()

Importing this module has no side effects; the install function is the entry
point so tests don't accidentally redirect their own stderr.
"""

from __future__ import annotations

import faulthandler
import logging
import os
import sys
import threading
import traceback
from datetime import datetime
from pathlib import Path

LOGGER = logging.getLogger(__name__)


def _log_dir() -> Path:
    appdata = os.environ.get("APPDATA")
    if appdata:
        return Path(appdata) / "TomographySessionBrowser" / "logs"
    xdg = os.environ.get("XDG_CACHE_HOME")
    if xdg:
        return Path(xdg) / "TomographySessionBrowser" / "logs"
    return Path.home() / ".cache" / "TomographySessionBrowser" / "logs"


def _setup_file_handler(log_dir: Path) -> Path:
    log_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_path = log_dir / f"session_browser_{timestamp}.log"
    handler = logging.FileHandler(log_path, encoding="utf-8")
    handler.setLevel(logging.INFO)
    handler.setFormatter(
        logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    )
    root = logging.getLogger()
    if root.level > logging.INFO or root.level == logging.NOTSET:
        root.setLevel(logging.INFO)
    root.addHandler(handler)
    return log_path


def install_crash_logging(*, also_stream: bool = True) -> Path:
    """Install all crash hooks. Returns the active log file path.

    ``also_stream`` keeps a stderr handler so developers running the app from a
    terminal still see exceptions on the console.
    """

    log_dir = _log_dir()
    log_path = _setup_file_handler(log_dir)

    if also_stream and not _has_stream_handler():
        stream = logging.StreamHandler()
        stream.setLevel(logging.WARNING)
        stream.setFormatter(logging.Formatter("[%(levelname)s] %(name)s: %(message)s"))
        # Loggers whose warnings are *expected user-data conditions* (rather
        # than software anomalies) — surface them in the file log only, not
        # the terminal. The tilt-series validator legitimately raises one
        # warning per failed tilt series, which is noisy when a session has
        # several aborted acquisitions.
        stream.addFilter(_QuietLoggersFilter({"tomography_session_browser.services.tilt_series_validation"}))
        logging.getLogger().addHandler(stream)

    # Native-stack traceback on segfaults / fatal aborts.
    fh_target = open(log_path, "a", encoding="utf-8")
    try:
        faulthandler.enable(file=fh_target)
    except (RuntimeError, AttributeError) as exc:  # pragma: no cover — defensive
        LOGGER.warning("Could not enable faulthandler: %s", exc)

    # Uncaught Python exceptions.
    def _excepthook(exc_type, exc_value, exc_tb) -> None:
        if issubclass(exc_type, KeyboardInterrupt):
            sys.__excepthook__(exc_type, exc_value, exc_tb)
            return
        LOGGER.error(
            "Uncaught exception:\n%s",
            "".join(traceback.format_exception(exc_type, exc_value, exc_tb)),
        )

    sys.excepthook = _excepthook

    # Worker-thread exceptions (Python 3.8+).
    def _thread_excepthook(args: threading.ExceptHookArgs) -> None:
        LOGGER.error(
            "Uncaught exception in thread %s:\n%s",
            args.thread.name if args.thread else "<unknown>",
            "".join(
                traceback.format_exception(args.exc_type, args.exc_value, args.exc_traceback)
            ),
        )

    threading.excepthook = _thread_excepthook

    _install_qt_message_handler()

    LOGGER.info("Crash logging installed; log file: %s", log_path)
    return log_path


def _has_stream_handler() -> bool:
    return any(
        isinstance(h, logging.StreamHandler) and not isinstance(h, logging.FileHandler)
        for h in logging.getLogger().handlers
    )


class _QuietLoggersFilter(logging.Filter):
    """Drop records from loggers whose warnings shouldn't reach the terminal.

    Records still propagate to other handlers (notably the file handler), so
    the diagnostics log keeps the full picture; only the stderr stream is
    kept quiet.
    """

    def __init__(self, logger_names: set[str]) -> None:
        super().__init__()
        self._silenced = set(logger_names)

    def filter(self, record: logging.LogRecord) -> bool:  # noqa: D401 — Qt-signature-style
        return record.name not in self._silenced


def _install_qt_message_handler() -> None:
    """Forward Qt's own warnings into Python logging, if Qt is importable."""

    try:
        from PySide6.QtCore import QtMsgType, qInstallMessageHandler
    except ImportError:  # pragma: no cover — Qt is a hard dep but stay safe
        return

    severity = {
        QtMsgType.QtDebugMsg: logging.DEBUG,
        QtMsgType.QtInfoMsg: logging.INFO,
        QtMsgType.QtWarningMsg: logging.WARNING,
        QtMsgType.QtCriticalMsg: logging.ERROR,
        QtMsgType.QtSystemMsg: logging.ERROR,
        QtMsgType.QtFatalMsg: logging.CRITICAL,
    }

    qt_logger = logging.getLogger("Qt")

    def handler(msg_type, context, message: str) -> None:
        level = severity.get(msg_type, logging.INFO)
        location = ""
        if context is not None and getattr(context, "file", None):
            location = f" ({context.file}:{context.line})"
        qt_logger.log(level, "%s%s", message, location)

    qInstallMessageHandler(handler)
