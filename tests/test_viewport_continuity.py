"""S2: zoom and pan survive a return to the same logical image — and only then.

The risk this module guards is subtle: a viewport that leaks between images
misrepresents where the operator is looking on a micrograph. So the tests care
as much about the crop *not* being inherited as about it being restored.
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtGui import QImage
from PySide6.QtWidgets import QApplication

from tomography_session_browser.ui.image_viewer import VIEWPORT_MEMORY_LIMIT, ImagePreviewView


def _app() -> QApplication:
    return QApplication.instance() or QApplication([])


def _image(width: int = 400, height: int = 300) -> QImage:
    image = QImage(width, height, QImage.Format.Format_RGB32)
    image.fill(0x202020)
    return image


def _view() -> ImagePreviewView:
    _app()
    view = ImagePreviewView()
    view.resize(200, 150)
    view.show()
    return view


def _range(view: ImagePreviewView):
    rect = view.mapToScene(view.viewport().rect()).boundingRect()
    return (round(rect.left(), 1), round(rect.top(), 1), round(rect.width(), 1))


def test_returning_to_the_same_image_restores_the_crop() -> None:
    view = _view()
    view.set_image(_image(), logical_image_key="image-a")
    view.zoom_in()
    view.zoom_in()
    zoomed = _range(view)

    # Look at a different image, then come back.
    view.set_image(_image(), logical_image_key="image-b")
    view.set_image(_image(), logical_image_key="image-a")

    restored = _range(view)
    assert restored[2] < view._pixmap_item.boundingRect().width(), "should still be zoomed in"
    # Same region, within a pixel of rounding.
    assert abs(restored[0] - zoomed[0]) <= 2
    assert abs(restored[2] - zoomed[2]) <= 2


def test_a_different_image_never_inherits_another_crop() -> None:
    view = _view()
    view.set_image(_image(), logical_image_key="image-a")
    view.zoom_in()
    view.zoom_in()
    zoomed_width = _range(view)[2]

    view.set_image(_image(), logical_image_key="image-b")

    fitted_width = _range(view)[2]
    assert fitted_width > zoomed_width, "image-b must be fitted, not cropped like image-a"
    assert view._pending_initial_fit is False or view._has_user_interacted is False


def test_an_untouched_view_is_not_remembered() -> None:
    """Only a crop the user chose is worth restoring."""

    view = _view()
    view.set_image(_image(), logical_image_key="image-a")
    fitted = _range(view)

    view.set_image(_image(), logical_image_key="image-b")
    view.set_image(_image(), logical_image_key="image-a")

    assert "image-a" not in view._view_memory
    assert _range(view) == fitted


def test_zooming_back_out_drops_the_memory() -> None:
    """A crop is remembered while it differs from the fit, not forever."""

    view = _view()
    view.set_image(_image(), logical_image_key="image-a")
    view.zoom_in()
    view.set_image(_image(), logical_image_key="image-b")
    assert "image-a" in view._view_memory

    view.set_image(_image(), logical_image_key="image-a")
    view.reset_view() if hasattr(view, "reset_view") else view._fit_image()
    view._has_user_interacted = False
    view.set_image(_image(), logical_image_key="image-b")

    assert "image-a" not in view._view_memory


def test_viewport_memory_is_bounded() -> None:
    view = _view()
    for index in range(VIEWPORT_MEMORY_LIMIT * 2):
        view.set_image(_image(), logical_image_key=f"image-{index}")
        view.zoom_in()
    view.set_image(_image(), logical_image_key="flush")

    assert len(view._view_memory) <= VIEWPORT_MEMORY_LIMIT


def test_forgetting_views_clears_the_memory() -> None:
    view = _view()
    view.set_image(_image(), logical_image_key="image-a")
    view.zoom_in()
    view.set_image(_image(), logical_image_key="image-b")
    assert view._view_memory

    view.forget_remembered_views()

    assert not view._view_memory


def test_clearing_banks_the_crop_before_dropping_the_key() -> None:
    """A tab rebuild must not lose the crop of the image it lands back on."""

    view = _view()
    view.set_image(_image(), logical_image_key="image-a")
    view.zoom_in()
    view.zoom_in()
    zoomed = _range(view)

    view.clear("rebuilding")
    view.set_image(_image(), logical_image_key="image-a")

    restored = _range(view)
    assert abs(restored[2] - zoomed[2]) <= 2


def test_a_resized_window_restores_the_same_region_not_the_same_pixels() -> None:
    """The crop is stored proportionally, so it survives a resize."""

    view = _view()
    view.set_image(_image(), logical_image_key="image-a")
    view.zoom_in()
    view.zoom_in()
    before_fraction = _range(view)[2] / view._pixmap_item.boundingRect().width()

    view.set_image(_image(), logical_image_key="image-b")
    view.resize(400, 300)
    view.set_image(_image(), logical_image_key="image-a")

    after_fraction = _range(view)[2] / view._pixmap_item.boundingRect().width()
    assert abs(after_fraction - before_fraction) < 0.15
