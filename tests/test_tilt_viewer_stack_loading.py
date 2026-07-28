from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtGui import QFontMetrics, QImage, QPainter
from PySide6.QtWidgets import QApplication

from tomography_session_browser.domain.models import MrcMetadata, TiltSeries
from tomography_session_browser.ui import image_viewer
from tomography_session_browser.ui.image_viewer import CachedPreview, ViewerTab


def test_tilt_viewer_loads_requested_raw_stack_slice(monkeypatch, tmp_path: Path) -> None:
    _app()
    path = tmp_path / "stack.mrc"
    path.write_bytes(b"")

    class FakeMrcSource:
        inspection = type("Inspection", (), {"nx": 4, "ny": 4})()

        def frame_count(self) -> int:
            return 5

    monkeypatch.setattr(image_viewer, "_mrc_source", lambda _path: FakeMrcSource())
    preview = QImage(4, 4, QImage.Format.Format_Grayscale8)
    preview.fill(128)
    image_viewer.MRC_PREVIEW_CACHE.put(
        image_viewer._preview_cache_key(path, 3, image_viewer.MAX_PREVIEW_DIMENSION),
        CachedPreview(preview, 5, [], preview.sizeInBytes()),
    )

    tilt = TiltSeries(
        id="tilt",
        name="tilt",
        mrc_path=path,
        mrc_metadata=MrcMetadata(
            path=path,
            size_bytes=0,
            nz=5,
            tilt_angles=[0.0, 2.0, -2.0, 4.0, -4.0],
            tilt_angle_source="MRC extended header",
        ),
    )
    viewer = ViewerTab("empty", show_list=False, show_tilt_controls=True)
    viewer._current_value = tilt
    viewer._current_path = path
    viewer._current_fallback = None
    viewer._prefetch_neighbors = lambda *_args, **_kwargs: None

    viewer._load_mrc_path(path, None, 3)

    assert viewer.slice_slider.value() == 3
    assert viewer._displayed_slice_index == 3
    assert "Frame 4 of 5" in viewer.frame_label.text()


def test_tilt_viewer_uses_parsed_metadata_before_worker_inspection(monkeypatch, tmp_path: Path) -> None:
    _app()
    path = tmp_path / "stack.mrc"
    path.write_bytes(b"")
    monkeypatch.setattr(
        image_viewer,
        "_mrc_source",
        lambda _path: (_ for _ in ()).throw(AssertionError("_mrc_source should run in the preview worker")),
    )
    preview = QImage(4, 4, QImage.Format.Format_Grayscale8)
    preview.fill(128)
    image_viewer.MRC_PREVIEW_CACHE.put(
        image_viewer._preview_cache_key(path, 2, image_viewer.FIRST_ZOOM_PREVIEW_DIMENSION),
        CachedPreview(preview, 5, [], preview.sizeInBytes()),
    )
    tilt = TiltSeries(
        id="tilt",
        name="tilt",
        mrc_path=path,
        mrc_metadata=MrcMetadata(
            path=path,
            size_bytes=0,
            nz=5,
            tilt_angles=[-2.0, -1.0, 0.0, 1.0, 2.0],
            tilt_angle_source="MRC extended header",
        ),
    )
    viewer = ViewerTab("empty", show_list=False, show_tilt_controls=True)
    viewer._prefetch_neighbors = lambda *_args, **_kwargs: None
    viewer.set_items([tilt], lambda _tilt: image_viewer.PreviewSources(path), lambda _tilt: "tilt")

    assert viewer.slice_slider.value() == 2
    assert viewer._displayed_slice_index == 2


def test_tilt_viewer_select_frame_updates_stack_slider(tmp_path: Path) -> None:
    _app()
    path = tmp_path / "stack.mrc"
    path.write_bytes(b"")
    preview = QImage(4, 4, QImage.Format.Format_Grayscale8)
    preview.fill(128)
    image_viewer.MRC_PREVIEW_CACHE.put(
        image_viewer._preview_cache_key(path, 0, image_viewer.FIRST_ZOOM_PREVIEW_DIMENSION),
        CachedPreview(preview, 5, [], preview.sizeInBytes()),
    )
    image_viewer.MRC_PREVIEW_CACHE.put(
        image_viewer._preview_cache_key(path, 4, image_viewer.FIRST_ZOOM_PREVIEW_DIMENSION),
        CachedPreview(preview, 5, [], preview.sizeInBytes()),
    )
    tilt = TiltSeries(
        id="tilt",
        name="tilt",
        mrc_path=path,
        mrc_metadata=MrcMetadata(path=path, size_bytes=0, nz=5),
    )
    viewer = ViewerTab("empty", show_list=False, show_tilt_controls=True)
    viewer._prefetch_neighbors = lambda *_args, **_kwargs: None
    viewer.set_items([tilt], lambda _tilt: image_viewer.PreviewSources(path), lambda _tilt: "tilt")

    viewer.select_frame(4)

    assert viewer.slice_slider.value() == 4
    assert "Frame 5 of 5" in viewer.frame_label.text()


def test_zoom_refresh_ignores_unreadable_mrc_path(tmp_path: Path) -> None:
    _app()
    path = tmp_path / "too_short.mrc"
    path.write_bytes(b"")
    viewer = ViewerTab("empty", show_list=False, show_tilt_controls=True)
    viewer._current_path = path

    viewer._zoom_changed(1.0)

    assert viewer.zoom_label.text() == "100%"


def test_zoom_strip_reserves_width_for_four_digit_percentages() -> None:
    _app()
    viewer = ViewerTab("empty", show_list=False)

    viewer._zoom_changed(10.0)

    assert viewer.zoom_label.text() == "1000%"
    text_width = QFontMetrics(
        viewer.zoom_label.font()
    ).horizontalAdvance(viewer.zoom_label.text())
    assert viewer.zoom_label.contentsRect().width() >= text_width
    assert viewer.zoom_panel.width() >= viewer.zoom_label.width() + 6


def test_image_preview_uses_smooth_pixmap_scaling() -> None:
    _app()
    preview = image_viewer.ImagePreviewView()
    image = QImage(4, 4, QImage.Format.Format_Grayscale8)
    image.fill(128)

    preview.set_image(image)

    assert preview._pixmap_item is not None
    assert preview._pixmap_item.transformationMode() == Qt.TransformationMode.SmoothTransformation


def test_image_preview_keeps_smooth_scaling_during_interactive_zoom() -> None:
    _app()
    preview = image_viewer.ImagePreviewView()
    image = QImage(4, 4, QImage.Format.Format_Grayscale8)
    image.fill(128)
    preview.set_image(image)

    preview.zoom_in()

    assert preview._pixmap_item is not None
    assert preview._pixmap_item.transformationMode() == Qt.TransformationMode.SmoothTransformation
    assert preview.renderHints() & QPainter.RenderHint.SmoothPixmapTransform


def test_mrc_zoom_refresh_queues_worker_without_synchronous_inspection(monkeypatch, tmp_path: Path) -> None:
    _app()
    image_viewer.clear_preview_cache()
    path = tmp_path / "stack.mrc"
    path.write_bytes(b"")
    monkeypatch.setattr(
        image_viewer,
        "_mrc_source",
        lambda _path: (_ for _ in ()).throw(AssertionError("_mrc_source should run only in the preview worker")),
    )

    started = []
    viewer = ViewerTab("empty", show_list=False, show_tilt_controls=True)
    monkeypatch.setattr(viewer._thread_pool, "start", lambda runnable: started.append(runnable))
    tilt = TiltSeries(
        id="tilt",
        name="tilt",
        mrc_path=path,
        mrc_metadata=MrcMetadata(path=path, size_bytes=0, nx=4096, ny=4096, nz=5),
    )
    image = QImage(4, 4, QImage.Format.Format_Grayscale8)
    image.fill(128)
    viewer.viewer.set_image(image)
    viewer._current_value = tilt
    viewer._current_path = path
    viewer._current_fallback = None
    viewer._current_mrc_max_size = image_viewer.MAX_PREVIEW_DIMENSION

    viewer._zoom_changed(2.0)

    assert viewer._current_mrc_max_size == 4096
    assert len(started) == 1


def test_first_zoom_ready_mrc_preview_does_not_queue_refinement(monkeypatch, tmp_path: Path) -> None:
    _app()
    image_viewer.clear_preview_cache()
    path = tmp_path / "stack.mrc"
    path.write_bytes(b"")
    started = []
    viewer = ViewerTab("empty", show_list=False, show_tilt_controls=True)
    monkeypatch.setattr(viewer._thread_pool, "start", lambda runnable: started.append(runnable))
    tilt = TiltSeries(
        id="tilt",
        name="tilt",
        mrc_path=path,
        mrc_metadata=MrcMetadata(path=path, size_bytes=0, nx=4096, ny=4096, nz=5),
    )
    image = QImage(4, 4, QImage.Format.Format_Grayscale8)
    image.fill(128)
    viewer.viewer.set_image(image)
    viewer._current_value = tilt
    viewer._current_path = path
    viewer._current_fallback = None
    viewer._current_mrc_max_size = image_viewer.FIRST_ZOOM_PREVIEW_DIMENSION

    viewer._zoom_changed(2.0)

    assert viewer._current_mrc_max_size == image_viewer.FIRST_ZOOM_PREVIEW_DIMENSION
    assert started == []


def test_mrc_initial_preview_is_first_zoom_ready_when_dimensions_are_known(tmp_path: Path) -> None:
    _app()
    path = tmp_path / "large.mrc"
    path.write_bytes(b"")
    viewer = ViewerTab("empty", show_list=False, show_tilt_controls=True)
    tilt = TiltSeries(
        id="tilt",
        name="tilt",
        mrc_path=path,
        mrc_metadata=MrcMetadata(path=path, size_bytes=0, nx=4096, ny=4096, nz=1),
    )
    viewer._current_value = tilt

    assert viewer._initial_mrc_preview_size(path) == image_viewer.FIRST_ZOOM_PREVIEW_DIMENSION


def test_mrc_initial_preview_is_bounded_by_native_dimensions(tmp_path: Path) -> None:
    _app()
    path = tmp_path / "small.mrc"
    path.write_bytes(b"")
    viewer = ViewerTab("empty", show_list=False, show_tilt_controls=True)
    tilt = TiltSeries(
        id="tilt",
        name="tilt",
        mrc_path=path,
        mrc_metadata=MrcMetadata(path=path, size_bytes=0, nx=1440, ny=1023, nz=1),
    )
    viewer._current_value = tilt

    assert viewer._initial_mrc_preview_size(path) == 1440


def test_mrc_path_delays_jpeg_fallback_until_grace_window(monkeypatch, tmp_path: Path) -> None:
    _app()
    image_viewer.clear_preview_cache()
    path = tmp_path / "large.mrc"
    fallback = tmp_path / "large.jpg"
    path.write_bytes(b"")
    fallback.write_bytes(b"")
    fallback_calls = []
    started = []
    viewer = ViewerTab("empty", show_list=False, show_tilt_controls=True)
    monkeypatch.setattr(viewer.viewer, "load_raster_path", lambda *args, **kwargs: fallback_calls.append(args) or [])
    monkeypatch.setattr(viewer._thread_pool, "start", lambda runnable: started.append(runnable))
    viewer._current_path = path
    viewer._current_fallback = fallback
    viewer._current_value = TiltSeries(
        id="tilt",
        name="tilt",
        mrc_path=path,
        mrc_metadata=MrcMetadata(path=path, size_bytes=0, nx=4096, ny=4096, nz=1),
    )
    viewer._current_mrc_max_size = image_viewer.FIRST_ZOOM_PREVIEW_DIMENSION

    viewer._load_mrc_path(path, fallback, 0)

    assert fallback_calls == []
    assert viewer._pending_fallback_request is not None
    assert len(started) == 1

    viewer._show_delayed_mrc_fallback()

    assert len(fallback_calls) == 1
    assert fallback_calls[0][0] == fallback
    assert viewer._displayed_path == fallback


def test_fast_mrc_load_cancels_delayed_jpeg_fallback(monkeypatch, tmp_path: Path) -> None:
    _app()
    image_viewer.clear_preview_cache()
    path = tmp_path / "large.mrc"
    fallback = tmp_path / "large.jpg"
    path.write_bytes(b"")
    fallback.write_bytes(b"")
    fallback_calls = []
    started = []
    viewer = ViewerTab("empty", show_list=False, show_tilt_controls=True)
    monkeypatch.setattr(viewer.viewer, "load_raster_path", lambda *args, **kwargs: fallback_calls.append(args) or [])
    monkeypatch.setattr(viewer._thread_pool, "start", lambda runnable: started.append(runnable))
    viewer._current_path = path
    viewer._current_fallback = fallback
    viewer._current_value = TiltSeries(
        id="tilt",
        name="tilt",
        mrc_path=path,
        mrc_metadata=MrcMetadata(path=path, size_bytes=0, nx=4096, ny=4096, nz=1),
    )
    viewer._current_mrc_max_size = image_viewer.FIRST_ZOOM_PREVIEW_DIMENSION
    image = QImage(4, 4, QImage.Format.Format_Grayscale8)
    image.fill(128)

    viewer._load_mrc_path(path, fallback, 0)
    viewer._mrc_loaded(viewer._request_id, path, 0, image, 1, [], image.sizeInBytes())
    viewer._show_delayed_mrc_fallback()

    assert fallback_calls == []
    assert viewer._pending_fallback_request is None
    assert viewer._displayed_path == path


def test_mrc_loaded_prefetches_current_high_detail_frame(monkeypatch, tmp_path: Path) -> None:
    _app()
    image_viewer.clear_preview_cache()
    path = tmp_path / "large.mrc"
    path.write_bytes(b"")
    started = []
    viewer = ViewerTab("empty", show_list=False, show_tilt_controls=True)
    monkeypatch.setattr(viewer._thread_pool, "start", lambda runnable: started.append(runnable))
    tilt = TiltSeries(
        id="tilt",
        name="tilt",
        mrc_path=path,
        mrc_metadata=MrcMetadata(path=path, size_bytes=0, nx=4096, ny=4096, nz=1),
    )
    viewer._request_id = 4
    viewer._current_value = tilt
    viewer._current_path = path
    viewer._current_fallback = None
    viewer._current_mrc_max_size = image_viewer.MAX_PREVIEW_DIMENSION
    image = QImage(4, 4, QImage.Format.Format_Grayscale8)
    image.fill(128)

    viewer._mrc_loaded(4, path, 0, image, 1, [], image.sizeInBytes())

    assert len(started) == 1
    assert started[0]._max_size == 4096


def test_mrc_prefetch_deduplicates_in_flight_work(monkeypatch, tmp_path: Path) -> None:
    _app()
    image_viewer.clear_preview_cache()
    path = tmp_path / "large.mrc"
    path.write_bytes(b"")
    started = []
    viewer = ViewerTab("empty", show_list=False, show_tilt_controls=True)
    monkeypatch.setattr(viewer._thread_pool, "start", lambda runnable: started.append(runnable))

    viewer._prefetch_mrc_frame(path, 0, 4096)
    viewer._prefetch_mrc_frame(path, 0, 4096)

    assert len(started) == 1


def _app() -> QApplication:
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app
