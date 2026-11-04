"""Focus chrome fades where it was on screen while image crops glide with uniform scale."""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING
from PySide6.QtCore import QPoint, QPointF, QRectF
from PySide6.QtGui import QColor, QRegion
from tomography_session_browser.presentation.design_tokens import CANVAS_CHROME
from tomography_session_browser.ui.motion.picture_layout import (
    OpacityWindow, PictureElement, PictureLayout, RectMotion, fit_rect,
)
from tomography_session_browser.ui.tomograms.pane_transition import image_bounds
from tomography_session_browser.ui.theme import current_palette

if TYPE_CHECKING:
    from tomography_session_browser.ui.tomograms.tab import TomogramTab


def regions(tab: TomogramTab) -> dict[str, QRectF]:
    window, stage = tab.window(), tab.stage
    rect = QRectF(stage.mapTo(window, QPoint()), stage.size())
    width, height = window.width(), window.height()
    result = {
        "stage": rect,
        "top": QRectF(0, 0, width, rect.top()),
        "bottom": QRectF(0, rect.bottom(), width, height-rect.bottom()),
        "left": QRectF(0, rect.top(), rect.left(), rect.height()),
        "right": QRectF(rect.right(), rect.top(), width-rect.right(), rect.height()),
    }
    result.update((axis, QRectF(pane.mapTo(window, QPoint()), pane.size()))
                  for axis, pane in stage.panes.items() if pane.isVisible())
    return result


@dataclass(frozen=True, slots=True)
class FocusTransition:
    source: dict[str, QRectF]
    images: dict[str, QRectF]
    entering: bool
    origin: QPoint  # the window's screen position before the commit

    def picture_layout(self, tab: TomogramTab) -> PictureLayout:
        window = tab.window()
        destination = regions(tab)
        bounds = QRectF(window.rect())
        # Fitting or restoring moves the window (262 px on a 1500 px window).
        # Each outgoing piece starts where it was on screen, not in the window.
        moved = QPointF(self.origin - window.mapToGlobal(QPoint()))
        chrome = OpacityWindow(0., .3, 1., 0.) if self.entering else OpacityWindow(.35, .8, 1., 0.)
        stage = destination["stage"] if self.entering else self.source["stage"].translated(moved)
        elements = [PictureElement(current_palette().background, RectMotion(bounds, bounds), chrome),
                    PictureElement(CANVAS_CHROME.background, RectMotion(stage, destination["stage"]))]
        elements.extend(PictureElement(rect, RectMotion(rect.translated(moved), rect.translated(moved)), chrome)
                        for name, rect in self.source.items() if name not in ("stage", "XY", "XZ", "YZ"))
        elements.extend(PictureElement(rect, RectMotion(rect.translated(moved), fit_rect(rect, destination[axis])))
                        for axis, rect in self.images.items() if axis in destination)
        return PictureLayout(tuple(elements))

    def beneath(self, tab: TomogramTab) -> tuple[QRegion, QColor]:
        """The committed window, pictured once, so its chrome need not repaint each frame.

        The stage stays canvas colour, as under the canvas fill: no rendered
        volume frame is pictured.
        """
        window = tab.window()
        stage = regions(tab)["stage"].toAlignedRect()
        return QRegion(window.rect()) - QRegion(stage), QColor(CANVAS_CHROME.background)
