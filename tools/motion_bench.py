"""Motion benchmarks against the fluid-UI budgets (plan F7).

Drives the real window on real session folders, natively, and measures:

* **The loader** (plans F6.0, F6.2): which renderer drew the orbs; the
  render thread's frame intervals through each load; the GUI thread's waits
  on scene-graph syncs; and how soon the orbs' first frame follows the card.
  The sync waits are the check on the second-window workaround in
  ``ui/widgets/loader_orbs.py``: it relies on Qt internals, and without it
  the waits took 31–45% of each load. **Run this after every Qt upgrade.**
* **The hand-off** (F6.3): frame intervals through the hand-off, and the time
  from the end of a load until the overlay has gone.
* **The theme switch** (F5.8): how soon the seed shows at the theme button,
  when the radial reveal starts, its frame intervals, and when it lands; to
  the other theme and back.

Budgets marked *gate* fail the run (exit status 1); *target* budgets are
reported only (the reveal's start is known to miss its target). A required
measurement that is missing, or rests on too few frames to judge, fails the
run too: an empty measurement is never read as a fast one. Run from the
repository root in the project Conda environment, alone, with nothing else
busy on the machine::

    python tools/motion_bench.py --session PATH [--session PATH ...]
        [--size 1600x960] [--theme dark] [--warm-up 4] [--out DIR]

``--warm-up 0`` loads before the loader's Qt Quick windows are readied;
``TOMOAPP_LOADER=painter`` measures the painted orbs a remote desktop gets
(reported, since their frame budget does not apply).

The benchmark re-runs itself in a child process with Qt's render-loop log on
(the render thread's frames and the GUI thread's sync waits are read from
it, so the measurement never runs Python on the render thread), and writes
``report.json`` and the log to the output folder. A window appears on screen
while it runs. Session folders are only read, and settings writes are stubbed,
as in ``tools/ui_capture.py``.
"""

from __future__ import annotations

import argparse
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import datetime
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

LOAD_TIMEOUT_S = 600
WARM_UP_S = 4.0  # the loader's Qt Quick windows are readied after start-up
SETTLE_MS = 1200
_SYNC = re.compile(r"gui thread\] Frame prepared, .*blockedForSync=(\d+) ms")
_CHILD_FLAG = "--child"
#: Fewer frame intervals than this say nothing about a frame budget.
MIN_FRAME_INTERVALS = 5


@dataclass
class Check:
    name: str
    #: None when the measurement is missing: that fails the run, gate or not.
    measured: float | None
    limit: float
    unit: str = "ms"
    gate: bool = True
    note: str = ""

    @property
    def missing(self) -> bool:
        return self.measured is None

    @property
    def passed(self) -> bool:
        return self.measured is not None and self.measured <= self.limit


def _stats(intervals: list[float]) -> dict[str, float | None]:
    if not intervals:
        return {"count": 0, "median": None, "p95": None, "worst": None}
    ordered = sorted(intervals)
    return {
        "count": len(ordered),
        "median": round(ordered[len(ordered) // 2], 1),
        "p95": round(ordered[int(0.95 * (len(ordered) - 1))], 1),
        "worst": round(ordered[-1], 1),
    }


def _frame_checks(name: str, stats: dict, *, typical: str, typical_limit: float, what: str) -> list[Check]:
    """A typical-frame and a worst-frame check, missing if too few frames."""

    enough = stats["count"] >= MIN_FRAME_INTERVALS
    note = "" if enough else f"missing: {stats['count']} {what} intervals, at least {MIN_FRAME_INTERVALS} needed"
    return [
        Check(f"{name} {typical}", stats[typical] if enough else None, typical_limit, note=note),
        Check(f"{name} worst", stats["worst"] if enough else None, 50.0, note=note),
    ]


def _gaps(times: list[float]) -> list[float]:
    return [1000.0 * (later - earlier) for earlier, later in zip(times, times[1:])]


def _connect_until_undone(signal, slot: Callable[[], None]) -> Callable[[], None]:
    """Connect ``slot`` to ``signal``; return what disconnects it, and only it.

    A bare ``disconnect()`` also cut the loader's own listener, which left
    the next load's orbs "starting" for good.
    """

    signal.connect(slot)

    def undo() -> None:
        try:
            signal.disconnect(slot)
        except (RuntimeError, TypeError):
            pass

    return undo


# -- the child: drives the window --------------------------------------------------


def _run_child(args: argparse.Namespace) -> int:
    from PySide6.QtCore import QTimer, qInfo
    from PySide6.QtWidgets import QApplication

    from tomography_session_browser.services.settings_service import Settings
    from tomography_session_browser.ui import main_window as main_window_module
    from tomography_session_browser.ui.animations import animations_enabled
    from tomography_session_browser.ui.motion import TICKER, motion_enabled
    from tomography_session_browser.ui.theme import apply_theme, palette_for

    # Never touch the reviewer's real settings file.
    main_window_module.save_settings = lambda *_a, **_k: None
    main_window_module.add_recent_session = lambda settings, *_a, **_k: settings
    app = QApplication(sys.argv[:1])
    apply_theme(app, palette_for(args.theme))
    window = main_window_module.MainWindow(settings=Settings(theme=args.theme))
    overlay = window.loading_overlay
    results: dict = {"renderer": None, "loads": [], "themes": []}
    now = time.perf_counter

    # The GUI thread's own lateness (informational: the app's work, not motion).
    gui_ticks: list[float] = []
    monitor = QTimer()
    monitor.setInterval(5)
    monitor.timeout.connect(lambda: gui_ticks.append(now()))

    marks: dict[str, float] = {}
    hand_off_frames: list[float] = []
    real_hand_off_update = overlay._hand_off._on_update  # noqa: SLF001

    def hand_off_update(progress: float) -> None:
        hand_off_frames.append(now())
        real_hand_off_update(progress)

    overlay._hand_off._on_update = hand_off_update  # noqa: SLF001
    real_finish = window._finish_loading  # noqa: SLF001

    def finish_loading(*a, **k):
        marks.setdefault("finished", now())
        return real_finish(*a, **k)

    window._finish_loading = finish_loading  # noqa: SLF001
    overlay.hidden.connect(lambda: marks.setdefault("gone", now()))
    real_start_quick = overlay._start_quick  # noqa: SLF001

    def start_quick() -> None:
        marks.setdefault("orbs_shown", now())
        real_start_quick()

    overlay._start_quick = start_quick  # noqa: SLF001

    ticker_frames: list[float] = []
    real_advance = TICKER.advance

    def advance(*a, **k):
        reveal = window.theme_reveal
        if reveal is not None and reveal.revealing:
            ticker_frames.append(now())
        return real_advance(*a, **k)

    TICKER.advance = advance
    real_start_reveal = window._start_theme_reveal  # noqa: SLF001

    def start_reveal(palette):
        reveal = real_start_reveal(palette)
        marks.setdefault("seed", now())
        return reveal

    window._start_theme_reveal = start_reveal  # noqa: SLF001
    real_landed = window._theme_reveal_landed  # noqa: SLF001

    def landed() -> None:
        marks.setdefault("landed", now())
        real_landed()

    window._theme_reveal_landed = landed  # noqa: SLF001

    def tour():
        width, height = (int(value) for value in args.size.lower().split("x"))
        window.resize(width, height)
        window.show()
        monitor.start()
        yield max(1, int(args.warm_up * 1000))
        results["renderer"] = {"kind": overlay.renderer.kind}
        # Without motion there is no hand-off or reveal to measure.
        results["motion"] = {"hand_off": animations_enabled(overlay), "reveal": motion_enabled(window)}
        for index, session in enumerate(args.session):
            marks.clear()
            hand_off_frames.clear()
            qInfo(f"MARK load{index}_start")
            started = now()
            window.load_session(session, replace=index == 0)
            # Connected before the event loop runs again, so before the orbs'
            # first frame can be reported, even if the load created them.
            quick = overlay._quick  # noqa: SLF001
            undo_ready = (
                _connect_until_undone(quick.ready, lambda: marks.setdefault("orbs_first_frame", now()))
                if quick is not None
                else None
            )
            yield 50
            while window._loading_overlay_active() and now() - started < LOAD_TIMEOUT_S:  # noqa: SLF001
                yield 20
            qInfo(f"MARK load{index}_end")
            ended = now()
            while overlay.isVisible() and now() - started < LOAD_TIMEOUT_S:
                yield 10
            if undo_ready is not None:
                undo_ready()
            gui =[gap for gap, at in zip(_gaps(gui_ticks), gui_ticks[1:]) if started <= at <= ended]
            results["loads"].append(
                {
                    "session": Path(session).name,
                    "seconds": round(ended - started, 2),
                    "renderer": overlay.renderer.kind,
                    "renderer_reason": overlay.renderer.reason,
                    "orbs_first_frame_ms": (
                        round(1000 * (marks["orbs_first_frame"] - marks["orbs_shown"]), 1)
                        if "orbs_first_frame" in marks and "orbs_shown" in marks
                        else None
                    ),
                    "painted_orbs": overlay.performance_snapshot(),
                    "gui_worst_gap_ms": round(max(gui, default=0.0), 1),
                    "hand_off": _stats(_gaps(hand_off_frames)),
                    "finish_to_gone_ms": (
                        round(1000 * (marks["gone"] - marks["finished"]), 1)
                        if "gone" in marks and "finished" in marks
                        else None
                    ),
                }
            )
            yield 800
        window.toast.dismiss()
        for target in ("light" if args.theme == "dark" else "dark", args.theme):
            marks.clear()
            ticker_frames.clear()
            clicked = now()
            window.theme_action.setChecked(target == "light")
            yield 50
            while window.theme_reveal is not None and now() - clicked < 10:
                yield 10
            yield 300
            results["themes"].append(
                {
                    "to": target,
                    "seed_ms": round(1000 * (marks["seed"] - clicked), 1) if "seed" in marks else None,
                    "reveal_start_ms": round(1000 * (ticker_frames[0] - clicked), 1) if ticker_frames else None,
                    "landed_ms": round(1000 * (marks["landed"] - clicked), 1) if "landed" in marks else None,
                    "frames": _stats(_gaps(ticker_frames)),
                }
            )
            yield SETTLE_MS
        args.out.joinpath("child.json").write_text(json.dumps(results, indent=2), encoding="utf-8")
        window.close()
        app.quit()

    steps = tour()

    def step() -> None:
        try:
            QTimer.singleShot(int(next(steps)), step)
        except StopIteration:
            pass
        except Exception:  # noqa: BLE001 - a benchmark must not idle on an error
            import traceback

            traceback.print_exc()
            app.exit(2)

    QTimer.singleShot(0, step)
    return app.exec()


# -- the parent: reads the log, checks the budgets --------------------------------------


def _read_log(path: Path) -> tuple[dict[str, float], dict[str, list[float]], list[tuple[float, int]], list[str]]:
    marks: dict[str, float] = {}
    frames: dict[str, list[float]] = {}
    syncs: list[tuple[float, int]] = []
    info: list[str] = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        parts = line.split("|", 3)
        if len(parts) < 4:
            continue
        stamp, category, thread, message = parts
        try:
            at = float(stamp.strip())
        except ValueError:
            continue
        if message.startswith("MARK "):
            marks[message[5:].strip()] = at
        elif "renderloop" in category and "render thread" in message and "frame rendered" in message.lower():
            frames.setdefault(thread, []).append(at)
        elif (match := _SYNC.search(message)) is not None:
            syncs.append((at, int(match.group(1))))
        elif category.startswith("qt.scenegraph.general") and "render loop" in message:
            info.append(message.strip())
    return marks, frames, syncs, sorted(set(info))


def _checks(child: dict, marks, frames, syncs) -> tuple[list[Check], list[dict]]:
    """The budgets, from the child's results and the render-loop log.

    Every measurement a run should produce is required: one that is missing
    becomes a check with no value, which fails the run.
    """

    checks: list[Check] = []
    loads: list[dict] = []
    motion = child.get("motion", {"hand_off": True, "reveal": True})
    for index, load in enumerate(child["loads"]):
        start, end = marks.get(f"load{index}_start"), marks.get(f"load{index}_end")
        name = load["session"]
        row = dict(load)
        if load["renderer"] == "quick":
            inside: list[float] = []
            samples: list[int] = []
            if start is not None and end is not None:
                # The orbs' thread draws every frame; the helper window's, once.
                thread = max(frames, key=lambda key: sum(1 for at in frames[key] if start <= at <= end), default=None)
                inside = [at for at in frames.get(thread, []) if start <= at <= end]
                samples = [ms for at, ms in syncs if start <= at <= end]
            row["orb_frames"] = _stats(_gaps(inside))
            share = None
            if samples:
                share = round(100.0 * sum(samples) / 1000.0 / max(1e-6, end - start), 1)
                row["gui_sync_wait_ms"] = sum(samples)
                row["gui_sync_wait_percent"] = share
            no_marks = "missing: the load's marks are not in the log" if start is None or end is None else ""
            orb_checks = _frame_checks(f"{name}: orb frames", row["orb_frames"], typical="p95", typical_limit=20.0, what="orb frame")
            for check in orb_checks if no_marks else ():
                check.note = no_marks
            checks += orb_checks
            checks += [
                Check(
                    f"{name}: GUI waits on syncs",
                    share,
                    10.0,
                    "%",
                    note=(
                        "31-45% without the second-window workaround"
                        if share is not None
                        else no_marks or "missing: no scene-graph syncs logged during the load"
                    ),
                ),
                Check(
                    f"{name}: orbs' first frame after showing",
                    load["orbs_first_frame_ms"],
                    150.0,
                    note="" if load["orbs_first_frame_ms"] is not None else "missing: the orbs never reported a frame",
                ),
            ]
        else:
            checks.append(
                Check(
                    f"{name}: renderer",
                    0.0,
                    -1.0,
                    "",
                    gate=False,
                    note=f"painted orbs ({load['renderer_reason']}): the frame budget does not apply",
                )
            )
        if motion["hand_off"]:
            checks += _frame_checks(
                f"{name}: hand-off frames", load["hand_off"], typical="median", typical_limit=20.0, what="hand-off frame"
            )
        loads.append(row)
    for theme in child["themes"]:
        label = f"to {theme['to']}"
        checks.append(
            Check(
                f"theme {label}: seed shows",
                theme["seed_ms"],
                150.0,
                note="" if theme["seed_ms"] is not None else "missing: no seed or reveal was started",
            )
        )
        if not motion["reveal"]:
            continue  # reduced motion: the seed alone, and no reveal
        checks.append(
            Check(
                f"theme {label}: reveal starts",
                theme["reveal_start_ms"],
                250.0,
                gate=False,
                note=(
                    "target; left: restyling what is visible, and the two window pictures"
                    if theme["reveal_start_ms"] is not None
                    else "missing: the reveal never grew"
                ),
            )
        )
        checks += _frame_checks(
            f"theme {label}: reveal frames", theme["frames"], typical="p95", typical_limit=25.0, what="reveal frame"
        )
    return checks, loads


def _run_parent(args: argparse.Namespace) -> int:
    args.out.mkdir(parents=True, exist_ok=True)
    log = args.out / "renderloop.log"
    env = dict(os.environ)
    env.update(
        {
            "QSG_INFO": "1",
            "QT_FORCE_STDERR_LOGGING": "1",  # else Qt logs to OutputDebugString
            "QT_LOGGING_RULES": "qt.scenegraph.time.renderloop.debug=true",
            "QT_MESSAGE_PATTERN": "%{time process}|%{category}|%{threadid}|%{message}",
            "TOMOAPP_PERF": "0",
        }
    )
    # The output folder is passed on: a default taken from the clock in each
    # process differed when the second ticked over between them.
    command = [sys.executable, str(Path(__file__).resolve()), _CHILD_FLAG, *sys.argv[1:], "--out", str(args.out)]
    print(f"[motion_bench] running {len(args.session)} load(s) and two theme switches ...", flush=True)
    with log.open("w", encoding="utf-8") as stderr:
        completed = subprocess.run(command, env=env, stderr=stderr, check=False, timeout=LOAD_TIMEOUT_S * 2)
    child_json = args.out / "child.json"
    if completed.returncode != 0 or not child_json.exists():
        print(f"[motion_bench] the run failed (exit {completed.returncode}); see {log}", flush=True)
        return 2
    child = json.loads(child_json.read_text(encoding="utf-8"))
    marks, frames, syncs, info = _read_log(log)
    checks, loads = _checks(child, marks, frames, syncs)
    lines, status = _summarise(checks)
    report = {
        "when": datetime.now().isoformat(timespec="seconds"),
        "scene_graph": info,
        "complete": not any(check.missing for check in checks),
        "loads": loads,
        "themes": child["themes"],
        "checks": [asdict(check) | {"passed": check.passed, "missing": check.missing} for check in checks],
    }
    (args.out / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"[motion_bench] scene graph: {', '.join(info) or 'no Qt Quick'}")
    for load in loads:
        orb = load.get("orb_frames", {})
        if load["renderer"] != "quick":
            # The painted orbs' own timer: reported, not judged.
            painted = load.get("painted_orbs") or {}
            orb = {"median": painted.get("median_interval_ms", "-"), "p95": "-", "worst": painted.get("max_interval_ms", "-")}
        print(
            f"  {load['session']}: {load['seconds']} s, {load['renderer']}; "
            f"GUI worst gap {load.get('gui_worst_gap_ms', '-')} ms; "
            f"orb frames median {orb.get('median', '-')} / p95 {orb.get('p95', '-')} / worst {orb.get('worst', '-')} ms; "
            f"GUI sync waits {load.get('gui_sync_wait_ms', '-')} ms ({load.get('gui_sync_wait_percent', '-')}%); "
            f"hand-off median {load['hand_off']['median']} / worst {load['hand_off']['worst']} ms; "
            f"finish to gone {load['finish_to_gone_ms']} ms"
        )
    for theme in child["themes"]:
        print(
            f"  theme to {theme['to']}: seed {theme['seed_ms']} ms, reveal starts {theme['reveal_start_ms']} ms, "
            f"lands {theme['landed_ms']} ms; frames median {theme['frames']['median']} / p95 {theme['frames']['p95']} "
            f"/ worst {theme['frames']['worst']} ms"
        )
    for line in lines:
        print(line)
    print(f"[motion_bench] report in {args.out}", flush=True)
    return status


def _summarise(checks: list[Check]) -> tuple[list[str], int]:
    """One line per check and a verdict, and the exit status.

    1 if a gate is missed or any measurement is missing (the run cannot
    vouch for what it did not measure), else 0.
    """

    lines = []
    for check in checks:
        if check.missing:
            verdict, value = "MISSING", "no value"
        else:
            verdict = "ok" if check.passed else ("FAIL" if check.gate else "miss")
            value = f"{check.measured} {check.unit}"
        kind = "" if check.gate else " (target)"
        note = f"  {check.note}" if check.note else ""
        lines.append(f"  [{verdict}] {check.name}: {value} (budget {check.limit} {check.unit}){kind}{note}")
    gates = [check for check in checks if check.gate]
    missing = [check for check in checks if check.missing]
    failed = [check for check in gates if not check.missing and not check.passed]
    missed = [check for check in checks if not check.gate and not check.missing and not check.passed]
    met = sum(1 for check in gates if check.passed)
    lines.append(
        f"[motion_bench] {met} of {len(gates)} budgets met, {len(failed)} failed, "
        f"{len(missing)} measurement(s) missing, {len(missed)} target(s) missed"
        + ("; INCOMPLETE: the run did not measure everything it should" if missing else "")
    )
    return lines, 1 if failed or missing else 0


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--session", action="append", required=True, help="Session folder (repeatable).")
    parser.add_argument("--size", default="1600x960", help="Logical window size WxH.")
    parser.add_argument("--theme", choices=("dark", "light"), default="dark")
    parser.add_argument(
        "--warm-up",
        type=float,
        default=WARM_UP_S,
        help="Seconds between showing the window and the first load (0: before the loader is readied).",
    )
    parser.add_argument("--out", type=Path, default=None, help="Output folder (default tmp/motion_bench/<time>).")
    args = parser.parse_args(argv)
    args.out = args.out or REPOSITORY_ROOT / "tmp" / "motion_bench" / datetime.now().strftime("%Y%m%d-%H%M%S")
    return args


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    child = _CHILD_FLAG in argv
    if child:
        argv.remove(_CHILD_FLAG)
    args = _parse_args(argv)
    return _run_child(args) if child else _run_parent(args)


if __name__ == "__main__":
    raise SystemExit(main())
