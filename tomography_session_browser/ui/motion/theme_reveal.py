"""Theme reveal components for the motion."""
from __future__ import annotations
from collections.abc import Callable
import math
from PySide6.QtCore import QEvent, QPoint, QPointF, QRect, QRectF, Qt, QTimer
from PySide6.QtGui import QBrush, QColor, QPainter, QPainterPath, QPen, QPixmap, QRegion, QTransform
from PySide6.QtWidgets import QApplication, QWidget
from tomography_session_browser.ui.motion.tokens import ANNULUS_BAND_PX, INTERRUPTING_INPUT, THEME_REVEAL_FEATHER_PX, THEME_REVEAL_FEATHER_RINGS, THEME_REVEAL_GLOW_WIDTH_PX, THEME_REVEAL_MS, THEME_REVEAL_SETTLE_TURNS
from tomography_session_browser.ui.motion.tween import Tween


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

