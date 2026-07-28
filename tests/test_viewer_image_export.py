from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QBrush, QColor, QImage, QKeySequence, QPen
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QGraphicsItem,
    QLabel,
    QTabWidget,
)

from tomography_session_browser.domain.markers import ImageMarker, MarkerType
from tomography_session_browser.ui import image_viewer
from tomography_session_browser.ui.image_viewer import (
    EXPORT_RENDER_SCALE,
    FLOATING_CONTROL_BOTTOM_INSET_PX,
    FLOATING_CONTROL_INSET_PX,
    ImageExportDialog,
    ImagePreviewView,
    ViewerTab,
)


def _app() -> QApplication:
    return QApplication.instance() or QApplication([])


def _shown_view() -> ImagePreviewView:
    app = _app()
    view = ImagePreviewView()
    view.resize(420, 320)
    view.show()
    app.processEvents()
    image = QImage(300, 180, QImage.Format.Format_RGB32)
    image.fill(QColor("#8b3547"))
    view.set_image(image)
    view.set_markers(
        [
            ImageMarker(
                id="export-marker",
                marker_type=MarkerType.EXPOSURE_AREA,
                linked_object_id="batch-1",
                source_object_id="search-map-1",
                x=150.0,
                y=90.0,
                radius=14.0,
            )
        ]
    )
    view.fit_image()
    app.processEvents()
    return view


def test_current_view_is_rendered_offscreen_at_export_resolution() -> None:
    view = _shown_view()
    visible = view.rendered_image_rect()

    rendered = view.render_current_view()

    assert visible is not None
    assert rendered is not None
    image, render_scale = rendered
    assert render_scale == EXPORT_RENDER_SCALE
    assert image.width() == round(visible.width() * render_scale)
    assert image.height() == round(visible.height() * render_scale)
    assert image.pixelColor(image.width() // 4, image.height() // 4) == QColor(
        "#8b3547"
    )
    assert image.pixelColor(image.width() // 2, image.height() // 2) != QColor(
        "#8b3547"
    )


def test_screen_fixed_overlays_scale_with_export_resolution() -> None:
    view = _shown_view()
    overlay_colour = QColor("#00ffff")
    fixed_size_overlay = view.scene().addRect(
        QRectF(0.0, 0.0, 20.0, 10.0),
        QPen(Qt.PenStyle.NoPen),
        QBrush(overlay_colour),
    )
    fixed_size_overlay.setPos(80.0, 50.0)
    fixed_size_overlay.setFlag(
        QGraphicsItem.GraphicsItemFlag.ItemIgnoresTransformations,
        True,
    )

    rendered = view.render_current_view()

    assert rendered is not None
    image, render_scale = rendered
    matching_x = [
        x
        for y in range(image.height())
        for x in range(image.width())
        if image.pixelColor(x, y) == overlay_colour
    ]
    assert matching_x
    rendered_width = max(matching_x) - min(matching_x) + 1
    assert rendered_width >= round(19 * render_scale)


def test_export_button_matches_zoom_button_and_ctrl_s_shortcut() -> None:
    app = _app()
    tab = ViewerTab("Empty", show_list=False)

    assert tab.export_image_button.width() == tab.zoom_in_button.width()
    assert (
        tab._export_shortcut.key().matches(QKeySequence("Ctrl+S"))
        == QKeySequence.SequenceMatch.ExactMatch
    )
    assert (
        tab._export_shortcut.context()
        == Qt.ShortcutContext.WindowShortcut
    )
    assert tab._export_shortcut.isEnabled() is False
    tab.show()
    app.processEvents()
    assert tab._export_shortcut.isEnabled() is True
    tab.hide()
    app.processEvents()
    assert tab._export_shortcut.isEnabled() is False
    assert "Ctrl+S" in tab.export_image_button.toolTip()


def test_only_visible_viewer_tab_enables_window_export_shortcut() -> None:
    app = _app()
    tabs = QTabWidget()
    first = ViewerTab("First", show_list=False)
    second = ViewerTab("Second", show_list=False)
    tabs.addTab(first, "First")
    tabs.addTab(second, "Second")
    tabs.show()
    app.processEvents()

    assert first._export_shortcut.isEnabled() is True
    assert second._export_shortcut.isEnabled() is False

    tabs.setCurrentWidget(second)
    app.processEvents()

    assert first._export_shortcut.isEnabled() is False
    assert second._export_shortcut.isEnabled() is True


def test_export_dialog_previews_full_resolution_and_normalises_png_path(
    tmp_path: Path,
) -> None:
    _app()
    image = QImage(1200, 800, QImage.Format.Format_RGB32)
    image.fill(QColor("#24425e"))
    dialog = ImageExportDialog(image, tmp_path / "atlas-view")

    preview = dialog.findChild(QLabel, "imageExportPreview")
    assert preview is not None
    assert preview.pixmap() is not None
    assert preview.pixmap().width() <= 760
    assert preview.pixmap().height() <= 480

    dialog._accept_path()

    assert dialog.result() == QDialog.DialogCode.Accepted
    assert dialog.selected_path == tmp_path / "atlas-view.png"


def test_exported_scale_bar_keeps_unpainted_widget_area_transparent() -> None:
    _app()
    tab = ViewerTab("Empty", show_list=False)
    tab.scale_bar.set_scale(100.0, "1 µm")

    rendered = tab._render_widget_for_export(
        tab.scale_bar,
        EXPORT_RENDER_SCALE,
    )

    # The fixed-width scale-bar widget has unused space to the right of this
    # 100 px bar. That area must remain transparent when composited onto the
    # microscope image rather than inheriting the viewer's black canvas.
    assert rendered.pixelColor(
        rendered.width() - 4,
        rendered.height() // 2,
    ).alpha() == 0
    assert any(
        rendered.pixelColor(x, y).alpha() > 0
        for y in range(rendered.height())
        for x in range(rendered.width() // 2)
    )


def test_export_saves_full_render_with_scale_bar(
    monkeypatch,
    tmp_path: Path,
) -> None:
    app = _app()
    tab = ViewerTab("Empty", show_list=False)
    tab.resize(640, 480)
    tab.show()
    app.processEvents()
    image = QImage(360, 240, QImage.Format.Format_RGB32)
    image.fill(QColor("#24425e"))
    tab.viewer.set_image(image)
    tab.viewer.fit_image()
    tab.scale_bar.set_scale(100.0, "1 µm")
    tab._displayed_path = tmp_path / "source.jpg"
    destination = tmp_path / "exported.png"
    app.processEvents()

    class AcceptedExportDialog:
        def __init__(self, exported: QImage, default_path: Path, parent) -> None:
            assert exported.width() > tab.viewer.rendered_image_rect().width()
            assert default_path.name.startswith("source_view_")
            assert parent is tab
            self.selected_path = destination

        def exec(self) -> QDialog.DialogCode:
            return QDialog.DialogCode.Accepted

    monkeypatch.setattr(
        image_viewer,
        "ImageExportDialog",
        AcceptedExportDialog,
    )

    tab._export_current_view()

    saved = QImage(str(destination))
    assert not saved.isNull()
    assert saved.width() > tab.viewer.rendered_image_rect().width()
    # The scale bar is composited inside the lower-left export inset rather
    # than captured as part of the viewer's floating QWidget chrome.
    lower_left = saved.copy(
        0,
        saved.height() - 100,
        min(saved.width(), 450),
        100,
    )
    assert any(
        lower_left.pixelColor(x, y) != QColor("#24425e")
        for y in range(lower_left.height())
        for x in range(lower_left.width())
    )
    unused_bar_x = round(FLOATING_CONTROL_INSET_PX * EXPORT_RENDER_SCALE) + (
        round(tab.scale_bar.width() * EXPORT_RENDER_SCALE) - 4
    )
    unused_bar_y = saved.height() - round(
        (
            FLOATING_CONTROL_BOTTOM_INSET_PX
            + (tab.scale_bar.height() / 2)
        )
        * EXPORT_RENDER_SCALE
    )
    assert saved.pixelColor(unused_bar_x, unused_bar_y) == QColor("#24425e")
