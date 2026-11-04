"""Single/Ortho choreography; no animation or accepted-frame ownership."""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING
from PySide6.QtCore import QPoint, QRectF
from PySide6.QtWidgets import QWidget
from tomography_session_browser.presentation.design_tokens import CANVAS_CHROME
from tomography_session_browser.ui.motion.picture_layout import (
    OpacityWindow, PictureElement, PictureLayout, RectMotion, fit_rect,
)
from tomography_session_browser.ui.motion.tokens import DURATION_PANEL_MS, DURATION_SMALL_MS

if TYPE_CHECKING:
    from tomography_session_browser.ui.tomograms.stage import VolumeStage


def pane_bounds(stage: VolumeStage) -> dict[str, QRectF]:
    return {axis: QRectF(pane.rect().translated(pane.mapTo(stage, QPoint())))
            for axis, pane in stage.panes.items() if pane.isVisible()}


def image_bounds(stage: VolumeStage, surface: QWidget) -> dict[str, QRectF]:
    """Displayed image crops in the snapshot's logical coordinates."""
    return {axis: QRectF(QPoint(*pane.position), pane.image_size()).intersected(QRectF(pane.rect()))
            .translated(pane.mapTo(surface, QPoint()))
            for axis, pane in stage.panes.items() if pane.isVisible()}


@dataclass(frozen=True, slots=True)
class PaneTransition:
    source: dict[str, QRectF]
    destination_mode: str

    def picture_layout(self, stage: VolumeStage) -> PictureLayout:
        destination = pane_bounds(stage)
        reveal_ms = DURATION_SMALL_MS if self.destination_mode == "Ortho" else 0
        background = QRectF(stage.rect())
        elements = [PictureElement(CANVAS_CHROME.background, RectMotion(background, background),
                    OpacityWindow(1., 1.+reveal_ms/DURATION_PANEL_MS, 1., 0.) if reveal_ms else OpacityWindow())]
        # The XY backing stays opaque when side panes reveal the live destination.
        xy = destination["XY"]
        elements.append(PictureElement(CANVAS_CHROME.background, RectMotion(xy, xy)))
        if self.destination_mode == "Single":
            elements.extend(PictureElement(rect, RectMotion(rect, rect), OpacityWindow(0., .25, 1., 0.))
                            for axis, rect in self.source.items() if axis != "XY")
        source = self.source["XY"]
        elements.append(PictureElement(source, RectMotion(source, fit_rect(source, xy))))
        return PictureLayout(tuple(elements), reveal_ms)
