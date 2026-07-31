from __future__ import annotations

import os
from pathlib import Path
from typing import get_args

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QColor, QIcon
from PySide6.QtWidgets import QApplication, QLabel

from tomography_session_browser.domain.enums import SessionKind
from tomography_session_browser.domain.models import (
    Atlas,
    BatchPosition,
    Overview,
    Sample,
    SearchMap,
    SearchTile,
    Session,
    TiltSeries,
)
from tomography_session_browser.ui.icons import themed_icon
from tomography_session_browser.ui.image_viewer import (
    VIEWER_LIST_MIN_WIDTH_PX,
    PreviewSources,
    ViewerTab,
)
from tomography_session_browser.ui.main_window import MainWindow, TAB_LABELS
from tomography_session_browser.ui.navigation_icons import (
    PROJECT_GROUP_ICONS,
    TAB_ICONS,
    TREE_ENTITY_GROUP_ICONS,
)
from tomography_session_browser.ui.project_model import ProjectGroupKind
from tomography_session_browser.ui.session_presenter import (
    EntityGroup,
    LinkedSampleGroup,
    TILE_STATUS_COMPLETE,
    StatCardModel,
)
from tomography_session_browser.ui.theme import (
    DARK_PALETTE,
    LIGHT_PALETTE,
    apply_theme,
)
from tomography_session_browser.ui.widgets.stat_card import StatCard


REPO_ROOT = Path(__file__).resolve().parents[1]
ICON_DIR = REPO_ROOT / "tomography_session_browser" / "assets" / "icons"
HANDOFF_ICON_NAMES = {
    "atlas",
    "batch-position",
    "chevron-right",
    "overview",
    "search-map",
    "search-tile",
    "session-dashboard",
    "shape-dot",
    "tilt-series",
}
METADATA_ICON_NAMES = {"clock", "link", "microscope"}


def test_navigation_icon_mappings_are_complete_and_resolve_to_svg_assets() -> None:
    assert set(TAB_ICONS) == set(TAB_LABELS)
    assert set(PROJECT_GROUP_ICONS) == set(get_args(ProjectGroupKind))
    assert set(TREE_ENTITY_GROUP_ICONS) == {
        "Overviews",
        "Search maps",
        "Search",
        "Batch positions",
        "Tilt series",
    }

    mapped_names = (
        set(TAB_ICONS.values())
        | set(PROJECT_GROUP_ICONS.values())
        | set(TREE_ENTITY_GROUP_ICONS.values())
    )
    for name in mapped_names | HANDOFF_ICON_NAMES | METADATA_ICON_NAMES:
        path = ICON_DIR / f"{name}.svg"
        assert path.is_file()
        text = path.read_text(encoding="utf-8")
        assert 'viewBox="0 0 24 24"' in text
        assert "currentColor" in text


def test_session_pill_uses_real_state_icons_instead_of_text_glyphs() -> None:
    app = _app()
    apply_theme(app, DARK_PALETTE)
    window = MainWindow()

    try:
        assert window.session_pill_icon.property("iconName") == "shape-dot"
        assert window.session_pill_icon.pixmap() is not None
        assert "●" not in window.session_pill.text_full()

        window._sessions = [
            Session(
                id="one",
                name="One",
                path=Path("C:/project/one"),
                kind=SessionKind.COLLECTION,
            ),
            Session(
                id="two",
                name="Two",
                path=Path("C:/project/two"),
                kind=SessionKind.COLLECTION,
            ),
        ]
        window._update_session_pill()

        assert window.session_pill_icon.property("iconName") == "link"
        assert window.session_pill_icon.pixmap() is not None
        assert "2 sessions" in window.session_pill.text_full()
        assert "●" not in window.session_pill.text_full()
    finally:
        window.close()
        app.processEvents()


def test_main_tabs_keep_labels_and_refresh_icons_for_both_palettes() -> None:
    app = _app()
    apply_theme(app, DARK_PALETTE)
    window = MainWindow()
    window.resize(2000, 1100)
    window.show()
    app.processEvents()

    try:
        dark_colours: list[str] = []
        for index, label in enumerate(TAB_LABELS):
            assert window.tabs.tabText(index) == (
                "Search maps" if label == "Search map"
                else "Search tiles" if label == "Search"
                else label
            )
            icon = window.tabs.tabIcon(index)
            assert not icon.isNull()
            dark_colours.append(_opaque_centre_colour(icon))

        assert window.tabs.tabBar().sizeHint().width() <= window.tabs.tabBar().width()

        apply_theme(app, LIGHT_PALETTE)
        window._refresh_action_icons()
        app.processEvents()

        light_colours = [
            _opaque_centre_colour(window.tabs.tabIcon(index))
            for index in range(len(TAB_LABELS))
        ]
        assert dark_colours != light_colours

        window.resize(960, 640)
        app.processEvents()
        assert window.tabs.tabBar().usesScrollButtons()
        assert all(
            window.tabs.tabText(index)
            for index in range(len(TAB_LABELS))
        )
        assert all(
            not window.tabs.tabIcon(index).isNull()
            for index in range(len(TAB_LABELS))
        )
    finally:
        window.close()
        app.processEvents()


def test_viewer_rows_keep_fixed_height_and_receive_their_entity_icon() -> None:
    app = _app()
    apply_theme(app, DARK_PALETTE)
    tab = ViewerTab("Empty", entity_icon="atlas")
    atlas = Atlas(id="atlas-1")
    tab.set_items(
        [atlas],
        lambda _value: PreviewSources(None),
        lambda _value: "Atlas 1",
        auto_load_preview=False,
    )

    item = tab.list.topLevelItem(0)
    decoration = item.data(0, Qt.ItemDataRole.DecorationRole)
    assert isinstance(decoration, QIcon)
    assert not decoration.isNull()
    assert item.sizeHint(0).height() == 58
    assert tab.list.iconSize() == QSize(16, 16)
    assert tab.list.minimumWidth() == VIEWER_LIST_MIN_WIDTH_PX

    apply_theme(app, LIGHT_PALETTE)
    before = _opaque_centre_colour(decoration)
    tab.refresh_theme()
    after = _opaque_centre_colour(item.icon(0))
    assert before != after
    tab.close()


def test_stat_card_uses_icon_dots_and_neutralises_zero_counts() -> None:
    app = _app()
    apply_theme(app, DARK_PALETTE)
    card = StatCard(
        StatCardModel(
            label="Atlases",
            value=0,
            sparkline=[],
            items=[],
            status_summary={TILE_STATUS_COMPLETE: 0},
            destination="Atlas",
        )
    )

    labels = card.findChildren(QLabel)
    assert all(not any(glyph in label.text() for glyph in "✓⚠✕") for label in labels)
    dot = card.findChild(QLabel, "statStatusDot")
    text = card.findChild(QLabel, "statStatusText")
    assert dot is not None and dot.pixmap() is not None
    assert text is not None and text.text() == "0 complete"
    assert dot.pixmap().toImage().pixelColor(5, 5).name() == DARK_PALETTE.unknown
    card.close()


def test_project_tree_nodes_use_semantic_icons_and_refresh_with_theme() -> None:
    app = _app()
    apply_theme(app, DARK_PALETTE)
    window = MainWindow()
    sample = Sample(id="sample", name="Sample 1", path=Path("C:/project/Sample1"))
    values_and_icons = (
        (
            Session(
                id="atlas-session",
                name="Atlas session",
                path=Path("C:/project/atlas"),
                kind=SessionKind.ATLAS_SCREENING,
            ),
            "atlas",
        ),
        (
            Session(
                id="collection-session",
                name="Collection",
                path=Path("C:/project/collection"),
                kind=SessionKind.MULTIGRID,
            ),
            "folder-open",
        ),
        (sample, "folder-open"),
        (
            LinkedSampleGroup(
                label="Linked sample",
                samples=[sample],
                atlas=None,
                overviews=[],
                search_maps=[],
                search_tiles=[],
                batch_positions=[],
                tilt_series=[],
            ),
            "layers",
        ),
        (EntityGroup("Search maps", [SearchMap(id="sm", name="Map")]), "search-map"),
        (Atlas(id="atlas"), "atlas"),
        (
            Overview(
                id="overview",
                name="Overview",
                image_path=Path("C:/project/overview.jpg"),
            ),
            "overview",
        ),
        (SearchMap(id="search-map", name="Search map"), "search-map"),
        (SearchTile(id="search-tile", name="Search tile"), "search-tile"),
        (BatchPosition(id="batch", name="Batch position"), "batch-position"),
        (
            TiltSeries(
                id="tilt",
                name="Tilt series",
                mrc_path=Path("C:/project/tilt.mrc"),
            ),
            "tilt-series",
        ),
    )

    try:
        window.tree.clear()
        for value, expected_icon in values_and_icons:
            assert window._tree_icon_name_for(value) == expected_icon
            item = window._item(window._label_for(value), value)
            window.tree.addTopLevelItem(item)
            assert _icon_images_match(item.icon(0), themed_icon(expected_icon))

        first_item = window.tree.topLevelItem(0)
        before = _opaque_centre_colour(first_item.icon(0))
        apply_theme(app, LIGHT_PALETTE)
        window._refresh_theme_dependent_widgets()
        app.processEvents()
        after = _opaque_centre_colour(first_item.icon(0))
        assert before != after
    finally:
        window.close()
        app.processEvents()


def _opaque_centre_colour(icon: QIcon) -> str:
    image = icon.pixmap(QSize(16, 16)).toImage()
    strongest = QColor()
    strongest_alpha = -1
    for y in range(image.height()):
        for x in range(image.width()):
            colour = image.pixelColor(x, y)
            if colour.alpha() > strongest_alpha:
                strongest = colour
                strongest_alpha = colour.alpha()
    if strongest_alpha <= 0:
        raise AssertionError("Icon has no visible pixels")
    return strongest.name()


def _icon_images_match(left: QIcon, right: QIcon) -> bool:
    size = QSize(18, 18)
    return left.pixmap(size).toImage() == right.pixmap(size).toImage()


def _app() -> QApplication:
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app
