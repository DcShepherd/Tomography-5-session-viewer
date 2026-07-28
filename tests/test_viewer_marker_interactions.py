from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QImage
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from tomography_session_browser.domain.markers import ImageMarker, MarkerType
from tomography_session_browser.domain.models import SearchTile
from tomography_session_browser.domain.models import Atlas
from tomography_session_browser.ui.image_viewer import (
    AtlasCollectionOverlayOption,
    ImagePreviewView,
    ViewerTab,
)


def _app() -> QApplication:
    app = QApplication.instance()
    return app or QApplication([])


def _exposure(
    marker_id: str,
    *,
    batch_id: str,
    x: float,
    y: float,
) -> ImageMarker:
    return ImageMarker(
        id=marker_id,
        marker_type=MarkerType.EXPOSURE_AREA,
        linked_object_id=batch_id,
        source_object_id="search-map-1",
        x=x,
        y=y,
        radius=8.0,
        metadata={
            "batch_id": batch_id,
            "area_name": marker_id,
        },
    )


def _prepared_view() -> tuple[
    ImagePreviewView,
    ImageMarker,
    ImageMarker,
    ImageMarker,
]:
    app = _app()
    view = ImagePreviewView()
    view.resize(320, 320)
    view.show()
    app.processEvents()
    view.set_image(QImage(100, 100, QImage.Format.Format_RGB32))
    first = _exposure("exposure-1", batch_id="batch-1", x=50.0, y=50.0)
    linked = _exposure("exposure-2", batch_id="batch-1", x=65.0, y=50.0)
    other = _exposure("exposure-3", batch_id="batch-2", x=80.0, y=80.0)
    view.set_markers([first, linked, other])
    app.processEvents()
    return view, first, linked, other


def test_middle_click_highlights_only_exposures_linked_to_same_batch() -> None:
    view, first, linked, other = _prepared_view()
    QTest.mouseClick(
        view.viewport(),
        Qt.MouseButton.MiddleButton,
        pos=view.mapFromScene(QPointF(first.x, first.y)),
    )

    assert view.highlighted_exposure_marker_ids() == {
        first.id,
        linked.id,
    }
    assert other.id not in view.highlighted_exposure_marker_ids()


def test_shift_click_highlights_linked_exposures() -> None:
    view, first, linked, _other = _prepared_view()
    QTest.mouseClick(
        view.viewport(),
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.ShiftModifier,
        pos=view.mapFromScene(QPointF(first.x, first.y)),
    )

    assert view.highlighted_exposure_marker_ids() == {
        first.id,
        linked.id,
    }


def test_empty_left_click_and_right_click_clear_selection_and_highlights() -> None:
    view, first, _linked, _other = _prepared_view()
    cleared: list[bool] = []
    view.set_selection_cleared_callback(lambda: cleared.append(True))
    view._highlight_linked_exposures(first)

    QTest.mouseClick(
        view.viewport(),
        Qt.MouseButton.LeftButton,
        pos=view.mapFromScene(QPointF(5.0, 5.0)),
    )

    assert view.highlighted_exposure_marker_ids() == frozenset()
    assert cleared == [True]

    view._highlight_linked_exposures(first)
    QTest.mouseClick(
        view.viewport(),
        Qt.MouseButton.RightButton,
        pos=view.mapFromScene(QPointF(first.x, first.y)),
    )

    assert view.highlighted_exposure_marker_ids() == frozenset()
    assert cleared == [True, True]


def test_atlas_overlay_menu_replaces_zoom_presets_with_legend_toggle() -> None:
    _app()
    tab = ViewerTab("Empty", atlas_lod=True)

    assert tab.findChildren(
        type(tab.fit_button),
        "viewerZoomPresetButton",
    ) == []
    assert tab.marker_legend_checkbox is not None
    assert tab.atlas_marker_legend.isVisibleTo(tab) is True

    tab.marker_legend_checkbox.setChecked(False)

    assert tab.atlas_marker_legend.isHidden() is True


def test_atlas_collection_controls_are_nested_and_toggle_independently() -> None:
    _app()
    atlas = Atlas(id="atlas-1")
    changed: list[tuple[str, bool]] = []
    tab = ViewerTab(
        "Empty",
        atlas_lod=True,
        markers_for=lambda _value: [],
    )
    tab._current_value = atlas
    tab.set_atlas_collection_overlay_provider(
        lambda _value: [
            AtlasCollectionOverlayOption(
                "collection-1",
                "Collection 1 (3)",
            ),
            AtlasCollectionOverlayOption(
                "collection-2",
                "Collection 2 (7)",
            ),
        ],
        lambda key, visible: changed.append((key, visible)),
    )

    assert tab._atlas_collection_section_button is not None
    assert tab._atlas_collection_section is not None
    assert tab._atlas_collection_section_button.text() == (
        "Data collections (2)"
    )
    assert tab._atlas_collection_section.isHidden() is True

    tab._atlas_collection_section_button.setChecked(True)
    assert tab._atlas_collection_section.isHidden() is False

    tab._atlas_collection_checks["collection-2"].setChecked(False)
    assert changed == [("collection-2", False)]
    assert tab._atlas_collection_checks["collection-1"].isChecked()


def test_cluster_popup_member_uses_activation_callback() -> None:
    _app()
    view = ImagePreviewView()
    marker = ImageMarker(
        id="atlas:batch:1",
        marker_type=MarkerType.BATCH_POSITION,
        linked_object_id="batch-1",
        source_object_id="atlas",
    )
    activated: list[ImageMarker] = []
    selected: list[ImageMarker] = []
    view.set_cluster_member_activated_callback(activated.append)
    view.set_marker_selected_callback(selected.append)

    view._select_popup_member(marker)

    assert activated == [marker]
    assert selected == []


def test_explicit_clear_suppresses_current_object_marker_auto_selection() -> None:
    _app()
    search_tile = SearchTile(
        id="tile-1",
        name="Tile 1",
        metadata={"ExposureAreaName": "Exposure"},
    )
    marker = ImageMarker(
        id="search-map:batch:1:exposure",
        marker_type=MarkerType.EXPOSURE_AREA,
        linked_object_id=search_tile.id,
        source_object_id="search-map-1",
        x=10.0,
        y=10.0,
        metadata={"area_name": "Exposure"},
    )
    tab = ViewerTab(
        "Empty",
        markers_for=lambda _value: [marker],
    )
    tab._current_value = search_tile

    tab.select_marker(None)

    assert tab._active_selected_marker_id(search_tile, [marker]) is None
    assert all(not value.selected for value in tab.viewer._markers)
