"""Qt's own warnings, in the app's log.

``install_crash_logging`` (``diagnostics/crash_logging.py``) sets up the log
file and the Python hooks; this forwards Qt warnings and criticals into the
same log, so paint-event exceptions and OpenGL warnings are captured too.
Call it where the app starts Qt, after ``install_crash_logging``.
"""

from __future__ import annotations

import logging

from PySide6.QtCore import QtMsgType, qInstallMessageHandler


def install_qt_message_handler() -> None:
    """Forward Qt's own warnings into Python logging."""

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
