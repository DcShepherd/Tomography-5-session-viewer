"""C2: the project tree as a first-class wayfinding surface.

The tree *is* the scope selector, but before this module Enter did nothing on
any row, double-click only responded on a Tilt series, and the LS/AT/DC/!
badges had no in-app explanation at all.
"""

from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QTreeWidgetItem

from tomography_session_browser.domain.enums import SessionKind
from tomography_session_browser.domain.models import (
    Atlas,
    BatchPosition,
    MdocSection,
    Overview,
    Sample,
    SearchMap,
    SearchTile,
    Session,
    TiltSeries,
)
from tomography_session_browser.ui.context_stack import (
    ENTITY_DISPLAY_LABELS,
    tab_key_for_display_label,
)
from tomography_session_browser.ui.main_window import (
    OBJECT_ROLE,
    PROJECT_BADGE_MEANINGS,
    TAB_LABELS,
    MainWindow,
)


def _app() -> QApplication:
    return QApplication.instance() or QApplication([])


def _session(tmp_path: Path) -> Session:
    mrc = tmp_path / "vellio_1.mrc"
    mrc.write_bytes(b"0")
    tilt = TiltSeries(
        id="tilt-1",
        name="vellio_1",
        mrc_path=mrc,
        sections=[MdocSection(z_value=i) for i in range(20)],
    )
    sample = Sample(
        id="s-1",
        name="Sample1",
        path=tmp_path,
        atlas=Atlas(id="atlas-1"),
        overviews=[Overview(id="ov-1", name="Overview_1", image_path=tmp_path / "ov.jpg")],
        search_maps=[SearchMap(id="sm-1", name="Map 1")],
        search_tiles=[SearchTile(id="tile-1", name="Tile 1")],
        batch_positions=[BatchPosition(id="bp-1", name="Position 1")],
        tilt_series=[tilt],
    )
    return Session(
        id="sess-1",
        name="Reference_Collection_B_20260319",
        path=tmp_path,
        kind=SessionKind.MULTIGRID,
        samples=[sample],
    )


def _window(tmp_path: Path) -> MainWindow:
    _app()
    session = _session(tmp_path)
    window = MainWindow()
    window._sessions = [session]
    window._session = session
    window._active_context = session
    window._rebuild_sample_index()
    window._populate_tree()
    window._render_viewer_tabs()
    return window


def _walk(window: MainWindow):
    def visit(item: QTreeWidgetItem):
        yield item
        for index in range(item.childCount()):
            yield from visit(item.child(index))

    for index in range(window.tree.topLevelItemCount()):
        yield from visit(window.tree.topLevelItem(index))


def _item_for(window: MainWindow, predicate):
    for item in _walk(window):
        value = item.data(0, OBJECT_ROLE)
        if value is not None and predicate(value):
            return item
    return None


# --- the regression this module exists to prevent --------------------------


def test_every_tree_group_heading_resolves_to_a_tab() -> None:
    """A tree entity-group heading must map back to a real tab key.

    C1 renamed the tree's ``Search`` heading to ``Search tiles`` and a
    hand-written second mapping in ``_switch_to_group_tab`` was left behind, so
    activating that group silently stopped switching tabs. The mapping is now
    derived; this pins it.
    """

    for tab_key, display in ENTITY_DISPLAY_LABELS.items():
        assert tab_key_for_display_label(display) == tab_key


@pytest.mark.parametrize("heading", ["Overviews", "Search maps", "Search tiles", "Batch positions", "Tilt series"])
def test_tree_group_headings_switch_to_their_tab(heading: str, tmp_path: Path) -> None:
    window = _window(tmp_path)
    window.tabs.setCurrentIndex(TAB_LABELS.index("Session"))

    window._switch_to_group_tab(heading)

    expected = tab_key_for_display_label(heading)
    assert TAB_LABELS[window.tabs.currentIndex()] == expected


# --- keyboard activation ---------------------------------------------------


@pytest.mark.parametrize(
    ("kind", "tab"),
    [
        (Atlas, "Atlas"),
        (Overview, "Overview"),
        (SearchMap, "Search map"),
        (SearchTile, "Search"),
        (BatchPosition, "Batch position"),
        (TiltSeries, "Tilt series"),
    ],
)
def test_enter_activates_every_entity_type(kind, tab: str, tmp_path: Path) -> None:
    """Before C2 the tree had no itemActivated connection at all."""

    window = _window(tmp_path)
    window.tabs.setCurrentIndex(TAB_LABELS.index("Session"))
    item = _item_for(window, lambda value: isinstance(value, kind))
    assert item is not None, f"no tree row for {kind.__name__}"

    handled = window._activate_project_tree_item(item)

    assert handled is True
    assert TAB_LABELS[window.tabs.currentIndex()] == tab


def test_double_click_does_not_fire_the_action_twice(tmp_path: Path) -> None:
    """Qt reports a double-click through both signals; only one may act."""

    window = _window(tmp_path)
    item = _item_for(window, lambda value: isinstance(value, TiltSeries))
    assert item is not None
    calls: list[str] = []
    window.navigate_to_tilt_series = lambda *a, **k: calls.append("nav")

    window._project_tree_item_double_clicked(item, 0)
    window._project_tree_item_activated(item, 0)

    assert calls == ["nav"]


def test_the_guard_releases_so_a_second_activation_works(tmp_path: Path) -> None:
    app = _app()
    window = _window(tmp_path)
    item = _item_for(window, lambda value: isinstance(value, TiltSeries))
    calls: list[str] = []
    window.navigate_to_tilt_series = lambda *a, **k: calls.append("nav")

    window._activate_project_tree_item(item)
    app.processEvents()  # lets the single-shot guard release
    window._activate_project_tree_item(item)

    assert calls == ["nav", "nav"]


def test_activating_a_scope_row_toggles_expansion(tmp_path: Path) -> None:
    """A scope row has no destination beyond itself."""

    window = _window(tmp_path)
    item = _item_for(window, lambda value: isinstance(value, Session | Sample))
    assert item is not None
    before = item.isExpanded()

    window._activate_project_tree_item(item)

    assert item.isExpanded() is not before


def test_activation_is_distinct_from_selection(tmp_path: Path) -> None:
    """Selection re-scopes and describes; activation opens."""

    window = _window(tmp_path)
    window.tabs.setCurrentIndex(TAB_LABELS.index("Session"))
    item = _item_for(window, lambda value: isinstance(value, SearchMap))
    assert item is not None

    window._tree_selection_changed(item, None)
    after_selection = TAB_LABELS[window.tabs.currentIndex()]

    window._activate_project_tree_item(item)
    after_activation = TAB_LABELS[window.tabs.currentIndex()]

    assert after_activation == "Search map"
    assert after_activation != after_selection or after_selection == "Search map"


def test_activating_nothing_is_safe(tmp_path: Path) -> None:
    window = _window(tmp_path)

    assert window._activate_project_tree_item(None) is False
    assert window._activate_project_tree_item(QTreeWidgetItem(["orphan"])) is False


# --- badge discoverability -------------------------------------------------


def test_badge_legend_is_present_in_the_app(tmp_path: Path) -> None:
    """Badge meaning used to exist only in the handover documents."""

    window = _window(tmp_path)
    legend = window.project_badge_legend.toolTip()

    for badge, meaning in PROJECT_BADGE_MEANINGS.items():
        assert badge in legend
        assert meaning in legend


def test_every_badge_the_tree_can_render_has_a_meaning() -> None:
    from tomography_session_browser.ui.context_stack import SCOPE_BADGES

    assert set(SCOPE_BADGES.values()) == set(PROJECT_BADGE_MEANINGS)


def test_group_rows_announce_the_badge_in_words(tmp_path: Path) -> None:
    from tomography_session_browser.ui.project_model import ProjectTreeGroup

    window = _window(tmp_path)
    group = ProjectTreeGroup(
        key="k",
        kind="unresolved",
        automatic_name="DataCollection_04",
        display_name="DataCollection_04",
        sessions=[],
        warnings=["Ambiguous atlas link: matches Atlas_A, Atlas_B."],
    )

    item = window._project_group_item(group)

    accessible = item.data(0, Qt.ItemDataRole.AccessibleTextRole)
    assert "Unresolved or ambiguous atlas link" in accessible
    # The unresolved reason is stated, not left to a hover tooltip.
    assert "Ambiguous atlas link" in accessible
    assert item.data(1, Qt.ItemDataRole.AccessibleTextRole) == (
        "Unresolved or ambiguous atlas link"
    )
