"""S1: per-scope viewer state and filter integrity.

These pin the two Phase 1 gate conditions inherited from N2 — `Map 2` survives
a round trip, and same-scope navigation does not rebuild the viewer list — plus
the filter rules the plan sets out for arrival.
"""

from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from tomography_session_browser.domain.enums import SessionKind
from tomography_session_browser.domain.models import Atlas, Overview, Sample, SearchMap, Session
from tomography_session_browser.services.item_status import display_status_label
from tomography_session_browser.ui.image_viewer import (
    LIST_STATUS_ROLE,
    LIST_SUMMARY_ROLE,
    VIEWER_OBJECT_ROLE,
    ViewerViewState,
    search_map_preview_path,
)
from tomography_session_browser.ui.main_window import (
    VIEWER_STATE_CACHE_LIMIT,
    TAB_LABELS,
    MainWindow,
)


def _app() -> QApplication:
    return QApplication.instance() or QApplication([])


def _session(tmp_path: Path) -> tuple[Session, Overview, SearchMap, SearchMap]:
    atlas_path = tmp_path / "atlas.jpg"
    atlas_path.write_bytes(b"image")
    overview = Overview(id="ov-1", name="Overview_1", image_path=tmp_path / "ov.jpg")
    map_1 = SearchMap(id="sm-1", name="Map 1", overview=overview)
    map_2 = SearchMap(id="sm-2", name="Map 2", overview=overview)
    sample = Sample(
        id="sample-1",
        name="Sample1",
        path=tmp_path,
        atlas=Atlas(id="atlas-1", image_path=atlas_path),
        overviews=[overview],
        search_maps=[map_1, map_2],
    )
    session = Session(
        id="session-1",
        name="Session",
        path=tmp_path,
        kind=SessionKind.MULTIGRID,
        samples=[sample],
    )
    return session, overview, map_1, map_2


def _window(session: Session) -> MainWindow:
    _app()
    window = MainWindow()
    window._sessions = [session]
    window._session = session
    window._active_context = session
    window._rebuild_sample_index()
    window._render_viewer_tabs()
    return window


def _selected(tab) -> object | None:
    item = tab.list.currentItem()
    return item.data(0, VIEWER_OBJECT_ROLE) if item is not None else None


def test_search_map_selection_survives_a_jump_to_overview_and_back(tmp_path: Path) -> None:
    """Gate condition: 'Map 2 -> Overview -> Search maps' retains 'Map 2'."""

    session, _overview, _map_1, map_2 = _session(tmp_path)
    window = _window(session)
    tab = window._viewer_tabs["Search map"]
    tab.select_object(map_2)
    assert _selected(tab) is map_2

    window._viewer_navigation_requested(map_2, "overview")
    assert TAB_LABELS[window.tabs.currentIndex()] == "Overview"

    window.tabs.setCurrentIndex(TAB_LABELS.index("Search map"))

    assert _selected(tab) is map_2


def test_same_scope_navigation_does_not_rebuild_the_viewer_list(tmp_path: Path) -> None:
    """Gate condition: no full viewer-list rebuild when the scope is unchanged."""

    session, _overview, _map_1, map_2 = _session(tmp_path)
    window = _window(session)
    tab = window._viewer_tabs["Search map"]
    tab.select_object(map_2)

    # Identity of the row widgets is the cheapest proof the list was not
    # cleared and repopulated.
    rows_before = [tab.list.topLevelItem(i) for i in range(tab.list.topLevelItemCount())]
    assert rows_before

    window._viewer_navigation_requested(map_2, "overview")

    rows_after = [tab.list.topLevelItem(i) for i in range(tab.list.topLevelItemCount())]
    assert rows_after == rows_before


def test_atlas_image_status_stays_available_after_same_item_refresh(tmp_path: Path) -> None:
    """A status-less refresh must not replace known image availability with unknown."""

    session, _overview, _map_1, _map_2 = _session(tmp_path)
    window = _window(session)
    tab = window._viewer_tabs["Atlas"]
    row = tab.list.topLevelItem(0)

    assert row.data(0, LIST_STATUS_ROLE) == "Available"

    # Selecting an Atlas or linked-session node rebuilds the scope while the
    # same Atlas objects remain in the tab, taking the refresh_providers path.
    window._render_viewer_tabs()

    assert tab.list.topLevelItem(0) is row
    assert row.data(0, LIST_STATUS_ROLE) == "Available"


def test_scope_change_still_rebuilds(tmp_path: Path) -> None:
    """The optimisation must not stop a genuine scope change from rebuilding."""

    session, _overview, _map_1, _map_2 = _session(tmp_path)
    window = _window(session)
    tab = window._viewer_tabs["Search map"]
    rows_before = [tab.list.topLevelItem(i) for i in range(tab.list.topLevelItemCount())]

    other = SearchMap(id="sm-9", name="Map 9")
    session.samples[0].search_maps = [other]
    window._render_viewer_tabs()

    rows_after = [tab.list.topLevelItem(i) for i in range(tab.list.topLevelItemCount())]
    assert rows_after != rows_before
    assert [row.data(0, VIEWER_OBJECT_ROLE) for row in rows_after] == [other]


def test_navigation_clears_an_incompatible_filter_and_says_so(tmp_path: Path) -> None:
    session, _overview, _map_1, map_2 = _session(tmp_path)
    window = _window(session)
    tab = window._viewer_tabs["Search map"]
    tab.list_filter.setText("no such row")
    # E1 made the filtered count honest: "0 of 2" rather than a bare "0",
    # which reads the same as a scope that genuinely holds nothing.
    assert tab.list_count.text() == "0 of 2"

    window._select_viewer_object(map_2)

    assert tab.list_filter.text() == ""
    assert _selected(tab) is map_2
    # The count must agree with what is on screen; the old behaviour unhid a
    # single row while the count still read 0.
    assert tab.list_count.text() == str(tab.list.topLevelItemCount())
    assert not tab.list.currentItem().isHidden()
    assert "filter cleared" in window._pending_filter_notice.lower()


def test_a_compatible_filter_is_left_alone(tmp_path: Path) -> None:
    """Only an *incompatible* filter is cleared."""

    session, _overview, _map_1, map_2 = _session(tmp_path)
    window = _window(session)
    tab = window._viewer_tabs["Search map"]
    tab.list_filter.setText("Map")

    window._select_viewer_object(map_2)

    assert tab.list_filter.text() == "Map"
    assert _selected(tab) is map_2
    assert window._pending_filter_notice is None


def test_filter_is_applied_before_the_initial_preview_is_chosen(tmp_path: Path) -> None:
    """A rebuild must not open on a row the active filter excludes."""

    session, _overview, _map_1, map_2 = _session(tmp_path)
    window = _window(session)
    tab = window._viewer_tabs["Search map"]
    window.tabs.setCurrentIndex(TAB_LABELS.index("Search map"))
    tab.set_items(
        list(session.samples[0].search_maps),
        search_map_preview_path,
        lambda value: getattr(value, "name", ""),
        restore_state=ViewerViewState(filter_text="Map 2"),
    )

    visible = [
        tab.list.topLevelItem(i).data(0, VIEWER_OBJECT_ROLE)
        for i in range(tab.list.topLevelItemCount())
        if not tab.list.topLevelItem(i).isHidden()
    ]
    assert visible == [map_2]
    assert tab._current_value is map_2


def test_a_restored_filter_with_no_matches_does_not_load_a_hidden_row(
    tmp_path: Path,
) -> None:
    session, _overview, _map_1, _map_2 = _session(tmp_path)
    window = _window(session)
    tab = window._viewer_tabs["Search map"]

    tab.set_items(
        list(session.samples[0].search_maps),
        search_map_preview_path,
        lambda value: getattr(value, "name", ""),
        restore_state=ViewerViewState(filter_text="no-such-map"),
    )

    assert tab.list_count.text() == "0 of 2"
    assert tab._current_value is None
    assert tab.empty_state_panel.isHidden() is False


def test_state_is_dropped_when_the_project_changes(tmp_path: Path) -> None:
    session, _overview, _map_1, map_2 = _session(tmp_path)
    window = _window(session)
    window._viewer_tabs["Search map"].select_object(map_2)
    window._capture_viewer_states()
    assert window._viewer_states

    window.clear_viewer_states()

    assert not window._viewer_states


def test_state_cache_is_bounded(tmp_path: Path) -> None:
    """The cache must not grow without limit as scopes are visited."""

    session, _overview, _map_1, map_2 = _session(tmp_path)
    window = _window(session)
    window._viewer_tabs["Search map"].select_object(map_2)

    for index in range(VIEWER_STATE_CACHE_LIMIT * 2):
        session.name = f"Scope {index}"
        session.id = f"session-{index}"
        window._capture_viewer_states()

    assert len(window._viewer_states) <= VIEWER_STATE_CACHE_LIMIT
    # Only identifiers and scalars are retained.
    for state in window._viewer_states.values():
        assert isinstance(state, ViewerViewState)
        assert state.object_id is None or isinstance(state.object_id, str)


def test_refresh_providers_updates_the_rows_it_repaints_markers_for(tmp_path: Path) -> None:
    """Markers and row chips must never come from different providers.

    ``refresh_providers`` is the path a same-scope rebuild takes whenever the
    item list is unchanged. It repainted the overlay from the new providers but
    left every row's status chip, summary and announced text as the previous
    rebuild wrote them, so a re-derived status showed fresh markers beside a
    stale chip with nothing on screen to say they disagreed.
    """

    from tomography_session_browser.services.item_status import ItemListStatus

    session, _overview, map_1, _map_2 = _session(tmp_path)
    window = _window(session)
    tab = window._viewer_tabs["Search map"]

    tab.set_items(
        [map_1],
        search_map_preview_path,
        window._label_for,
        item_status_for=lambda _value: ItemListStatus("done", "12 tiles acquired"),
    )
    row = tab.list.topLevelItem(0)
    assert "12 tiles acquired" in str(row.data(0, LIST_SUMMARY_ROLE))

    assert tab.has_same_items([map_1]) is True
    tab.refresh_providers(
        search_map_preview_path,
        item_status_for=lambda _value: ItemListStatus("failed", "acquisition stopped"),
    )

    row = tab.list.topLevelItem(0)
    assert str(row.data(0, LIST_SUMMARY_ROLE)) == "acquisition stopped"
    assert str(row.data(0, LIST_STATUS_ROLE)) == display_status_label("failed")
    # The screen-reader text carries the new status too, not only the label.
    announced = str(row.data(0, Qt.ItemDataRole.AccessibleTextRole))
    assert "acquisition stopped" in announced


def test_a_refreshed_row_is_re_matched_against_the_active_filter(tmp_path: Path) -> None:
    """The visible set must agree with the decorations just written."""

    from tomography_session_browser.services.item_status import ItemListStatus

    session, _overview, map_1, _map_2 = _session(tmp_path)
    window = _window(session)
    tab = window._viewer_tabs["Search map"]
    tab.set_items(
        [map_1],
        search_map_preview_path,
        window._label_for,
        item_status_for=lambda _value: ItemListStatus("failed", "acquisition stopped"),
    )
    tab.list_filter.setText("acquisition stopped")
    assert tab.list.topLevelItem(0).isHidden() is False

    tab.refresh_providers(
        search_map_preview_path,
        item_status_for=lambda _value: ItemListStatus("done", "complete"),
    )

    assert tab.list.topLevelItem(0).isHidden() is True
    assert tab.list_count.text() == "0 of 1"
