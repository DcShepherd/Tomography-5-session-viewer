from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import gc
import logging
import time

from PySide6.QtCore import QEvent, QRect, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QImage, QPainter, QPen, QPixmap
from PySide6.QtWidgets import QApplication, QWidget
import shiboken6

from tomography_session_browser.ui.animations import animations_enabled
from tomography_session_browser.ui.motion import INTERRUPTING_INPUT, Tween, ease_in_out_cubic
from tomography_session_browser.ui.widgets.loader_orbs import (
    NATIVE_LOADER_PROPERTY,
    ORB_AREA,
    PAINTER_FRAME_MS,
    STAGE_GATHER,
    OrbClock,
    OrbColours,
    QuickOrbs,
    RendererChoice,
    choose_renderer,
    orb_image,
)

LOGGER = logging.getLogger(__name__)

#: The stages of a load, in order, as the stage rail names them (plan §3.2).
#: They replaced a monospace footer that guessed the stage from the title.
LOADING_STAGES = (
    ("discover", "Discover"),
    ("parse", "Parse"),
    ("link", "Link"),
    ("build", "Build"),
    ("finish", "Finish"),
)
_STAGE_KEYS = tuple(key for key, _label in LOADING_STAGES)
#: The current stage's segment shows at least this much, so it reads as
#: under way; without a count it shows this much.
_CURRENT_STAGE_MIN_FILL = 0.08
_CURRENT_STAGE_UNCOUNTED_FILL = 0.3
_RAIL_THICKNESS_PX = 3.0
_BRAND_SIZE_PX = 30.0
#: The Quick orbs' 2x2 helper window sits this far inside the card's corner,
#: on the card's own colour (see ``QuickOrbs``).
_HELPER_INSET_PX = 16
#: Between the two steps of ``prewarm``.
_WARM_STEP_MS = 600

#: The hand-off (plan §3.4, F6.3): the orbs coalesce into one over the first
#: ``COALESCE_MS``; from ``CARD_LEAVES_AFTER_MS`` the card scales to
#: ``CARD_END_SCALE`` and fades while the scrim lifts and the interface
#: comes into focus. The plan's budget is 300 ms in all.
HAND_OFF_MS = 300
COALESCE_MS = 180
CARD_LEAVES_AFTER_MS = 60
CARD_END_SCALE = 0.96
#: Passes over the finished interface's pending layouts before its picture
#: (a layout's activation can ask its parent's to run again).
HAND_OFF_LAYOUT_PASSES = 3
#: The backdrop's blur: the picture scaled down this much and back up. It
#: comes in as the orbs gather and clears as the scrim lifts.
BACKDROP_BLUR_DOWNSCALE = 8


class _GcPause:
    """Python's cyclic garbage collector held off for a short animation.

    Only if it was on, and always given back (a finished, cancelled or
    destroyed hand-off). Objects freed by reference counting still go at once.
    """

    def __init__(self) -> None:
        self._paused = False

    def pause(self) -> None:
        if not self._paused and gc.isenabled():
            gc.disable()
            self._paused = True

    def resume(self) -> None:
        if self._paused:
            self._paused = False
            gc.enable()


def _scrim(palette) -> QColor:
    scrim = QColor(palette.background)
    scrim.setAlpha(212 if palette.name == "dark" else 176)
    return scrim


@dataclass(frozen=True)
class _CardLayout:
    panel: QRectF
    brand: QRectF
    title: QRectF
    detail: QRectF
    orbs: QRect
    progress: QRectF
    rail_left: float
    rail_top: float
    rail_width: float
    rail_label_height: float
    title_font: QFont
    detail_font: QFont
    progress_font: QFont
    rail_font: QFont

    @property
    def helper(self) -> QRect:
        return QRect(int(self.panel.left()) + _HELPER_INSET_PX, int(self.panel.top()) + _HELPER_INSET_PX, 2, 2)


class LoadingOverlay(QWidget):
    """Prominent theme-aware loading overlay with thinking orbs (plan F6.2).

    Under the title, a line says what is being read or built ("Reading
    samples 4 of 13", "Building 3 of 6") and a rail shows the stage of the
    load: Discover, Parse, Link, Build, Finish. The rail is also the
    determinate bar: done stages full, the current one filling.

    The orbs are drawn by Qt Quick on its render thread when they can be, so
    they keep moving while the GUI thread builds the interface; otherwise they
    are painted here (``choose_renderer`` says which, and why). Under reduced
    motion they are a still picture that changes with the stage.
    """

    hidden = Signal()
    #: The load is done and the hand-off begins: the window should finish
    #: what it held back during the load now, under the overlay.
    leaving = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._message = "Loading session..."
        self._detail = "Please wait while the session is prepared."
        self._stage = _STAGE_KEYS[0]
        self._progress: tuple[str, int, int] | None = None
        self._shown_at = 0.0
        self._minimum_visible_ms = 350
        self._leaving = False
        # Painting nothing, so the screen keeps its look (see ``_hand_over``).
        self._held = False
        # None, or how far the hand-off has run (0..1).
        self._hand_off_progress: float | None = None
        self._hand_off = Tween(
            HAND_OFF_MS,
            on_update=self._hand_off_step,
            on_finished=self._finish_hand_off,
            widget=self,
            easing=lambda progress: progress,
        )
        self._gc_pause = _GcPause()
        self.destroyed.connect(
            lambda *_args, tween=self._hand_off, gc_pause=self._gc_pause: (tween.stop(), gc_pause.resume())
        )
        # The finished interface, sharp and blurred, in device pixels
        # (``_picture_ratio`` of them per logical pixel).
        self._backdrop: QPixmap | None = None
        self._blurred: QPixmap | None = None
        self._picture_ratio = 1.0
        self._card_picture: QPixmap | None = None
        self._card_bounds = QRect()
        self._orb_clock = OrbClock()
        self._renderer = RendererChoice("painter")
        self._quick: QuickOrbs | None = None
        # Keyboard focus while a load holds it: what had it before, and where
        # it goes when that is gone (the window sets this).
        self._focus_before: QWidget | None = None
        self.focus_after: Callable[[], QWidget | None] | None = None
        # off -> pending (shown after the card's first paint) -> starting
        # (waiting for its first frame) -> live (the Quick window covers the
        # painted orbs).
        self._quick_state = "off"
        self._quick_started_at = 0.0
        self._orbs_rect: QRect | None = None
        self._still: tuple[tuple, QImage] | None = None
        self._colour_key: tuple[str, str, str] | None = None
        # The painted orbs' frame timer (only when Qt Quick is not drawing
        # them). The orbs are slow, so 30 fps; the old dots ticked at 16 ms
        # and still measured a 47 ms median while the UI thread was busy.
        self._timer = QTimer(self)
        self._timer.setInterval(PAINTER_FRAME_MS)
        self._timer.setTimerType(Qt.TimerType.PreciseTimer)
        self._timer.timeout.connect(self._advance)
        self._last_tick_at = 0.0
        self._tick_intervals_ms: list[float] = []
        self._dropped_frames = 0
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, False)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAccessibleName("Loading overlay")
        self._sync_accessible_description()
        self.hide()

    def show_loading(self, message: str, detail: str | None = None) -> None:
        self._cancel_hand_off()
        self._message = message
        if detail is not None:
            self._detail = detail
        self._stage = _STAGE_KEYS[0]
        self._progress = None
        if self.parentWidget() is not None:
            self.setGeometry(self.parentWidget().rect())
        self._shown_at = time.monotonic()
        self._orb_clock.start(self._shown_at, self._stage)
        self._reset_timing()
        self._sync_accessible_description()
        self._stop_quick()
        self._renderer = self._choose_renderer()
        if not self.isVisible():
            # Given back when the load is done (``_give_focus_back``).
            focus = QApplication.focusWidget()
            self._focus_before = focus if focus is not None and focus is not self else None
        self.show()
        self.raise_()
        self.setFocus(Qt.FocusReason.OtherFocusReason)
        if self._renderer.kind == "quick":
            # Shown once the card has painted, so the orbs never appear
            # over the interface before the card does.
            self._quick_state = "pending"
        elif self._renderer.kind == "painter" and not self._timer.isActive():
            self._timer.start()
        self.update()

    def update_loading_message(self, message: str, detail: str | None = None) -> None:
        self._message = message
        if detail is not None:
            self._detail = detail
        self._sync_accessible_description()
        if self.isVisible():
            self.raise_()
            self.update()

    @property
    def stage(self) -> str:
        return self._stage

    @property
    def renderer(self) -> RendererChoice:
        """Which orbs this load shows, and why when it is not Qt Quick."""

        return self._renderer

    def prewarm(self) -> None:
        """Get the Quick orbs ready while the app is idle, so a load doesn't wait.

        Steps a moment apart, so none holds the GUI thread long: loading Qt
        Quick and the scene (~110 ms), then showing each window once out of
        sight (~160 ms each, creating its graphics device).
        """

        if choose_renderer(self).kind == "quick" and self._ensure_quick() is not None:
            QTimer.singleShot(_WARM_STEP_MS, self, self._warm_quick)

    def _warm_quick(self) -> None:
        if self._quick is None or self.isVisible():
            return
        if self._quick.warm(self._orb_colours()):
            QTimer.singleShot(_WARM_STEP_MS, self, self._warm_quick)

    def set_stage(self, stage: str) -> None:
        """Move the rail to ``stage`` (one of ``LOADING_STAGES``); its count restarts."""

        if stage not in _STAGE_KEYS or stage == self._stage:
            return
        self._stage = stage
        self._progress = None
        now = time.monotonic()
        self._orb_clock.set_stage(stage, now)
        if self._quick is not None and self._quick_state in ("starting", "live"):
            self._quick.follow(self._orb_clock, now)
        self._sync_accessible_description()
        self.update()

    def set_progress(self, label: str | None, current: int = 0, total: int = 0) -> None:
        """Say what is under way in this stage: ``current`` of ``total``."""

        self._progress = (label, int(current), int(total)) if label else None
        self._sync_accessible_description()
        self.update()

    def progress_text(self) -> str:
        """The line under the title: "Reading samples 4 of 13", or just a label."""

        if self._progress is None:
            return ""
        label, current, total = self._progress
        return f"{label} {current} of {total}" if total > 1 else label

    def _stage_fill(self, index: int) -> float:
        current = _STAGE_KEYS.index(self._stage)
        if index < current:
            return 1.0
        if index > current:
            return 0.0
        if self._progress is None or self._progress[2] <= 1:
            return _CURRENT_STAGE_UNCOUNTED_FILL
        _label, done, total = self._progress
        return max(_CURRENT_STAGE_MIN_FILL, min(1.0, (done - 1) / total))

    def hide_loading(self, *, fade: bool = True) -> None:
        if not self.isVisible():
            self._timer.stop()
            if not self._leaving:
                self._stop_quick()
            return
        if self._leaving:
            if not fade:
                self._finish_hand_off()
            return
        if not fade or not animations_enabled(self):
            self._timer.stop()
            self._stop_quick()
            self._give_focus_back()
            self.hide()
            self.hidden.emit()
            return
        elapsed_ms = int((time.monotonic() - self._shown_at) * 1000)
        remaining_ms = self._minimum_visible_ms - elapsed_ms
        if remaining_ms > 0:
            QTimer.singleShot(remaining_ms, self, lambda: self.hide_loading(fade=True))
            return
        self._begin_hand_off()

    # CamelCase aliases mirror the API named in the user request.
    def showLoading(self, message: str) -> None:  # noqa: N802
        self.show_loading(message)

    def updateLoadingMessage(self, message: str) -> None:  # noqa: N802
        self.update_loading_message(message)

    def hideLoading(self) -> None:  # noqa: N802
        self.hide_loading()

    # -- the hand-off (plan F6.3) ------------------------------------------------

    @property
    def is_leaving(self) -> bool:
        """The load is done and the overlay is handing over to the interface."""

        return self._leaving

    @property
    def blocking(self) -> bool:
        """Up, and still standing for a load: not while it hands over."""

        return self.isVisible() and not self._leaving

    def _begin_hand_off(self) -> None:
        """The load is done: finish the interface underneath, picture it, hand over.

        The window finishes what it held back during the load (``leaving``)
        while the orbs keep moving; after a few turns for its layouts, the
        overlay pictures the finished interface and plays the hand-off on
        that picture, so nothing beneath repaints per frame and nothing pops
        in afterwards. Clicks pass through at once, and any input ends it.
        """

        if self._leaving:
            return
        self._leaving = True
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        # One turn first, so whoever asked finishes (the window enables its
        # buttons) before the picture is taken.
        QTimer.singleShot(0, self, self._hand_over)

    def _hand_over(self) -> None:
        if not self._leaving or self._hand_off_progress is not None:
            return
        if not self.isVisible():
            self._finish_hand_off()
            return
        # Held as it is on screen while the window finishes the page under
        # it: an opaque widget that paints nothing keeps its pixels, so the
        # rebuilt page is not painted under the scrim, where nobody could see
        # it (~160 ms a turn, measured). The Quick orbs keep moving.
        self._held = True
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent, True)
        self.leaving.emit()
        # A turn for what the rebuild queued: new widgets show, and the page
        # they replace is deleted (~180 widgets, ~60 ms), then the picture.
        QTimer.singleShot(0, self, self._picture_and_start)

    def _picture_and_start(self) -> None:
        if not self._leaving or self._hand_off_progress is not None:
            return
        if not self.isVisible():
            self._finish_hand_off()
            return
        for _ in range(HAND_OFF_LAYOUT_PASSES):
            QApplication.sendPostedEvents(None, QEvent.Type.LayoutRequest)
        # The page is final: focus goes back before it is pictured, so the
        # picture shows it where it will be, and a key pressed during the
        # hand-off reaches that widget instead of this one as it goes.
        self._give_focus_back()
        self._capture_for_hand_off()
        self._held = False
        self._timer.stop()  # the hand-off repaints each frame itself
        self._hand_off_progress = 0.0
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent, self._backdrop is not None)
        # From here the picture is on screen: input must end it at once.
        QApplication.instance().installEventFilter(self)
        self._start_hand_off_motion()

    def _start_hand_off_motion(self) -> None:
        if not self._leaving or self._hand_off_progress is None or self._hand_off.is_animating:
            return
        # The first frame looks as the overlay did, with the card painting the
        # orbs as they are now before the Quick window leaves: nothing blank.
        quick_live = self._quick_state != "off"
        self._quick_state = "off"
        self.repaint()
        if quick_live and self._quick is not None:
            self._quick.park()
        # A full garbage collection paused a hand-off for ~95 ms (measured
        # after a large load); it waits the 300 ms instead.
        self._gc_pause.pause()
        self._hand_off.start()

    def _hand_off_step(self, progress: float) -> None:
        self._hand_off_progress = progress
        self.update()

    def _finish_hand_off(self) -> None:
        if not self._leaving:
            return
        self._cancel_hand_off()
        self._timer.stop()
        if self._quick is not None:
            self._quick.stop()
        self._quick_state = "off"
        self._give_focus_back()
        self.hide()
        self.hidden.emit()

    def _give_focus_back(self) -> None:
        """Return keyboard focus from the overlay to the interface.

        It took focus for the load, so keys never reached the page beneath,
        and never gave it back: hiding it left focus wherever Qt's tab chain
        went next, and a key pressed during the hand-off was lost with it.
        Focus returns to what had it before the load, if that still shows
        and takes focus, or else to ``focus_after`` (the project tree). If
        the reviewer has already put it somewhere, it stays there.
        """

        before, self._focus_before = self._focus_before, None
        window = self.window()
        holder = QApplication.focusWidget() or (window.focusWidget() if window is not None else None)
        if holder is not None and holder is not self:
            return
        for candidate in (before, self.focus_after() if self.focus_after is not None else None):
            if (
                candidate is not None
                and shiboken6.isValid(candidate)
                and candidate.isVisible()
                and candidate.isEnabled()
                and candidate.focusPolicy() != Qt.FocusPolicy.NoFocus
            ):
                candidate.setFocus(Qt.FocusReason.OtherFocusReason)
                return

    def _cancel_hand_off(self) -> None:
        if not self._leaving:
            return
        app = QApplication.instance()
        if app is not None:
            app.removeEventFilter(self)
        self._hand_off.stop()
        self._gc_pause.resume()
        self._leaving = False
        self._held = False
        self._hand_off_progress = None
        self._backdrop = self._blurred = self._card_picture = None
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, False)
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent, False)

    def eventFilter(self, watched, event) -> bool:  # noqa: N802 - Qt signature
        # Input acts on the finished interface at once (plan §5): the
        # picture must not hide what a click did.
        if self._hand_off_progress is not None and event.type() in INTERRUPTING_INPUT:
            self._finish_hand_off()
        return False

    def _capture_for_hand_off(self) -> None:
        """Picture the finished interface, sharp and blurred, and the card."""

        from tomography_session_browser.ui.theme import current_palette

        self._backdrop = self._blurred = self._card_picture = None
        window = self.parentWidget()
        if window is None or not window.isVisible():
            return
        siblings = window.children()
        above = [
            widget
            for widget in siblings[siblings.index(self) + 1 :]
            if isinstance(widget, QWidget) and widget.isVisible() and not widget.property(NATIVE_LOADER_PROPERTY)
        ]
        # Hidden for the grab only: the screen is not repainted in between.
        self.hide()
        for widget in above:
            widget.hide()
        try:
            picture = window.grab()
        finally:
            for widget in above:
                widget.show()
            self.show()
        palette = current_palette()
        # Kept in device pixels and drawn unscaled: a full-window picture drawn
        # at a 1.5 device pixel ratio with an opacity took ~18 ms a frame, and
        # ~2 ms in device pixels (measured).
        ratio = picture.devicePixelRatio()
        picture.setDevicePixelRatio(1.0)
        size = picture.size()
        small = picture.scaled(
            max(1, size.width() // BACKDROP_BLUR_DOWNSCALE),
            max(1, size.height() // BACKDROP_BLUR_DOWNSCALE),
            Qt.AspectRatioMode.IgnoreAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        blurred = small.scaled(size, Qt.AspectRatioMode.IgnoreAspectRatio, Qt.TransformationMode.SmoothTransformation)
        self._picture_ratio = ratio
        layout = self._card_layout()
        bounds = layout.panel.adjusted(-2, -2, 2, 2).toAlignedRect()
        card = QPixmap(round(bounds.width() * ratio), round(bounds.height() * ratio))
        card.setDevicePixelRatio(ratio)
        card.fill(Qt.GlobalColor.transparent)
        painter = QPainter(card)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.translate(-bounds.left(), -bounds.top())
        self._paint_card(painter, layout, palette, None)
        painter.end()
        self._backdrop, self._blurred = picture, blurred
        self._card_picture, self._card_bounds = card, bounds

    def _paint_hand_off(self, painter: QPainter, layout: _CardLayout, palette, colours: OrbColours) -> None:
        elapsed_ms = (self._hand_off_progress or 0.0) * HAND_OFF_MS
        coalesce = ease_in_out_cubic(min(1.0, elapsed_ms / COALESCE_MS))
        leaving = ease_in_out_cubic(
            min(1.0, max(0.0, (elapsed_ms - CARD_LEAVES_AFTER_MS) / (HAND_OFF_MS - CARD_LEAVES_AFTER_MS)))
        )
        if self._backdrop is not None:
            # The interface softens while the orbs gather and comes into
            # focus as the scrim lifts; the first frame is the loader's own.
            blur = min(1.0, elapsed_ms / COALESCE_MS) * (1.0 - leaving)
            painter.save()
            painter.scale(1.0 / self._picture_ratio, 1.0 / self._picture_ratio)
            painter.drawPixmap(0, 0, self._backdrop)
            if blur > 0.0:
                painter.setOpacity(blur)
                painter.drawPixmap(0, 0, self._blurred)
            painter.restore()
        scrim = _scrim(palette)
        scrim.setAlphaF(scrim.alphaF() * (1.0 - leaving))
        painter.fillRect(self.rect(), scrim)
        # The card and its orbs leave as one: composed first, then faded.
        ratio = self.devicePixelRatioF()
        if self._card_picture is None:
            bounds = layout.panel.adjusted(-2, -2, 2, 2).toAlignedRect()
            card = QPixmap(round(bounds.width() * ratio), round(bounds.height() * ratio))
            card.setDevicePixelRatio(ratio)
            card.fill(Qt.GlobalColor.transparent)
            card_painter = QPainter(card)
            card_painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            card_painter.translate(-bounds.left(), -bounds.top())
            self._paint_card(card_painter, layout, palette, None)
            card_painter.end()
        else:
            bounds, card = self._card_bounds, QPixmap(self._card_picture)
        now = time.monotonic()
        area = layout.orbs
        orbs = orb_image(
            round(area.width() * ratio),
            round(area.height() * ratio),
            self._orb_clock.time_at(now),
            self._orb_clock.gather_at(now),
            colours,
            coalesce,
        )
        card_painter = QPainter(card)
        card_painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        card_painter.drawImage(QRectF(area.translated(-bounds.topLeft())), orbs)
        card_painter.end()
        scale = 1.0 - (1.0 - CARD_END_SCALE) * leaving
        centre = layout.panel.center()
        painter.save()
        painter.setOpacity(1.0 - leaving)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        painter.translate(centre)
        painter.scale(scale, scale)
        painter.translate(-centre)
        painter.drawPixmap(bounds.topLeft(), card)
        painter.restore()

    def _advance(self) -> None:
        now = time.monotonic()
        if self._last_tick_at:
            interval_ms = (now - self._last_tick_at) * 1000
            self._tick_intervals_ms.append(interval_ms)
            expected_ms = max(1, self._timer.interval())
            if interval_ms > expected_ms * 2.5:
                self._dropped_frames += max(1, int(interval_ms // expected_ms) - 1)
        self._last_tick_at = now
        # Repaint only the orbs. A full-widget update refilled the
        # whole-window scrim every tick, which is the most expensive thing on
        # screen at exactly the moment the UI thread is busiest.
        if self._orbs_rect is None:
            self.update()
        else:
            self.update(self._orbs_rect)

    def performance_snapshot(self) -> dict[str, float | int | str]:
        intervals = list(self._tick_intervals_ms)
        intervals.sort()
        count = len(intervals)
        median = intervals[count // 2] if count else 0.0
        maximum = intervals[-1] if count else 0.0
        return {
            "target_interval_ms": self._timer.interval(),
            "tick_count": count,
            "median_interval_ms": round(median, 1),
            "max_interval_ms": round(maximum, 1),
            "dropped_frames": self._dropped_frames,
            "renderer": self._renderer.kind,
        }

    def _reset_timing(self) -> None:
        self._last_tick_at = 0.0
        self._tick_intervals_ms = []
        self._dropped_frames = 0

    def _sync_accessible_description(self) -> None:
        index = _STAGE_KEYS.index(self._stage)
        stage = f"Stage {index + 1} of {len(LOADING_STAGES)}: {LOADING_STAGES[index][1]}."
        progress = self.progress_text()
        self.setAccessibleDescription(
            " ".join(part.strip() for part in (self._message, self._detail, progress, stage) if part.strip())
        )

    # -- the orbs --------------------------------------------------------------

    def _choose_renderer(self) -> RendererChoice:
        # A load before the idle warm-up is done (within ~3 s of start-up)
        # readies Qt Quick itself: one pause of ~330 ms before the first orb,
        # then smooth. Painted orbs for such a load were measured and are
        # worse: they stop for up to ~375 ms at a time through the build,
        # where the Quick orbs, on the render thread, keep moving.
        choice = choose_renderer(self)
        if choice.kind == "quick" and self._ensure_quick() is None:
            return RendererChoice("painter", self._quick.error if self._quick is not None else "Qt Quick failed")
        return choice

    def _ensure_quick(self) -> QuickOrbs | None:
        if self._quick is None:
            self._quick = QuickOrbs(self)
            self._quick.ready.connect(self._quick_ready)
            self._quick.failed.connect(self._quick_failed)
        if not self._quick.prepare():
            LOGGER.info("Loading orbs are painted instead of Qt Quick: %s", self._quick.error)
            return None
        return self._quick

    def _start_quick(self) -> None:
        if self._quick_state != "starting" or self._quick is None or not self.isVisible():
            return
        layout = self._card_layout()
        self._orbs_rect = layout.orbs
        now = time.monotonic()
        self._quick_started_at = now
        self._quick.start(self._orb_clock, now, self._orb_colours(), layout.orbs, layout.helper)

    def _quick_ready(self) -> None:
        if self._quick_state != "starting":
            return
        # The render thread set the orbs going with this first frame.
        self._orb_clock.shift(time.monotonic() - self._quick_started_at)
        self._quick_state = "live"

    def _quick_failed(self, reason: str) -> None:
        self._quick_state = "off"
        self._renderer = RendererChoice("painter", reason)
        if self.isVisible() and self._hand_off_progress is None and not self._timer.isActive():
            self._timer.start()
        self.update()

    def _stop_quick(self) -> None:
        if self._quick is not None and self._quick_state != "off":
            self._quick.stop()
        self._quick_state = "off"

    def _place_quick(self) -> None:
        if self._quick is None or self._quick_state not in ("starting", "live"):
            return
        layout = self._card_layout()
        self._orbs_rect = layout.orbs
        self._quick.place(layout.orbs, layout.helper)

    def _orb_colours(self) -> OrbColours:
        from tomography_session_browser.ui.theme import current_palette

        return OrbColours.from_palette(current_palette())

    def _orbs_picture(self, rect: QRect, colours: OrbColours) -> QImage:
        if self._renderer.kind == "still":
            t = self._orb_clock.still_time()
            gather = STAGE_GATHER.get(self._orb_clock.stage, 0.0)
        else:
            now = time.monotonic()
            t = self._orb_clock.time_at(now)
            gather = self._orb_clock.gather_at(now)
        # The moving painted orbs are drawn at logical size (about 2 ms on the
        # GUI thread) and scaled; a still one at full resolution, once.
        moving = self._renderer.kind == "painter" or self._timer.isActive()
        scale = 1.0 if moving else self.devicePixelRatioF()
        size = (round(rect.width() * scale), round(rect.height() * scale))
        key = (t, gather, size, colours.key())
        if self._still is not None and self._still[0] == key:
            return self._still[1]
        image = orb_image(size[0], size[1], t, gather, colours)
        if not moving:
            self._still = (key, image)
        return image

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt signature
        super().resizeEvent(event)
        self._place_quick()

    def moveEvent(self, event) -> None:  # noqa: N802 - Qt signature
        super().moveEvent(event)
        # The Quick windows belong to this overlay's parent.
        self._place_quick()

    # -- painting ---------------------------------------------------------------

    def _card_layout(self) -> _CardLayout:
        from tomography_session_browser.ui.theme import (
            SANS_FONT_NAME,
            SPACE_L,
            SPACE_M,
            SPACE_S,
            SPACE_XL,
            SPACE_XS,
            SPACE_XXL,
            TYPE_BODY,
            TYPE_CAPTION,
            TYPE_HEADING,
        )

        panel_width = min(540, max(340, self.width() - 96))
        content_width = max(220, int(panel_width - 2 * SPACE_XXL))

        title_font = QFont(SANS_FONT_NAME, TYPE_HEADING, QFont.Weight.DemiBold)
        detail_font = QFont(SANS_FONT_NAME, TYPE_BODY)
        progress_font = QFont(SANS_FONT_NAME, TYPE_BODY, QFont.Weight.Medium)
        rail_font = QFont(SANS_FONT_NAME, TYPE_CAPTION)

        title_height = _text_height(title_font, content_width, self._message)
        detail_height = _text_height(detail_font, content_width, self._detail, word_wrap=True)
        progress_height = _text_height(progress_font, content_width, "Reading samples 88 of 88")
        rail_label_height = _text_height(rail_font, content_width, "Discover")
        orbs_height = ORB_AREA.height()
        panel_height = max(
            268,
            int(
                SPACE_XL
                + _BRAND_SIZE_PX
                + SPACE_M
                + title_height
                + SPACE_S
                + detail_height
                + SPACE_L
                + orbs_height
                + SPACE_M
                + progress_height
                + SPACE_S
                + _RAIL_THICKNESS_PX
                + SPACE_XS
                + rail_label_height
                + SPACE_XL
            ),
        )
        panel = QRectF(
            (self.width() - panel_width) / 2,
            (self.height() - panel_height) / 2,
            panel_width,
            panel_height,
        )
        brand = QRectF(panel.center().x() - _BRAND_SIZE_PX / 2, panel.top() + SPACE_XL, _BRAND_SIZE_PX, _BRAND_SIZE_PX)
        left = panel.left() + SPACE_XXL
        title = QRectF(left, brand.bottom() + SPACE_M, content_width, title_height)
        detail = QRectF(left, title.bottom() + SPACE_S, content_width, detail_height)
        orbs_width = min(ORB_AREA.width(), content_width)
        orbs = QRect(
            round(panel.center().x() - orbs_width / 2),
            round(detail.bottom() + SPACE_L),
            orbs_width,
            orbs_height,
        )
        rail_top = panel.bottom() - SPACE_XL - rail_label_height - SPACE_XS - _RAIL_THICKNESS_PX
        progress = QRectF(left, rail_top - SPACE_S - progress_height, content_width, progress_height)
        return _CardLayout(
            panel=panel,
            brand=brand,
            title=title,
            detail=detail,
            orbs=orbs,
            progress=progress,
            rail_left=left,
            rail_top=rail_top,
            rail_width=content_width,
            rail_label_height=rail_label_height,
            title_font=title_font,
            detail_font=detail_font,
            progress_font=progress_font,
            rail_font=rail_font,
        )

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt signature
        from tomography_session_browser.ui.theme import current_palette

        if self._held:
            return  # opaque and unpainted: the pixels on screen stay
        palette = current_palette()
        layout = self._card_layout()
        self._orbs_rect = layout.orbs
        colours = OrbColours.from_palette(palette)
        if colours.key() != self._colour_key:
            self._colour_key = colours.key()
            if self._quick is not None and self._quick_state in ("starting", "live"):
                self._quick.set_colours(colours)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        try:
            if self._hand_off_progress is not None:
                self._paint_hand_off(painter, layout, palette, colours)
            else:
                painter.fillRect(self.rect(), _scrim(palette))
                # Once the Quick window covers the orbs, they are not painted.
                show_orbs = self._quick_state != "live" and event.rect().intersects(layout.orbs)
                self._paint_card(painter, layout, palette, colours if show_orbs else None)
        finally:
            painter.end()
        if self._quick_state == "pending":
            self._quick_state = "starting"
            QTimer.singleShot(0, self._start_quick)

    def _paint_card(self, painter: QPainter, layout: _CardLayout, palette, colours: OrbColours | None) -> None:
        """The card, and the painted orbs in it unless ``colours`` is None."""

        from tomography_session_browser.ui.branding import draw_stack_mark
        from tomography_session_browser.ui.theme import RADIUS_POPOVER

        painter.setPen(QPen(QColor(palette.border_strong), 1.0))
        painter.setBrush(QColor(palette.surface))
        painter.drawRoundedRect(layout.panel, RADIUS_POPOVER, RADIUS_POPOVER)

        draw_stack_mark(painter, layout.brand, palette.name)

        painter.setFont(layout.title_font)
        painter.setPen(QColor(palette.text_strong))
        painter.drawText(layout.title, Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap, self._message)

        painter.setFont(layout.detail_font)
        painter.setPen(QColor(palette.text_muted))
        painter.drawText(layout.detail, Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap, self._detail)

        if colours is not None:
            painter.save()
            painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
            painter.drawImage(layout.orbs, self._orbs_picture(layout.orbs, colours))
            painter.restore()

        # What is under way, then the stage rail (plan §3.2).
        painter.setFont(layout.progress_font)
        painter.setPen(QColor(palette.text))
        painter.drawText(layout.progress, Qt.AlignmentFlag.AlignCenter, self.progress_text())
        self._paint_stage_rail(
            painter,
            palette,
            layout.rail_font,
            layout.rail_left,
            layout.rail_top,
            layout.rail_width,
            layout.rail_label_height,
        )

    def _paint_stage_rail(self, painter: QPainter, palette, font: QFont, left: float, top: float, width: float, label_height: float) -> None:
        from tomography_session_browser.ui.theme import SPACE_XS

        count = len(LOADING_STAGES)
        gap = float(SPACE_XS)
        segment = (width - gap * (count - 1)) / count
        current = _STAGE_KEYS.index(self._stage)
        painter.setFont(font)
        for index, (_key, label) in enumerate(LOADING_STAGES):
            x = left + index * (segment + gap)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(palette.border))
            track = QRectF(x, top, segment, _RAIL_THICKNESS_PX)
            radius = _RAIL_THICKNESS_PX / 2.0
            painter.drawRoundedRect(track, radius, radius)
            fill = self._stage_fill(index)
            if fill > 0.0:
                painter.setBrush(QColor(palette.accent))
                painter.drawRoundedRect(QRectF(x, top, segment * fill, _RAIL_THICKNESS_PX), radius, radius)
            if index == current:
                font.setWeight(QFont.Weight.DemiBold)
                colour = palette.text_strong
            else:
                font.setWeight(QFont.Weight.Normal)
                colour = palette.text if index < current else palette.text_muted
            painter.setFont(font)
            painter.setPen(QColor(colour))
            painter.drawText(
                QRectF(x, top + _RAIL_THICKNESS_PX + SPACE_XS, segment, label_height),
                Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop,
                label,
            )


def _text_height(font: QFont, width: int, text: str, *, word_wrap: bool = False) -> int:
    """Return a conservative text height for custom-painted overlay labels."""

    from PySide6.QtGui import QFontMetrics

    metrics = QFontMetrics(font)
    flags = Qt.AlignmentFlag.AlignCenter
    if word_wrap:
        flags |= Qt.TextFlag.TextWordWrap
    bounds = metrics.boundingRect(QRect(0, 0, width, 10_000), flags, text or " ")
    return max(metrics.lineSpacing(), bounds.height()) + 4
