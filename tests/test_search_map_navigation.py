"""Regression tests for Overview/Search map navigation buttons.

The Search tab, Batch position tab, and Tilt series tab each expose
jump buttons to their linked entities. The Overview and Search map tabs
mirror that pattern for selected exposure areas.

These tests pin:
* Search map still exposes its linked ``Overview`` button;
* Overview and Search map expose an exposure-sensitive ``Search`` button;
* exposure double-clicks on Overview/Search map open the matching Search item
  without falling back to Batch position.
"""

from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

import tomography_session_browser.ui.main_window as main_window
from tomography_session_browser.domain.enums import SessionKind
from tomography_session_browser.domain.markers import ImageMarker, MarkerType
from tomography_session_browser.domain.models import BatchPosition, Overview, Sample, SearchMap, SearchTile, Session, TiltSeries
from tomography_session_browser.ui.main_window import MainWindow, TAB_LABELS


def _app() -> QApplication:
    app = QApplication.instance()
    return app or QApplication([])


def _make_session(tmp_path: Path, *, with_overview: bool) -> Session:
    overview = Overview(id="ov-1", name="Overview_001", image_path=tmp_path / "ov.jpg")
    search_map = SearchMap(
        id="sm-1",
        name="SearchMap_001",
        overview=overview if with_overview else None,
    )
    sample = Sample(id="s", name="vellio", path=tmp_path / "Sample1", search_maps=[search_map])
    if with_overview:
        sample.overviews = [overview]
    return Session(
        id="sess",
        name="sess",
        path=tmp_path,
        kind=SessionKind.MULTIGRID,
        samples=[sample],
    )


def test_search_map_navigation_actions_offers_overview_jump_when_linked(tmp_path: Path) -> None:
    _app()
    session = _make_session(tmp_path, with_overview=True)
    window = MainWindow()
    window._sessions = [session]
    window._session = session
    window._active_context = session
    window._rebuild_sample_index()

    search_map = session.samples[0].search_maps[0]
    actions = window._viewer_navigation_actions(search_map)

    keys = [action.key for action in actions]
    assert keys == ["search", "overview"]
    assert actions[0].enabled is False
    assert "Select an exposure area" in actions[0].tooltip
    overview_action = actions[1]
    assert overview_action.label == "\u2197 Overview"
    assert overview_action.enabled is True
    assert "Open linked overview" in overview_action.tooltip


def test_search_map_navigation_action_disabled_when_no_overview_linked(tmp_path: Path) -> None:
    _app()
    session = _make_session(tmp_path, with_overview=False)
    window = MainWindow()
    window._sessions = [session]
    window._session = session
    window._active_context = session
    window._rebuild_sample_index()

    search_map = session.samples[0].search_maps[0]
    actions = window._viewer_navigation_actions(search_map)

    assert len(actions) == 2
    assert actions[0].key == "search"
    assert actions[0].enabled is False
    assert "Select an exposure area" in actions[0].tooltip
    assert actions[1].key == "overview"
    assert actions[1].enabled is False
    assert "No linked overview" in actions[1].tooltip


def test_search_map_navigation_request_opens_linked_overview(tmp_path: Path) -> None:
    """End-to-end: clicking the Overview button on a Search Map switches
    the active context to the overview's tab and selects it."""

    app = _app()
    session = _make_session(tmp_path, with_overview=True)
    window = MainWindow()
    window._sessions = [session]
    window._session = session
    window._active_context = session
    window._rebuild_sample_index()
    app.processEvents()

    search_map = session.samples[0].search_maps[0]
    overview = session.samples[0].overviews[0]

    window._viewer_navigation_requested(search_map, "overview")
    app.processEvents()

    # The status bar reports the jump; that's the cleanest "did anything
    # happen?" signal without coupling to widget internals.
    assert overview.name in window.statusBar().currentMessage()


def test_search_map_overview_jump_transfers_selected_exposure_marker(
    monkeypatch,
    tmp_path: Path,
) -> None:
    """The Overview jump keeps the selected exposure, not the Search-map tile."""

    _app()
    session, overview, search_map, batch, _search_tile = _make_exposure_link_session(
        tmp_path
    )
    source_exposure = _exposure_marker(
        search_map.id,
        batch.id,
        area_name="Exposure 2",
        exposure_index=1,
    )
    destination_primary = _exposure_marker(overview.id, batch.id)
    destination_exposure = _exposure_marker(
        overview.id,
        batch.id,
        area_name="Exposure 2",
        exposure_index=1,
    )
    search_map_region = ImageMarker(
        id=f"{overview.id}:search-map:{search_map.id}",
        marker_type=MarkerType.SEARCH_MAP,
        linked_object_id=search_map.id,
        source_object_id=overview.id,
    )
    markers_by_source = {
        search_map.id: [source_exposure],
        overview.id: [
            search_map_region,
            destination_primary,
            destination_exposure,
        ],
    }

    window = MainWindow()
    window._sessions = [session]
    window._session = session
    window._active_context = session
    window._rebuild_sample_index()
    window._viewer_tabs["Search map"].select_marker(source_exposure.id)
    monkeypatch.setattr(
        main_window,
        "markers_for_object",
        lambda value, **_kwargs: markers_by_source.get(value.id, []),
    )

    window._viewer_navigation_requested(search_map, "overview")

    selected_marker_id = window._viewer_tabs["Overview"].selected_marker_id()
    assert selected_marker_id == destination_exposure.id
    assert selected_marker_id != search_map_region.id
    selected_markers = [
        marker
        for marker in window._viewer_tabs["Overview"].viewer._markers
        if marker.selected
    ]
    assert [marker.id for marker in selected_markers] == [destination_exposure.id]


def test_search_map_overview_jump_without_exposure_keeps_region_highlight(
    monkeypatch,
    tmp_path: Path,
) -> None:
    """With no selected exposure, retain the established whole-map highlight."""

    _app()
    session, overview, search_map, _batch, _search_tile = (
        _make_exposure_link_session(tmp_path)
    )
    search_map_region = ImageMarker(
        id=f"{overview.id}:search-map:{search_map.id}",
        marker_type=MarkerType.SEARCH_MAP,
        linked_object_id=search_map.id,
        source_object_id=overview.id,
    )
    window = MainWindow()
    window._sessions = [session]
    window._session = session
    window._active_context = session
    window._rebuild_sample_index()
    monkeypatch.setattr(
        main_window,
        "markers_for_object",
        lambda value, **_kwargs: [search_map_region]
        if value.id == overview.id
        else [],
    )

    window._viewer_navigation_requested(search_map, "overview")

    assert (
        window._viewer_tabs["Overview"].selected_marker_id()
        == search_map_region.id
    )


def _make_exposure_link_session(tmp_path: Path) -> tuple[Session, Overview, SearchMap, BatchPosition, SearchTile]:
    overview = Overview(id="ov-1", name="Overview_001", image_path=tmp_path / "ov.jpg")
    search_map = SearchMap(
        id="sm-1",
        name="SearchMap_001",
        overview=overview,
    )
    overview.linked_search_map_ids.append(search_map.id)
    batch = BatchPosition(
        id="bp-1",
        name="Position_1",
        linked_overview_id=overview.id,
        linked_search_map_id=search_map.id,
    )
    search_tile = SearchTile(
        id="st-1",
        name="Position_1 · Tile 4 · SearchMap_001",
        search_map_id=search_map.id,
        batch_position_id=batch.id,
        metadata={
            "ExposureAreaName": "Exposure",
            "ExposureDisplayName": "Position_1",
            "ExposureIsPrimary": True,
        },
    )
    sample = Sample(
        id="sample-1",
        name="Sample 1",
        path=tmp_path / "Sample1",
        overviews=[overview],
        search_maps=[search_map],
        search_tiles=[search_tile],
        batch_positions=[batch],
    )
    session = Session(id="session-1", name="Session 1", path=tmp_path, kind=SessionKind.COLLECTION, samples=[sample])
    return session, overview, search_map, batch, search_tile


def _exposure_marker(
    source_id: str,
    batch_id: str,
    *,
    area_name: str = "Exposure",
    exposure_index: int = 0,
) -> ImageMarker:
    return ImageMarker(
        id=f"{source_id}:batch:{batch_id}:exposure_area:{area_name}",
        marker_type=MarkerType.EXPOSURE_AREA,
        linked_object_id=batch_id,
        source_object_id=source_id,
        metadata={
            "batch_id": batch_id,
            "area_name": area_name,
            "exposure_index": exposure_index,
        },
    )


def test_overview_exposure_selection_enables_search_jump(monkeypatch, tmp_path: Path) -> None:
    _app()
    session, overview, _search_map, batch, search_tile = _make_exposure_link_session(tmp_path)
    marker = _exposure_marker(overview.id, batch.id)
    window = MainWindow()
    window._sessions = [session]
    window._session = session
    window._active_context = session
    window._rebuild_sample_index()
    window._viewer_tabs["Overview"].select_marker(marker.id)
    monkeypatch.setattr(main_window, "markers_for_object", lambda _value, **_kwargs: [marker])

    action = next(action for action in window._viewer_navigation_actions(overview) if action.key == "search")

    assert action.enabled is True
    assert action.tooltip.startswith(
        "Jump to the search item associated with this exposure area."
    )
    assert search_tile.name in action.tooltip
    assert window._search_tile_for_exposure_marker(marker) is search_tile


def test_search_map_exposure_selection_enables_search_jump(monkeypatch, tmp_path: Path) -> None:
    _app()
    session, _overview, search_map, batch, _search_tile = _make_exposure_link_session(tmp_path)
    marker = _exposure_marker(search_map.id, batch.id)
    window = MainWindow()
    window._sessions = [session]
    window._session = session
    window._active_context = session
    window._rebuild_sample_index()
    window._viewer_tabs["Search map"].select_marker(marker.id)
    monkeypatch.setattr(main_window, "markers_for_object", lambda _value, **_kwargs: [marker])

    action = next(action for action in window._viewer_navigation_actions(search_map) if action.key == "search")

    assert action.enabled is True
    assert action.tooltip.startswith(
        "Jump to the search item associated with this exposure area."
    )
    assert "Position_1" in action.tooltip


def test_overview_exposure_jump_request_opens_search_item(monkeypatch, tmp_path: Path) -> None:
    _app()
    session, overview, _search_map, batch, search_tile = _make_exposure_link_session(tmp_path)
    marker = _exposure_marker(overview.id, batch.id)
    window = MainWindow()
    window._sessions = [session]
    window._session = session
    window._active_context = session
    window._rebuild_sample_index()
    window._viewer_tabs["Overview"].select_marker(marker.id)
    selected: list[object] = []
    monkeypatch.setattr(main_window, "markers_for_object", lambda _value, **_kwargs: [marker])
    monkeypatch.setattr(window, "_render_viewer_tabs", lambda: None)
    monkeypatch.setattr(window, "_select_viewer_object", lambda value: selected.append(value))

    window._viewer_navigation_requested(overview, "search")

    assert selected == [search_tile]


def test_overview_exposure_double_click_opens_matching_search_item(monkeypatch, tmp_path: Path) -> None:
    _app()
    session, overview, _search_map, batch, search_tile = _make_exposure_link_session(tmp_path)
    marker = _exposure_marker(overview.id, batch.id)
    window = MainWindow()
    window._sessions = [session]
    window._session = session
    window._active_context = session
    window._rebuild_sample_index()
    window.tabs.setCurrentIndex(TAB_LABELS.index("Overview"))
    selected: list[object] = []
    monkeypatch.setattr(window, "_render_viewer_tabs", lambda: None)
    monkeypatch.setattr(window, "_select_viewer_object", lambda value: selected.append(value))

    window._select_marker(marker, navigate=True)

    assert selected == [search_tile]
    assert window._selection_state.selected_object_type == "SearchTile"
    assert window._selection_state.selected_object_id == search_tile.id


def test_search_map_exposure_double_click_opens_matching_search_item(monkeypatch, tmp_path: Path) -> None:
    _app()
    search_map = SearchMap(id="sm-1", name="SearchMap_001")
    batch = BatchPosition(id="bp-1", name="Position_1", linked_search_map_id=search_map.id)
    search_tile = SearchTile(
        id="st-1",
        name="Position_1 · Tile 4 · SearchMap_001",
        search_map_id=search_map.id,
        batch_position_id=batch.id,
        metadata={
            "ExposureAreaName": "Exposure",
            "ExposureDisplayName": "Position_1",
            "ExposureIsPrimary": True,
        },
    )
    sample = Sample(
        id="sample-1",
        name="Sample 1",
        path=tmp_path / "Sample1",
        search_maps=[search_map],
        search_tiles=[search_tile],
        batch_positions=[batch],
    )
    session = Session(id="session-1", name="Session 1", path=tmp_path, kind=SessionKind.COLLECTION, samples=[sample])
    marker = ImageMarker(
        id="sm-1:batch:bp-1:exposure_area:Exposure",
        marker_type=MarkerType.EXPOSURE_AREA,
        linked_object_id=batch.id,
        source_object_id=search_map.id,
        metadata={"batch_id": batch.id, "area_name": "Exposure", "exposure_index": 0},
    )
    window = MainWindow()
    window._sessions = [session]
    window._session = session
    window._active_context = session
    window._rebuild_sample_index()
    window.tabs.setCurrentIndex(TAB_LABELS.index("Search map"))
    selected: list[object] = []
    monkeypatch.setattr(window, "_render_viewer_tabs", lambda: None)
    monkeypatch.setattr(window, "_select_viewer_object", lambda value: selected.append(value))

    window._select_marker(marker, navigate=True)

    assert selected == [search_tile]
    assert window._selection_state.selected_object_type == "SearchTile"
    assert window._selection_state.selected_object_id == search_tile.id


def test_search_map_exposure_double_click_does_not_fall_back_to_batch(monkeypatch, tmp_path: Path) -> None:
    _app()
    search_map = SearchMap(id="sm-1", name="SearchMap_001")
    batch = BatchPosition(id="bp-1", name="Position_1", linked_search_map_id=search_map.id)
    sample = Sample(
        id="sample-1",
        name="Sample 1",
        path=tmp_path / "Sample1",
        search_maps=[search_map],
        batch_positions=[batch],
    )
    session = Session(id="session-1", name="Session 1", path=tmp_path, kind=SessionKind.COLLECTION, samples=[sample])
    marker = ImageMarker(
        id="sm-1:batch:bp-1:exposure_area:Exposure",
        marker_type=MarkerType.EXPOSURE_AREA,
        linked_object_id=batch.id,
        source_object_id=search_map.id,
        metadata={"batch_id": batch.id, "area_name": "Exposure", "exposure_index": 0},
    )
    window = MainWindow()
    window._sessions = [session]
    window._session = session
    window._active_context = session
    window._rebuild_sample_index()
    window.tabs.setCurrentIndex(TAB_LABELS.index("Search map"))
    selected: list[object] = []
    monkeypatch.setattr(window, "_select_viewer_object", lambda value: selected.append(value))

    window._select_marker(marker, navigate=True)

    assert selected == []
    assert window.statusBar().currentMessage() == "No linked search item found for this exposure area."


def test_search_exposure_double_click_opens_matching_tilt_series(monkeypatch, tmp_path: Path) -> None:
    _app()
    search_map = SearchMap(id="sm-1", name="SearchMap_001")
    batch = BatchPosition(id="bp-1", name="Position_1", linked_search_map_id=search_map.id)
    tilt = TiltSeries(
        id="ts-1",
        name="Position_1",
        mrc_path=tmp_path / "Position_1.mrc",
        linked_batch_position_id=batch.id,
    )
    batch.linked_tilt_series_ids.append(tilt.id)
    search_tile = SearchTile(
        id="st-1",
        name="Position_1 · Tile 4 · SearchMap_001",
        search_map_id=search_map.id,
        batch_position_id=batch.id,
        linked_tilt_series_ids=[tilt.id],
        metadata={
            "ExposureAreaName": "Exposure",
            "ExposureDisplayName": "Position_1",
            "ExposureIsPrimary": True,
        },
    )
    sample = Sample(
        id="sample-1",
        name="Sample 1",
        path=tmp_path / "Sample1",
        search_maps=[search_map],
        search_tiles=[search_tile],
        batch_positions=[batch],
        tilt_series=[tilt],
    )
    session = Session(id="session-1", name="Session 1", path=tmp_path, kind=SessionKind.COLLECTION, samples=[sample])
    marker = _exposure_marker(search_tile.id, batch.id)
    window = MainWindow()
    window._sessions = [session]
    window._session = session
    window._active_context = session
    window._rebuild_sample_index()
    window.tabs.setCurrentIndex(TAB_LABELS.index("Search"))
    selected: list[object] = []
    monkeypatch.setattr(window, "_render_viewer_tabs", lambda: None)
    monkeypatch.setattr(window, "_select_viewer_object", lambda value: selected.append(value))

    window._select_marker(marker, navigate=True)

    assert selected == [tilt]
    assert window._selection_state.selected_object_type == "TiltSeries"
    assert window._selection_state.selected_object_id == tilt.id
