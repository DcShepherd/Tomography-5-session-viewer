"""The loading overlay's thinking orbs (plan F6.2).

Soft metaballs drift on slow, never-repeating paths, merging and splitting
like liquid (the F6.0 trial's look). The motion carries the load's state: the
orbs draw closer while the load links and builds, and briefly quicken at each
stage change.

Two renderers draw the same orbs:

* :class:`QuickOrbs`: Qt Quick, with the shader in ``assets/loader``,
  animated by Animators on the scene graph's render thread. A loader painted
  on the GUI thread stops 10+ times per load, while that thread builds the
  interface (plan A1); this one keeps moving.
* :func:`orb_image`: numpy on the GUI thread. It is the fallback (no render
  thread: offscreen, remote desktop, software rendering), the still picture
  under reduced motion, and the picture under the Quick window until its first
  frame and during the overlay's fade.

:func:`choose_renderer` picks one. Nothing here reads or changes session data.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
import logging
import math
import os
from pathlib import Path
import sys

import numpy as np
from PySide6.QtCore import QObject, QPoint, QRect, QSize, Qt, QUrl, Signal, Slot
from PySide6.QtGui import QColor, QGuiApplication, QImage, QVector4D
from PySide6.QtWidgets import QWidget

from tomography_session_browser.ui.motion import ease_in_out_cubic, motion_enabled

LOGGER = logging.getLogger(__name__)

ASSETS = Path(__file__).resolve().parents[2] / "assets" / "loader"
QML_PATH = ASSETS / "orbs.qml"
SHADER_SOURCE_PATH = ASSETS / "orbs.frag"
SHADER_PATH = ASSETS / "orbs.frag.qsb"

#: The orbs' area in the loading card, in logical pixels.
ORB_AREA = QSize(300, 110)

_QUARTER_TURN = math.pi / 2.0
#: Each orb: (x amplitude, x rate, x phase, radius) and (y amplitude, y rate,
#: y phase, share of the second tone), in units of the area's height. The
#: rates never line up, so the dance never visibly repeats.
ORBS: tuple[tuple[tuple[float, float, float, float], tuple[float, float, float, float]], ...] = (
    ((0.34, 0.61, 0.0, 0.110), (0.17, 0.83, 1.0, 0.0)),
    ((0.36, 0.47, 2.1, 0.094), (0.19, 0.71, _QUARTER_TURN, 1.0)),
    ((0.28, 0.53, 0.7 + _QUARTER_TURN, 0.082), (0.15, 0.97, 2.4, 0.0)),
    ((0.31, 0.39, 4.0, 0.070), (0.20, 0.59, 1.3 + _QUARTER_TURN, 1.0)),
)

#: The orbs' look, mirrored from ``orbs.frag`` (a test compares them).
ORB_SHAPE: Mapping[str, float] = {
    "BREATHE": 0.045,
    "BREATHE_RATE": 1.96,
    "GATHER_PULL": 0.35,
    "EDGE_LOW": 0.92,
    "EDGE_HIGH": 1.10,
    "TONE_B_MIX": 0.65,
    "GLOW": 0.10,
    "GLOW_LOW": 1.2,
    "GLOW_HIGH": 4.0,
}

#: Where every load's orbs start (seconds along their paths): four orbs
#: apart, about to meet.
ORBS_START_S = 9.0
#: How close the orbs draw at each stage (0 apart .. 1 gathered).
STAGE_GATHER: Mapping[str, float] = {"discover": 0.0, "parse": 0.0, "link": 1.0, "build": 1.0, "finish": 1.0}
#: A stage change adds this much travel over ``LIFT_MS``: a brief quickening
#: (about twice the tempo at its peak).
STAGE_LIFT_S = 0.7
LIFT_MS = 1200
GATHER_MS = 700
#: Reduced motion: each stage shows the still orbs this much later, so the
#: picture changes with the stage without moving.
STILL_STEP_S = 2.5
#: The fallback's frame interval. The orbs are slow; 30 fps is smooth enough,
#: and it halves the fallback's cost on the GUI thread.
PAINTER_FRAME_MS = 33

LOADER_ENV_VAR = "TOMOAPP_LOADER"
#: Set on the Quick windows' containers: widgets that must never be hidden
#: in passing (hiding one unexposes its window; see ``QuickOrbs``).
NATIVE_LOADER_PROPERTY = "_tomo_native_loader"
_PAINTER_PLATFORMS = frozenset({"offscreen", "minimal", "vnc", "linuxfb"})


# -- the orbs' clock ---------------------------------------------------------------


def ease_in_out_sine(t: float) -> float:
    return 0.5 - 0.5 * math.cos(math.pi * t)


@dataclass(frozen=True)
class Ease:
    """A value moving from ``start`` to ``end`` over ``duration_s`` from ``begun``."""

    start: float
    end: float
    begun: float = 0.0
    duration_s: float = 0.0
    curve: Callable[[float], float] = ease_in_out_sine

    def at(self, now: float) -> float:
        if self.duration_s <= 0.0 or now >= self.begun + self.duration_s:
            return self.end
        progress = max(0.0, (now - self.begun) / self.duration_s)
        return self.start + (self.end - self.start) * self.curve(progress)

    def remaining_ms(self, now: float) -> int:
        return max(0, int(round((self.begun + self.duration_s - now) * 1000)))


class OrbClock:
    """The orbs' time, gather and stage, from explicit timestamps.

    Both renderers read it, so the Quick orbs and the painted ones agree on
    where the orbs are.
    """

    def __init__(self) -> None:
        self.stage = "discover"
        self._started = 0.0
        self.lift = Ease(0.0, 0.0)
        self.gather = Ease(0.0, 0.0)

    def start(self, now: float, stage: str = "discover") -> None:
        self.stage = stage
        self._started = now
        self.lift = Ease(0.0, 0.0)
        target = STAGE_GATHER.get(stage, 0.0)
        self.gather = Ease(target, target)

    def set_stage(self, stage: str, now: float) -> None:
        """Quicken briefly and move the gather toward the stage's."""

        if stage == self.stage:
            return
        self.stage = stage
        lift = self.lift.at(now)
        self.lift = Ease(lift, lift + STAGE_LIFT_S, now, LIFT_MS / 1000.0, ease_in_out_sine)
        gather = self.gather.at(now)
        target = STAGE_GATHER.get(stage, gather)
        self.gather = Ease(gather, target, now, GATHER_MS / 1000.0, ease_in_out_cubic)

    def shift(self, seconds: float) -> None:
        """Start everything ``seconds`` later.

        The Quick orbs' animators start with their first frame, which can
        come a little after they were set going; this keeps the painted orbs
        where the Quick ones are.
        """

        self._started += seconds
        self.lift = replace(self.lift, begun=self.lift.begun + seconds)
        self.gather = replace(self.gather, begun=self.gather.begun + seconds)

    def base_at(self, now: float) -> float:
        """Seconds of steady travel, without the stage lifts."""

        return ORBS_START_S + max(0.0, now - self._started)

    def time_at(self, now: float) -> float:
        return self.base_at(now) + self.lift.at(now)

    def gather_at(self, now: float) -> float:
        return self.gather.at(now)

    def still_time(self) -> float:
        """Reduced motion: one picture per stage."""

        index = list(STAGE_GATHER).index(self.stage) if self.stage in STAGE_GATHER else 0
        return ORBS_START_S + index * STILL_STEP_S


# -- the orbs drawn with numpy -------------------------------------------------------


@dataclass(frozen=True)
class OrbColours:
    background: QColor
    tone_a: QColor
    tone_b: QColor

    @classmethod
    def from_palette(cls, palette) -> OrbColours:
        return cls(QColor(palette.surface), QColor(palette.accent), QColor(palette.chart_blue))

    def key(self) -> tuple[str, str, str]:
        return (self.background.name(), self.tone_a.name(), self.tone_b.name())


def _smoothstep(low: float, high: float, x: np.ndarray) -> np.ndarray:
    t = np.clip((x - low) / (high - low), 0.0, 1.0)
    return t * t * (3.0 - 2.0 * t)


def orb_centres(t: float, gather: float, coalesce: float = 0.0) -> list[tuple[float, float]]:
    """Where each orb is at ``t``: centred units, one unit = the area's height.

    ``coalesce`` (0..1) draws them into one at the centre: the load is done
    (plan F6.3; painted only, the Quick window has left by then).
    """

    pull = (1.0 - ORB_SHAPE["GATHER_PULL"] * gather) * (1.0 - coalesce)
    return [
        (ax * math.sin(fx * t + px) * pull, ay * math.sin(fy * t + py) * pull)
        for (ax, fx, px, _radius), (ay, fy, py, _tone) in ORBS
    ]


def orb_field(
    width: int, height: int, t: float, gather: float, coalesce: float = 0.0
) -> tuple[np.ndarray, np.ndarray]:
    """The orbs' field (inside where above 1) and second-tone share, per pixel.

    The same sums as ``orbs.frag``. Each orb's term is separable in x and y,
    so it is built from two short axes: this runs on the GUI thread.
    """

    xs = ((np.arange(width, dtype=np.float32) + 0.5) / width - 0.5) * np.float32(width / height)
    ys = (np.arange(height, dtype=np.float32) + 0.5) / height - 0.5
    breathe = 1.0 + ORB_SHAPE["BREATHE"] * math.sin(t * ORB_SHAPE["BREATHE_RATE"])
    field = np.zeros((height, width), dtype=np.float32)
    second = np.zeros_like(field)
    term = np.empty_like(field)
    for (cx, cy), ((_ax, _fx, _px, radius), (_ay, _fy, _py, tone)) in zip(orb_centres(t, gather, coalesce), ORBS):
        dx = xs - np.float32(cx)
        dy = ys - np.float32(cy)
        r = radius * breathe
        np.add((dy * dy)[:, None], (dx * dx)[None, :], out=term)
        np.maximum(term, 1e-5, out=term)
        np.divide(np.float32(r * r), term, out=term)
        field += term
        if tone:
            term *= np.float32(tone)
            second += term
    np.maximum(field, 1e-5, out=term)
    np.divide(second, term, out=second)
    np.clip(second, 0.0, 1.0, out=second)
    return field, second


def _rgb(colour: QColor) -> np.ndarray:
    return np.array([colour.red(), colour.green(), colour.blue()], dtype=np.float32)


def orb_image(
    width: int, height: int, t: float, gather: float, colours: OrbColours, coalesce: float = 0.0
) -> QImage:
    """The orbs at time ``t``, ``width`` x ``height`` device pixels, opaque."""

    width, height = max(2, int(width)), max(2, int(height))
    field, second = orb_field(width, height, t, gather, coalesce)
    shape = ORB_SHAPE
    inside = _smoothstep(shape["EDGE_LOW"], shape["EDGE_HIGH"], field)
    glow = _smoothstep(shape["GLOW_LOW"], shape["GLOW_HIGH"], field) * np.float32(shape["GLOW"] * 255.0)
    second *= np.float32(shape["TONE_B_MIX"])
    tone_a, tone_b, background = _rgb(colours.tone_a), _rgb(colours.tone_b), _rgb(colours.background)
    pixels = np.full((height, width), 0xFF000000, dtype=np.uint32)
    channel = np.empty_like(field)
    for index, shift in enumerate((16, 8, 0)):
        # background + inside * (orb - background), orb = mix(a, b, tone) + glow
        np.multiply(second, tone_b[index] - tone_a[index], out=channel)
        channel += tone_a[index] - background[index]
        channel += glow
        channel *= inside
        channel += background[index] + 0.5
        np.clip(channel, 0.0, 255.0, out=channel)
        pixels |= channel.astype(np.uint32) << np.uint32(shift)
    image = QImage(pixels.data, width, height, 4 * width, QImage.Format.Format_RGB32)
    return image.copy()


# -- which renderer ---------------------------------------------------------------------


@dataclass(frozen=True)
class RendererChoice:
    """``quick``, ``painter`` (numpy, animated) or ``still`` (reduced motion)."""

    kind: str
    reason: str = ""


def remote_session() -> bool:
    """Whether this is a remote desktop session (no GPU worth using)."""

    if sys.platform != "win32":
        return False
    try:
        import ctypes

        sm_remotesession = 0x1000
        return bool(ctypes.windll.user32.GetSystemMetrics(sm_remotesession))  # type: ignore[attr-defined]
    except (OSError, AttributeError):
        return False


def choose_renderer(
    widget: QWidget | None = None,
    *,
    environ: Mapping[str, str] | None = None,
    platform: str | None = None,
    remote: bool | None = None,
) -> RendererChoice:
    """Pick the orbs' renderer, with the reason when it is not Qt Quick.

    ``TOMOAPP_LOADER=painter`` or ``=quick`` overrides the platform and
    remote-desktop checks; reduced motion always gives the still orbs.
    """

    env = os.environ if environ is None else environ
    if not motion_enabled(widget):
        return RendererChoice("still", "reduced motion")
    override = env.get(LOADER_ENV_VAR, "").strip().lower()
    if override == "painter":
        return RendererChoice("painter", f"{LOADER_ENV_VAR}=painter")
    if override != "quick":
        name = QGuiApplication.platformName() if platform is None else platform
        if name in _PAINTER_PLATFORMS:
            return RendererChoice("painter", f"the {name} platform")
        if remote_session() if remote is None else remote:
            return RendererChoice("painter", "remote desktop session")
    if env.get("QSG_RENDER_LOOP", "").strip().lower() == "basic" or env.get(
        "QT_QUICK_BACKEND", ""
    ).strip().lower() in {"software", "softwarecontext"}:
        return RendererChoice("painter", "Qt Quick has no render thread")
    if not (QML_PATH.is_file() and SHADER_PATH.is_file()):
        return RendererChoice("painter", "orb assets missing")
    return RendererChoice("quick")


# -- the orbs on Qt Quick's render thread ------------------------------------------------


class QuickOrbs(QObject):
    """The orbs in a Qt Quick window embedded over ``host`` (plans F6.0, F6.2).

    **Two windows.** Beside the orbs' window is a 2x2 helper the host places
    where it cannot be seen (on the card, in the card's colour). With a single
    Quick window exposed, Qt advances the Animators' GUI-side proxies through
    a sync with the render thread every frame, and the GUI thread waited
    ~32 ms per sync (31–45% of load time). A second exposed window makes Qt
    drive them from a system timer instead, and the waits stop. **This relies
    on Qt internals:** re-check it on Qt upgrades (the F6 probes measure it).

    **Parked between loads.** Showing a Quick window blocks the GUI thread
    until its first frame; the first time, creating the graphics devices, for
    ~430 ms. So :meth:`warm` shows both windows once, while the app is idle,
    *outside* the host's window (a child window there is exposed but never
    seen), and they stay there, idle, between loads: a load moves them in
    (about one frame) and :meth:`park` moves them out again. The containers
    are children of the host's parent for that reason: the host (the loading
    overlay) is hidden between loads, and hiding them would unexpose them.
    """

    #: The first frame since :meth:`start` is on screen.
    ready = Signal()
    #: Qt Quick could not draw the orbs; the host should use the fallback.
    failed = Signal(str)

    def __init__(self, host: QWidget) -> None:
        super().__init__(host)
        self._host = host
        self._view = None
        self._helper = None
        self._container: QWidget | None = None
        self._helper_container: QWidget | None = None
        self._root: QObject | None = None
        self._orbs: QObject | None = None
        self._animators: dict[str, QObject] = {}
        self._awaiting_frame = False
        self._frames_connected = False
        self._running = False
        self._in_view = False
        self._placed = False
        self.error = ""

    @property
    def prepared(self) -> bool:
        return self._container is not None

    @property
    def running(self) -> bool:
        return self._running

    @property
    def in_view(self) -> bool:
        return self._in_view

    @property
    def warmed(self) -> bool:
        return (
            self._container is not None
            and not self._container.isHidden()
            and not self._helper_container.isHidden()
        )

    def prepare(self) -> bool:
        """Create the Quick windows and load the scene; ``False`` with :attr:`error` on failure."""

        if self.error:
            return False
        if self._container is not None:
            return True
        try:
            from PySide6.QtQuick import QQuickView, QQuickWindow
        except ImportError as exc:  # pragma: no cover - QtQuick ships with PySide6
            self.error = f"Qt Quick is unavailable: {exc}"
            return False
        flags = Qt.WindowType.WindowTransparentForInput | Qt.WindowType.WindowDoesNotAcceptFocus
        view = QQuickView()
        view.setFlags(view.flags() | flags)
        view.setResizeMode(QQuickView.ResizeMode.SizeRootObjectToView)
        view.setSource(QUrl.fromLocalFile(str(QML_PATH)))
        root = view.rootObject()
        if view.status() != QQuickView.Status.Ready or root is None:
            self.error = "; ".join(error.toString() for error in view.errors()) or "the orbs scene did not load"
            view.deleteLater()
            return False
        orbs = root.findChild(QObject, "orbs")
        animators = {name: root.findChild(QObject, name) for name in ("clock", "lifter", "gatherer")}
        if orbs is None or any(animator is None for animator in animators.values()) or root.property("shaderFailed"):
            self.error = str(root.property("shaderLog") or "") or "the orbs scene is incomplete"
            view.deleteLater()
            return False
        for index, (path_x, path_y) in enumerate(ORBS):
            orbs.setProperty(f"orb{index}x", QVector4D(*path_x))
            orbs.setProperty(f"orb{index}y", QVector4D(*path_y))
        view.sceneGraphError.connect(self._scene_graph_error)
        # The shader is read when the window first shows (the warm-up, or a
        # load): a missing or broken .qsb is only known then.
        root.shaderFailedChanged.connect(self._shader_status_changed)
        parent = self._host.parentWidget() or self._host
        helper = QQuickWindow()
        helper.setFlags(helper.flags() | flags)
        helper_container = QWidget.createWindowContainer(helper, parent)
        container = QWidget.createWindowContainer(view, parent)
        for widget in (helper_container, container):
            widget.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            widget.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
            widget.setProperty(NATIVE_LOADER_PROPERTY, True)
            widget.hide()
        self._view, self._helper = view, helper
        self._container, self._helper_container = container, helper_container
        self._root, self._orbs, self._animators = root, orbs, animators
        return True

    def warm(self, colours: OrbColours) -> bool:
        """Show one window, parked out of sight, so a load needn't wait for it.

        Each window's first showing creates its graphics device and holds the
        GUI thread ~160 ms, so it is one window per call; ``True`` while one
        is left.
        """

        if not self.prepare() or self.warmed or self._running:
            return False
        self.set_colours(colours)
        self._place_parked()
        if self._helper_container.isHidden():
            self._helper_container.show()
            return self._container.isHidden()
        self._container.show()
        return False

    def set_colours(self, colours: OrbColours) -> None:
        if self._root is None:
            return
        self._root.setProperty("surfaceColor", colours.background)
        self._root.setProperty("toneAColor", colours.tone_a)
        self._root.setProperty("toneBColor", colours.tone_b)
        self._view.setColor(colours.background)
        self._helper.setColor(colours.background)

    def start(self, clock: OrbClock, now: float, colours: OrbColours, area: QRect, helper: QRect) -> None:
        """Move the orbs into ``area`` (host coordinates) and set them moving where ``clock`` says."""

        if self._container is None:
            return
        self.set_colours(colours)
        self._orbs.setProperty("time", clock.base_at(now))
        self._run("clock", start=clock.base_at(now), end=clock.base_at(now) + 100_000.0)
        self._follow(clock, now)
        self._awaiting_frame = True
        if not self._frames_connected:
            self._view.frameSwapped.connect(self._frame_swapped)
            self._frames_connected = True
        self._running = True
        self.place(area, helper)
        self._helper_container.show()
        self._container.show()

    def place(self, area: QRect, helper: QRect) -> None:
        """Put the orbs over ``area`` and the helper at ``helper`` (host coordinates)."""

        if self._container is None or not self._running:
            return
        self._in_view = True
        self._placed = True
        self._helper_container.setGeometry(self._to_parent(helper))
        self._container.setGeometry(self._to_parent(area))
        self._container.raise_()

    def follow(self, clock: OrbClock, now: float) -> None:
        """The stage changed: ease the lift and the gather as ``clock`` does."""

        if self._running:
            self._follow(clock, now)

    def park(self) -> None:
        """Move out of sight at once; the orbs keep going until :meth:`rest`."""

        self._awaiting_frame = False
        self._disconnect_frames()
        self._in_view = False
        if self._container is not None:
            self._place_parked()

    def rest(self) -> None:
        """Stop the animators, so the parked windows draw nothing."""

        self._running = False
        for animator in self._animators.values():
            animator.setProperty("running", False)

    def stop(self) -> None:
        self.park()
        self.rest()

    def _to_parent(self, rect: QRect) -> QRect:
        parent = self._container.parentWidget()
        if parent is None or parent is self._host:
            return QRect(rect)
        return QRect(self._host.mapTo(parent, rect.topLeft()), rect.size())

    def _place_parked(self) -> None:
        # Outside the window: exposed as far as Qt is concerned, never seen.
        # The same size as in view, so moving in needs no new swap chain.
        size = self._container.size() if self._placed else ORB_AREA
        self._container.setGeometry(QRect(QPoint(-size.width() - 64, -size.height() - 64), size))
        self._helper_container.setGeometry(QRect(-40, -40, 2, 2))

    def _follow(self, clock: OrbClock, now: float) -> None:
        for name, uniform, ease in (("lifter", "lift", clock.lift), ("gatherer", "gather", clock.gather)):
            remaining = ease.remaining_ms(now)
            self._animators[name].setProperty("running", False)
            self._orbs.setProperty(uniform, ease.at(now))
            if remaining > 0:
                self._run(name, start=ease.at(now), end=ease.end, duration_ms=remaining)

    def _run(self, name: str, *, start: float, end: float, duration_ms: int | None = None) -> None:
        animator = self._animators[name]
        animator.setProperty("running", False)
        animator.setProperty("from", start)
        animator.setProperty("to", end)
        if duration_ms is not None:
            animator.setProperty("duration", max(1, duration_ms))
        animator.setProperty("running", True)

    # Connected by bound method, so each call is queued to the GUI thread:
    # no Python ever runs on the render thread.
    @Slot()
    def _frame_swapped(self) -> None:
        if not self._awaiting_frame:
            return
        self._awaiting_frame = False
        self._disconnect_frames()
        self.ready.emit()

    def _scene_graph_error(self, _error, message: str) -> None:
        self._fail(message or "Qt Quick reported a scene graph error")

    def _shader_status_changed(self) -> None:
        if self._root is not None and self._root.property("shaderFailed"):
            self._fail(str(self._root.property("shaderLog") or "") or "the orbs shader did not load")

    def _fail(self, reason: str) -> None:
        """Qt Quick cannot draw the orbs: hide them, for good, and say so."""

        if self.error:
            return
        self.error = reason
        LOGGER.warning("Loading orbs fall back to the painted ones: %s", reason)
        self.stop()
        for widget in (self._container, self._helper_container):
            if widget is not None:
                widget.hide()
        self.failed.emit(reason)

    def _disconnect_frames(self) -> None:
        if self._view is None or not self._frames_connected:
            return
        self._frames_connected = False
        try:
            self._view.frameSwapped.disconnect(self._frame_swapped)
        except (RuntimeError, TypeError):
            pass
