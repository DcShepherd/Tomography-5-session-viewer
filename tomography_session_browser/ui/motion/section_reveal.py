"""Section reveal components for the motion."""
from __future__ import annotations
from collections.abc import Callable
import math
from PySide6.QtCore import QEvent, QRect, QRectF, Qt
from PySide6.QtGui import QPainter, QPixmap
from PySide6.QtWidgets import QGraphicsOpacityEffect, QWidget
import shiboken6
from tomography_session_browser.ui.motion.spring import Spring
from tomography_session_browser.ui.motion.tokens import (
    MOTION_SECTION_OPEN, MOTION_SECTION_CLOSE, PIXEL_SETTLE_DISTANCE, PIXEL_SETTLE_VELOCITY, SpringSpec,
)


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


CONCEAL_EFFECT_NAME = "sectionRevealConceal"


def conceal_with_effect(section: QWidget, concealed: bool) -> None:
    """Hide a section's pixels while it keeps its place in the layout.

    A fully transparent opacity effect draws nothing, the widget and its
    children included, and costs nothing per frame. A section with an effect
    of its own keeps it: replacing it would delete it.
    """

    current = section.graphicsEffect()
    if concealed:
        if current is None:
            effect = QGraphicsOpacityEffect(section)
            effect.setObjectName(CONCEAL_EFFECT_NAME)
            effect.setOpacity(0.0)
            section.setGraphicsEffect(effect)
    elif current is not None and current.objectName() == CONCEAL_EFFECT_NAME:
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

    With ``closed_width``, the width follows the same spring from that width
    (closed) to the geometry's (open), anchored on the left: the right
    ``riding_width`` columns (the controls at a header's right end, the
    border) travel with the right edge and the columns between are clipped.
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
        spec: SpringSpec | None = None,
        on_progress: Callable[[float], None] | None = None,
        closed_width: int | None = None,
        riding_width: int = 0,
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
        self._closed_width = None if closed_width is None else max(0, min(closed_width, geometry.width()))
        self._riding_width = max(0, min(riding_width, self._closed_width if self._closed_width is not None else 0))
        # Told how far open it is, 0 to 1, on each frame and once at the end
        # (the end it was heading for, also when dismissed), so a control
        # beside the section can follow the same spring.
        self._on_progress = on_progress
        self._conceal = conceal if conceal is not None else (lambda hidden: conceal_with_effect(section, hidden))
        start = self._closed if opening else self._open
        self._shown = start
        self._spec = spec
        self._height = Spring(
            start,
            spec=spec or (MOTION_SECTION_OPEN if opening else MOTION_SECTION_CLOSE),
            on_update=self._moved,
            on_settled=self._finish,
            widget=self,
            settle_distance=PIXEL_SETTLE_DISTANCE,
            settle_velocity=PIXEL_SETTLE_VELOCITY,
        )
        self.destroyed.connect(
            lambda *_args, spring=self._height: spring.reset(spring.value) if spring.is_animating else None
        )
        previous = live_section_reveal(section)
        if previous is not None:
            previous.dismiss()
        setattr(section, "_tomo_section_reveal", self)
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

    @property
    def progress(self) -> float:
        """How far open it is now: 0 closed, 1 open, held inside that range."""

        span = self._open - self._closed
        return 1.0 if span <= 0 else min(1.0, max(0.0, (self._height.value - self._closed) / span))

    @property
    def width_shown(self) -> float:
        """The width painted now: the geometry's, or between ``closed_width`` and it."""

        if self._closed_width is None:
            return float(self.width())
        return self._closed_width + (self.width() - self._closed_width) * self.progress

    def retarget(self, opening: bool, picture: QPixmap | None = None) -> None:
        """Head open or closed from wherever it is now.

        ``picture`` replaces the open picture when the section's own look
        follows its state (an arrow that turns).
        """

        if picture is not None and picture.size() == self._picture.size():
            self._picture = picture
            self.update()
        self._height.spec = self._spec or (MOTION_SECTION_OPEN if opening else MOTION_SECTION_CLOSE)
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

        opening = self.opening  # before the reset moves the target to here
        self._height.reset(self._height.value)
        self._finish(opening)

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
        if self._on_progress is not None:
            self._on_progress(self.progress)
        old, self._shown = self._shown, value
        if self._closed_width is not None and self._closed_width < self.width():
            # The width moves too: every row's right end moves, so all of it repaints.
            self.update()
            return
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
        shown = self.width_shown
        if shown >= width:
            for source, target, rows in strips:
                painter.drawPixmap(
                    QRectF(0.0, target / ratio, width, rows / ratio),
                    picture,
                    QRectF(0.0, float(source), float(picture.width()), float(rows)),
                )
            return
        # Narrower than open: the left columns up to the riding ones, then the
        # riding columns at the right edge shown.
        ride = float(self._riding_width)
        left = max(0.0, shown - ride)
        for source, target, rows in strips:
            painter.drawPixmap(
                QRectF(0.0, target / ratio, left, rows / ratio),
                picture,
                QRectF(0.0, float(source), left * ratio, float(rows)),
            )
            painter.drawPixmap(
                QRectF(left, target / ratio, ride, rows / ratio),
                picture,
                QRectF((width - ride) * ratio, float(source), ride * ratio, float(rows)),
            )

    def _finish(self, opening: bool | None = None) -> None:
        if self._finished:
            return
        self._finished = True
        if self._on_progress is not None:
            self._on_progress(1.0 if (self.opening if opening is None else opening) else 0.0)
        section = self._section
        if shiboken6.isValid(section):
            section.removeEventFilter(self)
            # Only while it is still this reveal's section to show: a newer
            # reveal may have concealed it already.
            if getattr(section, "_tomo_section_reveal", None) is self:
                setattr(section, "_tomo_section_reveal", None)
                self._conceal(False)
        parent = self.parentWidget()
        if parent is not None:
            parent.removeEventFilter(self)
        self.hide()
        self.deleteLater()

