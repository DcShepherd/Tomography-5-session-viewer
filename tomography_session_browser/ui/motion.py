"""Motion foundation: springs on one shared ticker, tokens, reduced motion.

Plan phase F4. Qt style sheets cannot transition, so every hover and press in
the app snapped. This module supplies the small motion layer the fluid work
builds on:

* :class:`Spring`: a retargetable, velocity-preserving value. Changing the
  target mid-flight continues from the current value and velocity, so a
  reversed hover never snaps or queues.
* :data:`TICKER`: one shared frame timer for every active spring. It runs only
  while something is moving and stops as soon as everything settles.
* Motion tokens: the durations and spring settings the plan defines, so
  surfaces feel like one system.
* :func:`motion_enabled`: honours an explicit widget opt-out, the
  ``tomo_reduce_motion`` application property, the ``TOMOAPP_REDUCE_MOTION``
  environment variable, and the operating system's reduced-motion setting
  (Windows "Animation effects"). When motion is off, springs jump straight to
  their target: every state change still happens, only the movement is
  dropped.
* :class:`ItemHoverAnimator`: animated hover for delegate-painted rows.
* :class:`PanelSlide`: a side panel's snapshot sliding from its own edge.
* :class:`SmoothScroller`: eased, retargetable wheel scrolling in pixels.
* :class:`Tween` and :class:`PageFade`: a fixed-time eased fade, and the
  veil that lifts off a tab page as it appears; :class:`PictureFade`, the
  previous image dissolving into a newly selected one.
* :class:`SectionReveal` and :class:`BranchReveal`: a floating section, or
  a tree branch, opening and closing along its height.
* :func:`zoom_pan_path` and :class:`MarkerPulse`: the camera's path between
  two views, and the ring that shows where a jump landed.

Motion only ever animates presentation (opacity, colour, geometry, view
transforms), never scientific values.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import logging
import math
import os
import subprocess
import sys
import threading
import time
import weakref

from PySide6.QtCore import QEvent, QModelIndex, QObject, QPoint, QPointF, QRect, QRectF, Qt, QTimer
from PySide6.QtGui import QBrush, QColor, QCursor, QPainter, QPainterPath, QPen, QPixmap, QRegion, QTransform
from PySide6.QtWidgets import (
    QAbstractItemView,
    QAbstractScrollArea,
    QApplication,
    QGraphicsOpacityEffect,
    QWidget,
)
import shiboken6

LOGGER = logging.getLogger(__name__)

# -- tokens -----------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class SpringSpec:
    """Spring settings: stiffness (per unit mass) and damping ratio."""

    stiffness: float
    damping_ratio: float


#: Hover, press, focus, chip colour.
MOTION_MICRO = SpringSpec(stiffness=500.0, damping_ratio=1.0)
#: Tab indicator, toggles, row reveal.
MOTION_SMALL = SpringSpec(stiffness=380.0, damping_ratio=0.9)
#: Dock slide, section expand, layout reflow. Critically damped, ~90% of the
#: travel in 150 ms and at rest within ~300 ms. The plan's first guess,
#: k=260 / ζ=0.9, measured natively at 470 ms of frames for one panel slide.
MOTION_PANEL = SpringSpec(stiffness=700.0, damping_ratio=1.0)
#: Wheel zoom, eased in log-scale: quick enough to feel direct (about 80% of a
#: notch within 100 ms) and critically damped, so it never overshoots.
MOTION_ZOOM = SpringSpec(stiffness=900.0, damping_ratio=1.0)
#: Wheel and scroll-to-selection scrolling: ~90% of the way in 150 ms.
MOTION_SCROLL = SpringSpec(stiffness=700.0, damping_ratio=1.0)

#: Durations (ms) for tween-style transitions that are not springs.
DURATION_MICRO_MS = 110
DURATION_SMALL_MS = 180
DURATION_PANEL_MS = 240
DURATION_CAMERA_MS = 320
#: A long flight (several screens away) takes longer, up to this.
DURATION_CAMERA_MAX_MS = 640
#: The ring that shows where a jump landed.
DURATION_PULSE_MS = 650
#: Per-item stagger for entrances, and the most items worth staggering.
STAGGER_MS = 25
STAGGER_MAX_ITEMS = 8

#: Ticker cadence and the largest step a stall may produce. Capping the step
#: means a stalled UI thread slows an animation down rather than teleporting
#: it to the end.
FRAME_INTERVAL_MS = 16
MAX_STEP_S = 0.05

#: Input that ends a transition drawn over the live window (the loader's
#: hand-off, the theme reveal) at once: its picture must never hide what the
#: input did. Pointer moves and releases do not count.
INTERRUPTING_INPUT = frozenset(
    {
        QEvent.Type.MouseButtonPress,
        QEvent.Type.MouseButtonDblClick,
        QEvent.Type.KeyPress,
        QEvent.Type.Wheel,
        QEvent.Type.TouchBegin,
        QEvent.Type.NativeGesture,
    }
)

# -- reduced motion ---------------------------------------------------------

_ENV_VAR = "TOMOAPP_REDUCE_MOTION"
_TRUTHY = {"1", "true", "yes", "on"}
_FALSY = {"0", "false", "no", "off"}
_system_reduced_motion: bool | None = None


def _query_system_reduced_motion() -> bool:
    """Ask the operating system whether the user turned animations off."""

    try:
        if sys.platform == "win32":
            import ctypes

            spi_get_client_area_animation = 0x1042
            enabled = ctypes.c_int(1)
            ok = ctypes.windll.user32.SystemParametersInfoW(  # type: ignore[attr-defined]
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

    global _system_reduced_motion  # noqa: PLW0603 - process-wide cache
    if refresh or _system_reduced_motion is None:
        _system_reduced_motion = _query_system_reduced_motion()
    return _system_reduced_motion


_WATCH_PROPERTY = "_tomo_reduced_motion_watch"


def watch_system_reduced_motion(app: QApplication) -> None:
    """Re-read the OS setting whenever the application becomes active again.

    It was read once per run, so turning Windows' "Animation effects" off
    (or on) took a restart. Changing it means going to another application,
    so coming back to this one is when it may have changed. On Windows the
    read is one system call; elsewhere it runs a small command, so it is
    read on a worker thread and applies from the next animation on.
    Idempotent.
    """

    if app.property(_WATCH_PROPERTY):
        return
    app.setProperty(_WATCH_PROPERTY, True)
    app.applicationStateChanged.connect(_application_state_changed)


def _application_state_changed(state: Qt.ApplicationState) -> None:
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
    env_value = os.environ.get(_ENV_VAR, "").strip().lower()
    if env_value in _TRUTHY:
        return False
    if env_value in _FALSY:
        return True
    return not system_prefers_reduced_motion()


# -- springs and the shared ticker ------------------------------------------


class _Ticker(QObject):
    """One frame timer for every active animation; idle when nothing moves."""

    def __init__(self) -> None:
        super().__init__()
        self._active: list[Spring] = []
        self._timer: QTimer | None = None
        self._last_tick = 0.0

    @property
    def is_running(self) -> bool:
        return self._timer is not None and self._timer.isActive()

    @property
    def active_count(self) -> int:
        return len(self._active)

    def add(self, spring: Spring) -> None:
        if spring in self._active:
            return
        self._active.append(spring)
        if not self.is_running:
            self._start()

    def remove(self, spring: Spring) -> None:
        if spring in self._active:
            self._active.remove(spring)
        if not self._active:
            self._stop()

    def advance(self, dt: float) -> None:
        """Step every active spring by ``dt`` seconds (also used by tests)."""

        for spring in list(self._active):
            # One animation's update or finish may stop another further down
            # this frame's list (a smooth scroll moving a tree's scroll bar
            # ends its branch reveal). A stopped animation belongs to whoever
            # stopped it: it is neither stepped nor settled here.
            if spring not in self._active:
                continue
            spring.step(dt)
            if spring.settled and spring in self._active:
                self._active.remove(spring)
                spring._notify_settled()  # noqa: SLF001 - ticker owns settling
        if not self._active:
            self._stop()

    def _start(self) -> None:
        if QApplication.instance() is None:
            return
        if self._timer is None:
            self._timer = QTimer(self)
            self._timer.setTimerType(Qt.TimerType.PreciseTimer)
            self._timer.setInterval(FRAME_INTERVAL_MS)
            self._timer.timeout.connect(self._tick)
        self._last_tick = time.perf_counter()
        self._timer.start()

    def _stop(self) -> None:
        # At exit Qt may delete the timer before a widget whose spring is
        # still moving is destroyed (and resets that spring).
        if self._timer is not None and shiboken6.isValid(self._timer):
            self._timer.stop()

    def _tick(self) -> None:
        now = time.perf_counter()
        dt = min(MAX_STEP_S, max(0.0, now - self._last_tick))
        self._last_tick = now
        self.advance(dt)


TICKER = _Ticker()

_SETTLE_DISTANCE = 1e-3
_SETTLE_VELOCITY = 1e-2


class Spring:
    """A value that moves toward a target like a damped spring.

    Retargetable and velocity-preserving: :meth:`set_target` during motion
    continues from the current value and velocity. ``on_update`` runs every
    step; ``on_settled`` once the value comes to rest on the target.
    """

    def __init__(
        self,
        value: float = 0.0,
        *,
        spec: SpringSpec = MOTION_SMALL,
        on_update: Callable[[float], None] | None = None,
        on_settled: Callable[[], None] | None = None,
        widget: QWidget | None = None,
        settle_distance: float = _SETTLE_DISTANCE,
        settle_velocity: float = _SETTLE_VELOCITY,
    ) -> None:
        self.value = float(value)
        self.target = float(value)
        self.velocity = 0.0
        self.spec = spec
        self._on_update = on_update
        self._on_settled = on_settled
        self._widget = widget
        # How close counts as at rest, in the value's own units. A spring that
        # moves pixels should stop within half a pixel: the default ran slides
        # on for 200 ms of frames that moved nothing visible.
        self._settle_distance = settle_distance
        self._settle_velocity = settle_velocity

    @property
    def settled(self) -> bool:
        return (
            abs(self.value - self.target) < self._settle_distance
            and abs(self.velocity) < self._settle_velocity
        )

    @property
    def is_animating(self) -> bool:
        return self in TICKER._active  # noqa: SLF001

    def set_target(self, target: float, *, animate: bool = True) -> None:
        self.target = float(target)
        if not animate or not motion_enabled(self._widget):
            self.jump_to(self.target)
            return
        if self.settled:
            return
        TICKER.add(self)

    def reset(self, value: float) -> None:
        """Rest at ``value`` without animating or notifying.

        For when something else has already moved what the spring drives (a
        Fit click during a wheel zoom), so the spring only needs to agree.
        """

        TICKER.remove(self)
        self.value = self.target = float(value)
        self.velocity = 0.0

    def jump_to(self, value: float) -> None:
        """Move instantly (reduced motion, or a change that must not animate)."""

        TICKER.remove(self)
        self.value = self.target = float(value)
        self.velocity = 0.0
        if self._on_update is not None:
            self._on_update(self.value)
        self._notify_settled()

    def step(self, dt: float) -> None:
        # Semi-implicit Euler in small substeps: stable for these stiffnesses
        # even when a stalled frame hands us the maximum step.
        stiffness = self.spec.stiffness
        damping = 2.0 * self.spec.damping_ratio * math.sqrt(stiffness)
        remaining = max(0.0, dt)
        while remaining > 0.0:
            h = min(remaining, 1.0 / 240.0)
            acceleration = -stiffness * (self.value - self.target) - damping * self.velocity
            self.velocity += acceleration * h
            self.value += self.velocity * h
            remaining -= h
        if self.settled:
            self.value = self.target
            self.velocity = 0.0
        if self._on_update is not None:
            self._on_update(self.value)

    def _notify_settled(self) -> None:
        if self._on_settled is not None:
            self._on_settled()


def blend(start: QColor, end: QColor, amount: float) -> QColor:
    """Linear blend of two colours, ``amount`` clamped to 0..1."""

    t = min(1.0, max(0.0, amount))
    return QColor(
        round(start.red() + (end.red() - start.red()) * t),
        round(start.green() + (end.green() - start.green()) * t),
        round(start.blue() + (end.blue() - start.blue()) * t),
        round(start.alpha() + (end.alpha() - start.alpha()) * t),
    )


# -- animated hover for item views -----------------------------------------


_HOVER_EVENTS = frozenset({QEvent.Type.MouseMove, QEvent.Type.Leave, QEvent.Type.Hide})


def _row_key(index: QModelIndex) -> tuple[int, ...]:
    """A hashable path for a row (column-independent)."""

    parts: list[int] = []
    current = index.sibling(index.row(), 0) if index.isValid() else QModelIndex()
    while current.isValid():
        parts.append(current.row())
        current = current.parent()
    return tuple(reversed(parts))


class ItemHoverAnimator(QObject):
    """Animated per-row hover progress (0..1) for a delegate-painted view.

    The delegate asks :meth:`progress` for a row and blends its hover fill by
    it, instead of switching on ``State_MouseOver``. Moving between rows fades
    the old row out while the new one fades in, and a reversal mid-fade
    continues smoothly. With motion off, progress is simply 0 or 1.
    """

    def __init__(self, view: QAbstractItemView, *, spec: SpringSpec = MOTION_MICRO) -> None:
        super().__init__(view)
        self._view = view
        self._spec = spec
        self._springs: dict[tuple[int, ...], Spring] = {}
        self._hovered: tuple[int, ...] | None = None
        # The view holds the Python side alive: Qt owns the C++ object as the
        # view's child, but without a Python reference the wrapper could be
        # collected while the viewport still routes events to this filter.
        view._tomo_hover_animator = self  # type: ignore[attr-defined]
        view.setMouseTracking(True)
        self._viewport = view.viewport()
        self._viewport.installEventFilter(self)
        # Weak callbacks: signals from the model or scroll bar must never
        # reach a torn-down animator.
        ref = weakref.ref(self)

        def call(method: str, *_args) -> None:
            target = ref()
            if target is not None:
                getattr(target, method)()

        view.verticalScrollBar().valueChanged.connect(lambda *_a: call("_sync_to_cursor"))
        model = view.model()
        if model is not None:
            for signal in (model.modelReset, model.rowsInserted, model.rowsRemoved, model.layoutChanged):
                signal.connect(lambda *_a: call("reset"))

    def progress(self, index: QModelIndex) -> float:
        spring = self._springs.get(_row_key(index))
        return spring.value if spring is not None else 0.0

    def hovered_key(self) -> tuple[int, ...] | None:
        return self._hovered

    def reset(self, *_args) -> None:
        """Forget all rows (their indexes are no longer meaningful)."""

        for spring in self._springs.values():
            TICKER.remove(spring)
        self._springs.clear()
        self._hovered = None

    def eventFilter(self, watched, event) -> bool:  # noqa: N802 - Qt API
        # Check the event type and the cached viewport before touching the
        # view at all: while a view is being destroyed its viewport still
        # receives Hide/Leave, after the scroll-area part of the view is gone,
        # and calling into the view then crashed the process on exit.
        kind = event.type()
        if kind not in _HOVER_EVENTS or watched is not getattr(self, "_viewport", None):
            return False
        if kind == QEvent.Type.MouseMove:
            self._hover_at(event.position().toPoint())
        elif kind == QEvent.Type.Leave:
            self._set_hovered(None)
        else:  # Hide: nothing is visible to fade; drop state without repainting
            self.reset()
        return False

    def _sync_to_cursor(self, *_args) -> None:
        viewport = self._view.viewport()
        position = viewport.mapFromGlobal(QCursor.pos())
        if viewport.rect().contains(position):
            self._hover_at(position)

    def _hover_at(self, position) -> None:
        index = self._view.indexAt(position)
        self._set_hovered(_row_key(index) if index.isValid() else None)

    def _set_hovered(self, key: tuple[int, ...] | None) -> None:
        if key == self._hovered:
            return
        previous, self._hovered = self._hovered, key
        if previous is not None:
            self._spring_for(previous).set_target(0.0)
        if key is not None:
            self._spring_for(key).set_target(1.0)

    def _spring_for(self, key: tuple[int, ...]) -> Spring:
        spring = self._springs.get(key)
        if spring is None:

            def repaint(_value: float, key: tuple[int, ...] = key) -> None:
                self._repaint_row(key)

            def forget(key: tuple[int, ...] = key) -> None:
                settled = self._springs.get(key)
                if settled is not None and settled.value == 0.0 and key != self._hovered:
                    del self._springs[key]

            spring = Spring(0.0, spec=self._spec, on_update=repaint, on_settled=forget, widget=self._view)
            self._springs[key] = spring
        return spring

    def _repaint_row(self, key: tuple[int, ...]) -> None:
        model = self._view.model()
        if model is None:
            return
        index = QModelIndex()
        for row in key:
            index = model.index(row, 0, index)
            if not index.isValid():
                return
        rect = self._view.visualRect(index)
        if rect.isValid():
            viewport = self._view.viewport()
            viewport.update(QRect(0, rect.top(), viewport.width(), rect.height()))


# -- smooth scrolling ---------------------------------------------------------


_WHEEL_MODIFIERS = (
    Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.ShiftModifier | Qt.KeyboardModifier.AltModifier
)


class SmoothScroller(QObject):
    """Eased vertical scrolling for a scroll area or item view (plan F5.5).

    A wheel notch moved lists three whole rows at once. This eases the scroll
    bar towards a retargetable target instead: a notch during the glide adds
    to where it was heading, so fast scrolling loses nothing, and a drag on
    the bar or a key press takes over at once. Precision touchpads that
    report pixel deltas scroll 1:1, already smooth. Wheel events the area
    cannot use (at its end, or with a modifier held) pass on, so nested
    scroll areas and Ctrl+wheel still work. With motion off it jumps, the old
    distance in pixels.
    """

    def __init__(self, area: QAbstractScrollArea, *, spec: SpringSpec = MOTION_SCROLL) -> None:
        super().__init__(area)
        self._area = area
        self._bar = area.verticalScrollBar()
        self._viewport = area.viewport()
        self._setting = False
        self._spring = Spring(
            float(self._bar.value()),
            spec=spec,
            on_update=self._moved,
            widget=area,
            settle_distance=0.5,
            settle_velocity=20.0,
        )
        # The area keeps the Python side alive, as with ItemHoverAnimator.
        area._tomo_smooth_scroller = self  # type: ignore[attr-defined]
        self._viewport.installEventFilter(self)
        ref = weakref.ref(self)

        def bar_moved(value: int) -> None:
            scroller = ref()
            if scroller is not None:
                scroller._bar_moved(value)  # noqa: SLF001

        self._bar.valueChanged.connect(bar_moved)
        area.destroyed.connect(
            lambda *_args, spring=self._spring: spring.reset(spring.value) if spring.is_animating else None
        )

    @property
    def is_scrolling(self) -> bool:
        return self._spring.is_animating

    @property
    def target(self) -> float:
        return self._spring.target if self._spring.is_animating else float(self._bar.value())

    def scroll_to(self, value: float, *, animate: bool = True) -> None:
        """Ease (or jump) the scroll bar to ``value``, clamped to its range."""

        bar = self._bar
        target = float(min(bar.maximum(), max(bar.minimum(), value)))
        if not self._spring.is_animating:
            self._spring.reset(float(bar.value()))
        self._spring.set_target(target, animate=animate)

    def eventFilter(self, watched, event) -> bool:  # noqa: N802 - Qt API
        if event.type() != QEvent.Type.Wheel or watched is not getattr(self, "_viewport", None):
            return False
        if event.modifiers() & _WHEEL_MODIFIERS:
            return False
        pixel, angle = event.pixelDelta().y(), event.angleDelta().y()
        if not pixel and not angle:
            return False
        bar = self._bar
        if pixel:
            delta, animate = -float(pixel), False
        else:
            delta = -angle / 120.0 * QApplication.wheelScrollLines() * max(1, bar.singleStep())
            animate = True
        base = self.target
        target = min(bar.maximum(), max(bar.minimum(), base + delta))
        if target == base and not self._spring.is_animating:
            return False  # at an end: let an enclosing scroll area have it
        self.scroll_to(target, animate=animate)
        event.accept()
        return True

    def _moved(self, value: float) -> None:
        self._setting = True
        try:
            self._bar.setValue(round(value))
        finally:
            self._setting = False

    def _bar_moved(self, value: int) -> None:
        if self._setting:
            return
        # Someone else moved the bar (a drag, a key, a jump to a row): they
        # have the scroll now.
        if self._spring.is_animating:
            self._spring.reset(float(value))


# -- side-panel slides ------------------------------------------------------


class _SlideBackdrop(QWidget):
    """An opaque fill over a panel that is sliding in, until its picture lands."""

    def __init__(self, parent: QWidget, geometry: QRect, colour: QColor) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent)
        self._colour = QColor(colour)
        self.setGeometry(geometry)

    def paintEvent(self, _event) -> None:  # noqa: N802 - Qt API
        QPainter(self).fillRect(self.rect(), self._colour)


class PanelSlide(QWidget):
    """A side panel's snapshot sliding in from, or out to, its own edge.

    Plan F5.2. The panel itself is shown or hidden at once, so the window lays
    out a single time. This widget is the panel's picture, moved on a spring,
    and it removes itself when it settles. Animating the dock's real width
    re-laid out the whole window on every frame (40–125 ms a frame natively).

    It is opaque and *moved*, so each frame Qt repaints only the strip it
    uncovers; a transparent overlay repainted everything beneath it every
    frame. Mouse events pass straight through. Entering, the panel is already
    in place underneath, so an opaque ``backdrop`` in the panel's own colour
    covers it until the picture arrives; leaving, the widened workspace shows
    behind the picture as it goes.

    A toggle mid-way turns it round (``reverse``): the spring keeps its value
    and velocity, so the picture heads back from where it is instead of
    vanishing and starting over from the far edge.
    """

    def __init__(
        self,
        parent: QWidget,
        geometry: QRect,
        picture: QPixmap,
        *,
        from_left: bool,
        entering: bool,
        backdrop: QColor | None = None,
        spec: SpringSpec = MOTION_PANEL,
        on_finished: Callable[[], None] | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("panelSlide")
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent)
        self._home = QRect(geometry)
        self._picture = picture
        self._from_left = from_left
        self._entering = entering
        # Called when an entering slide arrives, before its picture goes
        # (never on ``dismiss``): the real panel can take the picture's place.
        self._on_finished = on_finished
        self.backdrop = (
            _SlideBackdrop(parent, geometry, backdrop) if entering and backdrop is not None else None
        )
        width = max(1, geometry.width())
        # How far out the picture is: 0 at the panel's place, 1 past its edge.
        self._out = Spring(
            1.0 if entering else 0.0,
            spec=spec,
            on_update=self._moved,
            on_settled=self._arrived,
            widget=self,
            # Within half a pixel of home, and all but still.
            settle_distance=0.5 / width,
            settle_velocity=20.0 / width,
        )

        # The shared ticker must never step a slide destroyed mid-way (the
        # window closing, or a second toggle replacing it). The handler holds
        # the spring and the backdrops, not this widget.
        backdrops = [self.backdrop] if self.backdrop is not None else []
        self._backdrops = backdrops

        def _cleanup(*_args, spring=self._out, backdrops=backdrops) -> None:
            if spring.is_animating:
                spring.reset(spring.value)
            for backdrop in backdrops:
                try:
                    backdrop.deleteLater()
                except RuntimeError:
                    pass  # went with its parent

        self.destroyed.connect(_cleanup)
        self.resize(geometry.size())
        self._moved(self._out.value)
        if self.backdrop is not None:
            self.backdrop.show()
            self.backdrop.raise_()
        self.show()
        self.raise_()
        self._out.set_target(0.0 if entering else 1.0)

    def dismiss(self) -> None:
        """Remove the slide now, finished or not."""

        self._out.reset(self._out.value)
        self._finish()

    def reverse(self, *, backdrop: QColor | None = None, on_finished: Callable[[], None] | None = None) -> None:
        """Head back the other way from where the picture is now.

        Turning to enter, the real panel is back in place underneath, so a
        ``backdrop`` covers it until the picture arrives, and ``on_finished``
        runs then. Turning to leave, any backdrop goes: the widened page
        shows behind the picture.
        """

        self._entering = not self._entering
        if self._entering:
            self._on_finished = on_finished
            if self.backdrop is None and backdrop is not None:
                self.backdrop = _SlideBackdrop(self.parentWidget(), self._home, backdrop)
                self._backdrops.append(self.backdrop)
            if self.backdrop is not None:
                self.backdrop.show()
                self.backdrop.raise_()
            self.raise_()
        else:
            self._on_finished = None
            if self.backdrop is not None:
                self.backdrop.hide()
        self._out.set_target(0.0 if self._entering else 1.0)

    @property
    def entering(self) -> bool:
        return self._entering

    @property
    def progress(self) -> float:
        """How far along the current direction it has travelled, 0 to 1."""

        out = min(1.0, max(0.0, self._out.value))
        return 1.0 - out if self._entering else out

    def picture_offset(self) -> int:
        """How far the picture sits from the panel's place, towards its edge."""

        out = min(1.0, max(0.0, self._out.value))
        distance = round(out * self._home.width())
        return -distance if self._from_left else distance

    def _moved(self, _value: float) -> None:
        self.move(self._home.x() + self.picture_offset(), self._home.y())

    def paintEvent(self, _event) -> None:  # noqa: N802 - Qt API
        QPainter(self).drawPixmap(0, 0, self._picture)

    def _arrived(self) -> None:
        on_finished, self._on_finished = self._on_finished, None
        if on_finished is not None and self._entering:
            on_finished()
        self._finish()

    def _finish(self) -> None:
        if self.backdrop is not None:
            self.backdrop.hide()
        self.hide()
        self.deleteLater()


# -- fixed-time fades -------------------------------------------------------


def ease_out_cubic(t: float) -> float:
    return 1.0 - (1.0 - t) ** 3


def ease_in_out_cubic(t: float) -> float:
    """For travel with a start and an end: the camera eases away and lands."""

    return 4.0 * t * t * t if t < 0.5 else 1.0 - (-2.0 * t + 2.0) ** 3 / 2.0


class Tween:
    """A value running 0 → 1 over a fixed time on the shared ticker, eased.

    For fire-and-forget fades, where a spring's long tail would only cost
    frames. It has the ticker's interface (``step``, ``settled``), so it
    shares the one frame timer with the springs. With motion off it jumps
    to 1 at once.
    """

    def __init__(
        self,
        duration_ms: float,
        *,
        on_update: Callable[[float], None] | None = None,
        on_finished: Callable[[], None] | None = None,
        widget: QWidget | None = None,
        easing: Callable[[float], float] = ease_out_cubic,
    ) -> None:
        self.duration_s = max(1e-3, duration_ms / 1000.0)
        self.elapsed = 0.0
        self.value = 0.0
        self._on_update = on_update
        self._on_finished = on_finished
        self._widget = widget
        self._easing = easing

    @property
    def settled(self) -> bool:
        return self.elapsed >= self.duration_s

    @property
    def is_animating(self) -> bool:
        return self in TICKER._active  # noqa: SLF001

    def start(self) -> None:
        self.elapsed = 0.0
        self.value = 0.0
        if not motion_enabled(self._widget):
            self.finish()
            return
        TICKER.add(self)  # type: ignore[arg-type]  # same interface as Spring

    def step(self, dt: float) -> None:
        self.elapsed += max(0.0, dt)
        self.value = self._easing(min(1.0, self.elapsed / self.duration_s))
        if self._on_update is not None:
            self._on_update(self.value)

    def finish(self) -> None:
        """Jump to the end now."""

        TICKER.remove(self)  # type: ignore[arg-type]
        self.elapsed = self.duration_s
        self.value = 1.0
        if self._on_update is not None:
            self._on_update(1.0)
        self._notify_settled()

    def stop(self) -> None:
        TICKER.remove(self)  # type: ignore[arg-type]

    def _notify_settled(self) -> None:
        if self._on_finished is not None:
            self._on_finished()


#: Page fades start part-way, so the first frame already shows the page: a
#: tab switch must never wait on motion (plan §1.6).
PAGE_FADE_START_OPACITY = 0.6


class PageFade(QWidget):
    """A veil in the page's colour lifting off a tab page as it appears.

    Plan F5.3. The page is live underneath from the first frame; the veil
    starts part-transparent and fades out in ``DURATION_MICRO_MS``. A snapshot
    crossfade was measured at ~30 ms per page grab, added to every tab
    switch before anything showed; this adds no latency, at the cost of the
    page repainting under the veil for the three or four frames it lasts.
    Mouse events pass straight through.
    """

    def __init__(
        self,
        parent: QWidget,
        geometry: QRect,
        colour: QColor,
        *,
        start_opacity: float = PAGE_FADE_START_OPACITY,
        duration_ms: float = DURATION_MICRO_MS,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("pageFade")
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setGeometry(geometry)
        self._colour = QColor(colour)
        self._start_opacity = start_opacity
        self.opacity = start_opacity
        self._fade = Tween(duration_ms, on_update=self._faded, on_finished=self._finish, widget=self)
        self.destroyed.connect(lambda *_args, fade=self._fade: fade.stop() if fade.is_animating else None)
        self.show()
        self.raise_()
        self._fade.start()

    def _faded(self, progress: float) -> None:
        self.opacity = self._start_opacity * (1.0 - progress)
        self.update()

    def paintEvent(self, _event) -> None:  # noqa: N802 - Qt API
        colour = QColor(self._colour)
        colour.setAlphaF(max(0.0, min(1.0, self.opacity)))
        QPainter(self).fillRect(self.rect(), colour)

    def dismiss(self) -> None:
        """Remove the veil now (another switch replaces it)."""

        self._fade.stop()
        self._finish()

    def _finish(self) -> None:
        self.hide()
        self.deleteLater()


def veil(
    widget: QWidget,
    colour: QColor,
    *,
    start_opacity: float,
    duration_ms: float = DURATION_MICRO_MS,
) -> PageFade | None:
    """Say that ``widget``'s content was just replaced: a veil lifts off it.

    For a list refilled or a dashboard rebuilt. It replaced fades that put a
    ``QGraphicsOpacityEffect`` on the whole widget, which renders the subtree
    off screen every frame (plan principle 8). The veil sits in the widget's
    parent, over its rectangle only (a scroll area's viewport moves its own
    children when it scrolls). None when motion is off or nothing shows.
    """

    parent = widget.parentWidget()
    window = widget.window()
    if parent is None or not motion_enabled(widget) or not widget.isVisible() or not window.isVisible():
        return None
    # A window can hold motion back (the main window, while its loader is
    # up: the loader's hand-off pictures the page, and would catch a veil
    # half-lifted).
    held = getattr(window, "motion_held", None)
    if callable(held) and held():
        return None
    return PageFade(parent, widget.geometry(), colour, start_opacity=start_opacity, duration_ms=duration_ms)


class PictureFade(QWidget):
    """A picture of what was shown, dissolving into what replaced it.

    Plan F5.4: the previous image fades out over the new one. It shows the
    old picture, fully opaque, until :meth:`start`; given the new picture
    too, it is opaque and composites both, so each frame is two blits rather
    than a repaint of the canvas beneath (21–27 ms a frame natively). Fixed
    time, eased out, on the shared ticker; clicks pass through; it removes
    itself.
    """

    def __init__(self, parent: QWidget, geometry: QRect, picture: QPixmap, *, duration_ms: float) -> None:
        super().__init__(parent)
        self.setObjectName("pictureFade")
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setGeometry(geometry)
        self._picture = picture
        self._beneath: QPixmap | None = None
        self.opacity = 1.0
        self._fade = Tween(duration_ms, on_update=self._faded, on_finished=self._finish, widget=self)
        self.destroyed.connect(lambda *_args, fade=self._fade: fade.stop() if fade.is_animating else None)
        self.show()
        self.raise_()

    def start(self, beneath: QPixmap | None = None) -> None:
        """Begin dissolving into ``beneath`` (or the live canvas, without it)."""

        if beneath is not None:
            self._beneath = beneath
            self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent)
        self._fade.start()

    @property
    def started(self) -> bool:
        return self._fade.is_animating or self._fade.settled

    def _faded(self, progress: float) -> None:
        self.opacity = 1.0 - progress
        self.update()

    def paintEvent(self, _event) -> None:  # noqa: N802 - Qt API
        painter = QPainter(self)
        if self._beneath is not None:
            painter.fillRect(self.rect(), QColor(0, 0, 0))
            painter.drawPixmap(0, 0, self._beneath)
        painter.setOpacity(max(0.0, min(1.0, self.opacity)))
        painter.drawPixmap(0, 0, self._picture)

    def dismiss(self) -> None:
        """Remove it now (an interaction, or another image, replaces it)."""

        self._fade.stop()
        self._finish()

    def _finish(self) -> None:
        self.hide()
        self.deleteLater()


# -- the theme switch (plan F5.8) -------------------------------------------------


class ThemeSeed(QWidget):
    """The theme button's icon, already in the new theme, shown at once.

    Plan F5.8a. Restyling the window for a new theme takes a few hundred
    milliseconds before anything repaints. This disc (the new theme's toolbar
    colour, with the new sun or moon icon) is painted straight to the screen
    first, so the click registers immediately; it blends into the toolbar
    once the switch lands, and is removed then. The radial reveal (F5.8b)
    grows from it. Clicks pass through.
    """

    def __init__(
        self,
        parent: QWidget,
        centre: QPoint,
        diameter: int,
        fill: QColor,
        edge: QColor,
        icon: QPixmap | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("themeSeed")
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setGeometry(QRect(centre.x() - diameter // 2, centre.y() - diameter // 2, diameter, diameter))
        self.fill = QColor(fill)
        self._edge = QColor(edge)
        self._icon = icon
        self.show()
        self.raise_()
        # Now, before the restyle blocks the event loop: paint and flush.
        self.repaint()

    def paintEvent(self, _event) -> None:  # noqa: N802 - Qt API
        painter = QPainter(self)
        paint_theme_seed(painter, QRectF(self.rect()).center(), self.width(), self.fill, self._edge, self._icon)

    def dismiss(self) -> None:
        self.hide()
        self.deleteLater()


def paint_theme_seed(
    painter: QPainter, centre: QPointF, diameter: float, fill: QColor, edge: QColor, icon: QPixmap | None
) -> None:
    """The seed disc: the new theme's toolbar colour, with its sun or moon."""

    painter.save()
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(QPen(edge, 1.0))
    painter.setBrush(fill)
    radius = diameter / 2.0 - 0.5
    painter.drawEllipse(centre, radius, radius)
    if icon is not None and not icon.isNull():
        ratio = icon.devicePixelRatio() or 1.0
        size = icon.size() / ratio
        painter.drawPixmap(QPointF(centre.x() - size.width() / 2.0, centre.y() - size.height() / 2.0), icon)
    painter.restore()


#: The reveal (plan §3b, decided 2026-09-26): the new theme radiates out of the
#: theme button in both directions, with a soft edge (four feather rings) and
#: one faint accent glow on the rim. 560 ms, ease-out: a quick burst that
#: settles.
THEME_REVEAL_MS = 560
THEME_REVEAL_FEATHER_PX = 36.0
THEME_REVEAL_FEATHER_RINGS = 4
THEME_REVEAL_GLOW_WIDTH_PX = 6.0
THEME_REVEAL_GLOW_ALPHA = 34
#: Event-loop turns to let the switch settle before the new theme is pictured:
#: the dashboard's deferred rebuild, layout requests and deferred deletes.
#: Too early pictured ghosts of the old cards, or a blank dashboard.
THEME_REVEAL_SETTLE_TURNS = 3


#: Height of the bands the swept ring is covered with, in pixels.
ANNULUS_BAND_PX = 24


def annulus_region(origin: QPointF, inner: float, outer: float, bounds: QRect, *, band: int = ANNULUS_BAND_PX) -> QRegion:
    """A region covering the ring between two radii, in a few rectangles.

    ``QRegion``'s own ellipse is one rectangle per scan line: 300 to 1,000
    for a frame of the theme reveal, each clipped and flushed separately.
    Bands ``band`` pixels tall, at most two rectangles each, cover the same
    ring (a little generously) in about a tenth as many.
    """

    region = QRegion()
    cx, cy = origin.x(), origin.y()
    top = max(bounds.top(), math.floor(cy - outer))
    bottom = min(bounds.bottom() + 1, math.ceil(cy + outer))
    y = top
    while y < bottom:
        y_end = min(bottom, y + band)
        # Nearest and farthest rows of the band from the centre.
        near = 0.0 if y <= cy <= y_end else min(abs(y - cy), abs(y_end - cy))
        far = max(abs(y - cy), abs(y_end - cy))
        reach = math.sqrt(max(0.0, outer * outer - near * near))
        hole = math.sqrt(inner * inner - far * far) if inner > far else 0.0
        left, right = math.floor(cx - reach), math.ceil(cx + reach)
        if hole <= 0.0:
            spans = [(left, right)]
        else:
            spans = [(left, math.ceil(cx - hole)), (math.floor(cx + hole), right)]
        for start, end in spans:
            start, end = max(bounds.left(), start), min(bounds.right() + 1, end)
            if end > start:
                region = region.united(QRect(start, y, end - start, y_end - y))
        y = y_end
    return region


def paint_theme_reveal(
    painter: QPainter,
    rect: QRectF,
    old: QPixmap,
    new: QPixmap,
    origin: QPointF,
    radius: float,
    *,
    glow: QColor | None = None,
    feather_px: float = THEME_REVEAL_FEATHER_PX,
    rings: int = THEME_REVEAL_FEATHER_RINGS,
) -> None:
    """One reveal frame: ``new`` inside a feathered circle, ``old`` outside.

    Three things the prototype found (plan §3b):
    - the feather is filled annulus paths, not pen strokes: a textured pen
      ignores the picture's device pixel ratio (ghost text at 150%);
    - texture brushes already honour it, so no brush scaling is added;
    - fills, not anti-aliased clip paths, which built window-sized masks.
    """

    painter.drawPixmap(rect, old, QRectF(old.rect()))
    half = feather_px / 2.0
    if radius + half <= 0:
        return
    painter.save()
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    brush = QBrush(new)
    brush.setTransform(QTransform.fromTranslate(rect.x(), rect.y()))
    painter.setBrush(brush)
    inner = max(0.0, radius - half)
    if inner > 0:
        painter.drawEllipse(origin, inner, inner)
    ring = feather_px / max(1, rings)
    for step in range(max(1, rings)):
        near, far = inner + ring * step, inner + ring * (step + 1)
        annulus = QPainterPath()
        annulus.setFillRule(Qt.FillRule.OddEvenFill)
        annulus.addEllipse(origin, far, far)
        if near > 0:
            annulus.addEllipse(origin, near, near)
        painter.setOpacity(1.0 - (step + 0.5) / max(1, rings))
        painter.drawPath(annulus)
    painter.setOpacity(1.0)
    if glow is not None and glow.alpha() and radius > 2:
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(glow, THEME_REVEAL_GLOW_WIDTH_PX))
        painter.drawEllipse(origin, radius, radius)
    painter.restore()


class ThemeRevealOverlay(QWidget):
    """The new theme radiating out of the theme button over the old one.

    Plan F5.8b. It covers the window with a picture of the old theme (and the
    seed of the new one at the button) while the theme switches underneath;
    once the switch settles it pictures the new theme and grows it from the
    seed. Opaque, so nothing beneath repaints meanwhile, and only the ring
    swept since the last frame is repainted.

    Input reaches the live window, already in the new theme, and ends the
    reveal at once (``INTERRUPTING_INPUT``, as the loader's hand-off does):
    otherwise the picture went on showing a button's old state after a click
    had changed it. ``finish`` jumps to the end (also a second toggle, or a
    resized window).
    """

    def __init__(
        self,
        parent: QWidget,
        old: QPixmap,
        origin: QPointF,
        *,
        seed_diameter: float,
        seed_fill: QColor,
        seed_edge: QColor,
        seed_icon: QPixmap | None,
        glow: QColor,
        on_finished: Callable[[], None] | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("themeReveal")
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent)
        self.setGeometry(parent.rect())
        self._old = old
        self._new: QPixmap | None = None
        self.origin = QPointF(origin)
        self._seed = (seed_diameter, QColor(seed_fill), QColor(seed_edge), seed_icon)
        self._glow = QColor(glow)
        self._on_finished = on_finished
        self._finished = False
        self._capture: Callable[[], QPixmap] | None = None
        self._turns_left = 0
        self._start_radius = seed_diameter / 2.0 + THEME_REVEAL_FEATHER_PX / 2.0
        self._end_radius = self._far_radius() + THEME_REVEAL_FEATHER_PX
        self.radius = self._start_radius
        self._settle_timer = QTimer(self)
        self._settle_timer.setSingleShot(True)
        self._settle_timer.timeout.connect(self._settle_turn)
        self._grow = Tween(THEME_REVEAL_MS, on_update=self._grown, on_finished=self.finish, widget=self)
        self.destroyed.connect(lambda *_args, tween=self._grow: tween.stop() if tween.is_animating else None)
        # Input is watched from ``after_switch``: an application filter during
        # the restyle ran for each of its tens of thousands of events (+80 ms).
        parent.installEventFilter(self)
        self.show()
        self.raise_()
        # Now, before the switch blocks the event loop: paint and flush.
        self.repaint()

    def eventFilter(self, watched, event) -> bool:  # noqa: N802 - Qt API
        kind = event.type()
        # Input: act on the live window, and show what it did. A resized
        # window makes both pictures wrong: show it as it is.
        if kind in INTERRUPTING_INPUT or (kind == QEvent.Type.Resize and watched is self.parentWidget()):
            self.finish()
        return False

    @property
    def revealing(self) -> bool:
        return self._grow.is_animating

    @property
    def has_new_picture(self) -> bool:
        return self._new is not None

    def after_switch(self, capture: Callable[[], QPixmap]) -> None:
        """The theme has switched underneath: settle, picture it, and grow."""

        if self._finished:
            return
        # The whole application's events from now: input anywhere ends the
        # reveal. Input sent during the restyle was queued, and arrives after.
        QApplication.instance().installEventFilter(self)
        self._capture = capture
        self._turns_left = THEME_REVEAL_SETTLE_TURNS
        self._settle_timer.start(0)

    def _settle_turn(self) -> None:
        if self._finished:
            return
        if self._turns_left > 0:
            self._turns_left -= 1
            self._settle_timer.start(0)
            return
        capture, self._capture = self._capture, None
        if capture is None:
            return
        self.hide()  # out of the picture; nothing paints before it is back
        try:
            self._new = capture()
        finally:
            self.show()
            self.raise_()
        self.repaint()
        self._grow.start()

    def _far_radius(self) -> float:
        rect = QRectF(self.rect())
        return max(
            math.hypot(self.origin.x() - corner.x(), self.origin.y() - corner.y())
            for corner in (rect.topLeft(), rect.topRight(), rect.bottomLeft(), rect.bottomRight())
        )

    def _grown(self, progress: float) -> None:
        previous = self.radius
        self.radius = self._start_radius + (self._end_radius - self._start_radius) * progress
        # Only the ring swept since the last frame, with the feather and glow.
        margin = THEME_REVEAL_FEATHER_PX / 2.0 + THEME_REVEAL_GLOW_WIDTH_PX + 2.0
        low = max(0.0, min(previous, self.radius) - margin)
        high = max(previous, self.radius) + margin
        self.update(annulus_region(self.origin, low, high, self.rect()))

    def paintEvent(self, _event) -> None:  # noqa: N802 - Qt API
        painter = QPainter(self)
        rect = QRectF(self.rect())
        if self._new is None:
            painter.drawPixmap(rect, self._old, QRectF(self._old.rect()))
            diameter, fill, edge, icon = self._seed
            paint_theme_seed(painter, self.origin, diameter, fill, edge, icon)
            return
        paint_theme_reveal(painter, rect, self._old, self._new, self.origin, self.radius, glow=self._glow)

    def finish(self) -> None:
        """End now, with the window as it is (the theme has already switched)."""

        if self._finished:
            return
        self._finished = True
        self._settle_timer.stop()
        self._grow.stop()
        app = QApplication.instance()
        if app is not None:
            app.removeEventFilter(self)
        parent = self.parentWidget()
        if parent is not None:
            parent.removeEventFilter(self)
        self.hide()
        self.deleteLater()
        if self._on_finished is not None:
            self._on_finished()


# -- the camera and the landing pulse (plan F5.7) ------------------------------

#: How much a flight zooms out to cross a distance (van Wijk and Nuij's rho;
#: they found about 1.4 reads as the most natural).
CAMERA_PATH_RHO = 1.4


@dataclass(frozen=True)
class ZoomPanPath:
    """A smooth zoom-and-pan path between two views of a plane.

    Views are a centre and a visible width. ``at(s)`` for ``s`` in
    ``[0, length]`` gives the view along the way; ``length`` measures the
    trip, so its duration can follow it.
    """

    start: tuple[float, float]
    end: tuple[float, float]
    start_width: float
    distance: float
    length: float
    rho: float
    r0: float
    zoom_sign: float

    def at(self, s: float) -> tuple[float, float, float]:
        s = min(self.length, max(0.0, s))
        w0, rho = self.start_width, self.rho
        if self.distance <= 0.0:
            width = w0 * math.exp(self.zoom_sign * rho * s)
            return self.start[0], self.start[1], width
        r0 = self.r0
        travelled = w0 / rho**2 * (math.cosh(r0) * math.tanh(rho * s + r0) - math.sinh(r0))
        width = w0 * math.cosh(r0) / math.cosh(rho * s + r0)
        fraction = min(1.0, max(0.0, travelled / self.distance))
        x = self.start[0] + (self.end[0] - self.start[0]) * fraction
        y = self.start[1] + (self.end[1] - self.start[1]) * fraction
        return x, y, width


def zoom_pan_path(
    start: tuple[float, float],
    start_width: float,
    end: tuple[float, float],
    end_width: float,
    *,
    rho: float = CAMERA_PATH_RHO,
) -> ZoomPanPath:
    """The path of van Wijk and Nuij (2003), "Smooth and efficient zooming
    and panning": a nearby target is panned to with a little zoom; a far one
    zooms out, crosses, and zooms back in, so the reviewer sees where the
    camera went instead of a blur of image.
    """

    w0, w1 = max(start_width, 1e-9), max(end_width, 1e-9)
    distance = math.hypot(end[0] - start[0], end[1] - start[1])
    if distance <= 1e-6 * max(w0, w1):
        return ZoomPanPath(
            start, end, w0, 0.0, abs(math.log(w1 / w0)) / rho, rho, 0.0, -1.0 if w1 < w0 else 1.0
        )
    # ln(-b + sqrt(b² + 1)) is -asinh(b), without the cancellation.
    b0 = (w1 * w1 - w0 * w0 + rho**4 * distance * distance) / (2.0 * w0 * rho**2 * distance)
    b1 = (w1 * w1 - w0 * w0 - rho**4 * distance * distance) / (2.0 * w1 * rho**2 * distance)
    r0, r1 = -math.asinh(b0), -math.asinh(b1)
    return ZoomPanPath(start, end, w0, distance, (r1 - r0) / rho, rho, r0, 1.0)


def camera_duration_ms(path_length: float) -> float:
    """``DURATION_CAMERA_MS`` for a hop, longer for a longer trip, capped."""

    return min(DURATION_CAMERA_MAX_MS, DURATION_CAMERA_MS + 160.0 * max(0.0, path_length - 1.0))


class MarkerPulse(QWidget):
    """One ring expanding from a marker and fading, where a jump landed.

    Plan F5.7. It floats over the canvas as a widget of its own, so a scene
    render (an image export, a crossfade's picture) never contains it, and it
    touches no marker. ``locate`` gives the marker's centre and radius in this
    widget's coordinates, or None; it is asked on every paint, so the ring
    stays on the marker if the view moves. Only the ring's own band is
    repainted each frame. Clicks pass through.
    """

    def __init__(
        self,
        parent: QWidget,
        locate: Callable[[], tuple[QPointF, float] | None],
        colour: QColor,
        *,
        duration_ms: float = DURATION_PULSE_MS,
        spread_px: float = 22.0,
        width_px: float = 2.5,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("markerPulse")
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setGeometry(parent.rect())
        self._locate = locate
        self._colour = QColor(colour)
        self._spread = spread_px
        self._width = width_px
        self.progress = 0.0
        self._painted: QRect | None = None
        self._tween = Tween(duration_ms, on_update=self._stepped, on_finished=self._finish, widget=self)
        self.destroyed.connect(lambda *_args, tween=self._tween: tween.stop() if tween.is_animating else None)
        parent.installEventFilter(self)
        self.show()
        self.raise_()
        self._tween.start()

    def ring(self) -> tuple[QPointF, float, float] | None:
        """The ring now: centre, radius and opacity."""

        found = self._locate()
        if found is None:
            return None
        centre, radius = found
        return centre, radius + self._spread * self.progress, 0.9 * (1.0 - self.progress)

    def _ring_rect(self) -> QRect | None:
        ring = self.ring()
        if ring is None:
            return None
        centre, radius, _opacity = ring
        reach = radius + self._width + 2.0
        return QRect(
            math.floor(centre.x() - reach), math.floor(centre.y() - reach), math.ceil(2 * reach) + 1, math.ceil(2 * reach) + 1
        )

    def _stepped(self, progress: float) -> None:
        self.progress = progress
        rect = self._ring_rect()
        dirty = rect if self._painted is None else (rect.united(self._painted) if rect is not None else self._painted)
        self._painted = rect
        if dirty is not None:
            self.update(dirty)

    def paintEvent(self, _event) -> None:  # noqa: N802 - Qt API
        ring = self.ring()
        if ring is None:
            return
        centre, radius, opacity = ring
        colour = QColor(self._colour)
        colour.setAlphaF(max(0.0, min(1.0, opacity)))
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(QPen(colour, self._width))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawEllipse(centre, radius, radius)

    def eventFilter(self, watched, event) -> bool:  # noqa: N802 - Qt API
        if event.type() == QEvent.Type.Resize and watched is self.parentWidget():
            self.setGeometry(watched.rect())
        return False

    def dismiss(self) -> None:
        self._tween.stop()
        self._finish()

    def _finish(self) -> None:
        parent = self.parentWidget()
        if parent is not None:
            parent.removeEventFilter(self)
        self.hide()
        self.deleteLater()


# -- section and branch reveals -----------------------------------------------


def reveal_strips(height: int, full: int, fixed: int, riding: int, *, grows_down: bool) -> list[tuple[int, int, int]]:
    """How to draw a section ``full`` rows tall, open to ``height`` rows.

    Returns ``(source_row, target_row, rows)`` strips of the open picture.
    The ``fixed`` rows at the anchored edge stay where they are; the
    ``riding`` rows at the far edge travel with it; everything between stays
    in place and is uncovered or covered as the edge moves. When the section
    is thinner than both edge bands together, it shows a proportional share of
    each, so a panel opening from nothing still has both of its borders.
    """

    h = max(0, min(full, height))
    if h <= 0:
        return []
    if h >= fixed + riding:
        still = h - riding
        if grows_down:
            strips = [(0, 0, still), (full - riding, still, riding)]
        else:
            strips = [(0, full - h, riding), (full - still, full - still, still)]
    else:
        near = round(h * fixed / (fixed + riding)) if fixed + riding else 0
        far = h - near
        if grows_down:
            strips = [(0, 0, near), (full - far, near, far)]
        else:
            strips = [(0, full - h, far), (full - near, full - near, near)]
    return [strip for strip in strips if strip[2] > 0]


def settle_layouts(widget: QWidget, up_to: QWidget) -> None:
    """Apply pending layout changes from ``widget`` up to ``up_to`` now.

    Innermost first: each activation marks its parent's layout for another
    pass, so after this every geometry on the way is final and a picture can
    be taken before the next paint.
    """

    widget.updateGeometry()
    node: QWidget | None = widget
    while node is not None:
        layout = node.layout()
        if layout is not None:
            layout.activate()
        if node is up_to:
            break
        node = node.parentWidget()


_CONCEAL_EFFECT_NAME = "sectionRevealConceal"


def _conceal_with_effect(section: QWidget, concealed: bool) -> None:
    """Hide a section's pixels while it keeps its place in the layout.

    A fully transparent opacity effect draws nothing, the widget and its
    children included, and costs nothing per frame. A section with an effect
    of its own keeps it: replacing it would delete it.
    """

    current = section.graphicsEffect()
    if concealed:
        if current is None:
            effect = QGraphicsOpacityEffect(section)
            effect.setObjectName(_CONCEAL_EFFECT_NAME)
            effect.setOpacity(0.0)
            section.setGraphicsEffect(effect)
    elif current is not None and current.objectName() == _CONCEAL_EFFECT_NAME:
        section.setGraphicsEffect(None)


def live_section_reveal(section: QWidget) -> SectionReveal | None:
    """The reveal currently showing ``section``, if any."""

    reveal = getattr(section, "_tomo_section_reveal", None)
    if reveal is None or not shiboken6.isValid(reveal):
        return None
    return reveal


class SectionReveal(QWidget):
    """A floating section's picture opening or closing along its height.

    Plan F5.6. The section itself changes at once (one layout, as before) and
    is concealed underneath while this widget shows its open picture at a
    height that follows a spring. Content stays in place and is uncovered as
    the far edge moves; the ``riding`` rows at that edge (a border, or the
    controls below a sub-section) travel with it. Resizing the section for
    real every frame would re-lay out the window each time.

    It floats over the viewer's canvas, so only the band that changes is
    repainted each frame. Clicks pass through. Toggling again mid-way
    retargets the same spring (:meth:`retarget`), so a reversal continues
    from where it is. A resized parent, or the section hidden while it
    opens, removes it at once.
    """

    def __init__(
        self,
        section: QWidget,
        parent: QWidget,
        geometry: QRect,
        picture: QPixmap,
        *,
        grows_down: bool,
        fixed: int,
        riding: int,
        closed: int,
        opening: bool,
        kind: str = "",
        conceal: Callable[[bool], None] | None = None,
        spec: SpringSpec = MOTION_PANEL,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("sectionReveal")
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setGeometry(geometry)
        self.kind = kind
        self._finished = False
        self._section = section
        self._picture = picture
        self._grows_down = grows_down
        self._fixed = max(0, fixed)
        self._riding = max(0, riding)
        self._open = float(geometry.height())
        self._closed = float(max(0, min(closed, geometry.height())))
        self._conceal = conceal if conceal is not None else (lambda hidden: _conceal_with_effect(section, hidden))
        start = self._closed if opening else self._open
        self._shown = start
        self._height = Spring(
            start,
            spec=spec,
            on_update=self._moved,
            on_settled=self._finish,
            widget=self,
            settle_distance=0.5,
            settle_velocity=20.0,
        )
        self.destroyed.connect(
            lambda *_args, spring=self._height: spring.reset(spring.value) if spring.is_animating else None
        )
        previous = live_section_reveal(section)
        if previous is not None:
            previous.dismiss()
        section._tomo_section_reveal = self  # type: ignore[attr-defined]
        self._conceal(True)
        parent.installEventFilter(self)
        section.installEventFilter(self)
        self.show()
        self.raise_()
        self.retarget(opening)

    @property
    def opening(self) -> bool:
        """Whether it is heading open (it may be part-way either way)."""

        return self._height.target >= self._open

    @property
    def height_shown(self) -> float:
        return self._height.value

    def retarget(self, opening: bool, picture: QPixmap | None = None) -> None:
        """Head open or closed from wherever it is now.

        ``picture`` replaces the open picture when the section's own look
        follows its state (an arrow that turns).
        """

        if picture is not None and picture.size() == self._picture.size():
            self._picture = picture
            self.update()
        self._height.set_target(self._open if opening else self._closed)
        if not self._height.is_animating:  # already there, or motion is off
            self._finish()

    def align_to(self, section_rect: QRect) -> None:
        """Keep the anchored edge on the section's, which moved (in parent coordinates)."""

        if self._grows_down:
            self.move(section_rect.left(), section_rect.top())
        else:
            self.move(section_rect.left(), section_rect.bottom() + 1 - self.height())

    def dismiss(self) -> None:
        """Remove it now, finished or not: the section shows as it really is."""

        self._height.reset(self._height.value)
        self._finish()

    def eventFilter(self, watched, event) -> bool:  # noqa: N802 - Qt API
        kind = event.type()
        if kind == QEvent.Type.Resize and watched is self.parentWidget():
            self.dismiss()
        elif kind == QEvent.Type.Hide and watched is self._section:
            # Hidden while opening, or hidden with its surroundings (an empty
            # canvas hides the floating controls): nothing left to show.
            # Hidden on its own while closing is this reveal's own doing.
            if self.opening or not self._section.isHidden():
                self.dismiss()
        return False

    def _moved(self, value: float) -> None:
        old, self._shown = self._shown, value
        low, high = sorted((old, value))
        width = self.width()
        if self._grows_down:
            top = 0.0 if low < self._fixed + self._riding else low - self._riding
            rect = QRect(0, math.floor(top) - 1, width, math.ceil(high) - math.floor(top) + 2)
        else:
            full = float(self.height())
            bottom = full if low < self._fixed + self._riding else full - low + self._riding
            top = full - high
            rect = QRect(0, math.floor(top) - 1, width, math.ceil(bottom) - math.floor(top) + 2)
        self.update(rect)

    def paintEvent(self, _event) -> None:  # noqa: N802 - Qt API
        picture = self._picture
        ratio = picture.devicePixelRatio() or 1.0
        strips = reveal_strips(
            round(self._height.value * ratio),
            picture.height(),
            round(self._fixed * ratio),
            round(self._riding * ratio),
            grows_down=self._grows_down,
        )
        painter = QPainter(self)
        width = float(self.width())
        for source, target, rows in strips:
            painter.drawPixmap(
                QRectF(0.0, target / ratio, width, rows / ratio),
                picture,
                QRectF(0.0, float(source), float(picture.width()), float(rows)),
            )

    def _finish(self) -> None:
        if self._finished:
            return
        self._finished = True
        section = self._section
        if shiboken6.isValid(section):
            section.removeEventFilter(self)
            # Only while it is still this reveal's section to show: a newer
            # reveal may have concealed it already.
            if getattr(section, "_tomo_section_reveal", None) is self:
                section._tomo_section_reveal = None  # type: ignore[attr-defined]
                self._conceal(False)
        parent = self.parentWidget()
        if parent is not None:
            parent.removeEventFilter(self)
        self.hide()
        self.deleteLater()


class BranchReveal(QWidget):
    """Rows opening or closing under a tree row (plan F5.6).

    The tree expands or collapses at once; this covers the viewport from the
    toggled row down with two pictures of it, open and closed. The open rows
    are uncovered in place while the rows that follow ride down from under
    the toggled row (or back up over them). It is opaque, so each frame is two
    blits, and clicks pass through. Qt's own animation was rejected: it
    swallows clicks on other rows while it runs.
    """

    def __init__(
        self,
        viewport: QWidget,
        top: int,
        opened: QPixmap,
        closed: QPixmap,
        extent: int,
        *,
        opening: bool,
        spec: SpringSpec = MOTION_PANEL,
    ) -> None:
        super().__init__(viewport)
        self.setObjectName("branchReveal")
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent)
        self.setGeometry(0, top, viewport.width(), viewport.height() - top)
        self._top = top
        self._opened = opened
        self._closed = closed
        self._extent = float(max(0, min(extent, self.height())))
        start = 0.0 if opening else self._extent
        self._shown = start
        self._offset = Spring(
            start,
            spec=spec,
            on_update=self._moved,
            on_settled=self._finish,
            widget=self,
            settle_distance=0.5,
            settle_velocity=20.0,
        )
        self.destroyed.connect(
            lambda *_args, spring=self._offset: spring.reset(spring.value) if spring.is_animating else None
        )
        self.show()
        self.raise_()
        self._offset.set_target(self._extent if opening else 0.0)
        if not self._offset.is_animating:  # nothing to travel, or motion is off
            self._finish()

    @property
    def offset(self) -> float:
        """How far the following rows sit below the toggled row."""

        return self._offset.value

    def dismiss(self) -> None:
        self._offset.reset(self._offset.value)
        self._finish()

    def _moved(self, value: float) -> None:
        low = min(self._shown, value)
        self._shown = value
        top = max(0, math.floor(low) - 1)
        self.update(QRect(0, top, self.width(), self.height() - top))

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt API
        ratio = self._opened.devicePixelRatio() or 1.0
        source_top = round(self._top * ratio)
        rows = max(0, min(self._opened.height(), self._closed.height()) - source_top)
        offset = max(0, min(rows, round(self._offset.value * ratio)))
        painter = QPainter(self)
        # Opaque widget: nothing is painted beneath it. Viewport pictures are
        # opaque natively; the fill only guards a style that leaves gaps.
        painter.fillRect(event.rect(), self.palette().color(self.backgroundRole()))
        width = float(self.width())
        pixel_width = float(self._opened.width())
        if offset:
            painter.drawPixmap(
                QRectF(0.0, 0.0, width, offset / ratio),
                self._opened,
                QRectF(0.0, float(source_top), pixel_width, float(offset)),
            )
        if rows - offset:
            painter.drawPixmap(
                QRectF(0.0, offset / ratio, width, (rows - offset) / ratio),
                self._closed,
                QRectF(0.0, float(source_top), pixel_width, float(rows - offset)),
            )

    def _finish(self) -> None:
        self.hide()
        self.deleteLater()
