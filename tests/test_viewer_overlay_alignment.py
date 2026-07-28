"""The two bottom-corner viewer overlays must share one baseline.

The scale bar is pinned to the rendered image rather than the canvas, so on
a letterboxed preview it rises out of the empty band below the data. The
zoom stack has to rise with it: anchoring only the scale bar left the two
overlays stepped apart by the height of the letterbox.
"""

from __future__ import annotations

from PySide6.QtCore import QRect
from PySide6.QtWidgets import QWidget

import pytest

from tomography_session_browser.ui.image_viewer import (
    FLOATING_CONTROL_BOTTOM_INSET_PX,
    FLOATING_CONTROL_INSET_PX,
    ViewerTab,
)


class _FakeViewport:
    def __init__(self, rect: QRect) -> None:
        self._rect = rect

    def rect(self) -> QRect:
        return self._rect


class _FakeViewer:
    """Stands in for the QGraphicsView so the geometry maths can be tested
    without a rendered image or a running event loop."""

    def __init__(self, viewport: QRect, image: QRect | None) -> None:
        self._viewport = _FakeViewport(viewport)
        self._image = image

    def viewport(self) -> _FakeViewport:
        return self._viewport

    def rendered_image_rect(self) -> QRect | None:
        return self._image


def _insets(viewport: QRect, image: QRect | None) -> tuple[int, int, int]:
    tab = ViewerTab.__new__(ViewerTab)  # geometry maths only; no Qt construction
    tab.viewer = _FakeViewer(viewport, image)
    return ViewerTab._floating_control_insets(tab)


VIEWPORT = QRect(0, 0, 900, 700)


def test_letterboxed_image_lifts_both_overlays_by_the_same_amount() -> None:
    """A 4:3 image in a taller viewport leaves a band top and bottom."""

    image = QRect(0, 100, 900, 500)  # 100px band above, 100px below
    left, right, bottom = _insets(VIEWPORT, image)

    # 699 - 599 = 100px of letterbox, plus the standing inset.
    assert bottom == 100 + FLOATING_CONTROL_BOTTOM_INSET_PX
    assert left == FLOATING_CONTROL_INSET_PX
    assert right == FLOATING_CONTROL_INSET_PX


def test_pillarboxed_image_insets_both_horizontal_edges() -> None:
    image = QRect(120, 0, 660, 700)  # right edge at x=779, viewport at x=899
    left, right, _bottom = _insets(VIEWPORT, image)

    assert left == 120 + FLOATING_CONTROL_INSET_PX
    assert right == 120 + FLOATING_CONTROL_INSET_PX


def test_image_filling_the_viewport_uses_the_standing_insets() -> None:
    left, right, bottom = _insets(VIEWPORT, QRect(0, 0, 900, 700))

    assert left == FLOATING_CONTROL_INSET_PX
    assert right == FLOATING_CONTROL_INSET_PX
    assert bottom == FLOATING_CONTROL_BOTTOM_INSET_PX


def test_no_image_falls_back_to_the_standing_insets() -> None:
    """Before the first preview loads there is nothing to align to."""

    assert _insets(VIEWPORT, None) == (
        FLOATING_CONTROL_INSET_PX,
        FLOATING_CONTROL_INSET_PX,
        FLOATING_CONTROL_BOTTOM_INSET_PX,
    )


def test_insets_never_drop_below_the_standing_minimum() -> None:
    """A zoomed-in image is clipped to the viewport and can report a rect
    flush with (or beyond) the edges; the overlays must not touch the frame."""

    left, right, bottom = _insets(VIEWPORT, QRect(-40, -40, 1000, 800))

    assert left == FLOATING_CONTROL_INSET_PX
    assert right == FLOATING_CONTROL_INSET_PX
    assert bottom == FLOATING_CONTROL_BOTTOM_INSET_PX


@pytest.mark.parametrize(
    "image",
    [
        QRect(0, 100, 900, 500),
        QRect(0, 0, 900, 700),
        QRect(60, 30, 780, 640),
        QRect(0, 250, 900, 200),
    ],
)
def test_both_anchors_receive_one_shared_bottom_margin(image: QRect) -> None:
    """This is the property the alignment depends on.

    ``_reposition_floating_controls`` applies a single computed bottom inset
    to both anchor layouts, so whatever the image geometry, the scale bar and
    the zoom stack sit on the same line.
    """

    _left, _right, bottom = _insets(VIEWPORT, image)

    scale_layout = _RecordingLayout()
    zoom_layout = _RecordingLayout()
    tab = ViewerTab.__new__(ViewerTab)
    tab.viewer = _FakeViewer(VIEWPORT, image)
    tab._scale_bar_anchor_layout = scale_layout
    tab._zoom_anchor_layout = zoom_layout

    ViewerTab._reposition_floating_controls(tab)

    assert scale_layout.margins[3] == bottom
    assert zoom_layout.margins[3] == bottom
    assert scale_layout.margins[3] == zoom_layout.margins[3]


class _RecordingLayout:
    """Minimal stand-in for the anchor QVBoxLayouts."""

    def __init__(self) -> None:
        self.margins = (0, 0, 0, 0)

    def contentsMargins(self) -> QWidget:  # noqa: N802 - Qt API shape
        from PySide6.QtCore import QMargins

        return QMargins(*self.margins)

    def setContentsMargins(self, left: int, top: int, right: int, bottom: int) -> None:  # noqa: N802
        self.margins = (left, top, right, bottom)
