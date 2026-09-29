"""The main tab bar: a selection underline that slides, and fading fills.

Plan F5.3. The style sheet drew the selected tab's accent underline and its
lifted background, and both snapped to the new tab on a switch; hover
snapped too, because style sheets cannot transition. This bar paints each
tab's fill and the underline itself, on springs, and asks the style only for
each tab's *label*, so the sheet still decides text colour, weight, padding
and icons.

It never draws the sheet's tab shape: with the sheet's fills made
transparent, Qt handed the shape to the native Windows 11 style, which drew
an opaque dark-grey system tab natively, in the light theme too; with them
opaque they would cover these fills.

Only presentation moves: which tab is current, and when, is unchanged.
"""

from __future__ import annotations

import re

from PySide6.QtCore import QRectF
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import QStyle, QStyleOptionTab, QTabBar, QWidget

from tomography_session_browser.ui.motion import MOTION_MICRO, MOTION_SMALL, Spring, SpringSpec, blend, motion_enabled
from tomography_session_browser.ui.theme import current_palette

INDICATOR_HEIGHT_PX = 2
# The style sheet's ``margin-right`` between tabs; fills and the underline
# stop short of it, as the sheet's own did.
TAB_GAP_PX = 2
# The edge moving towards the new tab leads and the other follows, so the
# underline stretches a little on its way rather than sliding rigidly. Both
# are stiffer than ``MOTION_SMALL``: over a long move (Session to Tilt series,
# ~900 px) a softer trailing edge was still creeping 650 ms later.
_LEADING_EDGE = SpringSpec(stiffness=620.0, damping_ratio=0.9)
_TRAILING_EDGE = SpringSpec(stiffness=450.0, damping_ratio=1.0)
_CSS_RGBA = re.compile(r"rgba\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*,\s*([\d.]+)\s*\)")


def css_colour(value: str) -> QColor:
    """A palette token as a QColor, including the sheet's ``rgba(r, g, b, a)``."""

    match = _CSS_RGBA.fullmatch(value.strip())
    if match is None:
        return QColor(value)
    red, green, blue, alpha = match.groups()
    colour = QColor(int(red), int(green), int(blue))
    colour.setAlphaF(float(alpha))
    return colour


class SlidingTabBar(QTabBar):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("mainTabBar")
        self.setMouseTracking(True)
        pixel = {"settle_distance": 0.5, "settle_velocity": 20.0}
        self._left = Spring(0.0, spec=_LEADING_EDGE, on_update=self._moved, widget=self, **pixel)
        self._right = Spring(0.0, spec=_LEADING_EDGE, on_update=self._moved, widget=self, **pixel)
        self._placed = False
        # Per tab index: how selected (0..1) and how hovered (0..1) it looks.
        self._selected: dict[int, Spring] = {}
        self._hovered: dict[int, Spring] = {}
        self._hover_index = -1
        self._last_index = -1
        self.currentChanged.connect(self._on_current_changed)

        # The shared ticker must never step a bar destroyed mid-animation.
        def _stop(*_args, edges=(self._left, self._right), maps=(self._selected, self._hovered)) -> None:
            for spring in (*edges, *(s for springs in maps for s in springs.values())):
                if spring.is_animating:
                    spring.reset(spring.value)

        self.destroyed.connect(_stop)

    # -- the underline --------------------------------------------------------

    def indicator_span(self) -> tuple[float, float]:
        """The underline's current left and right edges, in bar coordinates."""

        self._place_if_resting()
        return self._left.value, self._right.value

    def _target_span(self, index: int) -> tuple[float, float] | None:
        rect = self.tabRect(index)
        if index < 0 or not rect.isValid():
            return None
        return float(rect.left()), float(rect.right() + 1 - TAB_GAP_PX)

    def _place_if_resting(self) -> None:
        """At rest the underline simply sits under the current tab.

        Tab rects change with fit-first texts and resizes; reading them at
        paint time keeps a resting underline correct without a hook for
        every cause.
        """

        if self._left.is_animating or self._right.is_animating:
            return
        target = self._target_span(self.currentIndex())
        if target is not None:
            self._left.reset(target[0])
            self._right.reset(target[1])
            self._placed = True

    def tabLayoutChange(self) -> None:  # noqa: N802 - Qt API
        super().tabLayoutChange()
        # Mid-slide, the tab it is heading for may have moved.
        if self._left.is_animating or self._right.is_animating:
            target = self._target_span(self.currentIndex())
            if target is not None:
                self._left.set_target(target[0])
                self._right.set_target(target[1])

    def _on_current_changed(self, index: int) -> None:
        target = self._target_span(index)
        animate = self._placed and self.isVisible() and motion_enabled(self)
        if target is not None:
            if animate:
                moving_right = target[0] > self._left.value
                self._right.spec = _LEADING_EDGE if moving_right else _TRAILING_EDGE
                self._left.spec = _TRAILING_EDGE if moving_right else _LEADING_EDGE
                self._left.set_target(target[0])
                self._right.set_target(target[1])
            else:
                self._left.reset(target[0])
                self._right.reset(target[1])
                self._placed = True
        previous, self._last_index = self._last_index, index
        for tab in range(self.count()):
            # A tab first touched now starts from how it looked until now.
            spring = self._spring(self._selected, tab, MOTION_SMALL, start=1.0 if tab == previous else 0.0)
            spring.set_target(1.0 if tab == index else 0.0, animate=animate)
        self.update()

    # -- hover --------------------------------------------------------------

    def mouseMoveEvent(self, event) -> None:  # noqa: N802 - Qt API
        super().mouseMoveEvent(event)
        self._set_hover(self.tabAt(event.position().toPoint()))

    def leaveEvent(self, event) -> None:  # noqa: N802 - Qt API
        super().leaveEvent(event)
        self._set_hover(-1)

    def hover_progress(self, index: int) -> float:
        spring = self._hovered.get(index)
        return spring.value if spring is not None else 0.0

    def _set_hover(self, index: int) -> None:
        if index == self._hover_index:
            return
        previous, self._hover_index = self._hover_index, index
        if previous >= 0:
            self._spring(self._hovered, previous, MOTION_MICRO).set_target(0.0)
        if index >= 0:
            self._spring(self._hovered, index, MOTION_MICRO).set_target(1.0)

    # -- painting -------------------------------------------------------------

    def _spring(self, springs: dict[int, Spring], index: int, spec: SpringSpec, *, start: float = 0.0) -> Spring:
        spring = springs.get(index)
        if spring is None:
            spring = Spring(start, spec=spec, on_update=self._moved, widget=self, settle_distance=0.01)
            springs[index] = spring
        return spring

    def _selected_progress(self, index: int) -> float:
        spring = self._selected.get(index)
        if spring is None:
            return 1.0 if index == self.currentIndex() else 0.0
        return spring.value

    def _moved(self, _value: float) -> None:
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt API
        palette = current_palette()
        resting, lifted, hover_fill = QColor(palette.surface), QColor(palette.background), QColor(palette.surface_alt)
        focus_fill = css_colour(palette.accent_soft)
        painter = QPainter(self)
        style = self.style()
        for index in range(self.count()):
            tab_rect = self.tabRect(index)
            rect = tab_rect.adjusted(0, 0, -TAB_GAP_PX, 0)
            if not tab_rect.intersects(event.rect()):
                continue
            selected = min(1.0, max(0.0, self._selected_progress(index)))
            hovered = self.hover_progress(index) * (1.0 - selected)  # hover never lifts the current tab
            painter.fillRect(rect, blend(blend(resting, hover_fill, hovered), lifted, selected))
            if index == self.currentIndex() and self.hasFocus():
                painter.fillRect(rect, focus_fill)  # the keyboard-focus fill the sheet drew
            option = QStyleOptionTab()
            self.initStyleOption(option, index)
            style.drawControl(QStyle.ControlElement.CE_TabBarTabLabel, option, painter, self)
        if self.currentIndex() >= 0:
            left, right = self.indicator_span()
            current = self.tabRect(self.currentIndex())
            painter.fillRect(
                QRectF(left, current.bottom() + 1 - INDICATOR_HEIGHT_PX, max(0.0, right - left), INDICATOR_HEIGHT_PX),
                QColor(palette.accent),
            )
