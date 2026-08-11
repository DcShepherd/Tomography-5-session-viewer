"""H1: bounded Back/Forward over explicit navigation only."""

from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from tomography_session_browser.domain.enums import SessionKind
from tomography_session_browser.domain.models import (
    BatchPosition,
    MdocSection,
    Overview,
    Sample,
    SearchMap,
    Session,
    TiltSeries,
)
from tomography_session_browser.ui.navigation_history import (
    HistoryLocation,
    NavigationHistory,
)
from tomography_session_browser.ui.main_window import TAB_LABELS, MainWindow


def _loc(object_id: str, *, scope: str = "Session:s1", tab: str = "Search map", **kw):
    return HistoryLocation(scope_key=scope, tab=tab, object_id=object_id, **kw)


# --- pure model ------------------------------------------------------------


def test_a_new_history_goes_nowhere() -> None:
    history = NavigationHistory()

    assert history.current() is None
    assert history.can_go_back is False
    assert history.can_go_forward is False
    assert history.back() is None
    assert history.forward() is None


def test_back_and_forward_walk_the_stack() -> None:
    history = NavigationHistory()
    for name in ("a", "b", "c"):
        history.record(_loc(name))

    assert history.current().object_id == "c"
    assert history.back().object_id == "b"
    assert history.back().object_id == "a"
    assert history.can_go_back is False
    assert history.forward().object_id == "b"
    assert history.forward().object_id == "c"
    assert history.can_go_forward is False


def test_recording_after_going_back_discards_the_forward_branch() -> None:
    history = NavigationHistory()
    for name in ("a", "b", "c"):
        history.record(_loc(name))
    history.back()
    history.back()

    history.record(_loc("d"))

    assert [entry.object_id for entry in history.entries] == ["a", "d"]
    assert history.can_go_forward is False


def test_arriving_where_we_already_are_is_not_a_new_step() -> None:
    """Re-selecting the current row must not fill the stack."""

    history = NavigationHistory()
    history.record(_loc("a"))

    assert history.record(_loc("a")) is False
    assert len(history.entries) == 1


def test_repeating_a_place_still_refreshes_frame_and_filter() -> None:
    history = NavigationHistory()
    history.record(_loc("a", filter_text=""))

    history.record(_loc("a", filter_text="vellio", frame_index=4))

    assert history.current().filter_text == "vellio"
    assert history.current().frame_index == 4


def test_the_same_object_in_a_different_scope_is_a_different_place() -> None:
    history = NavigationHistory()
    history.record(_loc("a", scope="Session:s1"))
    history.record(_loc("a", scope="Sample:s2"))

    assert len(history.entries) == 2


def test_a_different_marker_is_a_different_place() -> None:
    history = NavigationHistory()
    history.record(_loc("a", marker_id="m1"))
    history.record(_loc("a", marker_id="m2"))

    assert len(history.entries) == 2


def test_history_is_bounded_and_drops_the_oldest() -> None:
    history = NavigationHistory(limit=5)
    for index in range(20):
        history.record(_loc(f"item-{index}"))

    assert len(history.entries) == 5
    assert history.entries[0].object_id == "item-15"
    assert history.current().object_id == "item-19"


def test_pruning_removes_stale_entries_and_keeps_the_cursor_sane() -> None:
    history = NavigationHistory()
    for name in ("a", "gone", "b", "c"):
        history.record(_loc(name))
    history.back()  # cursor on "b"

    removed = history.prune(lambda entry: entry.object_id != "gone")

    assert removed == 1
    assert [entry.object_id for entry in history.entries] == ["a", "b", "c"]
    assert history.current().object_id == "b"


def test_pruning_everything_before_the_cursor_lands_on_the_first_survivor() -> None:
    history = NavigationHistory()
    for name in ("gone1", "gone2", "c"):
        history.record(_loc(name))
    history.back()
    history.back()  # cursor on "gone1"

    history.prune(lambda entry: entry.object_id == "c")

    assert history.current().object_id == "c"
    assert history.can_go_back is False


def test_pruning_everything_empties_the_history() -> None:
    history = NavigationHistory()
    history.record(_loc("a"))

    history.prune(lambda _entry: False)

    assert history.entries == ()
    assert history.current() is None
    assert history.can_go_back is False


def test_history_holds_no_entity_references() -> None:
    """Entries must be identifiers only, or a removed session stays alive."""

    location = _loc("a", marker_id="m", frame_index=2, filter_text="f", label="Map 1")

    for value in vars(location).values() if hasattr(location, "__dict__") else (
        getattr(location, slot) for slot in location.__slots__
    ):
        assert value is None or isinstance(value, (str, int)), value


# --- live integration ------------------------------------------------------


def _window(tmp_path: Path):
    QApplication.instance() or QApplication([])
    mrc = tmp_path / "t.mrc"
    mrc.write_bytes(b"0")
    maps = [SearchMap(id="sm-1", name="Map 1"), SearchMap(id="sm-2", name="Map 2")]
    overview = Overview(id="ov-1", name="Overview_1", image_path=tmp_path / "ov.jpg")
    for search_map in maps:
        search_map.overview = overview
    sample = Sample(
        id="s-1",
        name="Sample1",
        path=tmp_path,
        overviews=[overview],
        search_maps=maps,
        batch_positions=[BatchPosition(id="bp-1", name="Position 1")],
        tilt_series=[
            TiltSeries(
                id="tilt-1",
                name="vellio_1",
                mrc_path=mrc,
                sections=[MdocSection(z_value=i) for i in range(20)],
            )
        ],
    )
    session = Session(
        id="sess-1",
        name="Reference collection B",
        path=tmp_path,
        kind=SessionKind.MULTIGRID,
        samples=[sample],
    )
    window = MainWindow()
    window._sessions = [session]
    window._session = session
    window._active_context = session
    window._rebuild_sample_index()
    window._render_viewer_tabs()
    return window, maps, overview


def test_toolbar_actions_start_disabled(tmp_path: Path) -> None:
    window, _maps, _ov = _window(tmp_path)

    assert window.back_action.isEnabled() is False
    assert window.forward_action.isEnabled() is False


def test_an_explicit_jump_enables_back(tmp_path: Path) -> None:
    window, maps, _ov = _window(tmp_path)
    window.tabs.setCurrentIndex(TAB_LABELS.index("Search map"))
    window._viewer_tabs["Search map"].select_object(maps[1])

    window._viewer_navigation_requested(maps[1], "overview")

    assert TAB_LABELS[window.tabs.currentIndex()] == "Overview"
    assert window.back_action.isEnabled() is True


def test_a_first_dashboard_drill_down_enables_back(tmp_path: Path) -> None:
    window, maps, _ov = _window(tmp_path)
    assert TAB_LABELS[window.tabs.currentIndex()] == "Session"

    window._on_dashboard_tile_clicked("Search map", maps[1].id)

    assert TAB_LABELS[window.tabs.currentIndex()] == "Search map"
    assert window.back_action.isEnabled() is True


def test_back_returns_to_the_previous_tab_and_row(tmp_path: Path) -> None:
    window, maps, _ov = _window(tmp_path)
    window.tabs.setCurrentIndex(TAB_LABELS.index("Search map"))
    window._viewer_tabs["Search map"].select_object(maps[1])
    window._viewer_navigation_requested(maps[1], "overview")

    assert window.navigate_history_back() is True

    assert TAB_LABELS[window.tabs.currentIndex()] == "Search map"
    assert window._viewer_tabs["Search map"]._current_value is maps[1]
    assert window.forward_action.isEnabled() is True


def test_going_back_does_not_itself_record_a_step(tmp_path: Path) -> None:
    """Otherwise Back would push a new entry each time it was pressed."""

    window, maps, _ov = _window(tmp_path)
    window.tabs.setCurrentIndex(TAB_LABELS.index("Search map"))
    window._viewer_tabs["Search map"].select_object(maps[1])
    window._viewer_navigation_requested(maps[1], "overview")
    depth = len(window._history.entries)

    window.navigate_history_back()
    window.navigate_history_forward()

    assert len(window._history.entries) == depth


def test_passive_selection_is_not_history(tmp_path: Path) -> None:
    """Only explicit jumps count; selecting rows must not fill the stack."""

    window, maps, _ov = _window(tmp_path)
    window.tabs.setCurrentIndex(TAB_LABELS.index("Search map"))
    before = len(window._history.entries)

    window._viewer_tabs["Search map"].select_object(maps[0])
    window._viewer_tabs["Search map"].select_object(maps[1])

    assert len(window._history.entries) == before


def test_plain_tab_switching_is_not_history(tmp_path: Path) -> None:
    window, _maps, _ov = _window(tmp_path)
    before = len(window._history.entries)

    window.tabs.setCurrentIndex(TAB_LABELS.index("Overview"))
    window.tabs.setCurrentIndex(TAB_LABELS.index("Tilt series"))

    assert len(window._history.entries) == before


def test_shortcuts_are_bound(tmp_path: Path) -> None:
    window, _maps, _ov = _window(tmp_path)

    assert "Alt+Left" in window.back_action.shortcut().toString()
    assert "Alt+Right" in window.forward_action.shortcut().toString()


def test_history_survives_a_removed_scope_without_navigating_wrongly(
    tmp_path: Path,
) -> None:
    window, maps, _ov = _window(tmp_path)
    window.tabs.setCurrentIndex(TAB_LABELS.index("Search map"))
    window._viewer_tabs["Search map"].select_object(maps[1])
    window._record_history()
    assert window._history.entries

    window._sessions = []
    window._prune_history()

    assert window._history.entries == ()
    assert window.back_action.isEnabled() is False


def test_back_restores_the_recorded_tilt_frame(tmp_path: Path, monkeypatch) -> None:
    window, _maps, _ov = _window(tmp_path)
    tilt = window._sessions[0].samples[0].tilt_series[0]
    scope_key = window._viewer_state_scope_key()
    restored: list[int] = []
    monkeypatch.setattr(window._viewer_tabs["Tilt series"], "select_frame", restored.append)
    window._history.record(
        HistoryLocation(
            scope_key=scope_key,
            tab="Tilt series",
            object_type="TiltSeries",
            object_id=tilt.id,
            frame_index=7,
            label=tilt.name,
        )
    )
    window._history.record(
        HistoryLocation(scope_key=scope_key, tab="Overview", object_id="ov-1")
    )

    assert window.navigate_history_back() is True
    QApplication.processEvents()

    assert restored == [7]


def test_scope_keys_do_not_collide_for_same_ids_in_different_paths(
    tmp_path: Path,
) -> None:
    window, _maps, _ov = _window(tmp_path)
    first = Sample(id="same", name="Sample1", path=tmp_path / "a")
    second = Sample(id="same", name="Sample1", path=tmp_path / "b")

    window._active_context = first
    first_key = window._viewer_state_scope_key()
    window._active_context = second
    second_key = window._viewer_state_scope_key()

    assert first_key != second_key
