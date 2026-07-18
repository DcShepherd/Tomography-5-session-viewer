"""Headless GUI perf bench: launch the app offscreen, open a session,
wait for loading to complete, print the perf report, exit.

Captures the metrics the headless :mod:`tools.perf_bench` cannot:

- Main-thread stall samples (``Max UI-thread stall``)
- Dashboard widget build time
- Context-panel update time
- Marker generation time
- Image load time
- Project tree rebuild time
- Dashboard rebuild count

Usage::

    PYTHONPATH=. python tools/gui_perf_bench.py "C:\\path\\to\\session"

Pass ``--display`` to run with a real display (defaults to offscreen).
The script auto-resolves ``.lnk`` shortcuts on Windows.

Exit status: 0 on success, 1 on any uncaught error / timeout.
"""

from __future__ import annotations

import argparse
import logging
import os
from pathlib import Path
import sys

LOGGER = logging.getLogger(__name__)


def _resolve_lnk(path: Path) -> Path:
    if path.suffix.lower() != ".lnk" or not path.exists():
        return path
    try:  # pragma: no cover - platform-specific path
        import win32com.client  # type: ignore

        shell = win32com.client.Dispatch("WScript.Shell")
        target = shell.CreateShortCut(str(path)).TargetPath
        if target:
            return Path(target)
    except Exception:
        pass
    return path


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Launch the GUI offscreen, open a session, print perf report.",
    )
    parser.add_argument(
        "session_path",
        help="Tomography session folder, or a .lnk shortcut to one.",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=120.0,
        help="Max seconds to wait for loading to complete (default 120).",
    )
    parser.add_argument(
        "--display",
        action="store_true",
        help="Run with the system display instead of QT_QPA_PLATFORM=offscreen.",
    )
    parser.add_argument(
        "--log-level",
        default="WARNING",
        help="Library logger level (default WARNING).",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(list(argv if argv is not None else sys.argv[1:]))
    logging.basicConfig(level=args.log_level.upper(), format="%(levelname)s %(name)s: %(message)s")

    session_path = _resolve_lnk(Path(args.session_path).expanduser())
    if not session_path.exists():
        print(f"ERROR: path does not exist: {session_path}", file=sys.stderr)
        return 1

    if not args.display:
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    # The GUI already emits the perf report on ``TOMOAPP_PERF=1``; force-set
    # so the user does not need to remember.
    os.environ["TOMOAPP_PERF"] = "1"

    # Local imports — must happen after the env vars above so Qt picks them up.
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication

    from tomography_session_browser.diagnostics import install_crash_logging
    from tomography_session_browser.services.settings_service import load_settings
    from tomography_session_browser.ui.main_window import MainWindow

    install_crash_logging(also_stream=False)
    app = QApplication.instance() or QApplication([])
    settings = load_settings()
    window = MainWindow(settings=settings)
    # We don't ``show()`` — offscreen doesn't need it, and avoiding paint
    # cycles for a window we never look at keeps the bench focused on
    # parsing/integration cost rather than redundant initial paints.

    # Track completion by watching for the "Loading complete" status, since
    # that's emitted exactly once at the end of the staged loader.
    completed = {"done": False, "error": ""}

    status_bar = window.statusBar()
    original_show_message = status_bar.showMessage

    def _capture_status(message: str, *args: object) -> None:
        original_show_message(message, *args)
        if "Loading complete" in message:
            completed["done"] = True

    status_bar.showMessage = _capture_status  # type: ignore[assignment]

    # Failure path: the load-failed signal is wired up; we listen for a
    # critical message box by monkeypatching the window's signal.
    original_failed = window._on_session_load_failed

    def _capture_failed(path: str, replace: bool, quiet: bool, animate: bool, error: str) -> None:
        completed["done"] = True
        completed["error"] = error
        original_failed(path, replace, quiet, animate, error)

    window._on_session_load_failed = _capture_failed  # type: ignore[assignment]

    # Kick off the load after the event loop starts. Use a single-shot
    # timer so the load is queued from the right thread.
    QTimer.singleShot(0, lambda: window.load_session(str(session_path), replace=True, quiet=True, animate=False))

    # Watchdog: time out after the configured number of seconds.
    deadline_timer = QTimer()
    deadline_timer.setSingleShot(True)
    deadline_timer.timeout.connect(lambda: (completed.__setitem__("error", "timeout"), completed.__setitem__("done", True), app.quit()))
    deadline_timer.start(int(args.timeout * 1000))

    # Poll for completion at ~30 Hz; this is cheap, and the actual work
    # happens on the loader's worker threads which preempt as usual.
    poll = QTimer()
    poll.setInterval(30)
    poll.timeout.connect(lambda: completed["done"] and app.quit())
    poll.start()

    app.exec()

    if completed["error"]:
        print(f"ERROR: {completed['error']}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
