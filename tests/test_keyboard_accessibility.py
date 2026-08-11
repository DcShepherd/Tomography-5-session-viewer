"""A1: keyboard and accessibility parity.

The invariant that outranks the rest: **no keyboard path may bypass the
navigation resolver**. Everything N1 and N2 established about refusing to guess
is worthless if Enter opens a marker the pointer would have declined.
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import QApplication

from tomography_session_browser.domain.markers import ImageMarker, MarkerType
from tomography_session_browser.ui.image_viewer import (
    ImagePreviewView,
    ViewerTab,
    atlas_preview_path,
)


def _app() -> QApplication:
    return QApplication.instance() or QApplication([])


def _marker(marker_id: str, x: float, y: float, *, navigable: bool = True) -> ImageMarker:
    return ImageMarker(
        id=marker_id,
        marker_type=MarkerType.BATCH_POSITION,
        linked_object_id=f"obj-{marker_id}",
        source_object_id="src",
        x=x,
        y=y,
        label=marker_id,
        metadata={"navigation_enabled": navigable},
    )


def _view(markers, *, atlas: bool = False) -> ImagePreviewView:
    _app()
    view = ImagePreviewView()
    view._atlas_lod_enabled = atlas
    view._last_display_markers = list(markers)
    view.resize(400, 400)
    return view


def _press(view: ImagePreviewView, key: Qt.Key) -> None:
    view.keyPressEvent(QKeyEvent(QKeyEvent.Type.KeyPress, key, Qt.KeyboardModifier.NoModifier))


# --- the invariant ---------------------------------------------------------


def test_enter_never_opens_a_marker_the_resolver_declined() -> None:
    """``navigation_enabled`` is the resolver's verdict carried on the marker."""

    view = _view([_marker("a", 10, 10, navigable=False)])
    opened: list[str] = []
    selected: list[str] = []
    view._marker_opened = lambda m: opened.append(m.id)
    view._marker_selected = lambda m: selected.append(m.id)

    _press(view, Qt.Key.Key_Right)
    _press(view, Qt.Key.Key_Return)

    assert opened == [], "Enter opened a marker the resolver refused to resolve"
    assert selected, "it should still be selectable and inspectable"


def test_enter_opens_a_uniquely_navigable_marker() -> None:
    view = _view([_marker("a", 10, 10, navigable=True)])
    opened: list[str] = []
    view._marker_opened = lambda m: opened.append(m.id)
    view._marker_selected = lambda _m: None

    _press(view, Qt.Key.Key_Right)
    _press(view, Qt.Key.Key_Return)

    assert opened == ["a"]


# --- markers are reachable outside Atlas -----------------------------------


def test_non_atlas_markers_are_keyboard_reachable() -> None:
    """Arrow traversal used to exist only when Atlas LOD was enabled."""

    view = _view([_marker("a", 10, 10), _marker("b", 100, 10)], atlas=False)
    selected: list[str] = []
    view._marker_selected = lambda m: selected.append(m.id)

    _press(view, Qt.Key.Key_Right)

    assert view._keyboard_marker_id is not None
    assert selected


def test_atlas_markers_remain_reachable() -> None:
    marker = _marker("a", 10, 10)
    marker.metadata["atlas_lod_role"] = "batch_position"
    view = _view([marker], atlas=True)
    view._marker_selected = lambda _m: None

    _press(view, Qt.Key.Key_Right)

    assert view._keyboard_marker_id == "a"


def test_camera_fov_and_label_overlays_are_not_keyboard_targets() -> None:
    """Decorative overlays would otherwise be dead stops in the tab order."""

    fov = ImageMarker(
        id="fov",
        marker_type=MarkerType.CAMERA_FOV,
        linked_object_id="obj",
        source_object_id="src",
        x=5,
        y=5,
    )
    view = _view([fov, _marker("a", 10, 10)], atlas=False)

    assert [m.id for m in view._interactive_markers()] == ["a"]


# --- movement follows geometry, not identifiers ----------------------------


def test_arrow_movement_is_spatial_not_alphabetical() -> None:
    """Traversal by sorted ID could jump across the micrograph."""

    # "z" is the nearest marker to the right of "a"; "b" is far left.
    markers = [_marker("a", 100, 100), _marker("b", 10, 100), _marker("z", 140, 100)]
    view = _view(markers, atlas=False)
    view._marker_selected = lambda _m: None
    view._keyboard_marker_id = "a"

    _press(view, Qt.Key.Key_Right)

    assert view._keyboard_marker_id == "z"


def test_moving_left_finds_the_marker_to_the_left() -> None:
    markers = [_marker("a", 100, 100), _marker("b", 10, 100), _marker("z", 140, 100)]
    view = _view(markers, atlas=False)
    view._marker_selected = lambda _m: None
    view._keyboard_marker_id = "a"

    _press(view, Qt.Key.Key_Left)

    assert view._keyboard_marker_id == "b"


def test_vertical_movement_uses_vertical_geometry() -> None:
    markers = [_marker("a", 100, 100), _marker("below", 100, 200)]
    view = _view(markers, atlas=False)
    view._marker_selected = lambda _m: None
    view._keyboard_marker_id = "a"

    _press(view, Qt.Key.Key_Down)

    assert view._keyboard_marker_id == "below"


def test_movement_stops_at_the_edge_rather_than_wrapping() -> None:
    """Wrapping to the far side of an image is disorienting."""

    markers = [_marker("a", 10, 10), _marker("b", 100, 10)]
    view = _view(markers, atlas=False)
    view._marker_selected = lambda _m: None
    view._keyboard_marker_id = "b"

    _press(view, Qt.Key.Key_Right)

    assert view._keyboard_marker_id == "b"


def test_first_arrow_press_enters_from_the_implied_edge() -> None:
    markers = [_marker("left", 10, 10), _marker("right", 200, 10)]
    view = _view(markers, atlas=False)
    view._marker_selected = lambda _m: None

    _press(view, Qt.Key.Key_Left)

    assert view._keyboard_marker_id == "right"


# --- escape ----------------------------------------------------------------


def test_the_keyboard_cursor_survives_a_marker_refresh() -> None:
    """Regression: keyboard selection used to wipe its own cursor.

    Arrowing to a marker notifies the window, which refreshes the overlay,
    which called ``set_markers()`` — and that cleared the cursor
    unconditionally. Every arrow press therefore started over from the edge.
    Only a *real* selection callback triggers this, so stubbed unit tests
    missed it; it was caught on live data.
    """

    markers = [_marker("a", 10, 10), _marker("b", 100, 10)]
    view = _view(markers, atlas=False)
    view._marker_selected = lambda _m: view.set_markers(markers)

    _press(view, Qt.Key.Key_Right)
    first = view._keyboard_marker_id
    _press(view, Qt.Key.Key_Right)

    assert first is not None
    assert view._keyboard_marker_id == "b", "the cursor was reset by the refresh"


def test_a_marker_that_disappears_drops_the_cursor() -> None:
    """The cursor is preserved only while its marker still exists."""

    view = _view([_marker("a", 10, 10)], atlas=False)
    view._marker_selected = lambda _m: None
    _press(view, Qt.Key.Key_Right)
    assert view._keyboard_marker_id == "a"

    view.set_markers([_marker("other", 50, 50)])

    assert view._keyboard_marker_id is None


def test_escape_clears_the_keyboard_marker() -> None:
    view = _view([_marker("a", 10, 10)], atlas=False)
    view._marker_selected = lambda _m: None
    _press(view, Qt.Key.Key_Right)
    assert view._keyboard_marker_id is not None

    _press(view, Qt.Key.Key_Escape)

    assert view._keyboard_marker_id is None


# --- what a screen reader hears --------------------------------------------


def test_viewer_rows_announce_label_status_and_summary() -> None:
    from tomography_session_browser.services.item_status import ItemListStatus

    _app()
    tab = ViewerTab("Atlas")

    class _Item:
        id = "sm-1"
        name = "Map 1"
        image_path = None
        mrc_path = None

    tab.set_items(
        [_Item()],
        atlas_preview_path,
        lambda value: value.name,
        item_status_for=lambda _v: ItemListStatus(
            status="failed", summary="3 of 5 tilt series"
        ),
    )

    announced = tab.list.topLevelItem(0).data(0, Qt.ItemDataRole.AccessibleTextRole)
    assert "Map 1" in announced
    assert "Failed" in announced
    assert "3 of 5 tilt series" in announced


def test_the_filter_count_is_announced_with_context() -> None:
    """A bare "0" is meaningless out of context."""

    _app()
    tab = ViewerTab("Atlas")

    class _Item:
        def __init__(self, name):
            self.id = name
            self.name = name
            self.image_path = None
            self.mrc_path = None

    tab.set_items([_Item("Map 1"), _Item("Map 2")], atlas_preview_path, lambda v: v.name)
    assert "2 shown" in tab.list_count.accessibleDescription()

    tab.list_filter.setText("Map 1")

    assert tab.list_count.text() == "1 of 2"
    assert "filtered by map 1" in tab.list_count.accessibleDescription().lower()


def test_a_cluster_row_announces_its_batch_and_status() -> None:
    """The row paints its label, so it has no text to announce otherwise."""

    from tomography_session_browser.ui.image_viewer import _AtlasClusterRow

    _app()
    marker = ImageMarker(
        id="atlas:batch:bp-1",
        marker_type=MarkerType.BATCH_POSITION,
        linked_object_id="bp-1",
        source_object_id="atlas",
        label="Position_1",
        status="failed",
        tooltip="Position_1 — acquisition failed",
        metadata={"navigation_enabled": True},
    )

    row = _AtlasClusterRow(marker)

    assert "Position_1" in row.accessibleName()
    assert "Failed" in row.accessibleName()


def test_an_unopenable_cluster_row_says_so() -> None:
    from tomography_session_browser.ui.image_viewer import _AtlasClusterRow

    _app()
    marker = ImageMarker(
        id="atlas:batch:bp-2",
        marker_type=MarkerType.BATCH_POSITION,
        linked_object_id="bp-2",
        source_object_id="atlas",
        label="Position_2",
        status="collected",
        tooltip="Overview link is ambiguous between: A, B.",
        metadata={"navigation_enabled": False},
    )

    row = _AtlasClusterRow(marker)

    assert row.accessibleDescription().startswith("Unavailable.")
    assert "ambiguous" in row.accessibleDescription()


# --- reduced motion --------------------------------------------------------


def test_reduced_motion_is_honoured_through_the_existing_hook(monkeypatch) -> None:
    from tomography_session_browser.ui.animations import animations_enabled

    _app()
    monkeypatch.setenv("TOMOAPP_REDUCE_MOTION", "1")

    assert animations_enabled() is False
