"""Scroll components for the motion."""
from __future__ import annotations
import weakref
from PySide6.QtCore import QEvent, QObject
from PySide6.QtWidgets import QAbstractScrollArea, QApplication
from tomography_session_browser.ui.motion.spring import Spring
from tomography_session_browser.ui.motion.tokens import MOTION_SCROLL, SpringSpec, WHEEL_MODIFIERS


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
        setattr(area, "_tomo_smooth_scroller", self)
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
        if event.modifiers() & WHEEL_MODIFIERS:
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

