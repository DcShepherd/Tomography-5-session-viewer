"""Headless performance bench for Tomography Session Browser.

Loads a session folder through :class:`SessionLoader`, then exercises the
heavy non-widget presenter models (dashboard, timeline, warnings). Prints
the developer perf report at the end.

This does NOT stand up Qt, so it can't measure widget construction, plot
rendering, marker overlay generation, or main-thread stalls. For those,
launch the GUI with ``TOMOAPP_PERF=1``.

Usage
-----

::

    python tools/perf_bench.py "C:\\path\\to\\session_folder"

The script accepts a ``.lnk`` file path on Windows and resolves it
automatically when the ``pywin32`` package or ``WScript.Shell`` is
available; otherwise pass the resolved target directly.

Exit status: 0 on success, 1 on any uncaught error.
"""

from __future__ import annotations

import argparse
import logging
from pathlib import Path
import sys
import time


def _resolve_lnk(path: Path) -> Path:
    """Best-effort .lnk resolution on Windows. Falls back to the original."""

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
        description="Headless performance bench for session loading."
    )
    parser.add_argument(
        "session_path",
        help="Path to a tomography session folder, or a .lnk shortcut to one.",
    )
    parser.add_argument(
        "--skip-dashboard",
        action="store_true",
        help="Skip building the presenter dashboard/timeline (measure parsing only).",
    )
    parser.add_argument(
        "--log-level",
        default="WARNING",
        help="Logging level for library loggers (default: WARNING).",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(list(argv if argv is not None else sys.argv[1:]))
    logging.basicConfig(level=args.log_level.upper(), format="%(levelname)s %(name)s: %(message)s")

    # Local imports keep argument parsing fast and avoid loading PySide6.
    from tomography_session_browser.services.loading_profiler import (
        LoadingProfiler,
        active_profile,
    )
    from tomography_session_browser.services.session_loader import SessionLoader

    session_path = _resolve_lnk(Path(args.session_path).expanduser())
    if not session_path.exists():
        print(f"ERROR: path does not exist: {session_path}", file=sys.stderr)
        return 1

    profile = LoadingProfiler()
    wall_start = time.perf_counter()

    with profile.phase("bench_total"), active_profile(profile):
        session = SessionLoader().load(session_path, profile=profile)

        if not args.skip_dashboard:
            # Lazy import: pulls UI presenter, which is heavy.
            from tomography_session_browser.services.timeline_service import (
                build_session_timeline,
            )
            from tomography_session_browser.ui.session_presenter import (
                session_dashboard_model,
                session_warnings,
            )

            scope_value = session if session is not None else None
            if scope_value is not None:
                with profile.phase("prepare_dashboard_model"):
                    session_dashboard_model(scope_value)
                with profile.phase("build_timeline_model"):
                    build_session_timeline(session)
                with profile.phase("prepare_warning_groups"):
                    session_warnings(session)

    wall_seconds = time.perf_counter() - wall_start

    counts = session.metadata_summary.get("counts", {}) if session is not None else {}
    print(f"Bench wall time:           {wall_seconds:6.2f} s", file=sys.stderr)
    print(
        "Counts: " + ", ".join(f"{key}={value}" for key, value in sorted(counts.items())),
        file=sys.stderr,
    )
    print("", file=sys.stderr)
    print(profile.format_report(path=str(session_path)), file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
