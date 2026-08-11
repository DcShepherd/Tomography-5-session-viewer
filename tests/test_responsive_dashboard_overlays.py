"""R2: responsive dashboard reflow and bounded overlay controls.

The overlay panel is the risk here. Its per-collection list is dynamic — a
linked root can contribute many collections — and an unbounded panel grows past
the bottom of the canvas until part of it is unreachable.
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import QApplication, QLabel

from tomography_session_browser.domain.models import Atlas
from tomography_session_browser.ui.image_viewer import (
    ATLAS_COLLECTION_LIST_MAX_PX,
    AtlasCollectionOverlayOption,
    ViewerTab,
    atlas_preview_path,
)


def _app() -> QApplication:
    return QApplication.instance() or QApplication([])


def _atlas_tab() -> ViewerTab:
    _app()
    tab = ViewerTab("Atlas", atlas_lod=True)
    tab.resize(900, 700)
    # Shown so Qt actually lays it out; an unshown widget reports a
    # meaningless viewport height.
    tab.show()
    _app().processEvents()
    return tab


def _options(count: int) -> list[AtlasCollectionOverlayOption]:
    return [
        AtlasCollectionOverlayOption(key=f"c{i}", label=f"DataCollection_{i:02d}")
        for i in range(count)
    ]


def _open_panel(tab: ViewerTab) -> None:
    tab.overlay_button.setChecked(True)
    _app().processEvents()


# --- dynamic collection controls: 0, 1 and 20 ------------------------------


@pytest.mark.parametrize("count", [0, 1, 20])
def test_the_panel_copes_with_any_number_of_collections(count: int) -> None:
    tab = _atlas_tab()
    atlas = Atlas(id="atlas-1")
    tab.set_atlas_collection_overlay_provider(lambda _v: _options(count), lambda _k, _v: None)
    tab._update_atlas_collection_overlay_controls(atlas)
    _open_panel(tab)

    assert len(tab._atlas_collection_checks) == count
    # The panel must stay inside its canvas whatever the count.
    tab._constrain_overlay_panel()
    assert tab.overlay_panel.maximumHeight() <= tab.viewer.viewport().height()


def test_no_collections_leaves_the_list_hidden() -> None:
    tab = _atlas_tab()
    tab.set_atlas_collection_overlay_provider(lambda _v: _options(0), lambda _k, _v: None)
    tab._update_atlas_collection_overlay_controls(Atlas(id="atlas-1"))

    assert tab._atlas_collection_scroll.isVisible() is False


def test_many_collections_scroll_rather_than_growing_the_panel() -> None:
    """20 collections must not push the panel off the bottom of the canvas."""

    tab = _atlas_tab()
    tab.set_atlas_collection_overlay_provider(lambda _v: _options(20), lambda _k, _v: None)
    tab._update_atlas_collection_overlay_controls(Atlas(id="atlas-1"))
    tab._atlas_collection_section_button.setChecked(True)
    _open_panel(tab)
    tab._constrain_overlay_panel()

    scroll = tab._atlas_collection_scroll
    assert scroll.isVisible() is True
    assert scroll.widgetResizable() is True
    assert scroll.maximumHeight() <= ATLAS_COLLECTION_LIST_MAX_PX
    # The content really is taller than the window it is shown through.
    assert tab._atlas_collection_section.sizeHint().height() > scroll.maximumHeight()
    assert scroll.horizontalScrollBarPolicy() == Qt.ScrollBarPolicy.ScrollBarAlwaysOff


def test_a_short_canvas_shrinks_the_panel_further() -> None:
    tab = _atlas_tab()
    tab.set_atlas_collection_overlay_provider(lambda _v: _options(20), lambda _k, _v: None)
    tab._update_atlas_collection_overlay_controls(Atlas(id="atlas-1"))
    _open_panel(tab)

    tab.resize(900, 700)
    tab._constrain_overlay_panel()
    tall = tab.overlay_panel.maximumHeight()

    tab.resize(900, 300)
    _app().processEvents()
    tab._constrain_overlay_panel()
    short = tab.overlay_panel.maximumHeight()

    assert short < tall


# --- Escape closes and restores focus --------------------------------------


def test_escape_closes_the_overlay_panel_and_restores_focus() -> None:
    tab = _atlas_tab()
    _open_panel(tab)
    assert tab.overlay_panel.isVisible() is True

    tab.keyPressEvent(
        QKeyEvent(QKeyEvent.Type.KeyPress, Qt.Key.Key_Escape, Qt.KeyboardModifier.NoModifier)
    )

    assert tab.overlay_panel.isVisible() is False
    assert tab.overlay_button.isChecked() is False
    assert tab.overlay_button.hasFocus() is True


def test_escape_with_no_panel_open_is_not_consumed() -> None:
    """Escape must stay available to whatever else wants it."""

    tab = _atlas_tab()

    assert tab.close_overlay_panel() is False


def test_ordinary_keypress_does_not_raise() -> None:
    tab = _atlas_tab()

    tab.keyPressEvent(
        QKeyEvent(QKeyEvent.Type.KeyPress, Qt.Key.Key_A, Qt.KeyboardModifier.NoModifier)
    )

    assert tab.overlay_button.toolTip() == "Show overlay controls"


# --- preserved behaviour ---------------------------------------------------


def test_the_scale_bar_and_marker_defaults_are_unchanged() -> None:
    """R2 must not disturb the scientific overlay defaults."""

    tab = _atlas_tab()

    assert tab.scale_bar_checkbox.isChecked() is True
    assert tab.marker_legend_checkbox.isChecked() is True


def test_collection_visibility_toggles_still_report_their_key() -> None:
    tab = _atlas_tab()
    seen: list[tuple[str, bool]] = []
    tab.set_atlas_collection_overlay_provider(
        lambda _v: _options(3), lambda key, visible: seen.append((key, visible))
    )
    tab._update_atlas_collection_overlay_controls(Atlas(id="atlas-1"))

    tab._atlas_collection_checks["c1"].setChecked(False)

    assert ("c1", False) in seen


# --- dashboard card reflow -------------------------------------------------


def _grid(count: int):
    from tomography_session_browser.ui.widgets.session_dashboard import _ResponsiveCardGrid

    _app()
    grid = _ResponsiveCardGrid([QLabel(f"card {i}") for i in range(count)])
    grid.show()
    _app().processEvents()
    return grid


@pytest.mark.parametrize(
    ("width", "expected"),
    [(1200, 5), (800, 3), (500, 2), (300, 1)],
)
def test_cards_reflow_before_anything_is_compressed(width: int, expected: int) -> None:
    grid = _grid(5)

    grid.resize(width, 400)
    _app().processEvents()

    assert grid.column_count() == expected


def test_reflow_never_exceeds_the_number_of_cards() -> None:
    grid = _grid(2)

    grid.resize(1600, 400)
    _app().processEvents()

    assert grid.column_count() <= 2
