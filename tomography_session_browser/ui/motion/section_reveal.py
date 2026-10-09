"""Section reveal components for the motion."""
from __future__ import annotations
from collections.abc import Callable
import math
from PySide6.QtCore import QEvent, QRect, QRectF, Qt
from PySide6.QtGui import QPainter, QPixmap
from PySide6.QtWidgets import QGraphicsOpacityEffect, QWidget
import shiboken6
from tomography_session_browser.ui.motion.spring import Spring
from tomography_session_browser.ui.motion.tokens import MOTION_PANEL, SpringSpec


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
        self._conceal = conceal if conceal is not None else (lambda hidden: conceal_with_effect(section, hidden))
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
                setattr(section, "_tomo_section_reveal", None)
                self._conceal(False)
        parent = self.parentWidget()
        if parent is not None:
            parent.removeEventFilter(self)
        self.hide()
        self.deleteLater()

