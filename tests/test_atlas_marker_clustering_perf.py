from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtGui import QImage
from PySide6.QtWidgets import QApplication

from tomography_session_browser.domain.markers import ImageMarker, MarkerType
from tomography_session_browser.ui.image_viewer import ImagePreviewView


def _app() -> QApplication:
    app = QApplication.instance()
    return app or QApplication([])


def test_cluster_groups_are_cached_across_repaints() -> None:
    _app()
    view = ImagePreviewView()
    view.resize(800, 600)
    view.set_atlas_lod_enabled(True)
    view.set_image(QImage(1000, 1000, QImage.Format.Format_RGB32))
    markers = [
        ImageMarker(
            id=f"atlas:batch:{index}",
            marker_type=MarkerType.BATCH_POSITION,
            linked_object_id=f"batch-{index}",
            source_object_id="atlas",
            x=float(100 + index * 5),
            y=float(100 + index * 3),
            label=str(index),
            status="collected",
            metadata={
                "atlas_lod_role": "batch_position",
                "navigation_enabled": True,
            },
        )
        for index in range(80)
    ]
    view.set_markers(markers)
    first_count = view._cluster_compute_count
    view._redraw_markers()
    view._redraw_markers()
    assert first_count > 0
    assert view._cluster_compute_count == first_count

