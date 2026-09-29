"""Native UI capture harness for visual review (plan phase F0).

Drives the real ``MainWindow`` on real session folders and saves a screenshot
of every tab at each requested window size and theme, plus each load's time
and the loader's renderer (frame timing here covers only the painted
fallback; ``tools/motion_bench.py`` measures the Qt Quick orbs). Use it to
compare before/after a UI change.

Run from the repository root in the project Conda environment::

    python tools/ui_capture.py --session PATH [--session PATH ...]
        [--size 1600x960] [--size max] [--theme dark] [--theme light]
        [--out DIR]

* The first ``--session`` replaces the project; later ones are imported, so an
  atlas session plus its data-collection session forms a linked group.
* ``--size`` is a logical window size ``WxH`` or ``max`` (maximised). The
  window loads at the first size and is then resized, so previews loaded at one
  size are observed after a resize, the way a reviewer maximises a window.
* Captures use the native platform plugin, not ``offscreen``, so text renders
  with real fonts. A window appears on screen while the harness runs.
* Session folders are only read. Settings writes are stubbed, so the real
  settings file is never modified.
* Tabs are switched the way a click switches them. On each viewer tab the
  harness checks that the context panel describes the displayed item, and
  lists any tab where it does not under ``context_mismatches`` in the report.

Emulating other displays on one machine: Qt lays out in logical pixels, so set
``QT_SCALE_FACTOR`` before running. On a 150%-scaled laptop,
``QT_SCALE_FACTOR=0.6667`` gives device-pixel-ratio 1.0, which matches a 1080p
or 2K monitor at 100% (and 4K at 200% or 150% respectively, in layout terms).
Windows cannot exceed the physical screen, so very large logical sizes need a
smaller factor (for example 0.4 for 3840×2112), at the cost of glyph fidelity.
"""

from __future__ import annotations

import argparse
from datetime import datetime
import json
from pathlib import Path
import sys
import time

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from PySide6.QtCore import QThreadPool, QTimer  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from tomography_session_browser.services.settings_service import Settings  # noqa: E402
from tomography_session_browser.ui import main_window as main_window_module  # noqa: E402
from tomography_session_browser.ui.branding import brand_window_icon  # noqa: E402
from tomography_session_browser.ui.main_window import TAB_LABELS, MainWindow  # noqa: E402
from tomography_session_browser.ui.theme import apply_theme, palette_for  # noqa: E402

LOAD_TIMEOUT_S = 600
SETTLE_MS = 1200


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--session", action="append", required=True, help="Session folder (repeatable).")
    parser.add_argument("--size", action="append", default=None, help="WxH or 'max' (repeatable).")
    parser.add_argument("--theme", action="append", choices=("dark", "light"), default=None)
    parser.add_argument("--out", type=Path, default=None, help="Output folder (default tmp/ui_capture/<time>).")
    args = parser.parse_args(argv)
    args.size = args.size or ["1600x960"]
    args.theme = args.theme or ["dark"]
    args.out = args.out or REPOSITORY_ROOT / "tmp" / "ui_capture" / datetime.now().strftime("%Y%m%d-%H%M%S")
    return args


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(sys.argv[1:] if argv is None else argv)
    args.out.mkdir(parents=True, exist_ok=True)

    # Never touch the reviewer's real settings file.
    main_window_module.save_settings = lambda *_a, **_k: None
    main_window_module.add_recent_session = lambda settings, *_a, **_k: settings

    app = QApplication.instance() or QApplication(sys.argv[:1])
    app.setWindowIcon(brand_window_icon())
    apply_theme(app, palette_for(args.theme[0]))
    window = MainWindow(settings=Settings(theme=args.theme[0]))
    report: dict = {
        "dpr": app.primaryScreen().devicePixelRatio(),
        "screen_available": app.primaryScreen().availableGeometry().getRect(),
        "loads": [],
        "captures": [],
        "context_mismatches": [],
    }

    def apply_size(size: str) -> None:
        if size == "max":
            window.showMaximized()
            return
        width, height = (int(value) for value in size.lower().split("x"))
        window.showNormal()
        window.resize(width, height)

    def capture(name: str) -> None:
        # A toast is news that fades on its own; it would only differ
        # between runs (plan F5.9).
        window.toast.dismiss()
        path = args.out / f"{name}.png"
        pixmap = window.grab()
        pixmap.save(str(path), "PNG")
        report["captures"].append(path.name)
        print(f"[ui_capture] {path.name} {pixmap.width()}x{pixmap.height()}", flush=True)

    def check_context(name: str, viewer_tab) -> None:
        # The context panel must describe the image on screen.
        value = getattr(viewer_tab, "_current_value", None)
        if value is None or window.context_panel._raw_text == window._context_description(value):  # noqa: SLF001
            return
        report["context_mismatches"].append(name)
        print(f"[ui_capture] MISMATCH {name}: context panel does not describe the displayed item", flush=True)

    def tour():
        apply_size(args.size[0])
        window.show()
        yield 600
        for index, session in enumerate(args.session):
            started = time.monotonic()
            window.load_session(session, replace=index == 0)
            yield 100
            while window._loading_overlay_active() and time.monotonic() - started < LOAD_TIMEOUT_S:  # noqa: SLF001
                yield 50
            # The overlay then hands over to the interface (plan F6.3).
            while window.loading_overlay.isVisible() and time.monotonic() - started < LOAD_TIMEOUT_S:
                yield 50
            load = {
                "session": session,
                "seconds": round(time.monotonic() - started, 2),
                "loader": window.loading_overlay.performance_snapshot(),
            }
            report["loads"].append(load)
            print(f"[ui_capture] loaded {Path(session).name} {load}", flush=True)
        yield 800
        if window.tree.topLevelItemCount():
            window.tree.setCurrentItem(window.tree.topLevelItem(0))
        for theme_index, theme in enumerate(args.theme):
            if theme_index or theme != args.theme[0]:
                window.theme_action.setChecked(theme == "light")
                yield SETTLE_MS
                # The switch spreads out from the button (plan F5.8b).
                while window.theme_reveal is not None:
                    yield 50
            for size in args.size:
                apply_size(size)
                yield SETTLE_MS
                for tab_index, label in enumerate(TAB_LABELS):
                    # The app loads the tab's preview itself, as it does for
                    # a click. Loading it here as well announced a selection,
                    # which hid a context panel left describing another tab.
                    window.tabs.setCurrentIndex(tab_index)
                    viewer_tab = window._viewer_tabs.get(label)  # noqa: SLF001
                    if viewer_tab is not None:
                        QThreadPool.globalInstance().waitForDone(8000)
                    yield SETTLE_MS
                    name = f"{theme}_{size}_{tab_index}_{label.lower().replace(' ', '_')}"
                    check_context(name, viewer_tab)
                    capture(name)
        (args.out / "report.json").write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
        print(f"[ui_capture] wrote {len(report['captures'])} captures to {args.out}", flush=True)
        window.close()
        app.quit()

    steps = tour()

    def step() -> None:
        try:
            delay = next(steps)
        except StopIteration:
            return
        QTimer.singleShot(int(delay), step)

    QTimer.singleShot(0, step)
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
